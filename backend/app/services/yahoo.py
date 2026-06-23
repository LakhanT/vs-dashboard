from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from app.models import Stock
from app.services.symbol_utils import base_symbol, scrip_from_yahoo, to_yahoo_ticker

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
REQUEST_PAUSE_SEC = 0.4
MAX_RETRIES = 3
HISTORY_PERIOD = "2y"


@dataclass
class LiveQuote:
    ltp: float | None
    pct_change: float | None
    week_52_high: float | None
    week_52_low: float | None
    source: str = "yahoo"


def to_yahoo_symbol(stock: Stock) -> str | None:
    return to_yahoo_ticker(stock)


def symbol_to_scrip(yahoo_symbol: str) -> str:
    return scrip_from_yahoo(yahoo_symbol)


def fetch_daily_history(
    stocks: list[Stock],
    *,
    period: str = HISTORY_PERIOD,
) -> dict[int, pd.DataFrame]:
    """Batch-download daily OHLC from Yahoo Finance."""
    symbol_to_id: dict[str, int] = {}
    for stock in stocks:
        symbol = to_yahoo_symbol(stock)
        if symbol:
            symbol_to_id[symbol] = stock.id

    if not symbol_to_id:
        return {}

    frames: dict[int, pd.DataFrame] = {}
    symbols = list(symbol_to_id.keys())

    for start in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[start : start + BATCH_SIZE]
        batch_frames = _download_batch(batch, period=period)
        for symbol, frame in batch_frames.items():
            stock_id = symbol_to_id.get(symbol)
            if stock_id and frame is not None:
                frames[stock_id] = frame
        time.sleep(REQUEST_PAUSE_SEC)

    return frames


def fetch_live_quotes(stocks: list[Stock]) -> dict[int, LiveQuote]:
    """Fetch live quotes — batch 1m bars first, then per-symbol fallback."""
    quotes = _fetch_live_quotes_batch(stocks)
    missing = [stock for stock in stocks if stock.id not in quotes]
    if missing:
        quotes.update(_fetch_live_quotes_sequential(missing))
    return quotes


def _fetch_live_quotes_sequential(stocks: list[Stock]) -> dict[int, LiveQuote]:
    quotes: dict[int, LiveQuote] = {}
    for stock in stocks:
        symbol = to_yahoo_symbol(stock)
        if not symbol:
            continue
        quote = _fetch_single_quote(symbol)
        if quote:
            quotes[stock.id] = quote
        time.sleep(REQUEST_PAUSE_SEC * 0.25)
    return quotes


def _fetch_live_quotes_batch(stocks: list[Stock]) -> dict[int, LiveQuote]:
    symbol_to_id: dict[str, int] = {}
    for stock in stocks:
        symbol = to_yahoo_symbol(stock)
        if symbol:
            symbol_to_id[symbol] = stock.id
    if not symbol_to_id:
        return {}

    quotes: dict[int, LiveQuote] = {}
    symbols = list(symbol_to_id.keys())

    for start in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[start : start + BATCH_SIZE]
        try:
            raw = yf.download(
                batch if len(batch) > 1 else batch[0],
                period="1d",
                interval="1m",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
            )
            if raw is None or raw.empty:
                continue

            for symbol in batch:
                try:
                    sub = raw[symbol] if isinstance(raw.columns, pd.MultiIndex) else raw
                    if sub is None or sub.empty:
                        continue
                    sub = sub.dropna(subset=["Close"])
                    if sub.empty:
                        continue
                    last = sub.iloc[-1]
                    prev = sub.iloc[-2] if len(sub) > 1 else last
                    ltp = float(last["Close"])
                    prev_close = float(prev["Close"])
                    stock_id = symbol_to_id[symbol]
                    quotes[stock_id] = LiveQuote(
                        ltp=ltp,
                        pct_change=(ltp - prev_close) / prev_close if prev_close else None,
                        week_52_high=None,
                        week_52_low=None,
                        source="yahoo_1m",
                    )
                except (KeyError, TypeError, ValueError):
                    continue
        except Exception as exc:  # noqa: BLE001
            logger.debug("Yahoo 1m batch failed: %s", exc)
        time.sleep(REQUEST_PAUSE_SEC * 0.5)

    return quotes


def fetch_live_quotes_from_history(histories: dict[int, pd.DataFrame]) -> dict[int, LiveQuote]:
    quotes: dict[int, LiveQuote] = {}
    for stock_id, frame in histories.items():
        if frame is None or frame.empty:
            continue
        last = frame.iloc[-1]
        prev = frame.iloc[-2] if len(frame) > 1 else last
        ltp = float(last["Close"])
        prev_close = float(prev["Close"])
        quotes[stock_id] = LiveQuote(
            ltp=ltp,
            pct_change=(ltp - prev_close) / prev_close if prev_close else None,
            week_52_high=float(frame["High"].tail(252).max()),
            week_52_low=float(frame["Low"].tail(252).min()),
            source="yahoo_history",
        )
    return quotes


def _fetch_single_quote(symbol: str) -> LiveQuote | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            ticker = yf.Ticker(symbol)
            fast = ticker.fast_info
            ltp = getattr(fast, "last_price", None) or getattr(fast, "lastPrice", None)
            prev_close = getattr(fast, "previous_close", None) or getattr(fast, "previousClose", None)
            high_52 = getattr(fast, "year_high", None) or getattr(fast, "fiftyTwoWeekHigh", None)
            low_52 = getattr(fast, "year_low", None) or getattr(fast, "fiftyTwoWeekLow", None)

            if ltp is not None:
                ltp = float(ltp)
                pct = (ltp - float(prev_close)) / float(prev_close) if prev_close else None
                return LiveQuote(
                    ltp=ltp,
                    pct_change=pct,
                    week_52_high=float(high_52) if high_52 else None,
                    week_52_low=float(low_52) if low_52 else None,
                    source="yahoo_live",
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Yahoo fast_info failed for %s: %s", symbol, exc)

        try:
            frame = _download_single(symbol, period="5d")
            if frame is not None and not frame.empty:
                last = frame.iloc[-1]
                prev = frame.iloc[-2] if len(frame) > 1 else last
                ltp = float(last["Close"])
                prev_close = float(prev["Close"])
                return LiveQuote(
                    ltp=ltp,
                    pct_change=(ltp - prev_close) / prev_close if prev_close else None,
                    week_52_high=float(frame["High"].max()),
                    week_52_low=float(frame["Low"].min()),
                    source="yahoo_history",
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Yahoo quote failed for %s (attempt %s): %s", symbol, attempt, exc)

        time.sleep(REQUEST_PAUSE_SEC * attempt)
    return None


def _download_batch(symbols: list[str], *, period: str) -> dict[str, pd.DataFrame]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if len(symbols) == 1:
                frame = _download_single(symbols[0], period=period)
                return {symbols[0]: frame} if frame is not None else {}

            raw = yf.download(
                symbols,
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
            )
            if raw is None or raw.empty:
                continue

            result: dict[str, pd.DataFrame] = {}
            for symbol in symbols:
                try:
                    if isinstance(raw.columns, pd.MultiIndex):
                        sub = raw[symbol]
                    else:
                        sub = raw
                    normalized = _normalize_history_frame(sub)
                    if normalized is not None:
                        result[symbol] = normalized
                except (KeyError, TypeError):
                    continue
            if result:
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("Yahoo batch failed (attempt %s): %s", attempt, exc)
        time.sleep(REQUEST_PAUSE_SEC * attempt)

    # Fallback: one-by-one
    result: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frame = _download_single(symbol, period=period)
        if frame is not None:
            result[symbol] = frame
    return result


def _download_single(symbol: str, *, period: str) -> pd.DataFrame | None:
    try:
        end = datetime.now()
        start = end - timedelta(days=800 if period == "2y" else 120)
        ticker = yf.Ticker(symbol)
        frame = ticker.history(start=start, end=end, interval="1d", auto_adjust=False)
        return _normalize_history_frame(frame)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Yahoo history failed for %s: %s", symbol, exc)
        return None


def _normalize_history_frame(frame: pd.DataFrame) -> pd.DataFrame | None:
    if frame is None or frame.empty:
        return None

    data = frame.copy()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.droplevel(-1)

    required = ["Open", "High", "Low", "Close"]
    if not all(col in data.columns for col in required):
        return None

    data = data[required].dropna()
    if data.empty:
        return None

    from app.services.market_calendar import normalize_daily_bars_to_ist

    data = normalize_daily_bars_to_ist(data)
    if data.empty:
        return None
    return data.sort_index()
