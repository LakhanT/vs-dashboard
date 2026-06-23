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
    PipelineTask,
    RsiSnapshot,
    Stock,
    TaskStatus,
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
    """Excel percent cells are whole numbers (5.35 = 5.35%); store as ratio."""
    if value is None:
        return None
    return value / 100


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
    """
    Import universe + static reference data only.

    From Excel we take:
    - Universe & RSI values (MRSI DIgger)
    - F&O flags / segment metadata
    - Fusion Matrix static scores
    - Stock master fields (scrip, sector, ticker, market cap) from Microsoft Price

    We do NOT import calculated columns (LTP, OHLC, ranks, retracement) — those are
    computed by refresh_live_data() from Yahoo OHLC + Fyers LTP using Excel formulas.
    """
    path = Path(excel_path)
    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_path}")

    counts = {
        "stocks": 0,
        "rsi": 0,
        "fusion": 0,
        "fno": 0,
        "metadata": 0,
    }
    as_of = date.today()

    # F&O membership (universe flags)
    fno_df = pd.read_excel(path, sheet_name="F&O", header=0)
    fno_scrips: set[str] = set()
    for _, row in fno_df.iterrows():
        scrip = _normalize_scrip(row.get("Scrip"))
        if not scrip:
            continue
        fno_scrips.add(scrip)
        _upsert_stock(
            db,
            scrip,
            sector=str(row.get("Sector") or "").strip() or None,
            segment=str(row.get("Segment") or "").strip() or None,
            market_cap_cr=_safe_float(row.get("Market Cap (Cr)")),
            is_fno=True,
        )
        db.flush()
        counts["fno"] += 1

    # Stock metadata only — no LTP / % change (calculated live)
    price_df = pd.read_excel(path, sheet_name="Microsoft Price", header=0)
    for _, row in price_df.iterrows():
        scrip = _normalize_scrip(row.get("Scrip"))
        if not scrip:
            continue
        _upsert_stock(
            db,
            scrip,
            ticker_symbol=str(row.get("Ticker symbol") or "").strip() or None,
            sector=str(row.get("Sector") or "").strip() or None,
            market_cap_cr=_safe_float(row.get("Market Cap")),
            is_fno=scrip in fno_scrips,
        )
        db.flush()
        counts["metadata"] += 1

    # RSI universe + static RSI columns (upload once, display as-is)
    from app.services.universe import clear_rsi_snapshots

    rsi_df = pd.read_excel(path, sheet_name="MRSI DIgger", header=0)
    clear_rsi_snapshots(db)
    db.flush()
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
        counts["stocks"] += 1
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

    # Fusion Matrix — static scores (upload once)
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
            pct_from_dtb=_as_decimal_pct(_safe_float(row.get("% From DTB"))),
            pct_from_dbs=_as_decimal_pct(_safe_float(row.get("% From DBS"))),
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
