"""Fyers API v3 live quotes (primary LTP source during market hours)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.config import get_settings
from app.models import Stock
from app.services.fyers_auth import (
    auth_status,
    ensure_access_token,
    fyers_authorization_header,
    resolve_app_credentials,
    resolve_fyers_auth,
    start_login_in_background,
    verify_fyers_connection,
)
from app.services.symbol_utils import to_fyers_symbol
from app.services.yahoo import LiveQuote

logger = logging.getLogger(__name__)
settings = get_settings()

MAX_SYMBOLS_PER_REQUEST = 50
FYERS_DATA_QUOTES_URL = "https://api-t1.fyers.in/data/quotes"

_auth_verified = False


def get_access_token(*, force_refresh: bool = False) -> str | None:
    if force_refresh:
        from app.services.fyers_auth import token_file_path

        path = token_file_path()
        if path.exists():
            path.unlink()
    return ensure_access_token()


def fyers_configured() -> bool:
    client_id, secret_key, _ = resolve_app_credentials()
    return bool(client_id and secret_key)


def _register_symbol(symbol_to_id: dict[str, int], fyers_symbol: str, stock_id: int) -> None:
    symbol_to_id[fyers_symbol] = stock_id
    symbol_to_id[fyers_symbol.upper()] = stock_id
    if ":" in fyers_symbol:
        short = fyers_symbol.split(":", 1)[1]
        symbol_to_id[short] = stock_id
        symbol_to_id[short.upper()] = stock_id


def _valid_fyers_symbol(symbol: str) -> bool:
    if not symbol or ":" not in symbol:
        return False
    exchange, short = symbol.split(":", 1)
    if exchange not in ("NSE", "BSE") or not short:
        return False
    return short.endswith("-EQ")


def _normalize_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for symbol in symbols:
        text = str(symbol).strip()
        if not _valid_fyers_symbol(text):
            continue
        key = text.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _resolve_stock_id(symbol_to_id: dict[str, int], fyers_symbol: str | None) -> int | None:
    if not fyers_symbol:
        return None
    text = str(fyers_symbol)
    return (
        symbol_to_id.get(text)
        or symbol_to_id.get(text.upper())
        or symbol_to_id.get(text.split(":")[-1].upper())
    )


def fetch_live_quotes(stocks: list[Stock]) -> dict[int, LiveQuote]:
    return fetch_live_quotes_parallel(stocks)


def _fetch_symbols_batch(
    client_id: str,
    access_token: str,
    symbols: list[str],
    symbol_to_id: dict[str, int],
) -> dict[int, LiveQuote]:
    symbols = _normalize_symbols(symbols)
    if not symbols:
        return {}
    return _fetch_batch_fyers_resilient(client_id, access_token, symbols, symbol_to_id)


def fetch_live_quotes_parallel(
    stocks: list[Stock],
    *,
    batch_size: int | None = None,
    max_workers: int | None = None,
    on_batch: Callable[[dict[int, LiveQuote]], None] | None = None,
) -> dict[int, LiveQuote]:
    """Fetch Fyers quotes for all stocks concurrently (async-style parallel HTTP)."""
    if not stocks or not fyers_configured():
        return {}

    global _auth_verified  # noqa: PLW0603

    auth = resolve_fyers_auth()
    if not auth:
        logger.debug("Fyers quotes skipped — no access token")
        return {}
    client_id, access_token = auth

    if not _auth_verified:
        ok, err = verify_fyers_connection()
        if not ok:
            logger.error("Fyers auth check failed: %s — re-upload token.json or login again", err)
            return {}
        _auth_verified = True
        logger.info("Fyers auth verified for app %s", client_id[:8] + "…")

    symbol_to_id: dict[str, int] = {}
    for stock in stocks:
        symbol = to_fyers_symbol(stock)
        if symbol and _valid_fyers_symbol(symbol):
            _register_symbol(symbol_to_id, symbol, stock.id)

    all_symbols = _normalize_symbols(list(symbol_to_id.keys()))
    if not all_symbols:
        logger.warning("Fyers quotes: no valid symbols for %s stocks", len(stocks))
        return {}

    chunk = min(
        MAX_SYMBOLS_PER_REQUEST,
        max(1, batch_size or settings.live_price_batch_size),
    )
    symbol_batches = [all_symbols[i : i + chunk] for i in range(0, len(all_symbols), chunk)]
    workers = max(1, min(max_workers or settings.live_price_parallel_workers, len(symbol_batches)))

    quotes: dict[int, LiveQuote] = {}
    failed_batches = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fyers-quotes") as executor:
        futures = [
            executor.submit(_fetch_symbols_batch, client_id, access_token, batch, symbol_to_id)
            for batch in symbol_batches
        ]
        for future in as_completed(futures):
            try:
                batch_quotes = future.result()
            except Exception as exc:  # noqa: BLE001
                failed_batches += 1
                logger.warning("Fyers parallel batch failed: %s", exc)
                continue
            if not batch_quotes:
                failed_batches += 1
                continue
            quotes.update(batch_quotes)
            if on_batch:
                try:
                    on_batch(batch_quotes)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Fyers on_batch callback failed: %s", exc)

    if quotes:
        logger.info(
            "Fyers parallel quotes: %s/%s stocks (%s symbols, %s batches, %s workers)",
            len(quotes),
            len(stocks),
            len(all_symbols),
            len(symbol_batches),
            workers,
        )
    elif stocks:
        logger.warning(
            "Fyers returned no quotes for %s stocks (%s batches failed of %s)",
            len(stocks),
            failed_batches,
            len(symbol_batches),
        )
    return quotes


def fetch_live_quotes_sequential(stocks: list[Stock]) -> dict[int, LiveQuote]:
    """Sequential fallback — one batch at a time."""
    if not stocks or not fyers_configured():
        return {}

    client_id, _, _ = resolve_app_credentials()
    auth = resolve_fyers_auth()
    if not auth:
        return {}

    client_id, access_token = auth
    symbol_to_id: dict[str, int] = {}
    for stock in stocks:
        symbol = to_fyers_symbol(stock)
        if symbol and _valid_fyers_symbol(symbol):
            _register_symbol(symbol_to_id, symbol, stock.id)

    all_symbols = _normalize_symbols(list(symbol_to_id.keys()))
    if not all_symbols:
        return {}

    chunk = min(MAX_SYMBOLS_PER_REQUEST, max(1, settings.live_price_batch_size))
    quotes: dict[int, LiveQuote] = {}
    for start in range(0, len(all_symbols), chunk):
        batch = all_symbols[start : start + chunk]
        quotes.update(_fetch_symbols_batch(client_id, access_token, batch, symbol_to_id))
    return quotes


def _fetch_batch_fyers_resilient(
    client_id: str,
    access_token: str,
    symbols: list[str],
    symbol_to_id: dict[str, int],
) -> dict[int, LiveQuote]:
    """Fetch quotes; on Bad Request split batch to isolate invalid symbols."""
    symbols = _normalize_symbols(symbols)
    if not symbols:
        return {}

    quotes, ok, _err = _fetch_batch_fyers_http(client_id, access_token, symbols, symbol_to_id)
    if ok:
        return quotes

    if len(symbols) == 1:
        logger.debug("Fyers no quote for symbol: %s", symbols[0])
        return {}

    mid = len(symbols) // 2
    left = _fetch_batch_fyers_resilient(client_id, access_token, symbols[:mid], symbol_to_id)
    right = _fetch_batch_fyers_resilient(client_id, access_token, symbols[mid:], symbol_to_id)
    left.update(right)
    return left


def _parse_quote_items(items: list, symbol_to_id: dict[str, int]) -> dict[int, LiveQuote]:
    quotes: dict[int, LiveQuote] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        fyers_symbol = item.get("n") or item.get("symbol")
        values = item.get("v") or {}
        if not fyers_symbol or not isinstance(values, dict):
            continue

        stock_id = _resolve_stock_id(symbol_to_id, str(fyers_symbol))
        if stock_id is None:
            short = values.get("short_name") or values.get("symbol")
            stock_id = _resolve_stock_id(symbol_to_id, str(short) if short else None)
        if stock_id is None:
            continue

        ltp = _to_float(values.get("lp"))
        if ltp is None:
            continue

        prev_close = _to_float(values.get("prev_close_price"))
        chp = _to_float(values.get("chp"))
        pct_change = chp / 100.0 if chp is not None else None
        if pct_change is None and prev_close:
            pct_change = (ltp - prev_close) / prev_close

        quotes[stock_id] = LiveQuote(
            ltp=ltp,
            pct_change=pct_change,
            week_52_high=_to_float(values.get("high_price")),
            week_52_low=_to_float(values.get("low_price")),
            source="fyers",
        )
    return quotes


def _fetch_batch_fyers_http(
    client_id: str,
    access_token: str,
    symbols: list[str],
    symbol_to_id: dict[str, int],
) -> tuple[dict[int, LiveQuote], bool, str | None]:
    if not symbols:
        return {}, True, None

    if len(symbols) > MAX_SYMBOLS_PER_REQUEST:
        symbols = symbols[:MAX_SYMBOLS_PER_REQUEST]

    try:
        import requests

        header = fyers_authorization_header(client_id, access_token)
        resp = requests.get(
            FYERS_DATA_QUOTES_URL,
            params={"symbols": ",".join(symbols)},
            headers={
                "Authorization": header,
                "Content-Type": "application/json",
                "version": "3",
            },
            timeout=20,
        )
        try:
            payload = resp.json()
        except Exception:
            return {}, False, f"HTTP {resp.status_code}: non-JSON body"

        if payload.get("s") != "ok":
            message = payload.get("message", payload)
            code = payload.get("code")
            detail = f"{message} (code={code}, http={resp.status_code})"
            if len(symbols) <= 3:
                logger.warning("Fyers quotes error: %s — symbols: %s", detail, symbols)
            else:
                logger.warning(
                    "Fyers quotes error: %s — batch size %s (first: %s)",
                    detail,
                    len(symbols),
                    symbols[0],
                )
            return {}, False, str(message)

        quotes = _parse_quote_items(payload.get("d", []), symbol_to_id)
        return quotes, True, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fyers quotes HTTP failed (%s symbols): %s", len(symbols), exc)
        return {}, False, str(exc)


def _fetch_batch_fyers(
    fyers,
    symbols: list[str],
    symbol_to_id: dict[str, int],
) -> tuple[dict[int, LiveQuote], bool]:
    """SDK fallback — prefer _fetch_batch_fyers_http."""
    if not symbols:
        return {}, True

    try:
        response = fyers.quotes({"symbols": ",".join(symbols)})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fyers SDK quotes failed (%s symbols): %s", len(symbols), exc)
        return {}, False

    if response.get("s") != "ok":
        return {}, False

    return _parse_quote_items(response.get("d", []), symbol_to_id), True


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_fyers_status() -> dict:
    status = auth_status()
    auth_ok, auth_error = verify_fyers_connection()
    return {
        "configured": status["app_configured"],
        "client_id_set": status["app_configured"],
        "token_ready": status["token_ready"],
        "auth_verified": auth_ok,
        "auth_error": auth_error,
        "login_in_progress": status["login_in_progress"],
        "redirect_uri": status["redirect_uri"],
        "expires_at": status["expires_at"],
        "credentials_file": settings.fyers_credentials_file or None,
    }


def trigger_browser_login() -> dict:
    if not fyers_configured():
        return {"started": False, "token_ready": False, "message": "Fyers app id / secret not configured"}

    if auth_status()["token_ready"]:
        return {
            "started": False,
            "token_ready": True,
            "message": "Fyers already connected.",
        }

    started = start_login_in_background()
    if not started:
        return {
            "started": False,
            "token_ready": auth_status()["token_ready"],
            "message": "Login already in progress…",
        }
    return {
        "started": True,
        "token_ready": False,
        "message": "Browser opened — complete Fyers login, then return here.",
        "redirect_uri": settings.fyers_redirect_uri,
    }
