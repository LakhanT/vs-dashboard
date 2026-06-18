"""Registry of dashboard filterable fields and dynamic filter evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.schemas import FilterRule


class FieldType(str, Enum):
    NUMBER = "number"
    STRING = "string"
    BOOLEAN = "boolean"
    PERCENT = "percent"


@dataclass
class FilterFieldDef:
    key: str
    label: str
    field_type: FieldType
    group: str
    operators: list[str]


FILTER_FIELDS: list[FilterFieldDef] = [
    # Stock
    FilterFieldDef("scrip", "Scrip", FieldType.STRING, "Stock", ["eq", "contains"]),
    FilterFieldDef("sector", "Sector", FieldType.STRING, "Stock", ["eq", "contains"]),
    FilterFieldDef("segment", "Segment", FieldType.STRING, "Stock", ["eq", "contains"]),
    FilterFieldDef("is_fno", "F&O Stock", FieldType.BOOLEAN, "Stock", ["is_true", "is_false"]),
    FilterFieldDef("market_cap_cr", "Market Cap (Cr)", FieldType.NUMBER, "Stock", ["lt", "lte", "gt", "gte", "eq"]),
    # Price
    FilterFieldDef("has_ltp", "Has LTP", FieldType.BOOLEAN, "Price", ["is_true", "is_false"]),
    FilterFieldDef("ltp", "LTP", FieldType.NUMBER, "Price", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("pct_change_today", "% Change Today", FieldType.PERCENT, "Price", ["lt", "lte", "gt", "gte", "eq"]),
  # Rankings
    FilterFieldDef("y_rank", "Y Rank", FieldType.NUMBER, "Rankings", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("q_rank", "Q Rank", FieldType.NUMBER, "Rankings", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("m_rank", "M Rank", FieldType.NUMBER, "Rankings", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("y_pct_change_open", "Y % From Open", FieldType.PERCENT, "Rankings", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("q_pct_change_open", "Q % From Open", FieldType.PERCENT, "Rankings", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("m_pct_change_open", "M % From Open", FieldType.PERCENT, "Rankings", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("y_high_retracement", "Y High Retracement", FieldType.PERCENT, "Rankings", ["lt", "lte", "gt", "gte", "eq"]),
    # RSI
    FilterFieldDef("rsi", "RSI", FieldType.NUMBER, "RSI", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("rsi_avg", "RSI Avg", FieldType.NUMBER, "RSI", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("rsi_diff", "RSI Diff", FieldType.NUMBER, "RSI", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("rsi_trend", "RSI Trend", FieldType.STRING, "RSI", ["eq", "contains"]),
    FilterFieldDef("crossover", "Crossover", FieldType.STRING, "RSI", ["eq", "contains"]),
    # Retracement
    FilterFieldDef("retracement_from_high", "Retracement From High", FieldType.PERCENT, "Retracement", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("green_range", "Green Range", FieldType.PERCENT, "Retracement", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("rise_from_low", "Rise From Low", FieldType.PERCENT, "Retracement", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("bullish_bo", "Bullish BO", FieldType.PERCENT, "Retracement", ["lt", "lte", "gt", "gte", "eq"]),
    # Fusion — PF/RS score groups (tiers + main totals)
    FilterFieldDef("pf_perf_score", "PF Performance Score", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("pf_perf_t0025", "PF Performance · 0.25", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("pf_perf_t01", "PF Performance · 1", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("pf_perf_t02", "PF Performance · 2", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("pf_perf_t03", "PF Performance · 3", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("pf_rank_score", "PF Ranking Score", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("pf_rank_t0025", "PF Ranking · 0.25", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("pf_rank_t01", "PF Ranking · 1", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("pf_rank_t02", "PF Ranking · 2", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("pf_rank_t03", "PF Ranking · 3", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("rs_perf_score", "RS Performance Score", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("rs_perf_t0025", "RS Performance · 0.25", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("rs_perf_t01", "RS Performance · 1", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("rs_perf_t02", "RS Performance · 2", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("rs_perf_t03", "RS Performance · 3", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("rs_rank_score", "RS Ranking Score", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("rs_rank_t0025", "RS Ranking · 0.25", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("rs_rank_t01", "RS Ranking · 1", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("rs_rank_t02", "RS Ranking · 2", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("rs_rank_t03", "RS Ranking · 3", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    # Fusion — setup & totals
    FilterFieldDef("fusion_setup", "Fusion Setup", FieldType.STRING, "Fusion", ["eq", "contains"]),
    FilterFieldDef("total_perf_score", "Total Perf Score", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("total_ranking_score", "Total Ranking Score", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("net_perf_score", "Net Perf Score", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("net_ranking_score", "Net Ranking Score", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("dtb_level", "DTB Level", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("dbs_level", "DBS Level", FieldType.NUMBER, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("pct_from_dtb", "% From DTB", FieldType.PERCENT, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
    FilterFieldDef("pct_from_dbs", "% From DBS", FieldType.PERCENT, "Fusion", ["lt", "lte", "gt", "gte", "eq"]),
]

FIELD_MAP = {f.key: f for f in FILTER_FIELDS}

DEFAULT_COLUMNS = [
    "scrip", "sector", "ltp", "pct_change_today",
    "y_rank", "q_rank", "m_rank",
    "rsi", "rsi_avg", "crossover",
    "retracement_from_high", "fusion_setup", "is_fno",
]

ALL_COLUMN_KEYS = list(dict.fromkeys(DEFAULT_COLUMNS + [f.key for f in FILTER_FIELDS]))


def get_filter_fields_payload() -> list[dict[str, Any]]:
    return [
        {
            "key": f.key,
            "label": f.label,
            "type": f.field_type.value,
            "group": f.group,
            "operators": f.operators,
        }
        for f in FILTER_FIELDS
    ]


def _coerce_value(field_key: str, raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    field = FIELD_MAP.get(field_key)
    if field is None:
        return raw
    if field.field_type == FieldType.BOOLEAN:
        if isinstance(raw, bool):
            return raw
        return str(raw).lower() in {"1", "true", "yes", "y"}
    if field.field_type in (FieldType.NUMBER, FieldType.PERCENT):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    return str(raw)


def _has_valid_ltp(row: dict[str, Any]) -> bool:
    ltp = row.get("ltp")
    if ltp is None or ltp == "":
        return False
    try:
        return float(ltp) > 0
    except (TypeError, ValueError):
        return False


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "is_true":
        return bool(actual) is True
    if operator == "is_false":
        return bool(actual) is False
    if actual is None:
        return False

    if operator == "contains":
        return str(expected).lower() in str(actual).lower()
    if operator == "eq":
        if isinstance(actual, str):
            return str(actual).lower() == str(expected).lower()
        return actual == expected
    if operator == "ne":
        if isinstance(actual, str):
            return str(actual).lower() != str(expected).lower()
        return actual != expected

    try:
        a = float(actual)
        e = float(expected)
    except (TypeError, ValueError):
        return False

    if operator == "lt":
        return a < e
    if operator == "lte":
        return a <= e
    if operator == "gt":
        return a > e
    if operator == "gte":
        return a >= e
    return False


def row_matches_rules(row: dict[str, Any], rules: list[FilterRule], logic: str = "and") -> bool:
    if not rules:
        return True

    results = []
    for rule in rules:
        if rule.field == "has_ltp":
            actual = _has_valid_ltp(row)
            results.append(_compare(actual, rule.operator, True))
            continue
        if rule.field not in FIELD_MAP:
            continue
        field_def = FIELD_MAP[rule.field]
        if field_def.field_type != FieldType.BOOLEAN and rule.operator not in {"is_true", "is_false"}:
            if rule.value is None or rule.value == "":
                continue
        actual = row.get(rule.field)
        expected = _coerce_value(rule.field, rule.value)
        results.append(_compare(actual, rule.operator, expected))

    if not results:
        return True
    return all(results) if logic == "and" else any(results)


def default_preset_rules() -> list[FilterRule]:
    from app.schemas import FilterRule as FR

    return [
        FR(field="y_rank", operator="lte", value=150),
        FR(field="q_rank", operator="lte", value=150),
        FR(field="m_rank", operator="lte", value=150),
        FR(field="rsi_avg", operator="gt", value=-2),
    ]
