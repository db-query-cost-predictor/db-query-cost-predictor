"""RQ1 Slice B: regression models, out-of-fold evaluation, metric tables and the regression stage.

SYNTHETIC package only (``rq1_fixtures.build_release``: 40 development rows in
10 groups, 4 heldout rows); every fit happens in memory or under ``tmp_path``.
"""

from __future__ import annotations

import csv
import io
import json
import warnings
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from query_cost_predictor import rq1_models
from query_cost_predictor.metrics import regression_metrics
from query_cost_predictor.rq1_bootstrap import write_bootstrap_outputs
from query_cost_predictor.rq1_config import SCHEMES, load_rq1_config
from query_cost_predictor.rq1_evaluate import (
    METRICS_REGRESSION_CSV,
    METRICS_REGRESSION_JSON,
    OOF_REGRESSION_FILE,
    Rq1EvaluationError,
    check_oof_coverage,
    evaluate_regression,
    load_feature_allowlist,
    regression_metric_table,
)
from query_cost_predictor.rq1_inputs import FeatureContractError, Rq1InputError, check_inputs
from query_cost_predictor.rq1_metrics import (
    REASON_CONSTANT_SCORES,
    REASON_EMPTY,
    REASON_OUTPUT_TYPE,
    REGRESSION_METRICS,
    Rq1MetricError,
)
from query_cost_predictor.rq1_models import (
    REGRESSION_MODEL_IDS,
    Rq1ModelOutputError,
    Rq1ModelSpecError,
    build_estimator,
    regression_model_specs,
)
from query_cost_predictor.rq1_report import REPORT_FILE, write_report
from query_cost_predictor.rq1_run import (
    COMPLETE,
    FAILED,
    RUN_RECORD_FILE,
    RunRefusedError,
    create_run_context,
    execute_run,
    production_stages,
    read_complete_run,
    read_status,
    stage_input_check,
    stage_regression,
    unimplemented_stages,
)
from rq1_fixtures import ALLOWLIST, build_release

pytestmark = [pytest.mark.filterwarnings("error::FutureWarning"), pytest.mark.filterwarnings("error::DeprecationWarning"),
              pytest.mark.filterwarnings("error::sklearn.exceptions.ConvergenceWarning")]

DEV_ROWS, DEV_GROUPS = 40, 10
SMALL_BOOTSTRAP = 40


def small_bootstrap(document):
    document["uncertainty"]["n_clusters"] = DEV_GROUPS
    document["uncertainty"]["replicates"] = SMALL_BOOTSTRAP


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    release = build_release(tmp_path_factory.mktemp("release"))
    config = release.reconfigure(small_bootstrap)
    dataset = check_inputs(config, release.evidence_dir, repo_root=release.repo).dataset
    allowlist = load_feature_allowlist(config, release.repo)
    return release, config, dataset, allowlist


@pytest.fixture(scope="module")
def evaluated(prepared):
    release, config, dataset, allowlist = prepared
    fits = []
    oof = evaluate_regression(dataset, config, allowlist,
                              on_fit=lambda scheme, fold, model, train, test: fits.append((scheme, fold, model, train, test)))
    return oof, fits


def heldout_keys(release) -> set[str]:
    with (release.evidence_dir / "derived" / "splits.csv").open(encoding="utf-8", newline="") as handle:
        return {row["modeling_key"] for row in csv.DictReader(handle) if row["split_role"] == "heldout_rq2"}


def fitted(fits, model_id):
    return [(scheme, fold, model, train, test) for scheme, fold, model, train, test in fits if model.model_id == model_id]


# ----------------------------------------------------------------------------- model factory
def test_the_six_declared_models_are_built_from_the_yaml_verbatim():
    config = load_rq1_config()
    specs = regression_model_specs(config)
    assert tuple(s.model_id for s in specs) == REGRESSION_MODEL_IDS
    by_id = {s.model_id: s for s in specs}
    assert by_id["reg_raw_cost_rank"].output_type == "rank_score"
    assert by_id["reg_cost_ols"].inputs == ("ln_cost",) and by_id["reg_scale_cost_ols"].inputs == ("ln_cost", "ln_scale")
    ridge = build_estimator(by_id["reg_ridge_all"])
    assert [name for name, _ in ridge.steps] == ["standardscaler", "ridge"]
    assert ridge.named_steps["ridge"].get_params()["alpha"] == 1.0 and ridge.named_steps["ridge"].solver == "svd"
    assert ridge.named_steps["standardscaler"].with_mean and ridge.named_steps["standardscaler"].with_std
    hgb = build_estimator(by_id["reg_hgb_all"]).get_params()
    assert {k: hgb[k] for k in ("loss", "max_iter", "learning_rate", "max_leaf_nodes", "min_samples_leaf",
                                "l2_regularization", "early_stopping", "categorical_features", "random_state")} == {
        "loss": "squared_error", "max_iter": 300, "learning_rate": 0.05, "max_leaf_nodes": 15, "min_samples_leaf": 5,
        "l2_regularization": 1.0, "early_stopping": False, "categorical_features": None, "random_state": 20260923}
    ols = build_estimator(by_id["reg_cost_ols"])
    assert type(ols).__name__ == "LinearRegression" and ols.fit_intercept is True
    assert build_estimator(by_id["reg_train_median"]) is None and build_estimator(by_id["reg_raw_cost_rank"]) is None


def test_model_specs_reject_unknown_parameters_and_changed_declarations(tmp_path):
    release = build_release(tmp_path)

    def bad_param(document):
        document["regression_models"][5]["params"]["n_iter_no_change_extra"] = 3
    with pytest.raises(Rq1ModelSpecError, match="does not accept"):
        regression_model_specs(release.reconfigure(bad_param))

    def reordered(document):
        document["regression_models"].reverse()
    with pytest.raises(Rq1ModelSpecError, match="exactly"):
        regression_model_specs(release.reconfigure(reordered))

    def learned_on_cost(document):
        document["regression_models"][4]["inputs"] = ["ln_cost"]
    with pytest.raises(Rq1ModelSpecError, match="allowlist"):
        regression_model_specs(release.reconfigure(learned_on_cost))


def test_learned_models_reject_a_changed_feature_matrix(prepared):
    _, config, dataset, allowlist = prepared
    ridge = next(s for s in regression_model_specs(config) if s.model_id == "reg_ridge_all")
    with pytest.raises(FeatureContractError):
        rq1_models.model_inputs(dataset, ridge, allowlist + ["est_total_cost"])
    with pytest.raises(FeatureContractError):
        rq1_models.model_inputs(dataset, ridge, list(reversed(allowlist)))


def test_allowlist_is_read_only_from_the_pinned_input_record(prepared, tmp_path):
    release, config, _, allowlist = prepared
    assert allowlist == ALLOWLIST and len(allowlist) == 69
    copy = tmp_path / "repo"
    (copy / "config").mkdir(parents=True)
    (copy / config.input_record_path).write_text(release.record_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(Rq1InputError, match="SHA-256"):
        load_feature_allowlist(config, copy)


# ----------------------------------------------------------------------------- fold loop and isolation
def test_every_model_sees_identical_rows_and_stored_folds(prepared, evaluated):
    _, _, dataset, _ = prepared
    _, fits = evaluated
    by_fold = defaultdict(list)
    for scheme, fold, model, train, test in fits:
        by_fold[(scheme, fold)].append((model.model_id, train, test))
    assert list(by_fold) == [(s, f) for s in SCHEMES for f in range(5)]
    for (scheme, fold), entries in by_fold.items():
        assert [m for m, _, _ in entries] == list(REGRESSION_MODEL_IDS)
        stored = dataset.folds[scheme].to_numpy()
        for _, train, test in entries:
            assert np.array_equal(test, np.flatnonzero(stored == fold))
            assert np.array_equal(train, np.flatnonzero(stored != fold))


def test_every_case_gets_exactly_one_oof_output_per_model_and_scheme(prepared, evaluated):
    _, _, dataset, _ = prepared
    oof, _ = evaluated
    assert len(oof) == DEV_ROWS * len(SCHEMES) * len(REGRESSION_MODEL_IDS)
    counts = oof.groupby(["fold_scheme", "model_id", "modeling_key"]).size()
    assert set(counts.tolist()) == {1}
    for scheme in SCHEMES:
        block = oof[(oof["fold_scheme"] == scheme) & (oof["model_id"] == "reg_hgb_all")].set_index("modeling_key")
        assert block["fold"].to_dict() == dataset.folds[scheme].to_dict()
    with pytest.raises(Rq1EvaluationError):
        check_oof_coverage(oof.iloc[1:], dataset.features.index, SCHEMES, REGRESSION_MODEL_IDS)
    with pytest.raises(Rq1EvaluationError):
        check_oof_coverage(pd.concat([oof, oof.iloc[:1]]), dataset.features.index, SCHEMES, REGRESSION_MODEL_IDS)


def test_fit_spy_proves_test_and_heldout_rows_never_reach_fitting(prepared, monkeypatch):
    release, config, dataset, allowlist = prepared
    heldout = heldout_keys(release)
    assert heldout  # the synthetic package does contain heldout_rq2 rows
    log = []
    original = rq1_models.build_regression_model

    def spying_build(spec):
        model = original(spec)
        fit, predict = model.fit, model.predict

        def spy_fit(inputs, y_ms):
            log.append(("fit", spec.model_id, list(inputs.index), list(y_ms.index)))
            if model.estimator is not None:
                estimator_fit = model.estimator.fit

                def spy_estimator_fit(X, y):
                    log.append(("estimator_fit", spec.model_id, np.array(X, copy=True), np.array(y, copy=True)))
                    return estimator_fit(X, y)
                model.estimator.fit = spy_estimator_fit
            return fit(inputs, y_ms)

        def spy_predict(inputs):
            log.append(("predict", spec.model_id, list(inputs.index), None))
            return predict(inputs)
        model.fit, model.predict = spy_fit, spy_predict
        return model
    monkeypatch.setattr(rq1_models, "build_regression_model", spying_build)
    evaluate_regression(dataset, config, allowlist)

    fits = [entry for entry in log if entry[0] == "fit"]
    predicts = [entry for entry in log if entry[0] == "predict"]
    assert len(fits) == len(predicts) == len(SCHEMES) * 5 * len(REGRESSION_MODEL_IDS)
    for (_, model_id, train_keys, y_keys), (_, predict_id, test_keys, _) in zip(fits, predicts):
        assert model_id == predict_id and train_keys == y_keys
        assert not set(train_keys) & set(test_keys)
        assert not (set(train_keys) | set(test_keys)) & heldout
        assert set(train_keys) | set(test_keys) == set(dataset.features.index)
    estimator_rows = [entry for entry in log if entry[0] == "estimator_fit"]
    fit_iter = iter(f for f in fits if f[1] not in ("reg_train_median", "reg_raw_cost_rank"))
    for (_, model_id, X, y), (_, fit_id, train_keys, _) in zip(estimator_rows, fit_iter):
        assert model_id == fit_id and X.shape[0] == len(train_keys) == y.shape[0]
        assert np.array_equal(y, dataset.targets.loc[train_keys, "ln_median_ms"].to_numpy())


def test_learned_models_get_exactly_the_69_features_and_baselines_do_not(evaluated):
    _, fits = evaluated
    for _, _, model, _, _ in fitted(fits, "reg_hgb_all") + fitted(fits, "reg_ridge_all"):
        assert model.estimator.n_features_in_ == 69
    for _, _, model, _, _ in fitted(fits, "reg_cost_ols"):
        assert model.estimator.n_features_in_ == 1
    for _, _, model, _, _ in fitted(fits, "reg_scale_cost_ols"):
        assert model.estimator.n_features_in_ == 2


def test_scaler_means_are_the_training_fold_means(prepared, evaluated):
    _, _, dataset, _ = prepared
    _, fits = evaluated
    everything = dataset.features.to_numpy().mean(axis=0)
    differs = []
    for _, _, model, train, test in fitted(fits, "reg_ridge_all"):
        scaler = model.estimator.named_steps["standardscaler"]
        expected = dataset.features.iloc[train].to_numpy().mean(axis=0)
        assert np.allclose(scaler.mean_, expected, rtol=0, atol=1e-12)
        differs.append(not np.allclose(scaler.mean_, everything))
    assert len(differs) == 15 and any(differs)  # the check can tell training-fold means from all-row means


def test_ols_coefficients_equal_lstsq_on_natural_log_cost(prepared, evaluated):
    _, _, dataset, _ = prepared
    _, fits = evaluated
    cost, scale = dataset.baseline_inputs["est_total_cost"].to_numpy(), dataset.baseline_inputs["snap_scale_factor"].to_numpy()
    y = np.log(dataset.targets["median_ms"].to_numpy())
    for model_id, columns in (("reg_cost_ols", [np.log(cost)]), ("reg_scale_cost_ols", [np.log(cost), np.log(scale)])):
        for _, _, model, train, _ in fitted(fits, model_id):
            design = np.column_stack([np.ones(train.size), *(c[train] for c in columns)])
            solution, *_ = np.linalg.lstsq(design, y[train], rcond=None)
            assert model.estimator.intercept_ == pytest.approx(solution[0], abs=1e-9)
            assert np.allclose(model.estimator.coef_, solution[1:], atol=1e-9)
            log1p_design = np.column_stack([np.ones(train.size), np.log1p(cost[train]), *(c[train] for c in columns[1:])])
            wrong, *_ = np.linalg.lstsq(log1p_design, y[train], rcond=None)
            assert not np.allclose(model.estimator.coef_, wrong[1:], atol=1e-6)  # natural log, not log1p


def test_ols_inputs_are_ln_cost_from_estimates_not_the_log1p_feature(prepared):
    _, config, dataset, allowlist = prepared
    spec = next(s for s in regression_model_specs(config) if s.model_id == "reg_cost_ols")
    inputs = rq1_models.model_inputs(dataset, spec, allowlist)
    assert list(inputs.columns) == ["ln_cost"]
    assert np.array_equal(inputs["ln_cost"].to_numpy(), np.log(dataset.baseline_inputs["est_total_cost"].to_numpy()))
    assert not np.allclose(inputs["ln_cost"].to_numpy(), np.log1p(dataset.baseline_inputs["est_total_cost"].to_numpy()))
    assert "est_total_cost_log" not in inputs.columns


def test_median_baseline_is_ln_of_the_training_fold_median(prepared, evaluated):
    _, _, dataset, _ = prepared
    oof, fits = evaluated
    y_ms = dataset.targets["median_ms"].to_numpy()
    for scheme, fold, model, train, test in fitted(fits, "reg_train_median"):
        expected = float(np.log(np.median(y_ms[train])))
        assert model.constant_ln_ == expected
        block = oof[(oof["fold_scheme"] == scheme) & (oof["model_id"] == "reg_train_median") & (oof["fold"] == fold)]
        assert set(block["pred_ln"].tolist()) == {expected}


def test_runtime_predictions_are_exp_of_predicted_ln_without_smearing(evaluated):
    oof, _ = evaluated
    runtime = oof[oof["model_id"] != "reg_raw_cost_rank"]
    assert np.array_equal(runtime["pred_ms"].to_numpy(), np.exp(runtime["pred_ln"].to_numpy()))
    assert runtime["score"].isna().all()
    raw = oof[oof["model_id"] == "reg_raw_cost_rank"]
    assert raw["pred_ln"].isna().all() and raw["pred_ms"].isna().all() and raw["score"].notna().all()


def test_raw_cost_score_is_the_raw_planner_cost(prepared, evaluated):
    _, _, dataset, _ = prepared
    oof, _ = evaluated
    raw = oof[(oof["model_id"] == "reg_raw_cost_rank") & (oof["fold_scheme"] == "fold_group")].set_index("modeling_key")
    assert raw["score"].to_dict() == dataset.baseline_inputs["est_total_cost"].to_dict()


@pytest.mark.parametrize("bad", [np.nan, np.inf, 1000.0])
def test_invalid_model_outputs_are_rejected_not_repaired(prepared, monkeypatch, bad):
    _, config, dataset, allowlist = prepared
    original = rq1_models.build_regression_model

    def broken(spec):
        model = original(spec)
        if spec.model_id == "reg_cost_ols":
            model.estimator.predict = lambda X: np.full(X.shape[0], bad)
        return model
    monkeypatch.setattr(rq1_models, "build_regression_model", broken)
    with pytest.raises(Rq1ModelOutputError, match="reg_cost_ols"):
        evaluate_regression(dataset, config, allowlist)


def test_output_shape_is_checked():
    with pytest.raises(Rq1ModelOutputError, match="expected 3"):
        rq1_models.validate_output("m", np.zeros(2), 3, "runtime_prediction")
    with pytest.raises(Rq1ModelOutputError, match="real numbers"):
        rq1_models.validate_output("m", np.array(["a", "b"]), 2, "rank_score")


# ----------------------------------------------------------------------------- metric table
@pytest.fixture(scope="module")
def table(prepared, evaluated):
    _, config, dataset, _ = prepared
    oof, _ = evaluated
    return regression_metric_table(oof, dataset, config, regression_model_specs(config))


def test_metric_table_covers_every_scheme_model_scope_and_metric(table):
    # 1 pooled + 5 folds + 2 snapshots + 4 components + 2 configurations + 4 runtime bins = 18 slices
    assert len(table) == len(SCHEMES) * len(REGRESSION_MODEL_IDS) * 18 * len(REGRESSION_METRICS)
    for row in table:
        assert (row["fold"] is None) == (row["scope"] == "pooled_out_of_fold")
        if row["scope"] == "per_fold":
            assert isinstance(row["fold"], int) and row["stratum_column"] is None
        assert row["n_positive"] is None and row["n_positive_groups"] is None
        assert row["n_contributing_positive_folds"] is None and row["flag"] is None
        assert (row["value"] is None) != (row["null_reason"] is None)
        assert row["ci_lower"] is None and row["ci_upper"] is None  # no bootstrap passed here


def test_pooled_values_equal_regression_metrics_on_the_oof_rows(table, evaluated):
    oof, _ = evaluated
    block = oof[(oof["fold_scheme"] == "fold_family") & (oof["model_id"] == "reg_ridge_all")]
    expected = regression_metrics(block["y_true_ms"], block["pred_ms"])
    rows = {r["metric"]: r for r in table if (r["fold_scheme"], r["model_id"], r["scope"], r["stratum_column"])
            == ("fold_family", "reg_ridge_all", "pooled_out_of_fold", None)}
    for metric in ("log_mae", "median_qerror", "p90_qerror", "p95_qerror", "mae_ms", "rmse_ms", "r2_log", "r2_ms"):
        assert rows[metric]["value"] == expected[metric]
    assert rows["log_mae"]["n_keys"] == DEV_ROWS and rows["log_mae"]["n_groups"] == DEV_GROUPS
    assert rows["log_mae"]["keys_per_group"] == DEV_ROWS / DEV_GROUPS and rows["log_mae"]["n_query_instances"] == 20


def test_raw_cost_rows_carry_the_output_type_reason(table):
    for row in table:
        if row["model_id"] == "reg_raw_cost_rank" and row["metric"] not in ("spearman", "kendall"):
            assert row["value"] is None and row["null_reason"] == REASON_OUTPUT_TYPE
    pooled = [r for r in table if r["model_id"] == "reg_raw_cost_rank" and r["scope"] == "pooled_out_of_fold"
              and r["stratum_column"] is None and r["metric"] == "spearman"]
    assert all(r["value"] is not None for r in pooled)


def test_constant_per_fold_median_scores_null_ranks_but_keep_errors(table):
    rows = [r for r in table if r["model_id"] == "reg_train_median" and r["scope"] == "per_fold"]
    assert all(r["null_reason"] == REASON_CONSTANT_SCORES for r in rows if r["metric"] in ("spearman", "kendall"))
    assert all(r["value"] is not None for r in rows if r["metric"] in ("log_mae", "r2_log", "r2_ms"))


def test_runtime_bin_counts_sum_to_the_evaluated_cases_and_empty_strata_are_null(table):
    for scheme in SCHEMES:
        for model in REGRESSION_MODEL_IDS:
            bins = [r for r in table if (r["fold_scheme"], r["model_id"], r["stratum_column"], r["metric"])
                    == (scheme, model, "runtime_bin_ms", "log_mae")]
            assert [r["stratum_value"] for r in bins] == ["<10", "10-100", "100-1000", "1000-10000"]
            assert sum(r["n_keys"] for r in bins) == DEV_ROWS
    empty = [r for r in table if r["stratum_value"] in ("100-1000", "C2_reduced_work_mem") and r["metric"] == "mae_ms"
             and r["model_id"] == "reg_hgb_all"]
    assert empty and all(r["n_keys"] == 0 and r["value"] is None and r["null_reason"] == REASON_EMPTY for r in empty)
    assert all(r["keys_per_group"] is None for r in empty)


def test_unexpected_stratum_values_are_errors_not_drops(prepared, evaluated):
    _, config, dataset, _ = prepared
    oof, _ = evaluated
    changed = dataset.metadata.copy()
    changed.iloc[0, changed.columns.get_loc("component")] = "unknown_component"
    altered = type(dataset)(features=dataset.features, baseline_inputs=dataset.baseline_inputs, targets=dataset.targets,
                            metadata=changed, folds=dataset.folds)
    with pytest.raises(Rq1MetricError, match="outside the declared strata"):
        regression_metric_table(oof, altered, config, regression_model_specs(config))


# ----------------------------------------------------------------------------- regression stage in a synthetic run
def final_stage(context):
    """Synthetic stand-in for the final assembly (Slice C adds classification first)."""
    write_bootstrap_outputs(context, ["regression"])
    write_report(context, ["regression"])


def synthetic_run(tmp_path: Path, name: str, config, release, extra=()):
    context = create_run_context(tmp_path / "results", name, "synthetic", config, evidence_dir=release.evidence_dir,
                                 repo_root=release.repo)
    stages = [("input_check", stage_input_check), ("regression", stage_regression), *extra]
    return context, stages


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory, prepared):
    release, config, _, _ = prepared
    root = tmp_path_factory.mktemp("runs")
    contexts = []
    for name in ("run_1", "run_2"):
        context, stages = synthetic_run(root, name, config, release, [("final", final_stage)])
        contexts.append(execute_run(context, stages))
    return contexts


def test_regression_stage_completes_a_synthetic_run(two_runs):
    context = two_runs[0]
    assert read_status(context.run_dir)["status"] == COMPLETE
    files = set(read_complete_run(context.run_dir)["files"])
    assert {"fold_membership.csv", OOF_REGRESSION_FILE, METRICS_REGRESSION_CSV, METRICS_REGRESSION_JSON,
            "bootstrap_groups.csv", "bootstrap_indices.npz", "bootstrap_replicates.csv", "bootstrap_summary.json",
            REPORT_FILE, RUN_RECORD_FILE} <= files


def test_written_tables_hold_no_nan_and_headline_rows_carry_intervals(two_runs):
    run_dir = two_runs[0].run_dir

    def refuse(token):
        raise AssertionError(f"{token} in exported JSON")
    document = json.loads((run_dir / METRICS_REGRESSION_JSON).read_text(encoding="utf-8"), parse_constant=refuse)
    rows = document["rows"]
    for name in (METRICS_REGRESSION_CSV, OOF_REGRESSION_FILE):
        text = (run_dir / name).read_text(encoding="utf-8").lower()
        assert "nan" not in text and "inf" not in text
    with_ci = [r for r in rows if r["ci_lower"] is not None]
    assert {(r["model_id"], r["metric"]) for r in with_ci} == {
        (m, k) for m in ("reg_train_median", "reg_cost_ols", "reg_scale_cost_ols", "reg_ridge_all", "reg_hgb_all")
        for k in ("log_mae", "median_qerror", "p90_qerror", "p95_qerror")}
    assert all(r["fold_scheme"] == "fold_group" and r["scope"] == "pooled_out_of_fold" and r["stratum_column"] is None
               for r in with_ci)
    assert all(r["ci_lower"] <= r["ci_upper"] for r in with_ci)
    oof = list(csv.DictReader(io.StringIO((run_dir / OOF_REGRESSION_FILE).read_text(encoding="utf-8"))))
    assert len(oof) == DEV_ROWS * 18 and all(r["fold"] in {"0", "1", "2", "3", "4"} for r in oof)


def test_bootstrap_point_estimates_are_the_pooled_headline_values(two_runs):
    run_dir = two_runs[0].run_dir
    rows = json.loads((run_dir / METRICS_REGRESSION_JSON).read_text(encoding="utf-8"))["rows"]
    table = {(r["model_id"], r["metric"]): r for r in rows if (r["fold_scheme"], r["scope"], r["stratum_column"])
             == ("fold_group", "pooled_out_of_fold", None)}
    summary = json.loads((run_dir / "bootstrap_summary.json").read_text(encoding="utf-8"))["tasks"]["regression"]
    assert summary["replicates"] == SMALL_BOOTSTRAP and summary["rows_per_full_sample"] == DEV_ROWS
    for entry in summary["models"]:
        row = table[(entry["model_id"], entry["metric"])]
        assert entry["point_estimate"] == row["value"]
        assert (entry["ci_lower"], entry["ci_upper"]) == (row["ci_lower"], row["ci_upper"])
        assert entry["n_valid"] + entry["n_invalid"] == SMALL_BOOTSTRAP
    pairs = {(e["model_id"], e["baseline_model_id"]) for e in summary["differences"]}
    assert pairs == {(l, b) for l in ("reg_ridge_all", "reg_hgb_all") for b in ("reg_cost_ols", "reg_scale_cost_ols")}
    for entry in summary["differences"]:
        expected = table[(entry["model_id"], entry["metric"])]["value"] - table[(entry["baseline_model_id"], entry["metric"])]["value"]
        assert entry["point_estimate"] == pytest.approx(expected)


def test_two_runs_give_byte_identical_oof_metric_and_bootstrap_files(two_runs):
    first, second = (context.run_dir for context in two_runs)
    for name in ("fold_membership.csv", OOF_REGRESSION_FILE, METRICS_REGRESSION_CSV, METRICS_REGRESSION_JSON,
                 "bootstrap_groups.csv", "bootstrap_indices.npz", "bootstrap_replicates.csv", "bootstrap_summary.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_report_contains_the_limitations_block_byte_for_byte_and_labels(two_runs, prepared):
    _, config, _, _ = prepared
    data = (two_runs[0].run_dir / REPORT_FILE).read_bytes()
    assert load_rq1_config().limitations_text.encode("utf-8") in data
    assert config.limitations_text.encode("utf-8") in data
    text = data.decode("utf-8")
    assert "### Primary: `fold_group`, pooled out-of-fold (headline)" in text
    assert "### Robustness check: `fold_family`" in text and "### Seen-template diagnostic: `fold_seen_template`" in text
    assert "never unseen-structure performance" in text
    assert "PARTIAL REPORT" in text and "not the RQ1 study" in text  # Slice B never presents a completed study
    assert "STOP (G-08)" in text and "NOT_YET_EVALUABLE" in text


def test_threadpool_limit_one_is_applied_and_recorded(two_runs):
    record = json.loads((two_runs[0].run_dir / RUN_RECORD_FILE).read_text(encoding="utf-8"))
    assert record["threadpool"]["requested_limit"] == 1
    during = record["regression"]["threadpool_during_fit"]
    assert during["requested_limit"] == 1 and during["effective"]
    assert all(entry["num_threads"] == 1 for entry in during["effective"])
    assert record["regression"]["models"] == list(REGRESSION_MODEL_IDS)
    assert record["regression"]["bootstrap_replicates"] == SMALL_BOOTSTRAP


def test_convergence_warning_during_a_fit_marks_the_run_failed(tmp_path, prepared, monkeypatch):
    from sklearn.exceptions import ConvergenceWarning

    release, config, _, _ = prepared
    original = rq1_models.build_regression_model

    def warning_build(spec):
        model = original(spec)
        if spec.model_id == "reg_ridge_all":
            estimator_fit = model.estimator.fit

            def fit(X, y):
                warnings.warn("synthetic non-convergence", ConvergenceWarning)
                return estimator_fit(X, y)
            model.estimator.fit = fit
        return model
    monkeypatch.setattr(rq1_models, "build_regression_model", warning_build)
    context, stages = synthetic_run(tmp_path, "run_cw", config, release, [("final", final_stage)])
    with pytest.raises(ConvergenceWarning):
        execute_run(context, stages)
    status = read_status(context.run_dir)
    assert status["status"] == FAILED and status["failed_stage"] == "regression"
    assert status["last_completed_stage"] == "input_check"
    assert not (context.run_dir / REPORT_FILE).exists() and not (context.run_dir / OOF_REGRESSION_FILE).exists()


def test_regression_stage_needs_the_checked_dataset(tmp_path, prepared):
    release, config, _, _ = prepared
    context = create_run_context(tmp_path / "results", "run_nodata", "synthetic", config, repo_root=release.repo)
    with pytest.raises(Rq1EvaluationError, match="input_check"):
        execute_run(context, [("regression", stage_regression)])
    assert read_status(context.run_dir)["status"] == FAILED


def test_production_pipeline_registers_regression_in_order(tmp_path):
    config = load_rq1_config()
    assert config.uncertainty["replicates"] == 2000 and config.uncertainty["seed"] == 20261021
    assert unimplemented_stages(config.stages) == []
    assert [name for name, _ in production_stages(config.stages)][1] == "regression"
    with pytest.raises(RunRefusedError, match="not implemented"):
        production_stages([*config.stages, "unknown_stage"])
