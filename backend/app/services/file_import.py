"""Import RSI Digger and Fusion Matrix from uploaded files."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FusionSnapshot, RsiSnapshot, Stock
from app.services.fusion_columns import fusion_scores_from_row
from app.services.universe import clear_rsi_snapshots
from app.services.pipeline import (
    _as_decimal_pct,
    _normalize_scrip,
    _safe_float,
    _safe_int,
    _safe_str,
    _upsert_stock,
)


def _read_upload(file_bytes: bytes, filename: str, sheet_name: str | None = None) -> pd.DataFrame:
    name = filename.lower()
    buffer = io.BytesIO(file_bytes)
    if name.endswith(".csv"):
        return pd.read_csv(buffer)
    if sheet_name:
        try:
            return pd.read_excel(buffer, sheet_name=sheet_name, header=0)
        except ValueError:
            buffer.seek(0)
    return pd.read_excel(buffer, header=0)


def import_rsi_digger_file(
    db: Session,
    file_bytes: bytes,
    filename: str,
    *,
    sheet_name: str | None = "MRSI DIgger",
) -> dict[str, int]:
    as_of = date.today()
    counts = {"rows": 0, "imported": 0, "skipped": 0}

    try:
        df = _read_upload(file_bytes, filename, sheet_name)
    except ValueError:
        df = _read_upload(file_bytes, filename, sheet_name=None)

    clear_rsi_snapshots(db)
    db.flush()

    for _, row in df.iterrows():
        counts["rows"] += 1
        scrip = _normalize_scrip(row.get("Scrip"))
        if not scrip:
            counts["skipped"] += 1
            continue

        stock = _upsert_stock(
            db,
            scrip,
            sector=_safe_str(row.get("Sector")),
            segment=_safe_str(row.get("Segment")),
            market_cap_cr=_safe_float(row.get("Market Cap (Cr)")),
        )
        db.flush()

        existing = db.scalar(
            select(RsiSnapshot).where(
                RsiSnapshot.stock_id == stock.id,
                RsiSnapshot.as_of == as_of,
            )
        )
        payload = dict(
            rsi=_safe_float(row.get("RSI")),
            rsi_avg=_safe_float(row.get("RSI Avg.") or row.get("RSI Avg")),
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
        counts["imported"] += 1

    db.commit()
    return counts


def import_fusion_matrix_file(
    db: Session,
    file_bytes: bytes,
    filename: str,
    *,
    sheet_name: str | None = "Fusion Matrix",
) -> dict[str, int]:
    as_of = date.today()
    counts = {"rows": 0, "imported": 0, "skipped": 0}

    try:
        df = _read_upload(file_bytes, filename, sheet_name)
    except ValueError:
        df = _read_upload(file_bytes, filename, sheet_name=None)

    for _, row in df.iterrows():
        counts["rows"] += 1
        scrip = _normalize_scrip(row.get("Scrip"))
        if not scrip:
            counts["skipped"] += 1
            continue

        stock = _upsert_stock(
            db,
            scrip,
            sector=_safe_str(row.get("Sector")),
            segment=_safe_str(row.get("Segment")),
            market_cap_cr=_safe_float(row.get("Market Cap (Cr)")),
        )
        db.flush()

        existing = db.scalar(
            select(FusionSnapshot).where(
                FusionSnapshot.stock_id == stock.id,
                FusionSnapshot.as_of == as_of,
            )
        )
        payload = dict(
            setup=_safe_str(row.get("Setup")),
            **fusion_scores_from_row(row),
            total_perf_score=_safe_float(row.get("Total Perf. Score") or row.get("Total Perf Score")),
            total_ranking_score=_safe_float(row.get("Total Ranking Score")),
            net_perf_score=_safe_float(row.get("Net Perf. Score") or row.get("Net Perf Score")),
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
        counts["imported"] += 1

    db.commit()
    return counts


def import_from_path(db: Session, path: str, import_type: str) -> dict[str, int]:
    data = Path(path).read_bytes()
    name = Path(path).name
    if import_type == "rsi":
        return import_rsi_digger_file(db, data, name)
    if import_type == "fusion":
        return import_fusion_matrix_file(db, data, name)
    raise ValueError(f"Unknown import type: {import_type}")
