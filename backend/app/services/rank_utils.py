"""Ranking helpers aligned with Excel RANK.EQ (descending)."""

from __future__ import annotations


def excel_rank_eq_desc(values: dict[int, float]) -> dict[int, int]:
    """
    Match Excel _xlfn.RANK.EQ(value, range) with default order (descending).

    Rank = 1 + count of values strictly greater than this value.
  Ties share the same rank; next rank skips (e.g. 1, 1, 3).
    """
    if not values:
        return {}
    all_values = list(values.values())
    return {
        stock_id: 1 + sum(1 for other in all_values if other > pct)
        for stock_id, pct in values.items()
    }
