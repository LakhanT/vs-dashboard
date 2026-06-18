"""Lightweight SQLite schema patches for columns added after first deploy."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models import FusionSnapshot


def _sqlite_column_names(engine: Engine, table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def ensure_sqlite_columns(engine: Engine) -> None:
    if not str(engine.url).startswith("sqlite"):
        return

    fusion_cols = {c.name: c for c in FusionSnapshot.__table__.columns}
    existing = _sqlite_column_names(engine, "fusion_snapshots")
    if not existing:
        return

    with engine.begin() as conn:
        for name, col in fusion_cols.items():
            if name in existing:
                continue
            col_type = col.type.compile(dialect=engine.dialect)
            conn.execute(text(f"ALTER TABLE fusion_snapshots ADD COLUMN {name} {col_type}"))
