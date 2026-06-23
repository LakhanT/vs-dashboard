"""NSE/BSE trading calendar helpers (IST)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")


def market_today() -> date:
    """Today's date in India (IST) — use instead of date.today() on UTC servers."""
    return datetime.now(IST).date()


def market_now() -> datetime:
    return datetime.now(IST)


def normalize_daily_bars_to_ist(data: pd.DataFrame) -> pd.DataFrame:
    """
    Map Yahoo daily bars to IST calendar dates and collapse duplicates per day.

    yfinance often returns UTC or US timestamps; a single NSE session can land on
    the wrong calendar day if we only strip timezone without converting.
    """
    if data is None or data.empty:
        return data

    frame = data.copy()
    idx = pd.to_datetime(frame.index)
    if idx.tz is None:
        # Daily .NS bars from Yahoo are exchange-local; treat naive as IST.
        idx = idx.tz_localize(IST)
    else:
        idx = idx.tz_convert(IST)

    frame.index = idx
    frame["_ist_date"] = frame.index.date
    grouped = (
        frame.groupby("_ist_date", sort=True)
        .agg(
            {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
            }
        )
        .dropna()
    )
    grouped.index = pd.to_datetime(grouped.index)
    return grouped.sort_index()


def reference_trading_day(daily: pd.DataFrame) -> pd.Timestamp:
    """Last available daily bar date, capped at IST today."""
    today = pd.Timestamp(market_today())
    if daily is None or daily.empty:
        return today
    last_bar = pd.Timestamp(daily.index[-1]).normalize()
    return min(last_bar, today)
