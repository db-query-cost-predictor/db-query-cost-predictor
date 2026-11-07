"""Exact multiset and declared-order comparisons; no legacy harness."""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from typing import Any, Sequence

EQUIVALENT = "EQUIVALENT_ON_SNAPSHOT"


NOT_EQUIVALENT = "NOT_EQUIVALENT"


INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class MultisetComparison:
    status: str  # EQUIVALENT_ON_SNAPSHOT | NOT_EQUIVALENT | INCONCLUSIVE
    multiset_equal: bool | None
    ordering_status: str
    original_rows: int
    rewrite_rows: int
    detail: str


def _order_key(row: Sequence[Any], columns: Sequence[int]) -> tuple[Any, ...]:
    # PostgreSQL's default ascending order puts NULLs last.
    return tuple((row[c] is None, row[c] if row[c] is not None else 0) for c in columns)


def _is_ordered(rows: list[tuple[Any, ...]], columns: Sequence[int]) -> bool:
    keys = [_order_key(r, columns) for r in rows]
    return all(a <= b for a, b in zip(keys, keys[1:]))


def compare_results(
    original: list[tuple[Any, ...]],
    rewrite: list[tuple[Any, ...]],
    *,
    original_columns: int,
    rewrite_columns: int,
    ordering_required: bool,
    order_by_columns: Sequence[int] = (),
    truncated: bool = False,
) -> MultisetComparison:
    """Exact multiset comparison (duplicates and NULLs retained; values compared with ==)."""
    if truncated:
        return MultisetComparison(INCONCLUSIVE, None, "NOT_CHECKED", len(original), len(rewrite),
                                  "result exceeded the row cap; equivalence not decided")
    if original_columns != rewrite_columns:
        return MultisetComparison(NOT_EQUIVALENT, False, "NOT_CHECKED", len(original), len(rewrite),
                                  f"column count differs ({original_columns} vs {rewrite_columns})")
    equal = Counter(original) == Counter(rewrite)
    if not ordering_required:
        ordering = "NOT_REQUIRED"
    elif order_by_columns:
        ordering = "SATISFIED" if _is_ordered(original, order_by_columns) and _is_ordered(rewrite, order_by_columns) else "VIOLATED"
    else:
        ordering = "SATISFIED" if original == rewrite else "VIOLATED"
    status = EQUIVALENT if equal and ordering in ("NOT_REQUIRED", "SATISFIED") else NOT_EQUIVALENT
    detail = "multisets equal" if equal else "multisets differ (row counts or values)"
    return MultisetComparison(status, equal, ordering, len(original), len(rewrite), detail)
