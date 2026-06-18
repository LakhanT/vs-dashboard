from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DashboardFilterPreset,
    FusionSnapshot,
    OhlcBar,
    PipelineTask,
    RankingSnapshot,
    RetracementSnapshot,
    RsiSnapshot,
    Stock,
    StockPrice,
    TaskStatus,
    Timeframe,
)
from app.services.fusion_columns import fusion_scores_from_row
from app.services.symbol_utils import normalize_scrip as _normalize_scrip_impl, parse_scrip


def _safe_float(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


def _safe_int(value) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_decimal_pct(value: float | None) -> float | None:
    """Excel sheets mix decimal and whole-percent values; store decimals in DB."""
    if value is None:
        return None
    if abs(value) > 2:
        return value / 100
    return value


def _normalize_scrip(value: str | None) -> str | None:
    return _normalize_scrip_impl(value)


def _upsert_stock(
    db: Session,
    scrip: str,
    *,
    ticker_symbol: str | None = None,
    sector: str | None = None,
    segment: str | None = None,
    market_cap_cr: float | None = None,
    is_fno: bool | None = None,
) -> Stock:
    stock = db.scalar(select(Stock).where(Stock.scrip == scrip))
    if stock is None:
        stock = Stock(scrip=scrip)
        db.add(stock)

    parsed = parse_scrip(scrip)
    if parsed:
        if parsed.exchange == "BSE" and not segment:
            segment = "BSE"
        elif parsed.series and not segment:
            segment = parsed.series

    if ticker_symbol:
        stock.ticker_symbol = ticker_symbol
    if sector:
        stock.sector = sector
    if segment:
        stock.segment = segment
    if market_cap_cr is not None:
        stock.market_cap_cr = market_cap_cr
    if is_fno is not None:
        stock.is_fno = is_fno
    return stock


def import_excel_workbook(db: Session, excel_path: str) -> dict[str, int]:
    path = Path(excel_path)
    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_path}")

    counts = {
        "stocks": 0,
        "prices": 0,
        "ohlc": 0,
        "rsi": 0,
        "rankings": 0,
        "retracements": 0,
        "fusion": 0,
        "fno": 0,
    }
    as_of = date.today()

    price_df = pd.read_excel(path, sheet_name="Microsoft Price", header=0)
    for _, row in price_df.iterrows():
        scrip = _normalize_scrip(row.get("Scrip"))
        if not scrip:
            continue
        stock = _upsert_stock(
            db,
            scrip,
            ticker_symbol=str(row.get("Ticker symbol") or "").strip() or None,
            sector=str(row.get("Sector") or "").strip() or None,
            market_cap_cr=_safe_float(row.get("Market Cap")),
        )
        db.flush()
        counts["stocks"] += 1

        price = StockPrice(
            stock_id=stock.id,
            ltp=_safe_float(row.get("Ltp")),
            pct_change=_safe_float(row.get("% change")),
            week_52_high=_safe_float(row.get("52 week high")),
            week_52_low=_safe_float(row.get("52 week low")),
            as_of=datetime.utcnow(),
        )
        db.add(price)
        counts["prices"] += 1

    fno_df = pd.read_excel(path, sheet_name="F&O", header=0)
    fno_scrips = set()
    for _, row in fno_df.iterrows():
        scrip = _normalize_scrip(row.get("Scrip"))
        if not scrip:
            continue
        fno_scrips.add(scrip)
        stock = _upsert_stock(
            db,
            scrip,
            ticker_symbol=str(row.get("Scrip") or "").strip() or None,
            sector=str(row.get("Sector") or "").strip() or None,
            segment=str(row.get("Segment") or "").strip() or None,
            market_cap_cr=_safe_float(row.get("Market Cap (Cr)")),
            is_fno=True,
        )
        db.flush()
        counts["fno"] += 1

    curr_df = pd.read_excel(path, sheet_name="OHLC Data Curr", header=0)
    _import_ohlc_block(db, curr_df, is_current=True, include_weekly=True, counts=counts)

    pre_df = pd.read_excel(path, sheet_name="Pre.OHLC Data", header=0)
    _import_ohlc_block(db, pre_df, is_current=False, include_weekly=False, counts=counts)

    rsi_df = pd.read_excel(path, sheet_name="MRSI DIgger", header=0)
    for _, row in rsi_df.iterrows():
        scrip = _normalize_scrip(row.get("Scrip"))
        if not scrip:
            continue
        stock = _upsert_stock(
            db,
            scrip,
            sector=str(row.get("Sector") or "").strip() or None,
            segment=str(row.get("Segment") or "").strip() or None,
            market_cap_cr=_safe_float(row.get("Market Cap (Cr)")),
            is_fno=scrip in fno_scrips,
        )
        db.flush()
        existing = db.scalar(
            select(RsiSnapshot).where(
                RsiSnapshot.stock_id == stock.id, RsiSnapshot.as_of == as_of
            )
        )
        payload = dict(
            rsi=_safe_float(row.get("RSI")),
            rsi_avg=_safe_float(row.get("RSI Avg.")),
            avg_diff=_safe_float(row.get("Avg Diff")),
            rsi_change=_safe_float(row.get("RSI Change")),
            rsi_trend=_safe_str(row.get("RSI Trend")),
            rsi_diff=_safe_float(row.get("RSI Diff")),
            crossover=_safe_str(row.get("Crossover") or row.get("RSI & Avg")),
            ranking_rsi_positive=_safe_int(row.get("Ranking RSI Value +VE")),
            ranking_rsi_negative=_safe_int(row.get("Ranking RSI Value --VE")),
        )
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
        else:
            db.add(RsiSnapshot(stock_id=stock.id, as_of=as_of, **payload))
        counts["rsi"] += 1

    for sheet_name, timeframe in [
        ("Y.Rank 01-01-2026", Timeframe.YEARLY),
        ("Q.Rank 01-10-2025", Timeframe.QUARTERLY),
        ("M.Rank 01-04-2026", Timeframe.MONTHLY),
    ]:
        rank_df = pd.read_excel(path, sheet_name=sheet_name, header=0)
        for _, row in rank_df.iterrows():
            scrip = _normalize_scrip(row.get("Scrip"))
            if not scrip:
                continue
            stock = _upsert_stock(
                db,
                scrip,
                sector=str(row.get("Sector") or "").strip() or None,
                segment=str(row.get("Segment") or "").strip() or None,
                is_fno=bool(row.get("F&O")) or scrip in fno_scrips,
            )
            db.flush()
            existing = db.scalar(
                select(RankingSnapshot).where(
                    RankingSnapshot.stock_id == stock.id,
                    RankingSnapshot.timeframe == timeframe,
                    RankingSnapshot.as_of == as_of,
                )
            )
            open_col = {
                Timeframe.YEARLY: "Y.open",
                Timeframe.QUARTERLY: "Q.open",
                Timeframe.MONTHLY: "M.open",
            }[timeframe]
            payload = dict(
                live_ranking=_safe_int(row.get("Live Ranking")),
                period_open=_safe_float(row.get(open_col)),
                pct_change_open=_as_decimal_pct(_safe_float(row.get("% change open"))),
                pct_change_today=_safe_float(row.get("% change today")),
                high_retracement=_as_decimal_pct(_safe_float(row.get("High Retracement"))),
                y_rank=_safe_int(row.get("Y, Rank") or row.get("Y Rank")),
                m_rank=_safe_int(row.get("M.Rank") or row.get("Monthly Ranking")),
                q_rank=_safe_int(row.get("Quaterly Ranking")),
            )
            if existing:
                for key, value in payload.items():
                    setattr(existing, key, value)
            else:
                db.add(
                    RankingSnapshot(
                        stock_id=stock.id,
                        timeframe=timeframe,
                        as_of=as_of,
                        **payload,
                    )
                )
            counts["rankings"] += 1

    retr_df = pd.read_excel(path, sheet_name="Retracement", header=0)
    for _, row in retr_df.iterrows():
        scrip = _normalize_scrip(row.get("Scrip"))
        if not scrip:
            continue
        stock = _upsert_stock(db, scrip, is_fno=scrip in fno_scrips)
        db.flush()
        existing = db.scalar(
            select(RetracementSnapshot).where(
                RetracementSnapshot.stock_id == stock.id,
                RetracementSnapshot.as_of == as_of,
            )
        )
        payload = dict(
            pre_high=_safe_float(row.get("PreHIgh") or row.get("PreHigh")),
            ltp=_safe_float(row.get("LTP")),
            pct_today=_safe_float(row.get("% Today")),
            green_range=_as_decimal_pct(_safe_float(row.get("Green Range"))),
            retracement_from_high=_as_decimal_pct(_safe_float(row.get("Retracement from High"))),
            rise_from_low=_as_decimal_pct(_safe_float(row.get("Rise From Low"))),
            bullish_bo=_as_decimal_pct(_safe_float(row.get("Bullish BO"))),
            yearly_ranking=_safe_int(row.get("Yearly Ranking")),
            rsi_diff=_safe_float(row.get("RSI Diff")),
            crossover=_safe_str(row.get("Crossover")),
        )
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
        else:
            db.add(RetracementSnapshot(stock_id=stock.id, as_of=as_of, **payload))
        counts["retracements"] += 1

    fusion_df = pd.read_excel(path, sheet_name="Fusion Matrix", header=0)
    for _, row in fusion_df.iterrows():
        scrip = _normalize_scrip(row.get("Scrip"))
        if not scrip:
            continue
        stock = _upsert_stock(
            db,
            scrip,
            sector=str(row.get("Sector") or "").strip() or None,
            segment=str(row.get("Segment") or "").strip() or None,
            market_cap_cr=_safe_float(row.get("Market Cap (Cr)")),
            is_fno=scrip in fno_scrips,
        )
        db.flush()
        existing = db.scalar(
            select(FusionSnapshot).where(
                FusionSnapshot.stock_id == stock.id, FusionSnapshot.as_of == as_of
            )
        )
        payload = dict(
            setup=str(row.get("Setup") or "").strip() or None,
            **fusion_scores_from_row(row),
            total_perf_score=_safe_float(row.get("Total Perf. Score")),
            total_ranking_score=_safe_float(row.get("Total Ranking Score")),
            net_perf_score=_safe_float(row.get("Net Perf. Score")),
            net_ranking_score=_safe_float(row.get("Net Ranking Score")),
            dtb_level=_safe_float(row.get("DTB Level")),
            dbs_level=_safe_float(row.get("DBS Level")),
            pct_from_dtb=_safe_float(row.get("% From DTB")),
            pct_from_dbs=_safe_float(row.get("% From DBS")),
        )
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
        else:
            db.add(FusionSnapshot(stock_id=stock.id, as_of=as_of, **payload))
        counts["fusion"] += 1

    _ensure_default_preset(db)
    db.commit()
    return counts


def _import_ohlc_block(
    db: Session,
    df: pd.DataFrame,
    *,
    is_current: bool,
    include_weekly: bool,
    counts: dict[str, int],
) -> None:
    blocks = [
        (Timeframe.YEARLY, 1, 2, 3, 4, 5, 6),
        (Timeframe.QUARTERLY, 9, 10, 11, 12, 13, 14),
        (Timeframe.MONTHLY, 17, 18, 19, 20, 21, 22),
    ]
    if include_weekly:
        blocks.append((Timeframe.WEEKLY, 25, 26, 27, 28, 29, 30))
    label_map = {
        Timeframe.YEARLY: "Yearly",
        Timeframe.QUARTERLY: "Quaterly",
        Timeframe.MONTHLY: "Monthly",
        Timeframe.WEEKLY: "Weekly",
    }

    for timeframe, scrip_idx, lcp_idx, open_idx, high_idx, low_idx, close_idx in blocks:
        period_label = label_map[timeframe] if is_current else f"Pre {label_map[timeframe]}"
        for _, row in df.iterrows():
            scrip = _normalize_scrip(row.iloc[scrip_idx] if scrip_idx < len(row) else None)
            if not scrip:
                continue
            stock = _upsert_stock(db, scrip)
            db.flush()
            existing = db.scalar(
                select(OhlcBar).where(
                    OhlcBar.stock_id == stock.id,
                    OhlcBar.timeframe == timeframe,
                    OhlcBar.period_label == period_label,
                )
            )
            payload = dict(
                is_current=is_current,
                lcp=_safe_float(row.iloc[lcp_idx] if lcp_idx < len(row) else None),
                open=_safe_float(row.iloc[open_idx] if open_idx < len(row) else None),
                high=_safe_float(row.iloc[high_idx] if high_idx < len(row) else None),
                low=_safe_float(row.iloc[low_idx] if low_idx < len(row) else None),
                close=_safe_float(row.iloc[close_idx] if close_idx < len(row) else None),
            )
            if existing:
                for key, value in payload.items():
                    setattr(existing, key, value)
            else:
                db.add(
                    OhlcBar(
                        stock_id=stock.id,
                        timeframe=timeframe,
                        period_label=period_label,
                        **payload,
                    )
                )
            counts["ohlc"] += 1


def _ensure_default_preset(db: Session) -> None:
    preset = db.scalar(select(DashboardFilterPreset).where(DashboardFilterPreset.is_default.is_(True)))
    if preset is None:
        db.add(
            DashboardFilterPreset(
                name="Default VS Dashboard",
                y_rank_max=150,
                q_rank_max=150,
                m_rank_max=150,
                rsi_avg_min=-2.0,
                fno_only=False,
                is_default=True,
            )
        )


def create_pipeline_task(db: Session, task_type: str, payload: dict | None = None) -> PipelineTask:
    task = PipelineTask(
        task_type=task_type,
        status=TaskStatus.PENDING,
        payload=json.dumps(payload or {}),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def mark_task_running(db: Session, task: PipelineTask) -> None:
    task.status = TaskStatus.RUNNING
    task.started_at = datetime.utcnow()
    db.commit()


def mark_task_success(db: Session, task: PipelineTask, summary: str) -> None:
    task.status = TaskStatus.SUCCESS
    task.result_summary = summary
    task.finished_at = datetime.utcnow()
    db.commit()


def mark_task_failed(db: Session, task: PipelineTask, error: str) -> None:
    task.status = TaskStatus.FAILED
    task.error_message = error
    task.finished_at = datetime.utcnow()
    db.commit()
