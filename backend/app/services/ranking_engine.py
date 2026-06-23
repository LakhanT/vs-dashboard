from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy import func
from sqlalchemy.orm import Session

import logging

logger = logging.getLogger(__name__)

from app.models import (
    OhlcBar,
    RankingSnapshot,
    RetracementSnapshot,
    RsiSnapshot,
    Stock,
    StockPrice,
    Timeframe,
)
from app.services.market_data import fetch_daily_history, fetch_live_quotes_for_stocks
from app.services.market_calendar import market_today
from app.services.fno_sync import sync_fno_flags
from app.services.ohlc_builder import CandleMetrics, build_period_metrics, effective_high_low
from app.services.rank_utils import excel_rank_eq_desc
from app.services.universe import get_rsi_universe_stocks


@dataclass
class RankCandidate:
    stock_id: int
    timeframe: Timeframe
    pct_change_open: float
    metrics: CandleMetrics


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_rsi_snapshot(daily: pd.DataFrame) -> dict[str, float | str | None]:
    if daily is None or len(daily) < 20:
        return {
            "rsi": None,
            "rsi_avg": None,
            "avg_diff": None,
            "rsi_change": None,
            "rsi_diff": None,
            "rsi_trend": None,
            "crossover": None,
        }

    closes = daily["Close"].astype(float)
    rsi_series = compute_rsi(closes)
    rsi_avg_series = rsi_series.rolling(window=14, min_periods=5).mean()

    rsi = float(rsi_series.iloc[-1]) if pd.notna(rsi_series.iloc[-1]) else None
    rsi_avg = float(rsi_avg_series.iloc[-1]) if pd.notna(rsi_avg_series.iloc[-1]) else None
    prev_rsi = float(rsi_series.iloc[-2]) if len(rsi_series) > 1 and pd.notna(rsi_series.iloc[-2]) else None

    rsi_diff = (rsi - rsi_avg) if rsi is not None and rsi_avg is not None else None
    rsi_change = (rsi - prev_rsi) if rsi is not None and prev_rsi is not None else None
    avg_diff = (
        (rsi_avg_series.iloc[-1] - rsi_avg_series.iloc[-2])
        if len(rsi_avg_series) > 1
        and pd.notna(rsi_avg_series.iloc[-1])
        and pd.notna(rsi_avg_series.iloc[-2])
        else None
    )

    rsi_trend = None
    crossover = None
    if rsi is not None and rsi_avg is not None:
        if rsi > rsi_avg:
            rsi_trend = "Positive"
            crossover = "Positive" if prev_rsi is not None and prev_rsi <= rsi_avg else "Remained above 50"
        else:
            rsi_trend = "Negative"
            crossover = "Negative" if prev_rsi is not None and prev_rsi >= rsi_avg else "Remained below 50"

        if rsi >= 70:
            rsi_trend = "Remained above 50" if rsi > 50 else rsi_trend
        if rsi <= 30:
            rsi_trend = "Remained below 50"

    return {
        "rsi": rsi,
        "rsi_avg": rsi_avg,
        "avg_diff": avg_diff,
        "rsi_change": rsi_change,
        "rsi_diff": rsi_diff,
        "rsi_trend": rsi_trend,
        "crossover": crossover,
    }


def _upsert_ohlc(db: Session, stock_id: int, metrics: CandleMetrics) -> None:
    existing = db.scalar(
        select(OhlcBar).where(
            OhlcBar.stock_id == stock_id,
            OhlcBar.timeframe == metrics.timeframe,
            OhlcBar.period_label == metrics.period_label,
        )
    )
    payload = dict(
        is_current=metrics.is_current,
        period_start=metrics.period_start,
        lcp=metrics.lcp,
        open=metrics.open,
        high=metrics.high,
        low=metrics.low,
        close=metrics.close,
    )
    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
    else:
        db.add(
            OhlcBar(
                stock_id=stock_id,
                timeframe=metrics.timeframe,
                period_label=metrics.period_label,
                **payload,
            )
        )


def _assign_live_ranks(candidates: list[RankCandidate]) -> dict[tuple[int, Timeframe], int]:
    grouped: dict[Timeframe, dict[int, float]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.timeframe, {})[candidate.stock_id] = candidate.pct_change_open

    ranks: dict[tuple[int, Timeframe], int] = {}
    for timeframe, values in grouped.items():
        ranked = excel_rank_eq_desc(values)
        for stock_id, rank in ranked.items():
            ranks[(stock_id, timeframe)] = rank
    return ranks


def _uploaded_rsi_fields(db: Session, stock_id: int, as_of: date) -> dict | None:
    """RSI Digger upload is source of truth — do not overwrite with Yahoo-computed RSI."""
    row = db.scalar(
        select(RsiSnapshot)
        .where(RsiSnapshot.stock_id == stock_id, RsiSnapshot.rsi.is_not(None))
        .order_by(RsiSnapshot.as_of.desc())
        .limit(1)
    )
    if row is None:
        return None
    return {
        "rsi": row.rsi,
        "rsi_avg": row.rsi_avg,
        "avg_diff": row.avg_diff,
        "rsi_change": row.rsi_change,
        "rsi_diff": row.rsi_diff,
        "rsi_trend": row.rsi_trend,
        "crossover": row.crossover,
    }


def patch_live_ltps_only(db: Session, ltp_by_stock: dict[int, float]) -> None:
    """Update current-period LCP from live ticks (fast — no full rank recompute)."""
    if not ltp_by_stock:
        return
    from app.db_write import serialized_write

    with serialized_write():
        for stock_id, ltp in ltp_by_stock.items():
            bars = db.scalars(
                select(OhlcBar).where(OhlcBar.stock_id == stock_id, OhlcBar.is_current.is_(True))
            ).all()
            for bar in bars:
                bar.lcp = ltp
                eff_high, eff_low = effective_high_low(
                    open_price=bar.open,
                    high=bar.high,
                    low=bar.low,
                    lcp=ltp,
                )
                if eff_high is not None:
                    bar.high = eff_high
                if eff_low is not None:
                    bar.low = eff_low
        db.flush()


def patch_live_ltps_and_recalc(db: Session, ltp_by_stock: dict[int, float]) -> int:
    """Update LCP and recompute all rankings (use sparingly — expensive)."""
    if not ltp_by_stock:
        return 0
    patch_live_ltps_only(db, ltp_by_stock)
    counts = recalculate_rankings_from_db(db)
    return counts.get("rankings", 0)


def refresh_live_data(db: Session, *, stock_limit: int | None = None, sync_fno: bool = True) -> dict[str, int]:
    as_of = market_today()
    counts = {
        "fno_synced": 0,
        "stocks_processed": 0,
        "stocks_skipped": 0,
        "prices": 0,
        "ohlc": 0,
        "rankings": 0,
        "rsi": 0,
        "retracements": 0,
        "fyers_prices": 0,
        "yahoo_prices": 0,
        "nse_fallback": 0,
    }

    if sync_fno:
        try:
            fno_counts = sync_fno_flags(db)
            counts["fno_synced"] = fno_counts.get("updated", 0) + fno_counts.get("created", 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("F&O sync failed (continuing with price refresh): %s", exc)

    stocks = get_rsi_universe_stocks(db)
    if not stocks:
        logger.warning("No RSI universe — upload RSI Digger first")
        return counts
    if stock_limit:
        stocks = stocks[:stock_limit]

    if not stocks:
        return counts

    history = fetch_daily_history(stocks)
    quotes = fetch_live_quotes_for_stocks(db, stocks)

    stock_metrics: dict[int, list[CandleMetrics]] = {}
    stock_rsi: dict[int, dict] = {}

    for stock in stocks:
        daily = history.get(stock.id)
        if daily is None or daily.empty:
            counts["stocks_skipped"] += 1
            continue

        quote = quotes.get(stock.id)
        ltp = quote.ltp if quote else float(daily["Close"].iloc[-1])

        if quote:
            db.add(
                StockPrice(
                    stock_id=stock.id,
                    ltp=quote.ltp,
                    pct_change=quote.pct_change,
                    week_52_high=quote.week_52_high,
                    week_52_low=quote.week_52_low,
                    as_of=datetime.utcnow(),
                )
            )
            counts["prices"] += 1
            source = getattr(quote, "source", "")
            if source.startswith("fyers"):
                counts["fyers_prices"] += 1
            elif source.startswith("yahoo"):
                counts["yahoo_prices"] += 1
            else:
                counts["nse_fallback"] += 1

        period_metrics = build_period_metrics(daily, ltp=ltp)
        stock_metrics[stock.id] = period_metrics
        uploaded_rsi = _uploaded_rsi_fields(db, stock.id, as_of)
        stock_rsi[stock.id] = uploaded_rsi or compute_rsi_snapshot(daily)

        for metrics in period_metrics:
            _upsert_ohlc(db, stock.id, metrics)
            counts["ohlc"] += 1

        counts["stocks_processed"] += 1

    for stock_id, period_metrics in stock_metrics.items():
        yearly_current = next(
            (m for m in period_metrics if m.timeframe == Timeframe.YEARLY and m.is_current),
            None,
        )
        if yearly_current is None:
            continue

        rsi_payload = stock_rsi.get(stock_id, {})
        existing_retr = db.scalar(
            select(RetracementSnapshot).where(
                RetracementSnapshot.stock_id == stock_id,
                RetracementSnapshot.as_of == as_of,
            )
        )
        retr = existing_retr or RetracementSnapshot(stock_id=stock_id, as_of=as_of)
        pre_bar = next(
            (m for m in period_metrics if m.timeframe == Timeframe.YEARLY and not m.is_current),
            None,
        )
        pre_high = pre_bar.high if pre_bar else None
        retr.pre_high = pre_high
        retr.ltp = yearly_current.lcp
        retr.pct_today = yearly_current.pct_change_today
        retr.green_range = yearly_current.green_range
        retr.retracement_from_high = yearly_current.retracement_from_high
        retr.rise_from_low = yearly_current.rise_from_low
        retr.bullish_bo = (
            (yearly_current.lcp - pre_high) / pre_high
            if pre_high and yearly_current.lcp is not None
            else None
        )
        retr.rsi_diff = rsi_payload.get("rsi_diff")
        retr.crossover = rsi_payload.get("crossover")

        if existing_retr is None:
            db.add(retr)
        counts["retracements"] += 1

    for stock_id, rsi_payload in stock_rsi.items():
        if stock_id not in stock_metrics:
            continue
        if _uploaded_rsi_fields(db, stock_id, as_of) is not None:
            counts["rsi"] += 1
            continue
        # RSI columns come only from RSI Digger upload — never auto-create snapshots.

    db.flush()
    recalc_counts = recalculate_rankings_from_db(db)
    counts["rankings"] = recalc_counts["rankings"]
    db.commit()
    _reload_live_ranking_cache(db)
    return counts


def _reload_live_ranking_cache(db: Session) -> None:
    try:
        from app.services.live_ranking import reload_live_ranking_cache

        reload_live_ranking_cache(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Live ranking cache reload failed: %s", exc)


def recalculate_rankings_from_db(db: Session) -> dict[str, int]:
    """Recompute Y/Q/M live rankings from stored current-period OHLC (universe only)."""
    as_of = market_today()
    counts = {"rankings": 0, "retracements": 0}

    stocks = get_rsi_universe_stocks(db)
    if not stocks:
        return counts

    universe_ids = {s.id for s in stocks}
    rank_candidates: list[RankCandidate] = []

    latest_prices: dict[int, float | None] = {}
    latest_as_of = db.scalar(select(func.max(StockPrice.as_of)))
    if latest_as_of:
        for row in db.scalars(select(StockPrice).where(StockPrice.as_of == latest_as_of)):
            latest_prices[row.stock_id] = row.pct_change

    for stock in stocks:
        current_bars = db.scalars(
            select(OhlcBar).where(OhlcBar.stock_id == stock.id, OhlcBar.is_current.is_(True))
        ).all()
        for bar in current_bars:
            if bar.open is None or bar.lcp is None:
                continue
            eff_high, eff_low = effective_high_low(
                open_price=bar.open,
                high=bar.high,
                low=bar.low,
                lcp=bar.lcp,
            )
            pct_change_open = (bar.lcp - bar.open) / bar.open
            metrics = CandleMetrics(
                timeframe=bar.timeframe,
                period_label=bar.period_label,
                is_current=True,
                period_start=bar.period_start,
                open=bar.open,
                high=eff_high,
                low=eff_low,
                close=bar.close,
                lcp=bar.lcp,
                pct_change_open=pct_change_open,
                pct_change_today=None,
                high_retracement=(bar.lcp - eff_high) / eff_high if eff_high else None,
                green_range=pct_change_open,
                retracement_from_high=(bar.lcp - eff_high) / eff_high if eff_high else None,
                rise_from_low=(bar.lcp - eff_low) / eff_low if eff_low else None,
            )
            rank_candidates.append(
                RankCandidate(
                    stock_id=stock.id,
                    timeframe=bar.timeframe,
                    pct_change_open=pct_change_open,
                    metrics=metrics,
                )
            )

    live_ranks = _assign_live_ranks(rank_candidates)

    for candidate in rank_candidates:
        if candidate.stock_id not in universe_ids:
            continue
        live_rank = live_ranks.get((candidate.stock_id, candidate.timeframe))
        existing = db.scalar(
            select(RankingSnapshot).where(
                RankingSnapshot.stock_id == candidate.stock_id,
                RankingSnapshot.timeframe == candidate.timeframe,
                RankingSnapshot.as_of == as_of,
            )
        )
        snapshot = existing or RankingSnapshot(
            stock_id=candidate.stock_id,
            timeframe=candidate.timeframe,
            as_of=as_of,
        )
        metrics = candidate.metrics
        snapshot.live_ranking = live_rank
        snapshot.period_open = metrics.open
        snapshot.pct_change_open = metrics.pct_change_open
        snapshot.high_retracement = metrics.high_retracement
        if candidate.timeframe == Timeframe.YEARLY:
            snapshot.pct_change_today = latest_prices.get(candidate.stock_id)

        if existing is None:
            db.add(snapshot)
        counts["rankings"] += 1

    db.flush()

    snapshots_by_stock: dict[int, list[RankingSnapshot]] = {}
    for candidate in rank_candidates:
        snapshot = db.scalar(
            select(RankingSnapshot).where(
                RankingSnapshot.stock_id == candidate.stock_id,
                RankingSnapshot.timeframe == candidate.timeframe,
                RankingSnapshot.as_of == as_of,
            )
        )
        if snapshot is None:
            continue
        snapshots_by_stock.setdefault(candidate.stock_id, []).append(snapshot)

    for stock_id, snapshots in snapshots_by_stock.items():
        y_rank = live_ranks.get((stock_id, Timeframe.YEARLY))
        q_rank = live_ranks.get((stock_id, Timeframe.QUARTERLY))
        m_rank = live_ranks.get((stock_id, Timeframe.MONTHLY))
        for snapshot in snapshots:
            snapshot.y_rank = y_rank
            snapshot.q_rank = q_rank
            snapshot.m_rank = m_rank

    db.commit()
    _reload_live_ranking_cache(db)
    return counts
