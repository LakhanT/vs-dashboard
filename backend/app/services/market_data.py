from __future__ import annotations

import logging

import pandas as pd

from app.config import get_settings
from app.models import Stock
from app.services.nse_data import fetch_nse_daily_history, fetch_nse_histories
from app.services.yahoo import LiveQuote
from app.services.yahoo import fetch_daily_history as fetch_yahoo_histories
from app.services.yahoo import fetch_live_quotes as fetch_yahoo_live_quotes
from app.services.yahoo import fetch_live_quotes_from_history
from app.services.symbol_utils import is_bse_stock

logger = logging.getLogger(__name__)
settings = get_settings()


def _ohlc_source() -> str:
    return settings.ohlc_data_source or settings.primary_data_source or "yahoo"


def fetch_daily_history(stocks: list[Stock]) -> dict[int, pd.DataFrame]:
    """
    Daily OHLC for Y/Q/M/W period candles and computed rankings.
    Primary: Yahoo Finance. Fallback: NSE archives for missing NSE symbols.
    """
    source = _ohlc_source()
    if source == "nse":
        histories = fetch_nse_histories(stocks)
        missing = [s for s in stocks if s.id not in histories]
        if missing:
            histories.update(fetch_yahoo_histories(missing))
        return histories

    histories = fetch_yahoo_histories(stocks)
    missing = [s for s in stocks if s.id not in histories and not is_bse_stock(s)]
    if missing:
        logger.info("Yahoo missed %s symbols, trying NSE fallback", len(missing))
        histories.update(fetch_nse_histories(missing))
    return histories


def fetch_live_quotes(
    stocks: list[Stock],
    histories: dict[int, pd.DataFrame] | None = None,
) -> dict[int, LiveQuote]:
    """
    Live LTP for dashboard and ranking engine.
    Primary: Fyers API (when configured). Fallback chain: Yahoo → OHLC last bar → NSE.
    """
    histories = histories or {}
    source = (settings.live_price_source or "fyers").lower()
    quotes: dict[int, LiveQuote] = {}

    if source == "fyers":
        from app.services.fyers import fetch_live_quotes as fetch_fyers_live_quotes

        quotes = fetch_fyers_live_quotes(stocks)

    if not quotes and source != "yahoo":
        logger.info("Fyers unavailable or returned no quotes; falling back to Yahoo")

    if source == "yahoo" or len(quotes) < len(stocks):
        missing = [s for s in stocks if s.id not in quotes]
        if missing:
            quotes.update(fetch_yahoo_live_quotes(missing))

    missing = [s for s in stocks if s.id not in quotes]
    if missing and histories:
        hist_quotes = fetch_live_quotes_from_history(
            {s.id: histories[s.id] for s in missing if s.id in histories}
        )
        quotes.update(hist_quotes)

    still_missing = [s for s in stocks if s.id not in quotes and not is_bse_stock(s)]
    if still_missing:
        nse_hist = fetch_nse_histories(still_missing)
        quotes.update(fetch_live_quotes_from_history(nse_hist))

    return quotes


def fetch_live_quotes_for_stocks(db, stocks: list[Stock]) -> dict[int, LiveQuote]:
    """
    Fetch LTP keyed by universe/dashboard stock id.
    Tries NSE and BSE listing variants when one exchange has no quote.
    """
    from sqlalchemy.orm import Session

    from app.services.stock_resolver import quote_stock_candidates

    if not isinstance(db, Session):
        raise TypeError("db must be a SQLAlchemy Session")

    if not stocks:
        return {}

    candidates_by_stock: dict[int, list[Stock]] = {
        stock.id: quote_stock_candidates(db, stock) for stock in stocks
    }
    unique_candidates: dict[int, Stock] = {}
    for candidate_list in candidates_by_stock.values():
        for candidate in candidate_list:
            unique_candidates[candidate.id] = candidate

    raw = fetch_live_quotes(list(unique_candidates.values()))

    mapped: dict[int, LiveQuote] = {}
    for stock in stocks:
        for candidate in candidates_by_stock[stock.id]:
            quote = raw.get(candidate.id)
            if quote and quote.ltp is not None and float(quote.ltp) > 0:
                mapped[stock.id] = quote
                break

    missing = len(stocks) - len(mapped)
    if missing:
        logger.info("LTP mapped %s/%s stocks (%s still missing after NSE/BSE/Yahoo)", len(mapped), len(stocks), missing)
    return mapped


def fetch_single_history(stock: Stock) -> pd.DataFrame | None:
    if _ohlc_source() == "yahoo" or is_bse_stock(stock):
        frame = fetch_yahoo_histories([stock]).get(stock.id)
        if frame is not None and not frame.empty:
            return frame
    if not is_bse_stock(stock):
        frame = fetch_nse_daily_history(stock)
        if frame is not None and not frame.empty:
            return frame
    return fetch_yahoo_histories([stock]).get(stock.id)


def market_data_status() -> dict:
    from app.services.fyers import get_fyers_status

    return {
        "ohlc_source": _ohlc_source(),
        "live_price_source": settings.live_price_source,
        "fyers": get_fyers_status(),
        "inputs": ["rsi_digger_upload", "fusion_matrix_upload"],
        "computed": ["ohlc_periods", "y_q_m_ranks", "retracement", "live_ltp"],
    }
