"""
FYERS API v3 auth — browser OAuth with local callback (matches your working flow).

First login:
  - Local callback server on redirect URI port (default :5000/callback)
  - Browser opens FYERS login
  - auth_code captured → access_token saved to token.json

Later runs:
  - Reuse token.json until expires_in elapses
  - When expired, call login_via_browser() again (FYERS limitation)
"""

from __future__ import annotations

import json
import logging
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

TOKEN_FILE_NAME = "token.json"
TOKEN_EXPIRY_BUFFER = 60
LOGIN_TIMEOUT = 180

_auth_code_holder: dict[str, str | None] = {"code": None}
_login_lock = threading.Lock()
_login_in_progress = False


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def token_file_path() -> Path:
    return _backend_root() / TOKEN_FILE_NAME


def load_credentials_file(path: str | Path) -> dict[str, str]:
    """Parse credentials.txt — App ID and Secret ID (no secrets logged)."""
    from app.services.fyers_credentials import load_credentials_file as _load

    return _load(path)


def resolve_app_credentials() -> tuple[str, str, str]:
    """Return (client_id, secret_key, redirect_uri)."""
    client_id = settings.fyers_client_id
    secret_key = settings.fyers_secret_key
    redirect_uri = settings.fyers_redirect_uri

    if settings.fyers_credentials_file:
        try:
            from_file = load_credentials_file(settings.fyers_credentials_file)
            client_id = client_id or from_file.get("client_id", "")
            secret_key = secret_key or from_file.get("secret_key", "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read Fyers credentials file: %s", exc)

    return normalize_client_id(client_id), secret_key, redirect_uri


def normalize_client_id(client_id: str) -> str:
    """Fyers app id must be e.g. XXXXXX-100."""
    cid = (client_id or "").strip()
    if not cid:
        return ""
    if "-" not in cid:
        return f"{cid}-100"
    return cid


def normalize_access_token(client_id: str, access_token: str) -> str:
    """Strip accidental client_id: prefix so FyersModel does not double-prefix."""
    token = (access_token or "").strip()
    if not token:
        return ""
    cid = normalize_client_id(client_id)
    if cid and token.startswith(f"{cid}:"):
        return token[len(cid) + 1 :]
    # Handle pasted Authorization header "APP-100:eyJ..."
    if token.count(":") >= 2 and cid:
        parts = token.split(":")
        if len(parts) >= 3 and parts[0] == parts[1]:
            return ":".join(parts[2:])
    return token


def fyers_authorization_header(client_id: str, access_token: str) -> str:
    cid = normalize_client_id(client_id)
    token = normalize_access_token(cid, access_token)
    return f"{cid}:{token}"


def resolve_fyers_auth() -> tuple[str, str] | None:
    """Normalized (client_id, access_token) for API calls, or None."""
    client_id, _, _ = resolve_app_credentials()
    if not client_id:
        return None
    raw = get_stored_access_token()
    if not raw:
        return None
    token = normalize_access_token(client_id, raw)
    if not token:
        return None
    return client_id, token


def save_token(resp: dict) -> None:
    data = resp.copy()
    data.setdefault("created_at", time.time())
    data.setdefault("expires_in", 86400)
    path = token_file_path()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.info("Fyers token saved to %s", path.name)
    try:
        from app.services import fyers as fyers_module

        fyers_module._auth_verified = False  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass


def import_fyers_token_bytes(content: bytes, filename: str) -> dict[str, object]:
    """Validate and save token.json from manual upload (same format as browser login)."""
    name = (filename or "").lower()
    if not name.endswith(".json"):
        raise ValueError("Upload a .json file (token.json from Fyers login on your PC)")

    try:
        data = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid JSON — export token.json from VS Dashboard after Fyers login") from exc

    if not isinstance(data, dict):
        raise ValueError("token.json must be a JSON object")
    if not data.get("access_token"):
        raise ValueError("token.json missing access_token — log in to Fyers locally first")

    save_token(data)
    status = auth_status()
    return {
        "token_ready": status["token_ready"],
        "expires_at": status.get("expires_at"),
    }


def load_token() -> dict | None:
    path = token_file_path()
    if not path.exists():
        return None
    try:
        token = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(token, dict):
        return None
    # Repair tokens saved without expiry metadata (common with Fyers v3).
    if token.get("access_token") and "expires_in" not in token:
        token["expires_in"] = 86400
    if token.get("access_token") and "created_at" not in token:
        token["created_at"] = path.stat().st_mtime
    return token


def is_token_valid(token: dict | None) -> bool:
    if not token or not token.get("access_token"):
        return False
    if str(token.get("s", "")).lower() == "error":
        return False

    try:
        created_at = float(token.get("created_at", time.time()))
        expires_in = float(token.get("expires_in", 86400))
    except (TypeError, ValueError):
        return True

    return (time.time() - created_at) < (expires_in - TOKEN_EXPIRY_BUFFER)


def get_stored_access_token() -> str | None:
    if settings.fyers_access_token:
        return settings.fyers_access_token
    token = load_token()
    if is_token_valid(token):
        return token.get("access_token")
    return None


def generate_token_from_auth_code(auth_code: str) -> dict:
    from fyers_apiv3 import fyersModel

    client_id, secret_key, _ = resolve_app_credentials()
    if not client_id or not secret_key:
        raise RuntimeError("FYERS app id / secret not configured")

    session = fyersModel.SessionModel(
        client_id=client_id,
        secret_key=secret_key,
        grant_type="authorization_code",
    )
    session.set_token(auth_code)
    resp = session.generate_token()

    if resp.get("s") != "ok":
        raise RuntimeError(f"Fyers token generation failed: {resp}")

    save_token(resp)
    return resp


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.endswith("/callback"):
            self.send_response(404)
            self.end_headers()
            return

        auth_code = parse_qs(parsed.query).get("auth_code", [None])[0]
        if not auth_code:
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"auth_code not found")
            return

        _auth_code_holder["code"] = auth_code
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h2>Login successful.</h2>"
            b"<p>You can close this tab and return to VS Dashboard.</p></body></html>"
        )

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def _callback_port(redirect_uri: str) -> int:
    parsed = urlparse(redirect_uri)
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def _run_callback_server(port: int) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def get_auth_code_via_browser() -> str:
    """Open FYERS login in browser and wait for redirect callback."""
    from fyers_apiv3 import fyersModel

    client_id, _, redirect_uri = resolve_app_credentials()
    if not client_id:
        raise RuntimeError("FYERS app id not configured")

    port = _callback_port(redirect_uri)
    if port in (80, 443):
        raise RuntimeError(
            f"Redirect URI must use a local port for callback, got: {redirect_uri}"
        )

    _auth_code_holder["code"] = None
    server = _run_callback_server(port)
    time.sleep(0.5)

    session = fyersModel.SessionModel(
        client_id=client_id,
        redirect_uri=redirect_uri,
        response_type="code",
        grant_type="authorization_code",
        state="vsdashboard",
        scope="",
    )
    login_url = session.generate_authcode()
    logger.info("Opening Fyers browser login (callback :%s)", port)
    webbrowser.open(login_url, new=1)

    start = time.time()
    while _auth_code_holder["code"] is None:
        if time.time() - start > LOGIN_TIMEOUT:
            server.shutdown()
            raise TimeoutError("Fyers login timed out after 3 minutes")
        time.sleep(0.5)

    auth_code = _auth_code_holder["code"]
    _auth_code_holder["code"] = None
    server.shutdown()
    return auth_code


def login_via_browser() -> dict:
    """Full browser login → token.json."""
    auth_code = get_auth_code_via_browser()
    return generate_token_from_auth_code(auth_code)


def start_login_in_background() -> bool:
    """Non-blocking login for API — opens browser in a worker thread."""
    global _login_in_progress  # noqa: PLW0603

    with _login_lock:
        if _login_in_progress:
            return False
        _login_in_progress = True

    def worker() -> None:
        global _login_in_progress  # noqa: PLW0603
        try:
            login_via_browser()
            logger.info("Fyers background login completed")
        except Exception as exc:  # noqa: BLE001
            logger.error("Fyers background login failed: %s", exc)
        finally:
            with _login_lock:
                _login_in_progress = False

    threading.Thread(target=worker, name="fyers-login", daemon=True).start()
    return True


def login_in_progress() -> bool:
    with _login_lock:
        return _login_in_progress


def get_fyers_client():
    """Return authenticated FyersModel client (browser login if token missing/expired)."""
    from fyers_apiv3 import fyersModel

    client_id, _, _ = resolve_app_credentials()
    if not client_id:
        raise RuntimeError("FYERS app id not configured")

    token = load_token()
    if not is_token_valid(token):
        logger.info("Fyers token missing or expired — starting browser login")
        token = login_via_browser()

    access_token = normalize_access_token(client_id, token["access_token"])
    return fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="")


def verify_fyers_connection() -> tuple[bool, str | None]:
    """Call /profile to verify token + app id. Returns (ok, error_message)."""
    auth = resolve_fyers_auth()
    if not auth:
        return False, "Fyers token missing or app id not configured"
    client_id, access_token = auth
    try:
        import requests

        header = fyers_authorization_header(client_id, access_token)
        resp = requests.get(
            "https://api-t1.fyers.in/api/v3/profile",
            headers={"Authorization": header, "Content-Type": "application/json", "version": "3"},
            timeout=15,
        )
        payload = resp.json()
        if payload.get("s") == "ok":
            return True, None
        return False, str(payload.get("message") or payload)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def ensure_access_token(*, force_login: bool = False) -> str | None:
    """
    Return a valid access token without blocking the live-price loop.
    Does NOT auto-open browser — use start_login_in_background() or login_via_browser().
    """
    if force_login:
        path = token_file_path()
        if path.exists():
            path.unlink()

    stored = get_stored_access_token()
    if stored:
        client_id, _, _ = resolve_app_credentials()
        return normalize_access_token(client_id, stored)

    if login_in_progress():
        return None

    return None


def auth_status() -> dict:
    client_id, secret_key, redirect_uri = resolve_app_credentials()
    token = load_token()
    valid = is_token_valid(token)
    expires_at = None
    if valid and token:
        try:
            expires_at = token["created_at"] + token["expires_in"]
        except (KeyError, TypeError):
            pass

    return {
        "app_configured": bool(client_id and secret_key),
        "redirect_uri": redirect_uri,
        "token_ready": valid or bool(settings.fyers_access_token),
        "login_in_progress": login_in_progress(),
        "expires_at": expires_at,
    }
