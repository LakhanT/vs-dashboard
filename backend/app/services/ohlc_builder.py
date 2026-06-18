from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from app.models import Timeframe


@dataclass
class CandleMetrics:
    timeframe: Timeframe
    period_label: str
    is_current: bool
    period_start: date | None
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    lcp: float | None
    pct_change_open: float | None
    pct_change_today: float | None
    high_retracement: float | None
    green_range: float | None
    retracement_from_high: float | None
    rise_from_low: float | None


def build_period_metrics(
    daily: pd.DataFrame,
    *,
    ltp: float | None = None,
) -> list[CandleMetrics]:
    if daily is None or daily.empty:
        return []

    daily = daily.sort_index()
    ltp = ltp if ltp is not None else float(daily["Close"].iloc[-1])
    pct_today = _pct_change_today(daily, ltp)

    metrics: list[CandleMetrics] = []
    specs = [
        (Timeframe.YEARLY, "YE", "Yearly", "Pre Yearly"),
        (Timeframe.QUARTERLY, "QE", "Quaterly", "Pre Quaterly"),
        (Timeframe.MONTHLY, "ME", "Monthly", "Pre Monthly"),
        (Timeframe.WEEKLY, "W-MON", "Weekly", "Pre Weekly"),
    ]

    for timeframe, rule, current_label, previous_label in specs:
        resampled = _resample_ohlc(daily, rule)
        if resampled.empty:
            continue

        current = resampled.iloc[-1]
        metrics.append(
            _to_metrics(
                timeframe=timeframe,
                period_label=current_label,
                is_current=True,
                candle=current,
                ltp=ltp,
                pct_today=pct_today,
            )
        )

        if len(resampled) > 1:
            previous = resampled.iloc[-2]
            metrics.append(
                _to_metrics(
                    timeframe=timeframe,
                    period_label=previous_label,
                    is_current=False,
                    candle=previous,
                    ltp=float(previous["Close"]),
                    pct_today=None,
                )
            )

    return metrics


def _resample_ohlc(daily: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (
        daily.resample(rule)
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
        .dropna()
    )


def _pct_change_today(daily: pd.DataFrame, ltp: float) -> float | None:
    if len(daily) < 2:
        return None
    prev_close = float(daily["Close"].iloc[-2])
    if not prev_close:
        return None
    return (ltp - prev_close) / prev_close


def _to_metrics(
    *,
    timeframe: Timeframe,
    period_label: str,
    is_current: bool,
    candle: pd.Series,
    ltp: float,
    pct_today: float | None,
) -> CandleMetrics:
    period_open = float(candle["Open"])
    high = float(candle["High"])
    low = float(candle["Low"])
    close = float(candle["Close"])

    pct_change_open = (ltp - period_open) / period_open if period_open else None
    high_retracement = (ltp - high) / high if high else None
    green_range = (ltp - period_open) / period_open if period_open else None
    retracement_from_high = (ltp - high) / high if high else None
    rise_from_low = (ltp - low) / low if low else None

    period_start = candle.name.date() if hasattr(candle.name, "date") else None

    return CandleMetrics(
        timeframe=timeframe,
        period_label=period_label,
        is_current=is_current,
        period_start=period_start,
        open=period_open,
        high=high,
        low=low,
        close=close,
        lcp=ltp,
        pct_change_open=pct_change_open,
        pct_change_today=pct_today,
        high_retracement=high_retracement,
        green_range=green_range,
        retracement_from_high=retracement_from_high,
        rise_from_low=rise_from_low,
    )
