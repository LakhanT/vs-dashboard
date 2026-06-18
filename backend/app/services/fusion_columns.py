"""Fusion Matrix PF/RS score column mapping (Excel → DB keys)."""

from __future__ import annotations

from typing import Any

import pandas as pd


def _safe_float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

# Excel tier headers (pandas may suffix duplicates as 0.0025.1, etc.)
FUSION_SCORE_GROUPS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "pf_perf",
        "PF Performance Score",
        [
            ("pf_perf_t0025", "0.0025"),
            ("pf_perf_t01", "0.01"),
            ("pf_perf_t02", "0.02"),
            ("pf_perf_t03", "0.03"),
        ],
    ),
    (
        "pf_rank",
        "PF Ranking Score",
        [
            ("pf_rank_t0025", "0.0025.1"),
            ("pf_rank_t01", "0.01.1"),
            ("pf_rank_t02", "0.02.1"),
            ("pf_rank_t03", "0.03.1"),
        ],
    ),
    (
        "rs_perf",
        "RS Performance Score",
        [
            ("rs_perf_t0025", "0.0025.2"),
            ("rs_perf_t01", "0.01.2"),
            ("rs_perf_t02", "0.02.2"),
            ("rs_perf_t03", "0.03.2"),
        ],
    ),
    (
        "rs_rank",
        "RS Ranking Score",
        [
            ("rs_rank_t0025", "0.0025.3"),
            ("rs_rank_t01", "0.01.3"),
            ("rs_rank_t02", "0.02.3"),
            ("rs_rank_t03", "0.03.3"),
        ],
    ),
]

FUSION_MAIN_SCORE_KEYS: dict[str, str] = {
    "pf_perf": "pf_perf_score",
    "pf_rank": "pf_rank_score",
    "rs_perf": "rs_perf_score",
    "rs_rank": "rs_rank_score",
}

FUSION_MAIN_SCORE_LABELS: dict[str, str] = {
    "pf_perf_score": "PF Performance Score",
    "pf_rank_score": "PF Ranking Score",
    "rs_perf_score": "RS Performance Score",
    "rs_rank_score": "RS Ranking Score",
}

FUSION_TIER_DISPLAY: dict[str, str] = {
    "t0025": "0.25",
    "t01": "1",
    "t02": "2",
    "t03": "3",
}


def _cell(row: Any, *names: str | float) -> Any:
    for name in names:
        if name in row.index:
            return row[name]
        s = str(name)
        if s in row.index:
            return row[s]
    return None


def fusion_scores_from_row(row: Any) -> dict[str, float | None]:
    """Extract PF/RS tier + main scores from a Fusion Matrix upload row."""
    out: dict[str, float | None] = {}

    for prefix, main_label, tiers in FUSION_SCORE_GROUPS:
        main_key = FUSION_MAIN_SCORE_KEYS[prefix]
        out[main_key] = _safe_float(_cell(row, main_label))

        # First PF perf tier group uses unsuffixed Excel headers (0.0025, 0.01, …)
        if prefix == "pf_perf":
            for (db_key, _), hdr in zip(tiers, [0.0025, 0.01, 0.02, 0.03]):
                out[db_key] = _safe_float(_cell(row, hdr, str(hdr)))
        else:
            for db_key, hdr in tiers:
                out[db_key] = _safe_float(_cell(row, hdr))

    return out


def all_fusion_score_db_keys() -> list[str]:
    keys: list[str] = []
    for prefix, _, tiers in FUSION_SCORE_GROUPS:
        keys.append(FUSION_MAIN_SCORE_KEYS[prefix])
        keys.extend(k for k, _ in tiers)
    return keys
