"""Definedge / NSE / BSE scrip parsing (e.g. ATLANTAELE-BE-EQ = BSE equity)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models import Stock

Exchange = Literal["NSE", "BSE"]

# Definedge: SYMBOL-BE-EQ means BSE-listed equity (not NSE T2T series).
EXCHANGE_CODES: dict[str, Exchange] = {
    "BE": "BSE",
}

# NSE series codes between symbol and -EQ (excluding BE which is BSE).
NSE_SERIES_CODES = frozenset(
    {
        "BZ",
        "BL",
        "IL",
        "IQ",
        "ST",
        "GS",
        "SM",
        "IT",
        "RR",
        "IV",
        "E1",
        "E2",
        "T0",
        "T1",
    }
)


@dataclass(frozen=True)
class ParsedScrip:
    canonical_scrip: str
    base_symbol: str
    exchange: Exchange
    series: str | None
    raw: str


def _strip_exchange_suffix(text: str) -> str:
    for suffix in (".NS", ".BO", ".NSE", ".BSE"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _exchange_suffix(exchange: Exchange) -> str:
    return ".BO" if exchange == "BSE" else ".NS"


def parse_scrip(raw: str | None) -> ParsedScrip | None:
    if raw is None:
        return None
    text = _strip_exchange_suffix(str(raw).strip().upper())
    if not text or text == "SCRIP":
        return None

    exchange: Exchange = "BSE" if str(raw).strip().upper().endswith(".BO") else "NSE"
    series: str | None = None
    symbol = text

    if text.endswith("-EQ"):
        body = text[: -len("-EQ")]
        if "-" in body:
            symbol_part, middle = body.rsplit("-", 1)
            if middle in EXCHANGE_CODES:
                symbol = symbol_part
                exchange = EXCHANGE_CODES[middle]
            elif middle in NSE_SERIES_CODES:
                symbol = symbol_part
                series = middle
                exchange = "NSE"
            else:
                symbol = body
        else:
            symbol = body
    else:
        parts = text.rsplit("-", 1)
        if len(parts) == 2:
            if parts[1] in EXCHANGE_CODES:
                symbol = parts[0]
                exchange = EXCHANGE_CODES[parts[1]]
            elif parts[1] in NSE_SERIES_CODES:
                symbol = parts[0]
                series = parts[1]
                exchange = "NSE"

    if exchange == "BSE":
        canonical = f"{symbol}-BE-EQ"
    elif series:
        canonical = f"{symbol}-{series}-EQ"
    else:
        canonical = f"{symbol}-EQ"

    return ParsedScrip(
        canonical_scrip=canonical,
        base_symbol=symbol,
        exchange=exchange,
        series=series,
        raw=str(raw).strip(),
    )


def normalize_scrip(value: str | None) -> str | None:
    parsed = parse_scrip(value)
    return parsed.canonical_scrip if parsed else None


def parse_stock(stock: Stock) -> ParsedScrip | None:
    if stock.scrip:
        parsed = parse_scrip(stock.scrip)
        if parsed:
            return parsed
    if stock.ticker_symbol:
        return parse_scrip(stock.ticker_symbol)
    return None


def base_symbol(value: str | Stock | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Stock):
        parsed = parse_stock(value)
        return parsed.base_symbol if parsed else None
    parsed = parse_scrip(value)
    return parsed.base_symbol if parsed else None


def stock_exchange(value: str | Stock) -> Exchange:
    if isinstance(value, Stock):
        parsed = parse_stock(value)
    else:
        parsed = parse_scrip(value)
    return parsed.exchange if parsed else "NSE"


def is_bse_stock(stock: Stock) -> bool:
    return stock_exchange(stock) == "BSE"


def to_fyers_symbol(value: str | Stock) -> str | None:
    """Map Definedge scrip to Fyers symbol, e.g. NSE:RELIANCE-EQ or BSE:ATLANTAELE-EQ."""
    if isinstance(value, Stock):
        parsed = parse_stock(value)
    else:
        parsed = parse_scrip(value)
    if not parsed:
        return None

    exchange = "BSE" if parsed.exchange == "BSE" else "NSE"
    if parsed.exchange == "BSE":
        short = f"{parsed.base_symbol}-EQ"
    elif parsed.series:
        short = f"{parsed.base_symbol}-{parsed.series}-EQ"
    else:
        short = f"{parsed.base_symbol}-EQ"
    return f"{exchange}:{short}"


def to_yahoo_ticker(value: str | Stock) -> str | None:
    if isinstance(value, Stock):
        parsed = parse_stock(value)
    else:
        parsed = parse_scrip(value)
    if not parsed:
        return None
    return f"{parsed.base_symbol}{_exchange_suffix(parsed.exchange)}"


def scrip_from_yahoo(yahoo_symbol: str, *, series: str | None = None) -> str:
    text = yahoo_symbol.strip().upper()
    if text.endswith(".BO"):
        base = _strip_exchange_suffix(text)
        return f"{base}-BE-EQ"
    parsed = parse_scrip(yahoo_symbol)
    if parsed is None:
        base = _strip_exchange_suffix(text)
        return f"{base}-EQ"
    if series and series in EXCHANGE_CODES:
        return f"{parsed.base_symbol}-{series.upper()}-EQ"
    if series and series in NSE_SERIES_CODES:
        return f"{parsed.base_symbol}-{series.upper()}-EQ"
    return parsed.canonical_scrip
