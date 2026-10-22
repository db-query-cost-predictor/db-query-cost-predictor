"""RQ1 hand-off refits: the eight fitted estimators refitted on all development cases, never scored.

These refits are **separate from the out-of-fold evaluation**: every reported
metric comes from the stored-fold OOF predictions, never from these models.
Each declared estimator is built fresh by the same factory and preprocessing
contract as the evaluation (``rq1_models``), fitted once on every development
row (``split_role == development``; no ``heldout_rq2`` row) under the
one-thread limit, where a ``ConvergenceWarning`` fails the run, and serialized
with ``joblib`` (pipelines include their fitted ``StandardScaler``). Nothing
is predicted or scored here.

``handoff/handoff_manifest.json`` carries the label
``REFIT_ON_ALL_DEVELOPMENT_NO_EVALUATION``, the files and their SHA-256, the
fit-row count and identity, the source revision, config hash, input
identities and full environment, each model's ordered inputs, transformations,
target and output units, the 1 s label mapping and the 0.5 operating-point
context, and the all-development median runtime and positive prevalence as
stored constants for the two constant baselines.
"""

from __future__ import annotations

import io
from typing import Any, Sequence

import numpy as np

from query_cost_predictor import rq1_models
from query_cost_predictor.hashing import sha256_bytes, sha256_json
from query_cost_predictor.rq1_config import DECISION_THRESHOLD, Rq1Config
from query_cost_predictor.rq1_inputs import DevelopmentDataset
from query_cost_predictor.rq1_metrics import json_bytes
from query_cost_predictor.rq1_models import TASK_CLASSIFICATION, TASK_REGRESSION, FoldModel, ModelSpec

HANDOFF_DIR = "handoff"
HANDOFF_MANIFEST = "handoff/handoff_manifest.json"
HANDOFF_LABEL = "REFIT_ON_ALL_DEVELOPMENT_NO_EVALUATION"
HANDOFF_MODEL_IDS = ("reg_cost_ols", "reg_scale_cost_ols", "reg_ridge_all", "reg_hgb_all", "clf_cost_logistic",
                     "clf_scale_cost_logistic", "clf_logistic_all", "clf_hgb_all")
INPUT_DEFINITIONS = {
    "ln_cost": {"source": "derived/estimates.csv:est_total_cost",
                "transformation": "natural log; requires est_total_cost > 0 (not log1p, not the est_total_cost_log "
                                  "feature)"},
    "ln_scale": {"source": "derived/features.csv:snap_scale_factor",
                 "transformation": "natural log; requires snap_scale_factor > 0"},
}


class Rq1HandoffError(ValueError):
    """The hand-off specification or a refit violates the approved design."""


def handoff_model_ids(config: Rq1Config) -> tuple[str, ...]:
    spec = config.raw["outputs"]["handoff"]
    ids = tuple(str(m) for m in spec["models"])
    if ids != HANDOFF_MODEL_IDS:
        raise Rq1HandoffError(f"outputs.handoff.models must be {HANDOFF_MODEL_IDS}, got {ids}")
    if (spec["label"], spec["format"], spec["heldout_rq2_predictions"], spec["manifest"]) != \
            (HANDOFF_LABEL, "joblib", "not_in_v1", "handoff_manifest.json"):
        raise Rq1HandoffError("outputs.handoff label, format, manifest or heldout rule differ from the implementation")
    return ids


def model_file(model_id: str) -> str:
    return f"{HANDOFF_DIR}/{model_id}.joblib"


def handoff_output_paths(config: Rq1Config) -> list[str]:
    return [model_file(m) for m in handoff_model_ids(config)] + [HANDOFF_MANIFEST]


def _specs(config: Rq1Config) -> dict[str, ModelSpec]:
    specs = {s.model_id: s for s in rq1_models.regression_model_specs(config)}
    specs.update({s.model_id: s for s in rq1_models.classification_model_specs(config)})
    return specs


def fit_handoff_models(dataset: DevelopmentDataset, config: Rq1Config,
                       allowlist: Sequence[str]) -> list[tuple[ModelSpec, FoldModel]]:
    """Fresh instances of the eight estimators, each fitted once on every development row; never used to predict."""
    from query_cost_predictor.rq1_evaluate import _check_aligned

    keys = _check_aligned(dataset)
    specs = _specs(config)
    fitted = []
    for model_id in handoff_model_ids(config):
        spec = specs[model_id]
        if spec.estimator is None and not spec.steps and spec.kind != "ols":
            raise Rq1HandoffError(f"{model_id} has no fitted estimator to hand off")
        inputs = rq1_models.model_inputs(dataset, spec, allowlist)
        if spec.task == TASK_REGRESSION:
            model = rq1_models.build_regression_model(spec)
            model.fit(inputs, dataset.targets["median_ms"])
        else:
            model = rq1_models.build_classification_model(spec)
            model.fit(inputs, dataset.targets["hr_1s"].astype("int64"))
        if model.fit_keys_ != tuple(str(k) for k in keys):
            raise Rq1HandoffError(f"{model_id} was not fitted on exactly the development rows")
        fitted.append((spec, model))
    return fitted


def serialize(estimator: Any) -> bytes:
    import joblib

    buffer = io.BytesIO()
    joblib.dump(estimator, buffer)
    return buffer.getvalue()


def input_contract(spec: ModelSpec, allowlist: Sequence[str]) -> dict[str, Any]:
    if spec.uses_allowlist:
        return {"ordered_columns": list(allowlist), "source": "derived/features.csv (the 69-column allowlist, this "
                "exact order)", "transformation": "none outside the estimator; a declared StandardScaler is inside "
                "the serialized pipeline"}
    return {"ordered_columns": list(spec.inputs),
            "definitions": {name: INPUT_DEFINITIONS[name] for name in spec.inputs},
            "transformation": "compute each column as defined, in this order; a declared StandardScaler is inside "
                              "the serialized pipeline"}


def target_and_output(spec: ModelSpec, config: Rq1Config) -> dict[str, Any]:
    if spec.task == TASK_REGRESSION:
        return {"target": "ln(labels.csv:median_ms), median of three measured EXPLAIN ANALYZE (TIMING OFF) runs",
                "method": "predict", "output": "predicted ln runtime", "output_units": "ln(ms)",
                "runtime_ms": "exp(predicted ln runtime) with no smearing correction (a typical, median-like runtime)"}
    return {"target": "hr_1s_class mapped positive=1, negative=0 (frozen 1,000 ms label boundary)",
            "label_mapping": config.class_mapping, "method": "predict_proba",
            "output": "probability of the positive class (the column where classes_ == 1)",
            "output_units": "probability in [0, 1]",
            "decision_threshold": {"probability": DECISION_THRESHOLD, "rule": "positive when probability >= 0.5",
                                   "context": config.raw["fixed"]["operating_point"], "tuned": False}}


def handoff_manifest(context: Any, dataset: DevelopmentDataset, fitted: list[tuple[ModelSpec, FoldModel]],
                     files: dict[str, tuple[str, int]], allowlist: Sequence[str]) -> dict[str, Any]:
    from query_cost_predictor.rq1_provenance import dependency_versions, full_environment

    config = context.config
    keys = sorted(str(k) for k in dataset.features.index)
    provenance = context.provenance
    median_ms = dataset.targets["median_ms"].to_numpy(dtype="float64")
    positives = dataset.targets["hr_1s"].to_numpy(dtype="int64")
    versions = provenance.get("dependency_versions") or dependency_versions()
    models = []
    for spec, model in fitted:
        name, size = files[spec.model_id]
        models.append({
            "model_id": spec.model_id, "task": spec.task, "role": spec.role, "kind": spec.kind,
            "file": name, "sha256": context.payloads[name], "bytes": size,
            "estimator_class": type(model.estimator).__name__,
            "declared_steps": [{"estimator": e, "params": p} for e, p in spec.steps] or None,
            "declared_params": spec.params or None,
            "inputs": input_contract(spec, allowlist), **target_and_output(spec, config),
            "fit_rows": len(model.fit_keys_ or ()),
        })
    return {
        "schema": "qcp.rq1_handoff_manifest", "schema_version": 1, "label": HANDOFF_LABEL,
        "status": "NOT_SCORED", "scored": False, "predictions_written": False,
        "heldout_rq2_predictions": config.raw["outputs"]["handoff"]["heldout_rq2_predictions"],
        "distinction": "deployment hand-off refits on all development cases; every RQ1 result comes from the "
                       "stored-fold out-of-fold evaluation (oof_*.csv, metrics_*.json), never from these models",
        "spec_id": config.spec_id, "run_id": context.run_id, "config_sha256": config.sha256,
        "input_record_sha256": provenance.get("input_record_sha256", config.input_record_sha256),
        "input_identities": provenance.get("input_identities"),
        "source_revision": provenance.get("source_revision"),
        "source_tracked_dirty": provenance.get("source_tracked_dirty"),
        "fit_rows": {"selection": "split_role == development (heldout_rq2 excluded)", "count": len(keys),
                     "modeling_keys_sha256": sha256_json(keys),
                     "identity": "SHA-256 of the canonical JSON list of the sorted development modeling_key values"},
        "models": models,
        "constant_baselines": {
            "reg_train_median": {"median_ms": float(np.median(median_ms)), "ln_median_ms": float(np.log(np.median(median_ms))),
                                 "definition": "median of median_ms over all development cases; predict ln of it for "
                                               "every case"},
            "clf_train_prevalence": {"positive_prevalence": float(positives.mean()), "n_positive": int(positives.sum()),
                                     "n_cases": int(positives.size),
                                     "definition": "positive share of hr_1s over all development cases; the probability "
                                                   "for every case"},
        },
        "classification_label_mapping": config.class_mapping,
        "decision_threshold": {"probability": DECISION_THRESHOLD, "context": config.raw["fixed"]["operating_point"]},
        "serialization": {"format": "joblib", "joblib": versions.get("joblib"), "scikit_learn": versions.get("scikit-learn"),
                          "object": "the fitted scikit-learn estimator or pipeline",
                          "loading": "joblib.load in an environment with these versions; never load an untrusted file"},
        "dependency_versions": versions,
        "full_environment": provenance.get("full_environment") or full_environment(),
    }


def stage_handoff(context: Any) -> None:
    """Refit, serialize and describe the eight hand-off models (no scoring, no predictions)."""
    from query_cost_predictor import rq1_report
    from query_cost_predictor.rq1_evaluate import _stage_inputs, _under_thread_limit
    from query_cost_predictor.rq1_run import THREADPOOL_LIMIT

    dataset, config, allowlist = _stage_inputs(context, "handoff")
    fitted, effective = _under_thread_limit(lambda: fit_handoff_models(dataset, config, allowlist))
    files: dict[str, tuple[str, int]] = {}
    for spec, model in fitted:
        data = serialize(model.estimator)
        name = model_file(spec.model_id)
        context.write_payload(name, data)
        files[spec.model_id] = (name, len(data))
    manifest = handoff_manifest(context, dataset, fitted, files, allowlist)
    context.write_payload(HANDOFF_MANIFEST, json_bytes(manifest))
    context.artifacts.setdefault("report_sections", {})["handoff"] = rq1_report.handoff_section(manifest)
    context.provenance["handoff"] = {
        "label": HANDOFF_LABEL, "models": [spec.model_id for spec, _ in fitted], "fit_rows": len(dataset.features),
        "manifest_sha256": context.payloads[HANDOFF_MANIFEST], "scored": False,
        "threadpool_during_fit": {"requested_limit": THREADPOOL_LIMIT, "effective": effective},
    }
