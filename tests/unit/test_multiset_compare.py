"""Exact multiset comparison and the refusal rule for manual reference rewrites."""

from __future__ import annotations

from decimal import Decimal

from query_cost_predictor.rewrite_verify import EQUIVALENT, INCONCLUSIVE, NOT_EQUIVALENT, compare_results, decide


def compare(a, b, **kwargs):
    columns = len(a[0]) if a else (len(b[0]) if b else 1)
    kwargs.setdefault("ordering_required", False)
    return compare_results(a, b, original_columns=kwargs.pop("original_columns", columns),
                           rewrite_columns=kwargs.pop("rewrite_columns", columns), **kwargs)


def test_same_multiset_in_different_order_is_equivalent() -> None:
    result = compare([(1, Decimal("2.50")), (2, None)], [(2, None), (1, Decimal("2.5"))])
    assert result.status == EQUIVALENT and result.multiset_equal is True
    assert result.ordering_status == "NOT_REQUIRED"


def test_duplicate_rows_matter() -> None:
    assert compare([(1,), (1,), (2,)], [(1,), (2,)]).status == NOT_EQUIVALENT  # UNION ALL vs UNION


def test_null_rows_are_retained() -> None:
    assert compare([(None,)], []).status == NOT_EQUIVALENT
    assert compare([(None,), (None,)], [(None,)]).status == NOT_EQUIVALENT
    assert compare([(None,), (1,)], [(1,), (None,)]).status == EQUIVALENT


def test_column_count_mismatch_is_not_equivalent() -> None:
    assert compare([(1, 2)], [(1,)], original_columns=2, rewrite_columns=1).status == NOT_EQUIVALENT


def test_truncated_results_are_inconclusive() -> None:
    result = compare([(1,)], [(1,)], truncated=True)
    assert result.status == INCONCLUSIVE and result.multiset_equal is None


def test_ordering_is_checked_only_when_required() -> None:
    a, b = [(1,), (2,)], [(2,), (1,)]
    assert compare(a, b).status == EQUIVALENT
    required = compare(a, b, ordering_required=True, order_by_columns=(0,))
    assert required.ordering_status == "VIOLATED" and required.status == NOT_EQUIVALENT


def test_nulls_sort_last_in_ascending_order() -> None:
    result = compare([(1,), (None,)], [(1,), (None,)], ordering_required=True, order_by_columns=(0,))
    assert result.ordering_status == "SATISFIED" and result.status == EQUIVALENT


def test_recommendation_requires_equivalence_and_speedup() -> None:
    assert decide("reference_pair", NOT_EQUIVALENT, "SKIPPED_NOT_EQUIVALENT", None, 1.10)[0] == "DO_NOT_RECOMMEND"
    assert decide("reference_pair", INCONCLUSIVE, "NOT_RUN", None, 1.10)[0] == "DO_NOT_RECOMMEND"
    assert decide("reference_pair", EQUIVALENT, "INCOMPLETE (rewrite timeout)", None, 1.10)[0] == "DO_NOT_RECOMMEND"
    assert decide("reference_pair", EQUIVALENT, "COMPLETE", 1.05, 1.10)[0] == "DO_NOT_RECOMMEND"
    assert decide("reference_pair", EQUIVALENT, "COMPLETE", 1.50, 1.10)[0] == "RECOMMEND"
    assert decide("negative_control", EQUIVALENT, "COMPLETE", 3.00, 1.10)[0] == "DO_NOT_RECOMMEND"
