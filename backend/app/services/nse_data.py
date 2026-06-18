from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from app.models import Stock
from app.services.symbol_utils import base_symbol

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "nse"
REQUEST_PAUSE_SEC = 0.25


def nse_symbol(stock: Stock) -> str | None:
    return base_symbol(stock)


def _ensure_jugaad_cache() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HOME", str(CACHE_DIR.parent.parent))


def fetch_nse_daily_history(stock: Stock, *, lookback_days: int = 500) -> pd.DataFrame | None:
    symbol = nse_symbol(stock)
    if not symbol:
        return None

    _ensure_jugaad_cache()
    to_date = date.today()
    from_date = to_date - timedelta(days=lookback_days)

    try:
        from jugaad_data.nse import stock_df

        raw = stock_df(symbol, from_date=from_date, to_date=to_date)
    except Exception as exc:  # noqa: BLE001
        logger.warning("NSE history failed for %s: %s", symbol, exc)
        return None

    if raw is None or raw.empty:
        return None

    frame = raw.rename(
        columns={
            "DATE": "Date",
            "OPEN": "Open",
            "HIGH": "High",
            "LOW": "Low",
            "CLOSE": "Close",
        }
    )
    if "Date" not in frame.columns:
        return None

    frame["Date"] = pd.to_datetime(frame["Date"]).dt.tz_localize(None)
    frame = frame.set_index("Date").sort_index()
    return frame[["Open", "High", "Low", "Close"]].astype(float)


def fetch_nse_histories(stocks: list[Stock]) -> dict[int, pd.DataFrame]:
    histories: dict[int, pd.DataFrame] = {}
    for stock in stocks:
        frame = fetch_nse_daily_history(stock)
        if frame is not None and not frame.empty:
            histories[stock.id] = frame
        time.sleep(REQUEST_PAUSE_SEC)
    return histories
