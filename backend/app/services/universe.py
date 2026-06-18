"""RSI Digger upload defines the tradable universe until the next upload."""

from __future__ import annotations

from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import RsiSnapshot, Stock


def universe_rsi_date(db: Session) -> date | None:
    """
    The RSI upload session date — the snapshot date with the most stocks.

    We cannot use max(as_of) alone: a partial OHLC refresh on a newer day can
    create a handful of computed RSI rows and wrongly shrink the universe.
    """
    row = db.execute(
        select(RsiSnapshot.as_of, func.count(RsiSnapshot.id).label("cnt"))
        .group_by(RsiSnapshot.as_of)
        .order_by(func.count(RsiSnapshot.id).desc(), RsiSnapshot.as_of.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def latest_rsi_date(db: Session) -> date | None:
    """Alias used across the app — always the full RSI upload date."""
    return universe_rsi_date(db)


def count_rsi_universe(db: Session) -> int:
    rsi_date = universe_rsi_date(db)
    if rsi_date is None:
        return 0
    return int(
        db.scalar(
            select(func.count(RsiSnapshot.id)).where(RsiSnapshot.as_of == rsi_date)
        )
        or 0
    )


def get_rsi_universe_ids(db: Session) -> set[int]:
    rsi_date = universe_rsi_date(db)
    if rsi_date is None:
        return set()
    return set(db.scalars(select(RsiSnapshot.stock_id).where(RsiSnapshot.as_of == rsi_date)))


def get_rsi_universe_stocks(db: Session) -> list[Stock]:
    universe_ids = get_rsi_universe_ids(db)
    if not universe_ids:
        return []
    return list(
        db.scalars(
            select(Stock)
            .where(Stock.id.in_(universe_ids))
            .order_by(Stock.is_fno.desc(), Stock.scrip)
        )
    )


def clear_rsi_snapshots(db: Session) -> int:
    """Remove all RSI rows — called before a fresh RSI Digger upload."""
    result = db.execute(delete(RsiSnapshot))
    return result.rowcount or 0
