"""RQ1 Slice B: metric definedness, null reasons, two-case ranks and safe export (SYNTHETIC values)."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from query_cost_predictor.metrics import rank_correlations, regression_metrics
from query_cost_predictor.rq1_config import load_rq1_config
from query_cost_predictor.rq1_metrics import (
    METRIC_TABLE_COLUMNS,
    RANK_METRICS,
    REASON_CONSTANT_ACTUALS,
    REASON_CONSTANT_SCORES,
    REASON_EMPTY,
    REASON_MIN_CASES_2,
    REASON_OUTPUT_TYPE,
    RUNTIME_METRICS,
    MetricValue,
    Rq1MetricError,
    check_metric_spec,
    csv_bytes,
    json_bytes,
    metric_row,
    rank_metric_values,
    regression_metric_values,
    runtime_bin_labels,
    runtime_metric_values,
)
from rq1_fixtures import build_release

pytestmark = [pytest.mark.filterwarnings("error::FutureWarning"), pytest.mark.filterwarnings("error::DeprecationWarning"),
              pytest.mark.filterwarnings("error::sklearn.exceptions.ConvergenceWarning")]

EDGES, LABELS = [0, 10, 100, 1000, 10000], ["<10", "10-100", "100-1000", "1000-10000"]


def test_yaml_metric_rules_match_the_implementation(tmp_path):
    check_metric_spec(load_rq1_config())
    release = build_release(tmp_path)
    changed = release.reconfigure(lambda d: d["metrics"]["null_reasons"].__setitem__("empty", "no rows"))
    with pytest.raises(Rq1MetricError, match="null reason"):
        check_metric_spec(changed)
    changed = release.reconfigure(lambda d: d["metrics"]["definedness"].__setitem__("r2_ms", ["min_cases_2"]))
    with pytest.raises(Rq1MetricError, match="definedness for r2_ms"):
        check_metric_spec(changed)


def test_a_defined_zero_stays_zero():
    actual = [1.0, 10.0, 100.0]
    values = runtime_metric_values(actual, actual)
    assert values["mae_ms"] == MetricValue(0.0) and values["log_mae"] == MetricValue(0.0)
    assert values["rmse_ms"].value == 0.0 and values["median_qerror"].value == 1.0
    assert values["r2_log"].value == 1.0 and values["r2_ms"].value == 1.0


def test_r2_is_defined_for_constant_predictions_when_actuals_vary():
    actual = np.array([1.0, 2.0, 4.0, 8.0])
    pred = np.full(4, 3.0)
    values = runtime_metric_values(actual, pred)
    expected = 1 - np.sum((actual - pred) ** 2) / np.sum((actual - actual.mean()) ** 2)
    assert values["r2_ms"].value == pytest.approx(expected) and values["r2_ms"].null_reason is None
    assert values["r2_log"].value is not None


def test_r2_and_ranks_are_null_for_constant_actuals():
    actual, pred = [5.0, 5.0, 5.0], [1.0, 2.0, 3.0]
    values = regression_metric_values("runtime_prediction", actual, pred, np.log(pred))
    for name in ("r2_log", "r2_ms", "spearman", "kendall"):
        assert values[name] == MetricValue.null(REASON_CONSTANT_ACTUALS)
    assert values["mae_ms"].value == pytest.approx(3.0)  # error metrics stay defined


def test_r2_needs_two_cases_while_error_metrics_need_one():
    values = runtime_metric_values([4.0], [2.0])
    assert values["r2_log"] == MetricValue.null(REASON_MIN_CASES_2)
    assert values["r2_ms"] == MetricValue.null(REASON_MIN_CASES_2)
    assert values["median_qerror"].value == 2.0 and values["mae_ms"].value == 2.0


def test_empty_rows_give_the_metric_specific_reasons():
    values = regression_metric_values("runtime_prediction", [], [], [])
    for name in ("log_mae", "median_qerror", "p90_qerror", "p95_qerror", "mae_ms", "rmse_ms"):
        assert values[name] == MetricValue.null(REASON_EMPTY)
    for name in ("r2_log", "r2_ms", "spearman", "kendall"):
        assert values[name] == MetricValue.null(REASON_MIN_CASES_2)


def test_two_nonconstant_pairs_give_defined_ranks_while_the_legacy_helper_is_unchanged():
    increasing = rank_metric_values([1.0, 2.0], [10.0, 20.0])
    decreasing = rank_metric_values([1.0, 2.0], [20.0, 10.0])
    assert increasing["spearman"].value == pytest.approx(1.0) and increasing["kendall"].value == pytest.approx(1.0)
    assert decreasing["spearman"].value == pytest.approx(-1.0) and decreasing["kendall"].value == pytest.approx(-1.0)
    with pytest.raises(ValueError, match="at least three"):
        rank_correlations([1.0, 2.0], [10.0, 20.0])  # pilot/poster behaviour preserved


def test_ranks_with_three_or_more_pairs_reuse_the_shared_helper():
    actual, score = [1.0, 3.0, 2.0, 5.0], [0.1, 0.4, 0.2, 0.3]
    values = rank_metric_values(actual, score)
    legacy = rank_correlations(score, actual)
    assert values["spearman"].value == legacy["spearman"] and values["kendall"].value == legacy["kendall"]


def test_rank_null_reasons_follow_the_rule_order():
    assert rank_metric_values([1.0], [1.0])["spearman"] == MetricValue.null(REASON_MIN_CASES_2)
    assert rank_metric_values([], [])["kendall"] == MetricValue.null(REASON_MIN_CASES_2)
    assert rank_metric_values([2.0, 2.0], [1.0, 3.0])["spearman"] == MetricValue.null(REASON_CONSTANT_ACTUALS)
    assert rank_metric_values([1.0, 3.0], [7.0, 7.0])["kendall"] == MetricValue.null(REASON_CONSTANT_SCORES)


def test_rank_score_models_get_the_output_type_reason_for_runtime_metrics_only():
    values = regression_metric_values("rank_score", [1.0, 5.0, 3.0], None, [10.0, 40.0, 20.0])
    for name in RUNTIME_METRICS:
        assert values[name] == MetricValue.null(REASON_OUTPUT_TYPE)
    for name in RANK_METRICS:
        assert values[name].value == pytest.approx(1.0)


def test_runtime_metric_values_reuse_regression_metrics():
    actual, pred = [0.5, 3.0, 20.0, 400.0, 1200.0], [0.8, 2.0, 25.0, 300.0, 1500.0]
    values = runtime_metric_values(actual, pred)
    legacy = regression_metrics(actual, pred)
    for name in RUNTIME_METRICS:
        assert values[name].value == legacy[name]


def test_non_finite_inputs_are_rejected_not_repaired():
    with pytest.raises(Rq1MetricError, match="non-finite"):
        runtime_metric_values([1.0, 2.0], [1.0, float("nan")])
    with pytest.raises(Rq1MetricError, match="non-finite"):
        rank_metric_values([1.0, float("inf")], [1.0, 2.0])
    with pytest.raises(Rq1MetricError):
        MetricValue(float("nan"))
    with pytest.raises(Rq1MetricError):
        MetricValue(1.0, "both")


def test_runtime_bins_use_lower_inclusive_edges_and_never_drop_a_case():
    values = pd.Series([0.011, 9.999, 10.0, 99.0, 100.0, 999.9, 1000.0, 6906.413])
    labels = runtime_bin_labels(values, EDGES, LABELS)
    assert labels.tolist() == ["<10", "<10", "10-100", "10-100", "100-1000", "100-1000", "1000-10000", "1000-10000"]
    assert int(labels.value_counts().sum()) == len(values)
    for outside in (10000.0, -1.0):
        with pytest.raises(Rq1MetricError, match="outside the runtime bins"):
            runtime_bin_labels(pd.Series([5.0, outside]), EDGES, LABELS)


def test_metric_rows_keep_fold_and_stratum_rules():
    descriptor = {"n_keys": 2, "n_groups": 1, "keys_per_group": 2.0, "n_query_instances": 1}
    common = dict(task="regression", fold_scheme="fold_group", model_id="m", descriptor=descriptor, metric="mae_ms",
                  result=MetricValue(0.0))
    pooled = metric_row(scope="pooled_out_of_fold", fold=None, stratum_column=None, stratum_value=None, **common)
    per_fold = metric_row(scope="per_fold", fold=3, stratum_column=None, stratum_value=None, **common)
    assert pooled["fold"] is None and per_fold["fold"] == 3 and isinstance(per_fold["fold"], int)
    assert tuple(pooled) == METRIC_TABLE_COLUMNS
    assert pooled["n_positive"] is None and pooled["n_positive_groups"] is None
    assert pooled["n_contributing_positive_folds"] is None and pooled["value"] == 0.0
    with pytest.raises(Rq1MetricError):
        metric_row(scope="per_fold", fold=None, stratum_column=None, stratum_value=None, **common)
    with pytest.raises(Rq1MetricError):
        metric_row(scope="pooled_out_of_fold", fold=1, stratum_column=None, stratum_value=None, **common)


def test_exports_never_contain_nan_or_infinity():
    with pytest.raises(Rq1MetricError, match="never exported"):
        json_bytes({"rows": [{"value": float("nan")}]})
    with pytest.raises(Rq1MetricError):
        csv_bytes([{"a": float("inf")}], ["a"])
    text = json_bytes({"value": None, "zero": 0.0}).decode()
    assert json.loads(text) == {"value": None, "zero": 0.0}
    assert csv_bytes([{"a": None, "b": 0.1, "c": 3}], ["a", "b", "c"]) == b"a,b,c\n,0.1,3\n"
    assert math.isfinite(json.loads(json_bytes({"x": 1e-300}))["x"])
