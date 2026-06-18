"""Resolve NSE/BSE sibling listings for prices and live quotes."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import OhlcBar, RankingSnapshot, RsiSnapshot, Stock, StockPrice, Timeframe
from app.services.symbol_utils import parse_scrip, stock_exchange


def bse_scrip_for_base(base_symbol: str) -> str:
    return f"{base_symbol.upper()}-BE-EQ"


def nse_scrip_for_base(base_symbol: str) -> str:
    return f"{base_symbol.upper()}-EQ"


def get_bse_sibling(db: Session, stock: Stock) -> Stock | None:
    parsed = parse_scrip(stock.scrip)
    if not parsed or parsed.exchange == "BSE":
        return None
    return db.scalar(select(Stock).where(Stock.scrip == bse_scrip_for_base(parsed.base_symbol)))


def get_nse_sibling(db: Session, stock: Stock) -> Stock | None:
    parsed = parse_scrip(stock.scrip)
    if not parsed or parsed.exchange == "NSE":
        return None
    return db.scalar(select(Stock).where(Stock.scrip == nse_scrip_for_base(parsed.base_symbol)))


def _ohlc_count(db: Session, stock_id: int) -> int:
    return int(
        db.scalar(select(func.count()).select_from(OhlcBar).where(OhlcBar.stock_id == stock_id)) or 0
    )


def resolve_live_scrip(db: Session, stock: Stock) -> str:
    """Pick the scrip Yahoo should refresh for this dashboard row."""
    parsed = parse_scrip(stock.scrip)
    if not parsed:
        return stock.scrip
    if parsed.exchange == "BSE":
        return stock.scrip

    bse = get_bse_sibling(db, stock)
    if bse is None:
        return stock.scrip

    bse_ohlc = _ohlc_count(db, bse.id)
    nse_ohlc = _ohlc_count(db, stock.id)

    # BSE-primary names (OHLC imported as SYMBOL-BE) or NSE row without OHLC.
    if bse_ohlc > 0 and (nse_ohlc == 0 or bse_ohlc > nse_ohlc):
        return bse.scrip

    return stock.scrip


def resolve_live_stock(db: Session, stock: Stock) -> Stock:
    live_scrip = resolve_live_scrip(db, stock)
    if live_scrip == stock.scrip:
        return stock
    other = db.scalar(select(Stock).where(Stock.scrip == live_scrip))
    return other or stock


def quote_stock_candidates(db: Session, stock: Stock) -> list[Stock]:
    """Listing variants to try for live LTP (NSE preferred, then BSE)."""
    seen: set[int] = set()
    candidates: list[Stock] = []

    def add(candidate: Stock | None) -> None:
        if candidate is not None and candidate.id not in seen:
            seen.add(candidate.id)
            candidates.append(candidate)

    add(stock)
    if stock_exchange(stock) == "BSE":
        add(get_nse_sibling(db, stock))
    else:
        add(get_bse_sibling(db, stock))

    return sorted(candidates, key=lambda s: 0 if stock_exchange(s) == "NSE" else 1)


def ltp_from_current_bars(db: Session, stock: Stock) -> float | None:
    """Last computed LCP from stored current-period OHLC bars."""
    for candidate in quote_stock_candidates(db, stock):
        lcp = db.scalar(
            select(OhlcBar.lcp)
            .where(OhlcBar.stock_id == candidate.id, OhlcBar.is_current.is_(True))
            .limit(1)
        )
        if lcp is not None:
            try:
                value = float(lcp)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return None


def expand_scrips_for_live(db: Session, scrips: list[str]) -> list[str]:
    expanded: set[str] = set()
    for raw in scrips:
        if not raw:
            continue
        scrip = raw.strip().upper()
        expanded.add(scrip)
        stock = db.scalar(select(Stock).where(Stock.scrip == scrip))
        if stock is None:
            continue
        live_scrip = resolve_live_scrip(db, stock)
        expanded.add(live_scrip)
        parsed = parse_scrip(stock.scrip)
        if parsed and parsed.exchange == "NSE":
            expanded.add(bse_scrip_for_base(parsed.base_symbol))
    return sorted(expanded)


def _ranking_for_stock(
    db: Session,
    stock: Stock,
    timeframe: Timeframe,
    ranking_map: dict[int, RankingSnapshot],
) -> RankingSnapshot | None:
    row = ranking_map.get(stock.id)
    if row is not None:
        return row
    if stock_exchange(stock) == "NSE":
        bse = get_bse_sibling(db, stock)
        if bse is not None:
            return ranking_map.get(bse.id)
    else:
        nse = get_nse_sibling(db, stock)
        if nse is not None:
            return ranking_map.get(nse.id)
    return None


def _rsi_for_stock(db: Session, stock: Stock, rsi_map: dict[int, RsiSnapshot]) -> RsiSnapshot | None:
    row = rsi_map.get(stock.id)
    if row is not None:
        return row
    if stock_exchange(stock) == "NSE":
        bse = get_bse_sibling(db, stock)
        if bse is not None:
            return rsi_map.get(bse.id)
    else:
        nse = get_nse_sibling(db, stock)
        if nse is not None:
            return rsi_map.get(nse.id)
    return None


def _price_for_stock(
    db: Session,
    stock: Stock,
    latest_prices: dict[int, StockPrice],
) -> StockPrice | None:
    live_stock = resolve_live_stock(db, stock)
    price = latest_prices.get(live_stock.id)
    if price is not None:
        return price
    return latest_prices.get(stock.id)
