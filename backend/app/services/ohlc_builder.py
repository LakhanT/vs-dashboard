from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from app.models import Timeframe
from app.services.market_calendar import market_today, reference_trading_day


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
    """
    Build Y/Q/M/W candles from daily OHLC using calendar period boundaries.

    - Yearly: 1 Jan → today
    - Quarterly: calendar quarter (Jan–Mar, Apr–Jun, Jul–Sep, Oct–Dec)
    - Monthly: 1st of month → today
    - Weekly: Monday → today
    """
    if daily is None or daily.empty:
        return []

    daily = daily.sort_index()
    daily.index = pd.to_datetime(daily.index).normalize()
    ref = reference_trading_day(daily)
    ltp = ltp if ltp is not None else float(daily["Close"].iloc[-1])
    pct_today = _pct_change_today(daily, ltp)

    metrics: list[CandleMetrics] = []
    specs = [
        (Timeframe.YEARLY, "Yearly", "Pre Yearly"),
        (Timeframe.QUARTERLY, "Quaterly", "Pre Quaterly"),
        (Timeframe.MONTHLY, "Monthly", "Pre Monthly"),
        (Timeframe.WEEKLY, "Weekly", "Pre Weekly"),
    ]

    for timeframe, current_label, previous_label in specs:
        current_start, _ = _current_period_bounds(timeframe, ref)
        current_slice = daily[daily.index >= current_start]
        if current_slice.empty:
            continue

        current_candle = _aggregate_ohlc(current_slice)
        actual_start = pd.Timestamp(current_slice.index[0]).date()
        metrics.append(
            _to_metrics(
                timeframe=timeframe,
                period_label=current_label,
                is_current=True,
                candle=current_candle,
                period_start=actual_start,
                ltp=ltp,
                pct_today=pct_today,
            )
        )

        prev_start, prev_end = _previous_period_bounds(timeframe, ref)
        prev_slice = daily[(daily.index >= prev_start) & (daily.index <= prev_end)]
        if not prev_slice.empty:
            prev_candle = _aggregate_ohlc(prev_slice)
            metrics.append(
                _to_metrics(
                    timeframe=timeframe,
                    period_label=previous_label,
                    is_current=False,
                    candle=prev_candle,
                    period_start=pd.Timestamp(prev_slice.index[0]).date(),
                    ltp=float(prev_candle["Close"]),
                    pct_today=None,
                )
            )

    return metrics


def _current_period_bounds(timeframe: Timeframe, ref: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    ref = pd.Timestamp(ref).normalize()
    if timeframe == Timeframe.YEARLY:
        start = pd.Timestamp(ref.year, 1, 1)
    elif timeframe == Timeframe.QUARTERLY:
        q_month = ((ref.month - 1) // 3) * 3 + 1
        start = pd.Timestamp(ref.year, q_month, 1)
    elif timeframe == Timeframe.MONTHLY:
        start = pd.Timestamp(ref.year, ref.month, 1)
    elif timeframe == Timeframe.WEEKLY:
        start = ref - pd.Timedelta(days=int(ref.weekday()))
    else:
        start = ref
    return start, ref


def _previous_period_bounds(timeframe: Timeframe, ref: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    ref = pd.Timestamp(ref).normalize()
    current_start, _ = _current_period_bounds(timeframe, ref)
    prev_end = current_start - pd.Timedelta(days=1)

    if timeframe == Timeframe.YEARLY:
        prev_start = pd.Timestamp(prev_end.year, 1, 1)
    elif timeframe == Timeframe.QUARTERLY:
        q_month = ((prev_end.month - 1) // 3) * 3 + 1
        prev_start = pd.Timestamp(prev_end.year, q_month, 1)
    elif timeframe == Timeframe.MONTHLY:
        prev_start = pd.Timestamp(prev_end.year, prev_end.month, 1)
    elif timeframe == Timeframe.WEEKLY:
        prev_start = prev_end - pd.Timedelta(days=int(prev_end.weekday()))
    else:
        prev_start = prev_end
    return prev_start, prev_end


def _aggregate_ohlc(slice_df: pd.DataFrame) -> pd.Series:
    return pd.Series(
        {
            "Open": float(slice_df["Open"].iloc[0]),
            "High": float(slice_df["High"].max()),
            "Low": float(slice_df["Low"].min()),
            "Close": float(slice_df["Close"].iloc[-1]),
        }
    )


def _pct_change_today(daily: pd.DataFrame, ltp: float) -> float | None:
    """% vs previous IST trading session close."""
    if daily.empty:
        return None
    today = market_today()
    last_bar_date = pd.Timestamp(daily.index[-1]).date()
    if last_bar_date >= today and len(daily) >= 2:
        prev_close = float(daily["Close"].iloc[-2])
    elif len(daily) >= 1:
        prev_close = float(daily["Close"].iloc[-1])
    else:
        return None
    if not prev_close:
        return None
    return (ltp - prev_close) / prev_close


def effective_high_low(
    *,
    open_price: float | None,
    high: float | None,
    low: float | None,
    lcp: float | None,
) -> tuple[float | None, float | None]:
    """Extend period high/low when live LTP exceeds stored candle extremes."""
    if lcp is None:
        return high, low
    eff_high = max(v for v in (high, lcp) if v is not None) if high is not None or lcp is not None else None
    eff_low = min(v for v in (low, lcp) if v is not None) if low is not None or lcp is not None else None
    return eff_high, eff_low


def _to_metrics(
    *,
    timeframe: Timeframe,
    period_label: str,
    is_current: bool,
    candle: pd.Series,
    period_start: date | None,
    ltp: float,
    pct_today: float | None,
) -> CandleMetrics:
    period_open = float(candle["Open"])
    high = float(candle["High"])
    low = float(candle["Low"])
    close = float(candle["Close"])

    eff_high, eff_low = effective_high_low(open_price=period_open, high=high, low=low, lcp=ltp)

    pct_change_open = (ltp - period_open) / period_open if period_open else None
    high_retracement = (ltp - eff_high) / eff_high if eff_high else None
    green_range = pct_change_open
    retracement_from_high = high_retracement
    rise_from_low = (ltp - eff_low) / eff_low if eff_low else None

    return CandleMetrics(
        timeframe=timeframe,
        period_label=period_label,
        is_current=is_current,
        period_start=period_start,
        open=period_open,
        high=eff_high,
        low=eff_low,
        close=close,
        lcp=ltp,
        pct_change_open=pct_change_open,
        pct_change_today=pct_today,
        high_retracement=high_retracement,
        green_range=green_range,
        retracement_from_high=retracement_from_high,
        rise_from_low=rise_from_low,
    )
