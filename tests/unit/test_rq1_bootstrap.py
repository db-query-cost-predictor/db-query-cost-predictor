"""RQ1 Slice B: paired cluster bootstrap (plan, pairing, repeated groups, invalid replicates, shared files).

SYNTHETIC arrays and temporary run directories only.
"""

from __future__ import annotations

import csv
import io
import json
from types import SimpleNamespace

import numpy as np
import pytest

from query_cost_predictor.rq1_bootstrap import (
    GROUPS_FILE,
    INDICES_FILE,
    REPLICATES_FILE,
    SUMMARY_FILE,
    BootstrapContribution,
    BootstrapPlan,
    Rq1BootstrapError,
    add_contribution,
    bootstrap_output_bytes,
    get_or_create_plan,
    make_bootstrap_plan,
    paired_bootstrap,
    percentile_interval,
    write_bootstrap_outputs,
)
from query_cost_predictor.rq1_config import load_rq1_config
from query_cost_predictor.rq1_metrics import MetricValue, rank_metric_values, runtime_metric_values
from query_cost_predictor.rq1_run import create_run_context

pytestmark = [pytest.mark.filterwarnings("error::FutureWarning"), pytest.mark.filterwarnings("error::DeprecationWarning"),
              pytest.mark.filterwarnings("error::sklearn.exceptions.ConvergenceWarning")]

ROW_GROUPS = ["g_b", "g_a", "g_c", "g_a", "g_b", "g_c", "g_c"]
ACTUAL = np.array([1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0])


def small_config(n_clusters=3, replicates=25, seed=20261021):
    real = load_rq1_config().uncertainty
    return SimpleNamespace(uncertainty={**real, "n_clusters": n_clusters, "replicates": replicates, "seed": seed})


def runtime_statistic(pred):
    return lambda rows: runtime_metric_values(ACTUAL[rows], pred[rows])


def test_production_plan_settings_are_the_declared_ones():
    spec = load_rq1_config().uncertainty
    assert (spec["replicates"], spec["seed"], spec["n_clusters"], spec["cluster_column"]) == \
           (2000, 20261021, 194, "semantic_group_id")
    assert spec["interval"] == {"type": "percentile", "level": 0.95}


def test_same_seed_gives_identical_indices_and_another_seed_does_not():
    first, second = make_bootstrap_plan(ROW_GROUPS, small_config()), make_bootstrap_plan(ROW_GROUPS, small_config())
    assert first.groups == ("g_a", "g_b", "g_c")
    assert first.indices.shape == (25, 3) and np.array_equal(first.indices, second.indices)
    other = make_bootstrap_plan(ROW_GROUPS, small_config(seed=1))
    assert not np.array_equal(first.indices, other.indices)
    expected = np.random.default_rng(20261021).integers(0, 3, size=(25, 3), dtype=np.int64)
    assert np.array_equal(first.indices, expected)


def test_plan_requires_the_declared_group_count():
    with pytest.raises(Rq1BootstrapError, match="declares"):
        make_bootstrap_plan(ROW_GROUPS, small_config(n_clusters=4))


def test_a_group_drawn_twice_contributes_every_row_twice_case_weighted():
    plan = BootstrapPlan(groups=("g_a", "g_b", "g_c"), indices=np.array([[0, 0, 2]]), seed=0, level=0.95,
                         cluster_column="semantic_group_id")
    positions = plan.positions_by_group(ROW_GROUPS)
    rows = plan.replicate_rows(0, positions)
    assert rows.tolist() == [1, 3, 1, 3, 2, 5, 6]  # g_a twice (rows 1, 3), then g_c (rows 2, 5, 6)
    pred = ACTUAL * np.array([1.5, 0.5, 1.0, 2.0, 1.0, 1.25, 0.9])
    contribution = paired_bootstrap("regression", plan, ROW_GROUPS, {"m": runtime_statistic(pred)}, ["log_mae"], [], "x")
    expected = np.mean(np.abs(np.log(pred[rows]) - np.log(ACTUAL[rows])))
    assert contribution.replicate_rows[0]["value"] == pytest.approx(expected)
    assert contribution.summary["models"][0]["point_estimate"] == pytest.approx(np.mean(np.abs(np.log(pred / ACTUAL))))


def test_every_model_and_difference_uses_the_same_indices():
    plan = make_bootstrap_plan(ROW_GROUPS, small_config(replicates=30))
    seen = {"a": [], "b": [], "c": []}

    def recording(name, pred):
        def statistic(rows):
            seen[name].append(rows.tolist())
            return runtime_metric_values(ACTUAL[rows], pred[rows])
        return statistic
    preds = {"a": ACTUAL * 1.1, "b": ACTUAL * 0.7, "c": ACTUAL[::-1].copy()}
    contribution = paired_bootstrap("regression", plan, ROW_GROUPS, {n: recording(n, p) for n, p in preds.items()},
                                    ["log_mae", "median_qerror"], [("a", "b"), ("a", "c")], "learned minus baseline")
    assert seen["a"] == seen["b"] == seen["c"]  # first entry is the point estimate on all rows, then 30 replicates
    assert len(seen["a"]) == 31
    values = {(r["statistic_kind"], r["model_id"], r["baseline_model_id"], r["metric"], r["replicate"]): r["value"]
              for r in contribution.replicate_rows}
    for replicate in range(30):
        for metric in ("log_mae", "median_qerror"):
            for baseline in ("b", "c"):
                assert values[("difference", "a", baseline, metric, replicate)] == pytest.approx(
                    values[("model", "a", None, metric, replicate)] - values[("model", baseline, None, metric, replicate)])


def test_undefined_replicates_are_counted_invalid_with_their_reason():
    groups = ["g_a", "g_a", "g_b", "g_b"]
    actual = np.array([1.0, 1.0, 5.0, 5.0])  # constant within each group
    plan = make_bootstrap_plan(groups, small_config(n_clusters=2, replicates=200))
    statistics = {"m": lambda rows: rank_metric_values(actual[rows], np.arange(rows.size, dtype=float)),
                  "base": lambda rows: rank_metric_values(actual[rows], -np.arange(rows.size, dtype=float))}
    contribution = paired_bootstrap("regression", plan, groups, statistics, ["spearman"], [("m", "base")], "x")
    single_group = sum(len(set(plan.indices[r].tolist())) == 1 for r in range(200))
    assert 0 < single_group < 200
    model = contribution.summary["models"][0]
    assert model["n_invalid"] == single_group and model["n_valid"] == 200 - single_group
    assert model["invalid_reasons"] == {"actual values are constant": single_group}
    difference = contribution.summary["differences"][0]
    assert difference["n_invalid"] == single_group
    assert set(difference["invalid_reasons"]) == {"learned: actual values are constant; baseline: actual values are constant"}
    invalid_rows = [r for r in contribution.replicate_rows if r["value"] is None]
    assert len(invalid_rows) == 3 * single_group and all(r["invalid_reason"] for r in invalid_rows)


def test_percentile_interval_uses_the_valid_replicates_only():
    assert percentile_interval([], 0.95) == (None, None)
    lower, upper = percentile_interval(list(range(101)), 0.95)
    assert lower == pytest.approx(2.5) and upper == pytest.approx(97.5)


# ----------------------------------------------------------------------------- shared files (written once)
def regression_contribution(plan):
    return paired_bootstrap("regression", plan, ROW_GROUPS, {"m": runtime_statistic(ACTUAL * 1.2),
                                                             "b": runtime_statistic(ACTUAL * 0.8)},
                            ["log_mae"], [("m", "b")], "learned minus baseline")


@pytest.fixture
def context(tmp_path):
    return create_run_context(tmp_path / "results", "run_boot", "synthetic", small_config())


def test_outputs_are_written_once_after_every_required_task_contributed(context):
    plan = get_or_create_plan(context, ROW_GROUPS)
    assert get_or_create_plan(context, ROW_GROUPS) is plan
    add_contribution(context, regression_contribution(plan))
    with pytest.raises(Rq1BootstrapError, match="already exists"):
        add_contribution(context, regression_contribution(plan))
    with pytest.raises(Rq1BootstrapError, match="required"):
        write_bootstrap_outputs(context, ["regression", "classification_1s"])  # classification not contributed yet
    assert not any((context.run_dir / name).exists() for name in (GROUPS_FILE, INDICES_FILE, REPLICATES_FILE, SUMMARY_FILE))
    write_bootstrap_outputs(context, ["regression"])
    with pytest.raises(Rq1BootstrapError, match="once"):
        write_bootstrap_outputs(context, ["regression"])
    with pytest.raises(Rq1BootstrapError, match="already written"):
        add_contribution(context, BootstrapContribution(task="classification_1s"))

    saved = np.load(context.run_dir / INDICES_FILE, allow_pickle=False)
    assert np.array_equal(saved["indices"], plan.indices) and saved["groups"].tolist() == list(plan.groups)
    assert saved["seed"].shape == () and int(saved["seed"]) == plan.seed
    groups = list(csv.DictReader(io.StringIO((context.run_dir / GROUPS_FILE).read_text(encoding="utf-8"))))
    assert [r["semantic_group_id"] for r in groups] == ["g_a", "g_b", "g_c"]
    replicates = list(csv.DictReader(io.StringIO((context.run_dir / REPLICATES_FILE).read_text(encoding="utf-8"))))
    assert len(replicates) == 25 * 3
    summary = json.loads((context.run_dir / SUMMARY_FILE).read_text(encoding="utf-8"))
    assert summary["replicates"] == 25 and summary["seed"] == 20261021 and summary["n_clusters"] == 3
    assert summary["tasks"]["regression"]["models"][0]["n_valid"] == 25
    assert summary["tasks"]["regression"]["differences"][0]["invalid_reasons"] == {}


def test_a_later_task_must_share_the_plan(context):
    get_or_create_plan(context, ROW_GROUPS)
    with pytest.raises(Rq1BootstrapError, match="share one plan"):
        get_or_create_plan(context, ["g_a", "g_b", "g_x"])


def test_output_bytes_are_deterministic():
    plan = make_bootstrap_plan(ROW_GROUPS, small_config())
    first = bootstrap_output_bytes(plan, {"regression": regression_contribution(plan)}, small_config())
    second = bootstrap_output_bytes(make_bootstrap_plan(ROW_GROUPS, small_config()),
                                    {"regression": regression_contribution(plan)}, small_config())
    assert first == second


def test_metric_value_reasons_reach_the_replicate_rows():
    plan = BootstrapPlan(groups=("g_a", "g_b", "g_c"), indices=np.array([[1, 1, 1]]), seed=0, level=0.95,
                         cluster_column="semantic_group_id")
    contribution = paired_bootstrap("regression", plan, ROW_GROUPS,
                                    {"m": lambda rows: {"r2_ms": MetricValue.null("fewer than 2 cases")
                                                        if rows.size < 2 else MetricValue(0.5)}}, ["r2_ms"], [], "x")
    assert contribution.replicate_rows[0]["value"] == 0.5  # g_b drawn three times: 6 rows
