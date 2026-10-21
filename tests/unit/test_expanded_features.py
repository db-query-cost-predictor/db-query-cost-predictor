"""Pre-execution feature availability for the expanded release (no database).

Written but not run by Claude Code. Plans are synthetic.
"""

from __future__ import annotations

import pytest

from query_cost_predictor.contract import LeakageError, assert_feature_matrix_allowed, load_registry
from query_cost_predictor.expanded_derive import EXPANDED_LABEL_COLUMNS, RELEASE_COLUMNS
from query_cost_predictor.plans import estimate_features, estimate_scalars
from query_cost_predictor.smoke_derive import FEATURE_COLUMNS

EXECUTION_ONLY = ["execution_time_ms", "actual_rows", "shared_hit_blocks", "shared_read_blocks", "temp_written_blocks",
                  "io_read_time_ms", "workers_launched", "spill_detected", "median_ms", "censor_lower_bound_ms",
                  "exec_censor_lower_bound_ms", "statement_censor_bound_ms", "hr_1s_class", "hr_10s_class",
                  "observation_status", "failure_class", "key_state", "planning_time_ms", "estimate_planning_time_ms"]
NEW_COLUMNS = RELEASE_COLUMNS + ["in_sample", "observation_status", "statement_censor_bound_ms",
                                 "exec_censor_lower_bound_ms", "censor_bound_basis", "hr_1s_class", "hr_10s_class",
                                 "failure_class", "first_error_sqlstate", "estimate_planning_time_ms", "key_state"]


def test_expanded_release_reuses_the_contract_approved_feature_set() -> None:
    assert_feature_matrix_allowed(list(FEATURE_COLUMNS))
    assert not set(FEATURE_COLUMNS) & set(EXECUTION_ONLY)
    assert not set(FEATURE_COLUMNS) & set(EXPANDED_LABEL_COLUMNS) - {"modeling_key"}


@pytest.mark.parametrize("column", EXECUTION_ONLY + RELEASE_COLUMNS)
def test_runtime_outcomes_and_release_annotations_are_rejected_as_features(column: str) -> None:
    with pytest.raises(LeakageError):
        assert_feature_matrix_allowed(["est_total_cost_log", column])


def test_new_columns_are_registered_and_never_model_inputs() -> None:
    registry = load_registry()
    for column in NEW_COLUMNS:
        spec = registry.get(column)
        assert spec is not None, column
        assert not spec.is_model_input, column


def test_plain_explain_planning_time_does_not_change_features(plan_node) -> None:
    plain = {"Plan": plan_node("Hash Join", children=[plan_node(**{"Relation Name": "orders"}), plan_node("Hash")])}
    with_summary = {**plain, "Planning Time": 0.42}  # SUMMARY ON adds this key; nothing was executed
    assert estimate_features(plain) == estimate_features(with_summary)
    assert estimate_scalars(plain) == estimate_scalars(with_summary)


def test_analyzed_plan_values_never_reach_estimate_features(plan_node) -> None:
    plain = {"Plan": plan_node("Sort", children=[plan_node(**{"Relation Name": "lineitem"})])}
    analyzed = {"Plan": plan_node("Sort", children=[plan_node(**{"Relation Name": "lineitem", "Actual Rows": 600572,
                                                                  "Shared Read Blocks": 10, "Temp Written Blocks": 900})],
                                  **{"Sort Method": "external merge", "Sort Space Type": "Disk", "Actual Rows": 600572}),
                "Execution Time": 2300.0, "Planning Time": 0.3}
    assert estimate_features(plain) == estimate_features(analyzed)
