"""RQ1 Slice C1: 1 s classifiers, out-of-fold evaluation, metrics, flags and the classification_1s stage.

SYNTHETIC data only: ``rq1_fixtures.build_release`` (40 development rows in 10
groups, 4 positives, 4 heldout rows) and hand-made toy arrays. Every fit happens
in memory or under ``tmp_path``.
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
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import auc, precision_recall_curve

from query_cost_predictor import rq1_models
from query_cost_predictor.rq1_bootstrap import (
    BootstrapPlan,
    Rq1BootstrapError,
    paired_bootstrap,
    required_bootstrap_tasks,
    write_bootstrap_outputs,
)
from query_cost_predictor.rq1_config import SCHEMES, Rq1ConfigError, load_rq1_config
from query_cost_predictor.rq1_evaluate import (
    CALIBRATION_BINS_FILE,
    METRICS_CLASSIFICATION_CSV,
    METRICS_CLASSIFICATION_JSON,
    OOF_CLASSIFICATION_COLUMNS,
    OOF_CLASSIFICATION_FILE,
    OOF_REGRESSION_FILE,
    Rq1EvaluationError,
    Rq1NotEvaluableError,
    check_oof_coverage,
    classification_metric_table,
    evaluate_classification,
    load_feature_allowlist,
)
from query_cost_predictor.rq1_inputs import METADATA_COLUMNS, DevelopmentDataset, check_inputs
from query_cost_predictor.rq1_metrics import (
    CLASSIFICATION_METRICS,
    FLAG_SINGLE_FOLD_POSITIVES,
    FLAG_THIN_POSITIVE_SUPPORT,
    FLAG_TRIVIAL_SINGLE_CLASS,
    REASON_EMPTY,
    REASON_NO_NEGATIVES,
    REASON_NO_POSITIVES,
    REASON_NO_PREDICTED_POSITIVES,
    REASON_OUTPUT_TYPE,
    MetricValue,
    Rq1MetricError,
    average_precision_value,
    check_metric_spec,
    classification_flags,
    classification_metric_values,
    positive_support,
    predicted_positive,
    runtime_metric_values,
)
from query_cost_predictor.rq1_models import (
    CLASSIFICATION_MODEL_IDS,
    ClassificationModel,
    Rq1ModelOutputError,
    Rq1ModelSpecError,
    build_estimator,
    classification_model_specs,
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
    stage_classification_1s,
    stage_input_check,
    stage_regression,
    unimplemented_stages,
)
from rq1_fixtures import ALLOWLIST, build_release

pytestmark = [pytest.mark.filterwarnings("error::FutureWarning"), pytest.mark.filterwarnings("error::DeprecationWarning"),
              pytest.mark.filterwarnings("error::sklearn.exceptions.ConvergenceWarning")]

DEV_ROWS, DEV_GROUPS, DEV_POSITIVES = 40, 10, 4
SMALL_BOOTSTRAP = 40
PROBABILISTIC = [m for m in CLASSIFICATION_MODEL_IDS if m != "clf_raw_cost_rank"]


def small_bootstrap(document):
    document["uncertainty"]["n_clusters"] = DEV_GROUPS
    document["uncertainty"]["replicates"] = SMALL_BOOTSTRAP


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    release = build_release(tmp_path_factory.mktemp("release"))
    config = release.reconfigure(small_bootstrap)
    dataset = check_inputs(config, release.evidence_dir, repo_root=release.repo).dataset
    return release, config, dataset, load_feature_allowlist(config, release.repo)


@pytest.fixture(scope="module")
def evaluated(prepared):
    _, config, dataset, allowlist = prepared
    fits = []
    oof = evaluate_classification(dataset, config, allowlist,
                                  on_fit=lambda scheme, fold, model, train, test: fits.append((scheme, fold, model, train, test)))
    return oof, fits


@pytest.fixture(scope="module")
def table(prepared, evaluated):
    _, config, dataset, _ = prepared
    oof, _ = evaluated
    return classification_metric_table(oof, dataset, config, classification_model_specs(config))


def fitted(fits, model_id):
    return [entry for entry in fits if entry[2].model_id == model_id]


def heldout_keys(release) -> set[str]:
    with (release.evidence_dir / "derived" / "splits.csv").open(encoding="utf-8", newline="") as handle:
        return {row["modeling_key"] for row in csv.DictReader(handle) if row["split_role"] == "heldout_rq2"}


# ----------------------------------------------------------------------------- model factory
def test_the_six_classifiers_are_built_from_the_yaml_verbatim():
    config = load_rq1_config()
    check_metric_spec(config)
    specs = {s.model_id: s for s in classification_model_specs(config)}
    assert tuple(specs) == CLASSIFICATION_MODEL_IDS
    assert specs["clf_raw_cost_rank"].output_type == "rank_score"
    assert specs["clf_cost_logistic"].inputs == ("ln_cost",)
    assert specs["clf_scale_cost_logistic"].inputs == ("ln_cost", "ln_scale")
    assert specs["clf_logistic_all"].uses_allowlist and specs["clf_hgb_all"].uses_allowlist
    for model_id in ("clf_cost_logistic", "clf_scale_cost_logistic", "clf_logistic_all"):
        pipeline = build_estimator(specs[model_id])
        assert [name for name, _ in pipeline.steps] == ["standardscaler", "logisticregression"]
        assert all("penalty" not in params for _, params in specs[model_id].steps)
        logistic = pipeline.named_steps["logisticregression"].get_params()
        assert {k: logistic[k] for k in ("C", "l1_ratio", "solver", "max_iter", "tol", "fit_intercept", "class_weight")} \
            == {"C": 1.0, "l1_ratio": 0.0, "solver": "lbfgs", "max_iter": 5000, "tol": 1e-6, "fit_intercept": True,
                "class_weight": None}
        assert logistic["penalty"] == LogisticRegression().get_params()["penalty"]  # never passed: library default
    hgb = build_estimator(specs["clf_hgb_all"]).get_params()
    assert {k: hgb[k] for k in ("loss", "max_iter", "learning_rate", "max_leaf_nodes", "min_samples_leaf",
                                "l2_regularization", "early_stopping", "categorical_features", "random_state",
                                "class_weight")} == {
        "loss": "log_loss", "max_iter": 300, "learning_rate": 0.05, "max_leaf_nodes": 15, "min_samples_leaf": 5,
        "l2_regularization": 1.0, "early_stopping": False, "categorical_features": None, "random_state": 20260923,
        "class_weight": None}
    assert build_estimator(specs["clf_train_prevalence"]) is None and build_estimator(specs["clf_raw_cost_rank"]) is None


def test_penalty_is_refused_in_the_yaml_and_in_the_factory(tmp_path):
    release = build_release(tmp_path)

    def with_penalty(document):
        document["classification_models"][2]["steps"][1]["params"] = {
            **document["classification_models"][2]["steps"][1]["params"], "penalty": "l2"}
    with pytest.raises(Rq1ConfigError, match="penalty"):
        release.reconfigure(with_penalty)
    with pytest.raises(Rq1ModelSpecError, match="never passed"):
        rq1_models._check_params("LogisticRegression", {"penalty": "l2"}, "m", rq1_models.CLASSIFICATION_ESTIMATORS)


def test_logistic_fit_raises_no_warning():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(60, 3))
    y = (X[:, 0] + 0.3 * rng.normal(size=60) > 0).astype(int)
    spec = next(s for s in classification_model_specs(load_rq1_config()) if s.model_id == "clf_scale_cost_logistic")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build_estimator(spec).fit(X[:, :2], y)


def test_learned_classifiers_get_exactly_the_69_features(evaluated):
    _, fits = evaluated
    for entry in fitted(fits, "clf_logistic_all") + fitted(fits, "clf_hgb_all"):
        assert entry[2].estimator.n_features_in_ == len(ALLOWLIST) == 69
    for entry in fitted(fits, "clf_cost_logistic"):
        assert entry[2].estimator.n_features_in_ == 1
    for entry in fitted(fits, "clf_scale_cost_logistic"):
        assert entry[2].estimator.n_features_in_ == 2


# ----------------------------------------------------------------------------- fold loop and isolation
def test_every_classifier_sees_identical_rows_and_stored_folds(prepared, evaluated):
    _, _, dataset, _ = prepared
    oof, fits = evaluated
    by_fold = defaultdict(list)
    for scheme, fold, model, train, test in fits:
        by_fold[(scheme, fold)].append((model.model_id, train, test))
    assert list(by_fold) == [(s, f) for s in SCHEMES for f in range(5)]
    for (scheme, fold), entries in by_fold.items():
        assert [m for m, _, _ in entries] == list(CLASSIFICATION_MODEL_IDS)
        stored = dataset.folds[scheme].to_numpy()
        for _, train, test in entries:
            assert np.array_equal(test, np.flatnonzero(stored == fold))
            assert np.array_equal(train, np.flatnonzero(stored != fold))
    assert len(oof) == DEV_ROWS * len(SCHEMES) * len(CLASSIFICATION_MODEL_IDS)
    assert list(oof.columns) == list(OOF_CLASSIFICATION_COLUMNS)
    assert set(oof.groupby(["fold_scheme", "model_id", "modeling_key"]).size().tolist()) == {1}
    with pytest.raises(Rq1EvaluationError):
        check_oof_coverage(oof.iloc[1:], dataset.features.index, SCHEMES, CLASSIFICATION_MODEL_IDS)


def test_fit_spy_proves_test_and_heldout_rows_never_reach_fitting(prepared, monkeypatch):
    release, config, dataset, allowlist = prepared
    heldout = heldout_keys(release)
    log = []
    original = rq1_models.build_classification_model

    def spying_build(spec):
        model = original(spec)
        fit, predict = model.fit, model.predict

        def spy_fit(inputs, y):
            log.append(("fit", spec.model_id, list(inputs.index), y.to_numpy().copy()))
            return fit(inputs, y)

        def spy_predict(inputs):
            log.append(("predict", spec.model_id, list(inputs.index), None))
            return predict(inputs)
        model.fit, model.predict = spy_fit, spy_predict
        return model
    monkeypatch.setattr(rq1_models, "build_classification_model", spying_build)
    evaluate_classification(dataset, config, allowlist)
    fits = [e for e in log if e[0] == "fit"]
    predicts = [e for e in log if e[0] == "predict"]
    assert len(fits) == len(predicts) == len(SCHEMES) * 5 * len(CLASSIFICATION_MODEL_IDS)
    for (_, model_id, train_keys, y), (_, predict_id, test_keys, _) in zip(fits, predicts):
        assert model_id == predict_id
        assert not set(train_keys) & set(test_keys) and not (set(train_keys) | set(test_keys)) & heldout
        assert np.array_equal(y, dataset.targets.loc[train_keys, "hr_1s"].to_numpy())


def test_scaler_statistics_come_from_the_training_fold_only(prepared, evaluated):
    _, _, dataset, _ = prepared
    _, fits = evaluated
    for model_id, frame in (("clf_cost_logistic", dataset.baseline_inputs[["ln_cost"]]),
                            ("clf_logistic_all", dataset.features)):
        for _, _, model, train, _ in fitted(fits, model_id):
            scaler = model.estimator.named_steps["standardscaler"]
            assert np.allclose(scaler.mean_, frame.iloc[train].to_numpy().mean(axis=0), rtol=0, atol=1e-12)
            assert np.allclose(scaler.var_, frame.iloc[train].to_numpy().var(axis=0), rtol=0, atol=1e-12)


def test_cost_logistic_uses_natural_log_cost_from_estimates(prepared):
    _, config, dataset, allowlist = prepared
    spec = next(s for s in classification_model_specs(config) if s.model_id == "clf_cost_logistic")
    inputs = rq1_models.model_inputs(dataset, spec, allowlist)
    assert list(inputs.columns) == ["ln_cost"]
    assert np.array_equal(inputs["ln_cost"].to_numpy(), np.log(dataset.baseline_inputs["est_total_cost"].to_numpy()))


def test_prevalence_baseline_is_the_training_fold_positive_share(prepared, evaluated):
    _, _, dataset, _ = prepared
    oof, fits = evaluated
    y = dataset.targets["hr_1s"].to_numpy()
    for scheme, fold, model, train, _ in fitted(fits, "clf_train_prevalence"):
        assert model.constant_probability_ == float(np.mean(y[train] == 1))
        block = oof[(oof["fold_scheme"] == scheme) & (oof["model_id"] == "clf_train_prevalence") & (oof["fold"] == fold)]
        assert set(block["score"].tolist()) == {model.constant_probability_}


def test_raw_cost_scores_are_rank_scores_without_labels(prepared, evaluated):
    _, _, dataset, _ = prepared
    oof, _ = evaluated
    raw = oof[(oof["model_id"] == "clf_raw_cost_rank") & (oof["fold_scheme"] == "fold_group")].set_index("modeling_key")
    assert raw["score"].to_dict() == dataset.baseline_inputs["est_total_cost"].to_dict()
    assert set(raw["score_type"]) == {"rank_score"} and raw["pred_label"].isna().all()
    probabilistic = oof[oof["model_id"] != "clf_raw_cost_rank"]
    assert set(probabilistic["score_type"]) == {"probability"}
    assert ((probabilistic["score"] >= 0) & (probabilistic["score"] <= 1)).all()
    assert (probabilistic["pred_label"] == (probabilistic["score"] >= 0.5).astype(int)).all()


@pytest.mark.parametrize("bad", [np.nan, -0.01, 1.01])
def test_invalid_probabilities_fail_and_are_never_clipped(prepared, monkeypatch, bad):
    _, config, dataset, allowlist = prepared
    original = rq1_models.build_classification_model

    def broken(spec):
        model = original(spec)
        if spec.model_id == "clf_hgb_all":
            model.estimator.predict_proba = lambda X: np.column_stack([np.full(X.shape[0], 0.5), np.full(X.shape[0], bad)])
        return model
    monkeypatch.setattr(rq1_models, "build_classification_model", broken)
    with pytest.raises(Rq1ModelOutputError, match="clf_hgb_all"):
        evaluate_classification(dataset, config, allowlist)


def test_positive_class_column_and_shape_are_validated():
    spec = next(s for s in classification_model_specs(load_rq1_config()) if s.model_id == "clf_hgb_all")
    model = ClassificationModel(spec)
    model.estimator.classes_ = np.array([1, 0])
    with pytest.raises(Rq1ModelOutputError, match="classes"):
        model._positive_column(np.zeros((2, 2)), 2)
    model.estimator.classes_ = np.array([0, 1])
    with pytest.raises(Rq1ModelOutputError, match="shape"):
        model._positive_column(np.zeros((2, 3)), 2)
    assert model._positive_column(np.array([[0.7, 0.3], [0.2, 0.8]]), 2).tolist() == [0.3, 0.8]


def test_ten_second_classification_is_refused_before_any_fit(prepared, monkeypatch):
    _, config, dataset, allowlist = prepared
    built = []
    monkeypatch.setattr(rq1_models, "build_classification_model", lambda spec: built.append(spec))
    with pytest.raises(Rq1NotEvaluableError, match="NOT_YET_EVALUABLE"):
        evaluate_classification(dataset, config, allowlist, task="classification_10s")
    assert built == []


# ----------------------------------------------------------------------------- metric values and definedness
def test_probability_exactly_half_is_positive():
    assert predicted_positive([0.5, np.nextafter(0.5, 0.0), 1.0, 0.0]).tolist() == [True, False, True, False]
    values = classification_metric_values("probability", [1, 0], [0.5, np.nextafter(0.5, 0.0)])
    assert values["recall"].value == 1.0 and values["false_positive_rate"].value == 0.0
    assert values["warning_rate_per_100"].value == 50.0


def test_average_precision_is_the_step_sum_not_the_trapezoid():
    y = np.array([1, 0, 1, 1, 0, 0, 1, 0])
    s = np.array([0.9, 0.85, 0.7, 0.6, 0.55, 0.4, 0.3, 0.1])
    order = np.argsort(-s)
    tp = np.cumsum(y[order])
    precision, recall = tp / np.arange(1, y.size + 1), tp / y.sum()
    step_sum = float(np.sum(np.diff(np.concatenate([[0.0], recall])) * precision))
    ap = average_precision_value(y, s).value
    assert ap == pytest.approx(step_sum)
    curve_precision, curve_recall, _ = precision_recall_curve(y, s)
    assert abs(ap - auc(curve_recall, curve_precision)) > 1e-3  # trapezoidal PR-AUC is a different number


def test_pooled_counterexample_values():
    y, scores = [1, 1, 0, 1, 0, 0], [1 / 3] * 3 + [2 / 3] * 3
    values = classification_metric_values("probability", y, scores)
    assert values["average_precision"].value == pytest.approx(4 / 9)
    assert values["roc_auc"].value == pytest.approx(1 / 3)
    assert np.mean(y) == 0.5


def toy_dataset(y, folds, groups):
    keys = pd.Index([f"k{i}" for i in range(len(y))], name="modeling_key")
    metadata = pd.DataFrame({"semantic_group_id": groups, "structure_family": groups,
                             "query_instance_id": [f"qi{i}" for i in range(len(y))], "template_id": groups,
                             "component": "structural", "snapshot_name": "tpch_sf1",
                             "configuration_name": "C1_baseline"}, index=keys)[list(METADATA_COLUMNS)]
    median = np.where(np.asarray(y) == 1, 1500.0, 50.0)
    return DevelopmentDataset(
        features=pd.DataFrame(0.0, index=keys, columns=ALLOWLIST),
        baseline_inputs=pd.DataFrame({"est_total_cost": 10.0, "ln_cost": np.log(10.0), "snap_scale_factor": 1.0,
                                      "ln_scale": 0.0}, index=keys),
        targets=pd.DataFrame({"median_ms": median, "ln_median_ms": np.log(median), "hr_1s": np.asarray(y, dtype="int8")},
                             index=keys),
        metadata=metadata, folds=pd.DataFrame({s: folds for s in SCHEMES}, index=keys))


def test_pooled_prevalence_values_come_from_fold_specific_scores_not_a_per_fold_identity():
    config = load_rq1_config()
    y, folds = [1, 1, 0, 1, 0, 0], [0, 0, 0, 1, 1, 1]
    dataset = toy_dataset(y, folds, ["g0", "g0", "g1", "g2", "g3", "g3"])
    scores = [1 / 3] * 3 + [2 / 3] * 3  # each fold's training prevalence
    oof = pd.DataFrame([{"modeling_key": f"k{i}", "fold_scheme": scheme, "fold": folds[i],
                         "model_id": "clf_train_prevalence", "y_true": y[i], "score": scores[i],
                         "score_type": "probability", "pred_label": int(scores[i] >= 0.5)}
                        for scheme in SCHEMES for i in range(6)])
    spec = classification_model_specs(config)[0]
    rows = classification_metric_table(oof, dataset, config, [spec])
    get = {(r["fold_scheme"], r["scope"], r["fold"], r["stratum_column"], r["metric"]): r for r in rows}
    pooled = lambda metric: get[("fold_group", "pooled_out_of_fold", None, None, metric)]["value"]
    assert pooled("average_precision") == pytest.approx(4 / 9) and pooled("roc_auc") == pytest.approx(1 / 3)
    assert pooled("average_precision") != pytest.approx(0.5)  # not the overall prevalence
    for fold, prevalence in ((0, 2 / 3), (1, 1 / 3)):  # per fold, both classes present: AP = prevalence, ROC-AUC 0.5
        assert get[("fold_group", "per_fold", fold, None, "average_precision")]["value"] == pytest.approx(prevalence)
        assert get[("fold_group", "per_fold", fold, None, "roc_auc")]["value"] == pytest.approx(0.5)
    fold0 = {m: get[("fold_group", "per_fold", 0, None, m)] for m in ("recall", "precision")}
    assert fold0["recall"]["value"] == 0.0 and fold0["precision"]["null_reason"] == REASON_NO_PREDICTED_POSITIVES
    fold1_recall = get[("fold_group", "per_fold", 1, None, "recall")]
    assert fold1_recall["value"] == 1.0  # training prevalence 2/3 >= 0.5 predicts positives
    empty_fold = get[("fold_group", "per_fold", 3, None, "average_precision")]
    assert empty_fold["n_keys"] == 0 and empty_fold["null_reason"] == REASON_NO_POSITIVES


@pytest.mark.parametrize("share, expected_label", [(0.4, 0), (0.5, 1), (0.6, 1)])
def test_prevalence_baseline_predicts_positives_only_from_training_prevalence_at_least_half(share, expected_label):
    spec = classification_model_specs(load_rq1_config())[0]
    n_pos = int(round(share * 10))
    train = pd.DataFrame(index=[f"t{i}" for i in range(10)])
    model = ClassificationModel(spec).fit(train, pd.Series([1] * n_pos + [0] * (10 - n_pos), index=train.index))
    probabilities = model.predict(pd.DataFrame(index=["a", "b", "c"]))
    assert set(predicted_positive(probabilities).astype(int).tolist()) == {expected_label}
    values = classification_metric_values("probability", [1, 0, 0], probabilities)
    if expected_label == 0:
        assert values["recall"] == MetricValue(0.0) and values["precision"].null_reason == REASON_NO_PREDICTED_POSITIVES
    else:
        assert values["recall"] == MetricValue(1.0) and values["precision"].value == pytest.approx(1 / 3)


def test_all_positive_rows_give_ap_one_flagged_and_null_roc():
    values = classification_metric_values("probability", [1, 1, 1], [0.2, 0.6, 0.9])
    assert values["average_precision"] == MetricValue(1.0)
    assert values["roc_auc"] == MetricValue.null(REASON_NO_NEGATIVES)
    assert values["false_positive_rate"] == MetricValue.null(REASON_NO_NEGATIVES)
    assert values["recall"].value == pytest.approx(2 / 3)
    descriptor = {"n_keys": 3, "n_positive": 3, "n_positive_groups": 1, "n_contributing_positive_folds": 1}
    assert FLAG_TRIVIAL_SINGLE_CLASS in classification_flags("pooled_out_of_fold", descriptor, defined=True).split(";")


def test_no_positive_rows_keep_brier_and_false_positive_rate_defined():
    p = np.array([0.1, 0.7, 0.3])
    values = classification_metric_values("probability", [0, 0, 0], p)
    for name in ("average_precision", "roc_auc", "recall", "false_negative_rate"):
        assert values[name] == MetricValue.null(REASON_NO_POSITIVES)
    assert values["false_positive_rate"].value == pytest.approx(1 / 3)
    assert values["brier"].value == pytest.approx(np.mean(p ** 2))
    assert values["precision"].value == 0.0  # one case predicted positive, and it is negative: a defined zero


def test_empty_rows_give_the_rule_specific_reasons():
    values = classification_metric_values("probability", [], [])
    assert values["average_precision"].null_reason == REASON_NO_POSITIVES
    assert values["roc_auc"].null_reason == REASON_NO_POSITIVES
    assert values["precision"].null_reason == REASON_NO_PREDICTED_POSITIVES
    assert values["false_positive_rate"].null_reason == REASON_NO_NEGATIVES
    assert values["brier"].null_reason == REASON_EMPTY and values["warning_rate_per_100"].null_reason == REASON_EMPTY


def test_brier_is_the_mean_squared_difference_and_zero_stays_zero():
    y, p = np.array([1, 0, 1, 0]), np.array([0.9, 0.2, 0.4, 0.0])
    assert classification_metric_values("probability", y, p)["brier"].value == pytest.approx(np.mean((p - y) ** 2))
    perfect = classification_metric_values("probability", [1, 0], [1.0, 0.0])
    assert perfect["brier"] == MetricValue(0.0) and perfect["false_positive_rate"] == MetricValue(0.0)
    assert perfect["false_negative_rate"] == MetricValue(0.0)


def test_rank_scores_have_ranking_metrics_only():
    values = classification_metric_values("rank_score", [1, 0, 1, 0], [40.0, 10.0, 30.0, 20.0])
    assert values["average_precision"].value == pytest.approx(1.0) and values["roc_auc"].value == pytest.approx(1.0)
    for name in ("recall", "false_negative_rate", "precision", "false_positive_rate", "warning_rate_per_100", "brier"):
        assert values[name] == MetricValue.null(REASON_OUTPUT_TYPE)
    assert classification_metric_values("rank_score", [1, 0], [5000.0, 2.0])["average_precision"].value == 1.0


def test_labels_must_be_zero_or_one():
    with pytest.raises(Rq1MetricError):
        classification_metric_values("probability", [1, 2], [0.1, 0.2])


@pytest.mark.parametrize("n_positive, n_groups, thin", [(29, 10, True), (30, 4, True), (30, 5, False), (0, 0, True)])
def test_thin_positive_support_boundaries(n_positive, n_groups, thin):
    descriptor = {"n_keys": 100, "n_positive": n_positive, "n_positive_groups": n_groups,
                  "n_contributing_positive_folds": 2}
    flags = (classification_flags("pooled_out_of_fold", descriptor, defined=True) or "").split(";")
    assert (FLAG_THIN_POSITIVE_SUPPORT in flags) is thin


def test_single_fold_positives_applies_to_pooled_rows_only():
    descriptor = {"n_keys": 100, "n_positive": 40, "n_positive_groups": 8, "n_contributing_positive_folds": 1}
    assert classification_flags("pooled_out_of_fold", descriptor, defined=True) == FLAG_SINGLE_FOLD_POSITIVES
    assert classification_flags("per_fold", descriptor, defined=True) is None
    assert classification_flags("pooled_out_of_fold", {**descriptor, "n_contributing_positive_folds": 2}, True) is None
    none = {"n_keys": 10, "n_positive": 0, "n_positive_groups": 0, "n_contributing_positive_folds": 0}
    assert FLAG_SINGLE_FOLD_POSITIVES not in classification_flags("pooled_out_of_fold", none, defined=True)


def test_flags_are_joined_in_declared_order():
    descriptor = {"n_keys": 5, "n_positive": 5, "n_positive_groups": 1, "n_contributing_positive_folds": 1}
    assert classification_flags("pooled_out_of_fold", descriptor, defined=True) == \
        "trivial_single_class;thin_positive_support;single_fold_positives"
    assert classification_flags("pooled_out_of_fold", descriptor, defined=False) == \
        "thin_positive_support;single_fold_positives"


def test_positive_support_counts_groups_and_contributing_folds():
    support = positive_support([1, 0, 1, 1, 0], ["a", "a", "b", "b", "c"], [0, 0, 2, 2, 3])
    assert support == {"n_positive": 3, "n_positive_groups": 2, "n_contributing_positive_folds": 2}


# ----------------------------------------------------------------------------- metric table on the fixture
def test_classification_table_covers_every_scheme_model_slice_and_metric(table):
    assert len(table) == len(SCHEMES) * len(CLASSIFICATION_MODEL_IDS) * 18 * len(CLASSIFICATION_METRICS)
    for row in table:
        assert (row["fold"] is None) == (row["scope"] == "pooled_out_of_fold")
        assert row["n_positive"] is not None and row["n_positive_groups"] is not None
        assert row["n_contributing_positive_folds"] is not None
        assert (row["value"] is None) != (row["null_reason"] is None)
        assert row["ci_lower"] is None  # no bootstrap passed here
        if row["scope"] == "per_fold":
            assert row["n_contributing_positive_folds"] in (0, 1)
            assert FLAG_SINGLE_FOLD_POSITIVES not in (row["flag"] or "")
        if row["model_id"] == "clf_raw_cost_rank" and row["metric"] not in ("average_precision", "roc_auc"):
            assert row["null_reason"] == REASON_OUTPUT_TYPE


def test_counts_come_from_the_evaluated_rows(table, evaluated, prepared):
    _, _, dataset, _ = prepared
    oof, _ = evaluated
    pooled = next(r for r in table if (r["fold_scheme"], r["model_id"], r["scope"], r["stratum_column"], r["metric"])
                  == ("fold_group", "clf_hgb_all", "pooled_out_of_fold", None, "average_precision"))
    assert (pooled["n_keys"], pooled["n_positive"], pooled["n_positive_groups"]) == (DEV_ROWS, DEV_POSITIVES, 4)
    block = oof[(oof["fold_scheme"] == "fold_group") & (oof["model_id"] == "clf_hgb_all")]
    assert pooled["n_contributing_positive_folds"] == block.loc[block["y_true"] == 1, "fold"].nunique()
    assert pooled["value"] == average_precision_value(block["y_true"], block["score"]).value


def test_runtime_bin_strata_show_single_class_handling(table):
    rows = {(r["stratum_value"], r["metric"]): r for r in table
            if (r["fold_scheme"], r["model_id"], r["stratum_column"]) == ("fold_group", "clf_hgb_all", "runtime_bin_ms")}
    high = rows[("1000-10000", "average_precision")]  # every fixture case >= 1,000 ms is positive
    assert high["value"] == 1.0 and FLAG_TRIVIAL_SINGLE_CLASS in high["flag"]
    assert rows[("1000-10000", "roc_auc")]["null_reason"] == REASON_NO_NEGATIVES
    low = rows[("<10", "average_precision")]
    assert low["value"] is None and low["null_reason"] == REASON_NO_POSITIVES
    assert rows[("<10", "brier")]["value"] is not None and FLAG_TRIVIAL_SINGLE_CLASS in rows[("<10", "brier")]["flag"]
    assert rows[("<10", "false_positive_rate")]["value"] is not None
    empty = rows[("100-1000", "brier")]
    assert empty["n_keys"] == 0 and empty["null_reason"] == REASON_EMPTY
    assert sum(rows[(v, "brier")]["n_keys"] for v in ("<10", "10-100", "100-1000", "1000-10000")) == DEV_ROWS


def test_pooled_strata_with_positives_from_one_fold_are_flagged(table):
    rewrite = next(r for r in table if (r["fold_scheme"], r["model_id"], r["stratum_value"], r["metric"])
                   == ("fold_group", "clf_hgb_all", "rewrite", "average_precision"))
    assert rewrite["n_contributing_positive_folds"] == 1 and FLAG_SINGLE_FOLD_POSITIVES in rewrite["flag"]


# ----------------------------------------------------------------------------- bootstrap
def test_required_tasks_map_headline_tables_to_task_ids(tmp_path):
    config = load_rq1_config()
    assert list(config.uncertainty["applies_to"]) == ["headline_regression", "headline_classification"]
    assert required_bootstrap_tasks(config) == ("regression", "classification_1s")
    context = create_run_context(tmp_path / "results", "run_tasks", "synthetic", config)
    with pytest.raises(Rq1BootstrapError, match="not contribution task ids"):
        write_bootstrap_outputs(context, ["headline_regression", "headline_classification"])


def test_no_positive_draws_are_invalid_and_all_positive_draws_stay_valid():
    groups = ["pos", "pos", "neg", "neg"]
    y = np.array([1, 1, 0, 0])
    scores = np.array([0.9, 0.4, 0.6, 0.1])
    plan = BootstrapPlan(groups=("neg", "pos"), indices=np.array([[0, 0], [1, 1], [0, 1]]), seed=0, level=0.95,
                         cluster_column="semantic_group_id")
    statistics = {name: (lambda rows, s=s: {"average_precision": average_precision_value(y[rows], s[rows])})
                  for name, s in (("learned", scores), ("baseline", scores[::-1].copy()))}
    contribution = paired_bootstrap("classification_1s", plan, groups, statistics, ["average_precision"],
                                    [("learned", "baseline")], "learned minus baseline")
    values = {(r["statistic_kind"], r["model_id"], r["replicate"]): r for r in contribution.replicate_rows}
    assert values[("model", "learned", 0)]["invalid_reason"] == REASON_NO_POSITIVES  # only the negative group drawn
    assert values[("model", "learned", 1)]["value"] == 1.0  # only positives: AP = 1, valid
    assert values[("difference", "learned", 0)]["value"] is None
    summary = contribution.summary["models"][0]
    assert summary["n_invalid"] == 1 and summary["invalid_reasons"] == {REASON_NO_POSITIVES: 1}


# ----------------------------------------------------------------------------- the classification stage in synthetic runs
def final_stage(context):
    """Synthetic stand-in for the C2 final assembly (diagnostics and handoff are not implemented yet)."""
    write_bootstrap_outputs(context, required_bootstrap_tasks(context.config))
    write_report(context, ["regression", "classification_1s"])


def synthetic_run(root: Path, name: str, config, release, stages):
    context = create_run_context(root / "results", name, "synthetic", config, evidence_dir=release.evidence_dir,
                                 repo_root=release.repo)
    return context, [("input_check", stage_input_check), *stages]


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory, prepared):
    release, config, _, _ = prepared
    root = tmp_path_factory.mktemp("runs")
    contexts = []
    for name in ("run_1", "run_2"):
        context, stages = synthetic_run(root, name, config, release, [
            ("regression", stage_regression), ("classification_1s", stage_classification_1s), ("final", final_stage)])
        contexts.append(execute_run(context, stages))
    return contexts


def test_classification_stage_completes_a_synthetic_run(two_runs):
    run_dir = two_runs[0].run_dir
    assert read_status(run_dir)["status"] == COMPLETE
    files = set(read_complete_run(run_dir)["files"])
    assert {OOF_CLASSIFICATION_FILE, METRICS_CLASSIFICATION_CSV, METRICS_CLASSIFICATION_JSON, CALIBRATION_BINS_FILE,
            OOF_REGRESSION_FILE, "fold_membership.csv", "bootstrap_summary.json", REPORT_FILE} <= files
    record = json.loads((run_dir / RUN_RECORD_FILE).read_text(encoding="utf-8"))
    during = record["classification_1s"]["threadpool_during_fit"]
    assert during["requested_limit"] == 1 and all(e["num_threads"] == 1 for e in during["effective"])
    assert record["classification_1s"]["decision_threshold"] == 0.5


def test_exports_are_nan_free_and_headline_ap_rows_carry_intervals(two_runs):
    run_dir = two_runs[0].run_dir

    def refuse(token):
        raise AssertionError(f"{token} in exported JSON")
    rows = json.loads((run_dir / METRICS_CLASSIFICATION_JSON).read_text(encoding="utf-8"), parse_constant=refuse)["rows"]
    for name in (METRICS_CLASSIFICATION_CSV, OOF_CLASSIFICATION_FILE, CALIBRATION_BINS_FILE):
        text = (run_dir / name).read_text(encoding="utf-8").lower()
        assert "nan" not in text and "inf" not in text
    with_ci = [r for r in rows if r["ci_lower"] is not None]
    assert {r["model_id"] for r in with_ci} == set(CLASSIFICATION_MODEL_IDS)
    assert all((r["fold_scheme"], r["scope"], r["stratum_column"], r["metric"])
               == ("fold_group", "pooled_out_of_fold", None, "average_precision") for r in with_ci)
    oof = list(csv.DictReader(io.StringIO((run_dir / OOF_CLASSIFICATION_FILE).read_text(encoding="utf-8"))))
    assert len(oof) == DEV_ROWS * 18
    assert {r["pred_label"] for r in oof if r["model_id"] == "clf_raw_cost_rank"} == {""}


def test_regression_and_classification_share_one_plan_and_identical_draws(two_runs):
    context = two_runs[0]
    run_dir = context.run_dir
    saved = np.load(run_dir / "bootstrap_indices.npz", allow_pickle=False)
    summary = json.loads((run_dir / "bootstrap_summary.json").read_text(encoding="utf-8"))
    assert set(summary["tasks"]) == {"regression", "classification_1s"}
    replicates = list(csv.DictReader(io.StringIO((run_dir / "bootstrap_replicates.csv").read_text(encoding="utf-8"))))
    membership = pd.read_csv(run_dir / "fold_membership.csv", dtype=str).set_index("modeling_key")
    positions = [np.flatnonzero(membership["semantic_group_id"].to_numpy() == g) for g in saved["groups"]]
    oof_c = pd.read_csv(run_dir / OOF_CLASSIFICATION_FILE, dtype={"modeling_key": str})
    oof_r = pd.read_csv(run_dir / OOF_REGRESSION_FILE, dtype={"modeling_key": str})
    for replicate in (0, 7, SMALL_BOOTSTRAP - 1):
        rows = np.concatenate([positions[g] for g in saved["indices"][replicate]])
        clf = oof_c[(oof_c["fold_scheme"] == "fold_group") & (oof_c["model_id"] == "clf_hgb_all")].set_index("modeling_key")
        clf = clf.loc[membership.index]
        expected_ap = average_precision_value(clf["y_true"].to_numpy()[rows], clf["score"].to_numpy()[rows])
        reg = oof_r[(oof_r["fold_scheme"] == "fold_group") & (oof_r["model_id"] == "reg_hgb_all")].set_index("modeling_key")
        reg = reg.loc[membership.index]
        expected_mae = runtime_metric_values(reg["y_true_ms"].to_numpy()[rows], reg["pred_ms"].to_numpy()[rows])["log_mae"]
        saved_rows = {(r["task"], r["model_id"], r["metric"]): r for r in replicates
                      if r["statistic_kind"] == "model" and int(r["replicate"]) == replicate}
        ap_row = saved_rows[("classification_1s", "clf_hgb_all", "average_precision")]
        if expected_ap.value is None:
            assert ap_row["invalid_reason"] == REASON_NO_POSITIVES
        else:
            assert float(ap_row["value"]) == pytest.approx(expected_ap.value, abs=1e-15)
        assert float(saved_rows[("regression", "reg_hgb_all", "log_mae")]["value"]) == pytest.approx(expected_mae.value,
                                                                                                       abs=1e-15)


def test_two_runs_give_byte_identical_classification_exports(two_runs):
    first, second = (context.run_dir for context in two_runs)
    for name in (OOF_CLASSIFICATION_FILE, METRICS_CLASSIFICATION_CSV, METRICS_CLASSIFICATION_JSON, CALIBRATION_BINS_FILE,
                 OOF_REGRESSION_FILE, "bootstrap_indices.npz", "bootstrap_replicates.csv", "bootstrap_summary.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_report_has_both_sections_labels_and_the_limitations_verbatim(two_runs, prepared):
    _, config, _, _ = prepared
    data = (two_runs[0].run_dir / REPORT_FILE).read_bytes()
    assert load_rq1_config().limitations_text.encode("utf-8") in data and config.limitations_text.encode("utf-8") in data
    text = data.decode("utf-8")
    assert text.index("## Runtime regression") < text.index("## High-runtime classification at 1 s")
    for label in ("### Primary: one predeclared operating point (probability >= 0.5; separate table)",
                  "not a recommended production threshold", "### Robustness check: `fold_family`",
                  "### Seen-template diagnostic: `fold_seen_template`", "NOT_YET_EVALUABLE",
                  "never trapezoidal PR-AUC", "learned minus baseline (positive is higher average precision)"):
        assert label in text
    assert "PARTIAL REPORT" in text and "diagnostics, handoff" in text


def test_convergence_warning_in_a_classifier_fit_marks_the_run_failed(tmp_path, prepared, monkeypatch):
    from sklearn.exceptions import ConvergenceWarning

    release, config, _, _ = prepared
    original = rq1_models.build_classification_model

    def warning_build(spec):
        model = original(spec)
        if spec.model_id == "clf_logistic_all":
            estimator_fit = model.estimator.fit

            def fit(X, y):
                warnings.warn("synthetic lbfgs non-convergence", ConvergenceWarning)
                return estimator_fit(X, y)
            model.estimator.fit = fit
        return model
    monkeypatch.setattr(rq1_models, "build_classification_model", warning_build)
    context, stages = synthetic_run(tmp_path, "run_cw", config, release,
                                    [("classification_1s", stage_classification_1s), ("final", final_stage)])
    with pytest.raises(ConvergenceWarning):
        execute_run(context, stages)
    status = read_status(context.run_dir)
    assert status["status"] == FAILED and status["failed_stage"] == "classification_1s"
    assert not (context.run_dir / OOF_CLASSIFICATION_FILE).exists() and not (context.run_dir / REPORT_FILE).exists()


def test_classification_stage_needs_the_checked_dataset(tmp_path, prepared):
    release, config, _, _ = prepared
    context = create_run_context(tmp_path / "results", "run_nodata", "synthetic", config, repo_root=release.repo)
    with pytest.raises(Rq1EvaluationError, match="input_check"):
        execute_run(context, [("classification_1s", stage_classification_1s)])
    assert read_status(context.run_dir)["status"] == FAILED


def test_production_pipeline_still_refuses_with_diagnostics_and_handoff_missing():
    config = load_rq1_config()
    assert config.uncertainty["replicates"] == 2000 and config.uncertainty["seed"] == 20261021
    assert unimplemented_stages(config.stages) == ["diagnostics", "handoff"]
    with pytest.raises(RunRefusedError, match="diagnostics"):
        production_stages(config.stages)
