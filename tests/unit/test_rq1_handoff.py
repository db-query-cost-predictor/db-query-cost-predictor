"""RQ1 Slice C2: hand-off refits on all development rows, never scored (SYNTHETIC data only)."""

from __future__ import annotations

import csv
import io
import json
import warnings

import joblib
import numpy as np
import pytest

from query_cost_predictor import rq1_models
from query_cost_predictor.hashing import sha256_bytes, sha256_json
from query_cost_predictor.rq1_config import load_rq1_config
from query_cost_predictor.rq1_evaluate import load_feature_allowlist
from query_cost_predictor.rq1_handoff import (
    HANDOFF_LABEL,
    HANDOFF_MANIFEST,
    HANDOFF_MODEL_IDS,
    Rq1HandoffError,
    fit_handoff_models,
    handoff_model_ids,
    handoff_output_paths,
)
from query_cost_predictor.rq1_inputs import check_inputs
from query_cost_predictor.rq1_run import FAILED, execute_run, read_complete_run, read_status
from rq1_fixtures import ALLOWLIST, build_release, production_context, small_bootstrap

pytestmark = [pytest.mark.filterwarnings("error::FutureWarning"), pytest.mark.filterwarnings("error::DeprecationWarning"),
              pytest.mark.filterwarnings("error::sklearn.exceptions.ConvergenceWarning")]


@pytest.fixture(scope="module")
def release(tmp_path_factory):
    return build_release(tmp_path_factory.mktemp("release"))


@pytest.fixture(scope="module")
def config(release):
    return release.reconfigure(small_bootstrap)


@pytest.fixture(scope="module")
def completed(tmp_path_factory, release, config):
    context, stages = production_context(tmp_path_factory.mktemp("runs"), release, config)
    return execute_run(context, stages)


def heldout_keys(release) -> set[str]:
    with (release.evidence_dir / "derived" / "splits.csv").open(encoding="utf-8", newline="") as handle:
        return {row["modeling_key"] for row in csv.DictReader(handle) if row["split_role"] == "heldout_rq2"}


def test_handoff_models_are_the_eight_declared_ones(tmp_path):
    config = load_rq1_config()
    assert handoff_model_ids(config) == HANDOFF_MODEL_IDS and len(HANDOFF_MODEL_IDS) == 8
    assert handoff_output_paths(config)[-1] == HANDOFF_MANIFEST
    changed = build_release(tmp_path).reconfigure(lambda d: d["outputs"]["handoff"]["models"].pop())
    with pytest.raises(Rq1HandoffError):
        handoff_model_ids(changed)


def test_refits_use_exactly_the_development_rows_and_never_predict(release, config, monkeypatch):
    dataset = check_inputs(config, release.evidence_dir, repo_root=release.repo).dataset
    allowlist = load_feature_allowlist(config, release.repo)
    fits, calls = [], []
    builders = {"build_regression_model": rq1_models.build_regression_model,
                "build_classification_model": rq1_models.build_classification_model}

    def spy(name):
        def build(spec):
            model = builders[name](spec)
            fit = model.fit
            model.fit = lambda inputs, target: (fits.append((spec.model_id, list(inputs.index), list(inputs.columns))),
                                                fit(inputs, target))[1]
            model.predict = lambda *a, **k: calls.append(("predict", spec.model_id))
            if model.estimator is not None:
                for method in ("predict", "predict_proba", "decision_function", "score"):
                    if hasattr(model.estimator, method):
                        setattr(model.estimator, method, lambda *a, _m=method, **k: calls.append((_m, spec.model_id)))
            return model
        return build
    for name in builders:
        monkeypatch.setattr(rq1_models, name, spy(name))
    fitted = fit_handoff_models(dataset, config, allowlist)
    assert [spec.model_id for spec, _ in fitted] == list(HANDOFF_MODEL_IDS)
    development = list(dataset.features.index)
    heldout = heldout_keys(release)
    assert len(fits) == 8 and heldout
    for model_id, keys, columns in fits:
        assert keys == development and not set(keys) & heldout
        if model_id.endswith("_all"):
            assert columns == ALLOWLIST
        elif "scale" in model_id:
            assert columns == ["ln_cost", "ln_scale"]
        else:
            assert columns == ["ln_cost"]
    assert calls == []  # nothing was predicted or scored


def test_eight_serialized_models_with_hashes_and_contracts(completed):
    manifest_files = read_complete_run(completed.run_dir)["files"]
    manifest = json.loads((completed.run_dir / HANDOFF_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["label"] == HANDOFF_LABEL and manifest["scored"] is False and manifest["status"] == "NOT_SCORED"
    assert manifest["predictions_written"] is False and manifest["heldout_rq2_predictions"] == "not_in_v1"
    assert [m["model_id"] for m in manifest["models"]] == list(HANDOFF_MODEL_IDS)
    for entry in manifest["models"]:
        data = (completed.run_dir / entry["file"]).read_bytes()
        assert sha256_bytes(data) == entry["sha256"] == manifest_files[entry["file"]]
        estimator = joblib.load(io.BytesIO(data))
        assert type(estimator).__name__ == entry["estimator_class"] and entry["fit_rows"] == 40
        columns = entry["inputs"]["ordered_columns"]
        assert estimator.n_features_in_ == len(columns)
        if entry["model_id"].endswith("_all"):
            assert columns == ALLOWLIST
        else:
            assert columns[0] == "ln_cost" and "not log1p" in entry["inputs"]["definitions"]["ln_cost"]["transformation"]
        if entry["task"] == "classification_1s":
            assert list(estimator.classes_) == [0, 1] and entry["method"] == "predict_proba"
            assert entry["decision_threshold"]["probability"] == 0.5 and entry["label_mapping"] == {"positive": 1,
                                                                                                    "negative": 0}
        else:
            assert entry["output_units"] == "ln(ms)" and "no smearing" in entry["runtime_ms"]
    learned = joblib.load(completed.run_dir / "handoff/clf_logistic_all.joblib")
    assert [name for name, _ in learned.steps] == ["standardscaler", "logisticregression"]


def test_manifest_records_rows_constants_and_provenance(completed):
    manifest = json.loads((completed.run_dir / HANDOFF_MANIFEST).read_text(encoding="utf-8"))
    dataset = completed.artifacts["dataset"]
    keys = sorted(dataset.features.index)
    assert manifest["fit_rows"]["count"] == 40 and manifest["fit_rows"]["modeling_keys_sha256"] == sha256_json(keys)
    constants = manifest["constant_baselines"]
    assert constants["reg_train_median"]["median_ms"] == float(np.median(dataset.targets["median_ms"]))
    assert constants["clf_train_prevalence"]["positive_prevalence"] == pytest.approx(4 / 40)
    assert manifest["source_revision"] == "f" * 40 and manifest["config_sha256"] == completed.config.sha256
    assert manifest["input_record_sha256"] == completed.provenance["input_record_sha256"]
    assert {i["item"] for i in manifest["input_identities"]} >= {"input_record", "release_identity.derived_release_id"}
    assert manifest["full_environment"] and any(e.lower().startswith("scikit-learn==") for e in manifest["full_environment"])
    assert manifest["classification_label_mapping"] == {"positive": 1, "negative": 0}
    report = (completed.run_dir / "report.md").read_text(encoding="utf-8")
    assert "## Hand-off refits (not evaluated)" in report and "never scored" in report


def test_convergence_warning_during_a_handoff_refit_fails_the_run(tmp_path, release, config, monkeypatch):
    from sklearn.exceptions import ConvergenceWarning

    original = rq1_models.build_classification_model

    def warning_on_full_refit(spec):
        model = original(spec)
        if spec.model_id == "clf_logistic_all":
            fit = model.estimator.fit

            def warn_fit(X, y):
                if X.shape[0] == 40:  # only the all-development hand-off refit, not the OOF folds
                    warnings.warn("synthetic non-convergence", ConvergenceWarning)
                return fit(X, y)
            model.estimator.fit = warn_fit
        return model
    monkeypatch.setattr(rq1_models, "build_classification_model", warning_on_full_refit)
    context, stages = production_context(tmp_path, release, config, run_id="run_cw")
    with pytest.raises(ConvergenceWarning):
        execute_run(context, stages)
    status = read_status(context.run_dir)
    assert status["status"] == FAILED and status["failed_stage"] == "handoff"
    assert not (context.run_dir / HANDOFF_MANIFEST).exists() and not (context.run_dir / "report.md").exists()
