"""Assemble universe rows and apply dynamic dashboard filters."""

from __future__ import annotations

import threading
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    FusionSnapshot,
    RankingSnapshot,
    RetracementSnapshot,
    RsiSnapshot,
    Stock,
    StockPrice,
    Timeframe,
)
from app.services.universe import get_rsi_universe_ids, latest_rsi_date, universe_rsi_date
from app.services.stock_resolver import _price_for_stock, ltp_from_current_bars
from app.schemas import DashboardFilterIn, DashboardQueryIn, DashboardResponse, FilterRule
from app.services.filter_engine import DEFAULT_COLUMNS, row_matches_rules


def _latest_by_stock(rows: list, stock_id_attr: str = "stock_id") -> dict[int, Any]:
    result: dict[int, Any] = {}
    for row in rows:
        sid = getattr(row, stock_id_attr)
        if sid not in result:
            result[sid] = row
    return result


def _ranking_maps(db: Session, timeframe: Timeframe) -> dict[int, RankingSnapshot]:
    ranking_date = db.scalar(
        select(func.max(RankingSnapshot.as_of)).where(RankingSnapshot.timeframe == timeframe)
    )
    if not ranking_date:
        return {}
    rows = db.scalars(
        select(RankingSnapshot).where(
            RankingSnapshot.timeframe == timeframe,
            RankingSnapshot.as_of == ranking_date,
        )
    ).all()
    return {r.stock_id: r for r in rows}


def _latest_map_for_model(db: Session, model: Any) -> dict[int, Any]:
    latest_date = db.scalar(select(func.max(model.as_of)))
    if latest_date is None:
        return {}
    rows = list(db.scalars(select(model).where(model.as_of == latest_date)))
    return _latest_by_stock(rows)


def _latest_prices_map(db: Session) -> dict[int, StockPrice]:
    latest_as_of = db.scalar(select(func.max(StockPrice.as_of)))
    if latest_as_of is None:
        return {}
    rows = list(db.scalars(select(StockPrice).where(StockPrice.as_of == latest_as_of)))
    return _latest_by_stock(rows)


_CACHE_LOCK = threading.Lock()
_CACHE_VERSION: int = 0
_UNIVERSE_CACHE: dict[str, Any] = {"version": None, "as_of": None, "rows": None}


def invalidate_universe_cache() -> None:
    """Clear cached universe rows after any pipeline mutation."""
    global _CACHE_VERSION  # noqa: PLW0603
    with _CACHE_LOCK:
        _CACHE_VERSION += 1
        _UNIVERSE_CACHE["version"] = None
        _UNIVERSE_CACHE["as_of"] = None
        _UNIVERSE_CACHE["rows"] = None


def _overlay_live_prices(rows: list[dict[str, Any]]) -> None:
    try:
        from app.services.live_price_service import live_price_service

        overlay = live_price_service.get_scrip_ltps()
    except Exception:  # noqa: BLE001
        return
    if not overlay:
        return
    for row in rows:
        key = str(row.get("scrip", "")).upper()
        tick = overlay.get(key)
        if not tick:
            continue
        row["ltp"] = tick.get("ltp", row.get("ltp"))
        if tick.get("pct_change") is not None:
            row["pct_change_today"] = tick["pct_change"]


def _rsi_map_for_universe(db: Session) -> dict[int, Any]:
    rsi_date = universe_rsi_date(db)
    if rsi_date is None:
        return {}
    rows = list(db.scalars(select(RsiSnapshot).where(RsiSnapshot.as_of == rsi_date)))
    return _latest_by_stock(rows)


def build_universe_rows(db: Session) -> tuple[date | None, list[dict[str, Any]]]:
    with _CACHE_LOCK:
        cached_version = _UNIVERSE_CACHE.get("version")
        cached_rows = _UNIVERSE_CACHE.get("rows")
        cached_as_of = _UNIVERSE_CACHE.get("as_of")
        if cached_rows is not None and cached_version == _CACHE_VERSION:
            rows = [dict(r) for r in cached_rows]
            _overlay_live_prices(rows)
            return cached_as_of, rows

    ranking_date = db.scalar(select(func.max(RankingSnapshot.as_of)))
    rsi_date = latest_rsi_date(db)
    as_of = ranking_date or rsi_date

    y_ranks = _ranking_maps(db, Timeframe.YEARLY)
    q_ranks = _ranking_maps(db, Timeframe.QUARTERLY)
    m_ranks = _ranking_maps(db, Timeframe.MONTHLY)

    rsi_map = _rsi_map_for_universe(db)
    retr_map = _latest_map_for_model(db, RetracementSnapshot)
    fusion_map = _latest_map_for_model(db, FusionSnapshot)
    latest_prices = _latest_prices_map(db)

    # Universe is defined by RSI Digger (latest RSI snapshot date).
    rsi_universe_ids = get_rsi_universe_ids(db)

    stock_stmt = select(Stock).order_by(Stock.scrip)
    if rsi_universe_ids:
        stock_stmt = stock_stmt.where(Stock.id.in_(rsi_universe_ids))

    rows: list[dict[str, Any]] = []
    for stock in db.scalars(stock_stmt):
        y = y_ranks.get(stock.id)
        q = q_ranks.get(stock.id)
        m = m_ranks.get(stock.id)
        rsi = rsi_map.get(stock.id)
        retr = retr_map.get(stock.id)
        fusion = fusion_map.get(stock.id)
        price = _price_for_stock(db, stock, latest_prices)
        ltp = price.ltp if price and price.ltp else None
        if ltp is None or float(ltp) <= 0:
            ltp = ltp_from_current_bars(db, stock)
        if (ltp is None or float(ltp) <= 0) and retr and retr.ltp:
            ltp = retr.ltp

        rows.append(
            {
                "scrip": stock.scrip,
                "ticker_symbol": stock.ticker_symbol,
                "sector": stock.sector,
                "segment": stock.segment,
                "market_cap_cr": stock.market_cap_cr,
                "is_fno": stock.is_fno,
                "ltp": ltp,
                "pct_change_today": (y.pct_change_today if y else None)
                or (m.pct_change_today if m else None)
                or (price.pct_change if price else None),
                "y_rank": y.live_ranking if y else None,
                "q_rank": q.live_ranking if q else None,
                "m_rank": m.live_ranking if m else None,
                "y_pct_change_open": y.pct_change_open if y else None,
                "q_pct_change_open": q.pct_change_open if q else None,
                "m_pct_change_open": m.pct_change_open if m else None,
                "y_high_retracement": y.high_retracement if y else None,
                "rsi": rsi.rsi if rsi else None,
                "rsi_avg": rsi.rsi_avg if rsi else None,
                "rsi_diff": rsi.rsi_diff if rsi else None,
                "rsi_trend": rsi.rsi_trend if rsi else None,
                "crossover": rsi.crossover if rsi else None,
                "retracement_from_high": retr.retracement_from_high if retr else None,
                "green_range": retr.green_range if retr else None,
                "rise_from_low": retr.rise_from_low if retr else None,
                "bullish_bo": retr.bullish_bo if retr else None,
                "fusion_setup": fusion.setup if fusion else None,
                "pf_perf_score": fusion.pf_perf_score if fusion else None,
                "pf_perf_t0025": fusion.pf_perf_t0025 if fusion else None,
                "pf_perf_t01": fusion.pf_perf_t01 if fusion else None,
                "pf_perf_t02": fusion.pf_perf_t02 if fusion else None,
                "pf_perf_t03": fusion.pf_perf_t03 if fusion else None,
                "pf_rank_score": fusion.pf_rank_score if fusion else None,
                "pf_rank_t0025": fusion.pf_rank_t0025 if fusion else None,
                "pf_rank_t01": fusion.pf_rank_t01 if fusion else None,
                "pf_rank_t02": fusion.pf_rank_t02 if fusion else None,
                "pf_rank_t03": fusion.pf_rank_t03 if fusion else None,
                "rs_perf_score": fusion.rs_perf_score if fusion else None,
                "rs_perf_t0025": fusion.rs_perf_t0025 if fusion else None,
                "rs_perf_t01": fusion.rs_perf_t01 if fusion else None,
                "rs_perf_t02": fusion.rs_perf_t02 if fusion else None,
                "rs_perf_t03": fusion.rs_perf_t03 if fusion else None,
                "rs_rank_score": fusion.rs_rank_score if fusion else None,
                "rs_rank_t0025": fusion.rs_rank_t0025 if fusion else None,
                "rs_rank_t01": fusion.rs_rank_t01 if fusion else None,
                "rs_rank_t02": fusion.rs_rank_t02 if fusion else None,
                "rs_rank_t03": fusion.rs_rank_t03 if fusion else None,
                "total_perf_score": fusion.total_perf_score if fusion else None,
                "total_ranking_score": fusion.total_ranking_score if fusion else None,
                "net_perf_score": fusion.net_perf_score if fusion else None,
                "net_ranking_score": fusion.net_ranking_score if fusion else None,
                "dtb_level": fusion.dtb_level if fusion else None,
                "dbs_level": fusion.dbs_level if fusion else None,
                "pct_from_dtb": fusion.pct_from_dtb if fusion else None,
                "pct_from_dbs": fusion.pct_from_dbs if fusion else None,
            }
        )

    _overlay_live_prices(rows)

    with _CACHE_LOCK:
        _UNIVERSE_CACHE["version"] = _CACHE_VERSION
        _UNIVERSE_CACHE["as_of"] = as_of
        _UNIVERSE_CACHE["rows"] = rows

    return as_of, rows


def _legacy_filters_to_rules(filters: DashboardFilterIn) -> list[FilterRule]:
    rules = [
        FilterRule(field="y_rank", operator="lte", value=filters.y_rank_max),
        FilterRule(field="q_rank", operator="lte", value=filters.q_rank_max),
        FilterRule(field="m_rank", operator="lte", value=filters.m_rank_max),
        FilterRule(field="rsi_avg", operator="gt", value=filters.rsi_avg_min),
    ]
    if filters.fno_only:
        rules.append(FilterRule(field="is_fno", operator="is_true", value=True))
    if filters.sector:
        rules.append(FilterRule(field="sector", operator="eq", value=filters.sector))
    return rules


def _apply_search(rows: list[dict[str, Any]], search: str | None) -> list[dict[str, Any]]:
    if not search:
        return rows
    needle = search.lower()
    return [
        row
        for row in rows
        if needle
        in " ".join(
            str(v) for v in [row.get("scrip"), row.get("ticker_symbol"), row.get("sector"), row.get("segment")] if v
        ).lower()
    ]


def _project_columns(rows: list[dict[str, Any]], columns: list[str] | None) -> list[dict[str, Any]]:
    cols = columns or DEFAULT_COLUMNS
    return [{col: row.get(col) for col in cols if col in row or True} for row in rows]


def _sort_rows(rows: list[dict[str, Any]], sort_by: str | None, sort_dir: str) -> list[dict[str, Any]]:
    if not rows:
        return rows

    key = sort_by if sort_by and sort_by in rows[0] else "y_rank"
    reverse = sort_dir == "desc"

    def sort_key(row: dict[str, Any]) -> tuple[int, Any]:
        value = row.get(key)
        if value is None:
            return (1, 0)
        if isinstance(value, str):
            return (0, value.lower())
        return (0, value)

    # Stable tie-breaker on scrip.
    if key != "scrip":
        rows.sort(key=lambda r: (r.get("scrip") or ""))
    rows.sort(key=sort_key, reverse=reverse)
    return rows


def query_dashboard(db: Session, query: DashboardQueryIn) -> DashboardResponse:
    if query.fresh:
        invalidate_universe_cache()
    as_of, universe = build_universe_rows(db)
    rules = query.rules
    filtered = [row for row in universe if row_matches_rules(row, rules, query.logic)]
    filtered = _apply_search(filtered, query.search)
    _sort_rows(filtered, query.sort_by, query.sort_dir)
    columns = query.columns or DEFAULT_COLUMNS
    return DashboardResponse(
        as_of=as_of,
        total_stocks=len(universe),
        matched_count=len(filtered),
        filters=None,
        rules=rules,
        logic=query.logic,
        columns=columns,
        rows=_project_columns(filtered, columns),
    )


def build_dashboard(db: Session, filters: DashboardFilterIn) -> DashboardResponse:
    rules = _legacy_filters_to_rules(filters)
    query = DashboardQueryIn(rules=rules, logic="and", search=filters.search)
    response = query_dashboard(db, query)
    response.filters = filters
    return response
