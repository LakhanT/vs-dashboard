"""Sync NSE F&O equity underlyings from official NSE archive."""

from __future__ import annotations

import io
import logging

import httpx
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Stock
from app.services.pipeline import _normalize_scrip, _upsert_stock

logger = logging.getLogger(__name__)

FNO_CSV_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
INDEX_SYMBOLS = {
    "NIFTY",
    "BANKNIFTY",
    "FINNIFTY",
    "MIDCPNIFTY",
    "NIFTYNXT50",
    "SYMBOL",
    "Symbol",
}


def fetch_fno_symbols() -> list[str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/csv,application/json,*/*",
        "Referer": "https://www.nseindia.com/",
    }
    with httpx.Client(headers=headers, timeout=45, follow_redirects=True) as client:
        client.get("https://www.nseindia.com")
        response = client.get(FNO_CSV_URL)
        response.raise_for_status()

    df = pd.read_csv(io.BytesIO(response.content))
    df.columns = [str(c).strip() for c in df.columns]
    symbol_col = "SYMBOL" if "SYMBOL" in df.columns else df.columns[1]
    symbols = (
        df[symbol_col]
        .astype(str)
        .str.strip()
        .str.upper()
        .tolist()
    )
    return sorted({s for s in symbols if s and s not in INDEX_SYMBOLS})


def sync_fno_flags(db: Session) -> dict[str, int]:
    """Mark F&O stocks in DB from NSE official list."""
    symbols = fetch_fno_symbols()
    counts = {"fno_symbols": len(symbols), "updated": 0, "created": 0}

    # Reset all to non-FNO first, then set matches
    for stock in db.scalars(select(Stock)):
        stock.is_fno = False
    db.flush()

    for symbol in symbols:
        scrip = _normalize_scrip(symbol)
        if not scrip:
            continue
        existing = db.scalar(select(Stock).where(Stock.scrip == scrip))
        if existing:
            existing.is_fno = True
            existing.ticker_symbol = existing.ticker_symbol or symbol
            counts["updated"] += 1
        else:
            _upsert_stock(db, scrip, ticker_symbol=symbol, is_fno=True)
            counts["created"] += 1

    db.commit()
    logger.info("F&O sync complete: %s", counts)
    return counts
