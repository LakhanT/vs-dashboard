"""Fyers API v3 live quotes (primary LTP source during market hours)."""

from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.models import Stock
from app.services.fyers_auth import (
    auth_status,
    ensure_access_token,
    resolve_app_credentials,
    start_login_in_background,
)
from app.services.symbol_utils import to_fyers_symbol
from app.services.yahoo import LiveQuote

logger = logging.getLogger(__name__)
settings = get_settings()

BATCH_SIZE = 50
REQUEST_PAUSE_SEC = 0.25


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
    if not stocks or not fyers_configured():
        return {}

    access_token = ensure_access_token()
    if not access_token:
        logger.debug("Fyers quotes skipped — no access token")
        return {}

    client_id, _, _ = resolve_app_credentials()
    symbol_to_id: dict[str, int] = {}
    for stock in stocks:
        symbol = to_fyers_symbol(stock)
        if symbol:
            _register_symbol(symbol_to_id, symbol, stock.id)

    if not symbol_to_id:
        return {}

    try:
        from fyers_apiv3 import fyersModel

        fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fyers client init failed: %s", exc)
        return {}

    quotes: dict[int, LiveQuote] = {}
    symbols = list({s for s in symbol_to_id if ":" in s})

    for start in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[start : start + BATCH_SIZE]
        batch_quotes = _fetch_batch_fyers(fyers, batch, symbol_to_id)
        quotes.update(batch_quotes)
        time.sleep(REQUEST_PAUSE_SEC)

    if quotes:
        logger.info("Fyers quotes: %s/%s symbols", len(quotes), len(stocks))
    elif stocks:
        logger.debug("Fyers returned no quotes for %s symbols", len(stocks))
    return quotes


def _fetch_batch_fyers(fyers, symbols: list[str], symbol_to_id: dict[str, int]) -> dict[int, LiveQuote]:
    try:
        response = fyers.quotes({"symbols": ",".join(symbols)})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fyers quotes request failed: %s", exc)
        return {}

    if response.get("s") != "ok":
        logger.warning("Fyers quotes error: %s", response.get("message", response))
        return {}

    quotes: dict[int, LiveQuote] = {}
    for item in response.get("d", []):
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


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_fyers_status() -> dict:
    status = auth_status()
    return {
        "configured": status["app_configured"],
        "client_id_set": status["app_configured"],
        "token_ready": status["token_ready"],
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
