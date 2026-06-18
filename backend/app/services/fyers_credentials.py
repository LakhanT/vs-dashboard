"""Read Fyers credentials.txt and sync values into backend/.env."""

from __future__ import annotations

import re
from pathlib import Path


def parse_credentials_text(text: str) -> dict[str, str]:
    """Parse Definedge/Fyers credentials.txt format."""
    patterns = {
        "client_id": r"App\s*ID\s*:\s*(\S+)",
        "secret_key": r"Secret\s*ID\s*:\s*(\S+)",
        "fy_id": r"Phone\s*No\s*:\s*(\S+)",
        "pin": r"Pin\s*:\s*(\S+)",
        "totp_key": r"TOTP\s*Key\s*:\s*(\S+)",
    }
    result: dict[str, str] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result[key] = match.group(1).strip()
    return result


def load_credentials_file(path: str | Path) -> dict[str, str]:
    return parse_credentials_text(Path(path).read_text(encoding="utf-8"))


def upsert_env_file(env_path: Path, updates: dict[str, str]) -> None:
    """Update or append KEY=value lines in a .env file."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    written: set[str] = set()
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                written.add(key)
                continue
        out.append(line)

    for key, value in updates.items():
        if key not in written:
            out.append(f"{key}={value}")

    env_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def credentials_env_updates(creds: dict[str, str], *, credentials_file: str | Path) -> dict[str, str]:
    """Map parsed credentials to Settings env var names."""
    updates: dict[str, str] = {
        "FYERS_CREDENTIALS_FILE": str(credentials_file),
    }
    if creds.get("client_id"):
        updates["FYERS_CLIENT_ID"] = creds["client_id"]
    if creds.get("secret_key"):
        updates["FYERS_SECRET_KEY"] = creds["secret_key"]
    if creds.get("fy_id"):
        updates["FYERS_FY_ID"] = creds["fy_id"]
    if creds.get("pin"):
        updates["FYERS_PIN"] = creds["pin"]
    if creds.get("totp_key"):
        updates["FYERS_TOTP_KEY"] = creds["totp_key"]
    return updates


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def save_credentials_and_sync_env(
    content: bytes,
    *,
    backend_root: Path | None = None,
    filename: str = "credentials.txt",
) -> dict[str, str]:
    """Save credentials file under backend/ and write FYERS_* vars to .env."""
    root = backend_root or _backend_root()
    name = (filename or "credentials.txt").lower()
    if not name.endswith(".txt"):
        raise ValueError("Upload a .txt file (credentials.txt from Fyers)")

    creds = parse_credentials_text(content.decode("utf-8-sig"))
    if not creds.get("client_id") or not creds.get("secret_key"):
        raise ValueError("credentials.txt must contain App ID and Secret ID")

    cred_path = root / "credentials.txt"
    cred_path.write_bytes(content)
    env_path = root / ".env"
    updates = credentials_env_updates(creds, credentials_file=cred_path)
    upsert_env_file(env_path, updates)

    from app.config import get_settings

    get_settings.cache_clear()

    return {
        "app_configured": True,
        "client_id_set": bool(creds.get("client_id")),
        "secret_set": bool(creds.get("secret_key")),
        "credentials_path": str(cred_path),
    }
