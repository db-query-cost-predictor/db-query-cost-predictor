"""RQ1 descriptive diagnostics: the repetition-noise reference and near-threshold sensitivity.

Both read the pinned ``derived/executions.csv`` (re-hashed against the input
record) for development keys only, and neither changes a label, a threshold
or a prediction. Nothing is fitted here.

**Repetition-noise reference** (``diagnostics_noise.json``). Every
development key must have exactly three measured runs (``repeat_no`` 1, 2, 3),
each ``completed``, not censored, finite and positive; their median must equal
the checked ``median_ms`` exactly (the label is the middle measured run).
Probe rows are excluded. A missing, duplicate, inconsistent or invalid run is
an error; nothing is dropped or imputed.

* leave-one-run-out q-error, per measurement: m_i = mean of the other two runs,
  q_i = max(t_i / m_i, m_i / t_i). The aggregation unit is the **measurement**
  (three values per case);
* coefficient of variation, per case: sample standard deviation / mean of the
  three runs. The aggregation unit is the **case**.

Median, p90 and p95 are reported overall and by snapshot, component and the
approved runtime bins. This is a descriptive reference for scale, not a proven
lower bound on prediction error.

**Threshold sensitivity** (``diagnostics_threshold.json``). From observed
development measurements:

* S: min(measured runs) < 1000 ms <= max(measured runs);
* B: 800 ms <= median_ms < 1250 ms.

The existing ``fold_group`` OOF classification outputs of every classifier
are scored on all cases, all minus S, all minus B and B only, with the same
metric, null-reason and flag rules. S and B use observed runtimes, so they are
retrospective diagnostics, never deployable pre-execution filters.
"""

from __future__ import annotations

import csv
import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from query_cost_predictor.metrics import describe
from query_cost_predictor.rq1_config import Rq1Config
from query_cost_predictor.rq1_inputs import DevelopmentDataset
from query_cost_predictor.rq1_metrics import (
    CLASSIFICATION_METRICS,
    REASON_EMPTY,
    SCOPE_POOLED,
    TASK_CLASSIFICATION,
    classification_flags,
    classification_metric_values,
    declared_strata,
    describe_rows,
    json_bytes,
    metric_row,
    positive_support,
    strata_frame,
)

EXECUTIONS_FILE = "derived/executions.csv"
NOISE_FILE = "diagnostics_noise.json"
THRESHOLD_FILE = "diagnostics_threshold.json"
MEASURED, PROBE, COMPLETED = "measured", "probe", "completed"
EXPECTED_MEASURED_REPEATS = (1, 2, 3)
REQUIRED_EXECUTION_COLUMNS = ("modeling_key", "repeat_no", "run_purpose", "status", "is_censored", "execution_time_ms")
BAND_LOWER_MS, BAND_UPPER_MS = 800.0, 1250.0
EVALUATIONS = ("all_cases", "excluding_straddle_set", "excluding_band_set", "band_set_only")
STRADDLE_DEFINITION = "development cases with min(measured runs) < 1000 ms <= max(measured runs)"
BAND_DEFINITION = "development cases with 800 ms <= median_ms < 1250 ms"
NOISE_BY = ("snapshot_name", "component", "runtime_bin_ms")
EVALUATION_COLUMN = "threshold_evaluation"


class Rq1DiagnosticsError(ValueError):
    """The measured runs or the diagnostics specification violate the approved rules."""


def check_diagnostics_spec(config: Rq1Config) -> None:
    """Fail unless the YAML diagnostics definitions equal the implemented ones (no silent drift)."""
    spec = config.raw["diagnostics"]
    noise, threshold = spec["repetition_noise"], spec["threshold_sensitivity"]
    if (noise["source"], tuple(noise["report"]), tuple(noise["by"])) != ("executions.csv", ("median", "p90", "p95"), NOISE_BY):
        raise Rq1DiagnosticsError("YAML repetition-noise source, statistics or strata differ from the implementation")
    if (threshold["straddle_set"], threshold["band_set"]) != (STRADDLE_DEFINITION, BAND_DEFINITION):
        raise Rq1DiagnosticsError("YAML straddle or band set definitions differ from the implementation")
    if tuple(threshold["evaluations"]) != EVALUATIONS or threshold["headline"] != "all_cases":
        raise Rq1DiagnosticsError("YAML threshold evaluations differ from the implementation")
    if threshold["labels_changed"] is not False or threshold["retrospective"] is not True:
        raise Rq1DiagnosticsError("threshold diagnostics never change labels and are retrospective")
    if float(config.raw["targets"]["classification_1s"]["label_boundary_ms"]) != 1000.0:
        raise Rq1DiagnosticsError("the 1 s label boundary is frozen at 1000 ms")


# ----------------------------------------------------------------------------------
# Measured runs
# ----------------------------------------------------------------------------------
def _fail(problem: str, keys: list[str]) -> None:
    if keys:
        raise Rq1DiagnosticsError(f"{len(keys)} development key(s) {problem}: {sorted(keys)[:5]}; no case is dropped "
                                  "or imputed")


def load_measured_runs(path: Path, dataset: DevelopmentDataset) -> tuple[pd.DataFrame, dict[str, int]]:
    """The three measured runs of every development key (columns ``run_1``..``run_3``) and audit counts."""
    keys = dataset.features.index
    development = set(keys)
    values: dict[str, dict[int, float]] = {}
    counts: Counter = Counter()
    invalid: dict[str, list[str]] = {}
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        absent = sorted(set(REQUIRED_EXECUTION_COLUMNS) - set(reader.fieldnames or []))
        if absent:
            raise Rq1DiagnosticsError(f"executions.csv lacks columns {absent}")
        for row in reader:
            counts["rows_read"] += 1
            key = row["modeling_key"]
            if key not in development:
                counts["non_development_rows_ignored"] += 1
                continue
            purpose = row["run_purpose"]
            if purpose == PROBE:
                counts["development_probe_rows_excluded"] += 1
                continue
            if purpose != MEASURED:
                invalid.setdefault(f"have an unknown run_purpose {purpose!r}", []).append(key)
                continue
            try:
                repeat = int(row["repeat_no"])
                time_ms = float(row["execution_time_ms"])
            except ValueError:
                invalid.setdefault("have a non-numeric repeat_no or execution_time_ms", []).append(key)
                continue
            if repeat not in EXPECTED_MEASURED_REPEATS:
                invalid.setdefault(f"have a measured repeat_no outside {EXPECTED_MEASURED_REPEATS}", []).append(key)
            elif row["status"] != COMPLETED or row["is_censored"] != "false":
                invalid.setdefault("have a measured run that is not completed and uncensored", []).append(key)
            elif not (math.isfinite(time_ms) and time_ms > 0):
                invalid.setdefault("have a non-finite or non-positive measured execution_time_ms", []).append(key)
            elif repeat in values.setdefault(key, {}):
                invalid.setdefault("have a duplicate measured repeat_no", []).append(key)
            else:
                values[key][repeat] = time_ms
                counts["measured_runs_used"] += 1
    for problem, offending in invalid.items():
        _fail(problem, offending)
    _fail(f"lack one of the measured runs {EXPECTED_MEASURED_REPEATS}",
          [k for k in keys if set(values.get(k, {})) != set(EXPECTED_MEASURED_REPEATS)])
    frame = pd.DataFrame([[values[k][r] for r in EXPECTED_MEASURED_REPEATS] for k in keys], index=keys,
                         columns=[f"run_{r}" for r in EXPECTED_MEASURED_REPEATS], dtype="float64")
    medians = np.median(frame.to_numpy(), axis=1)
    target = dataset.targets["median_ms"].to_numpy(dtype="float64")
    _fail("have a measured-run median that differs from the checked median_ms",
          [str(k) for k, a, b in zip(keys, medians, target) if a != b])
    counts["development_cases"] = len(keys)
    counts["median_reconciled_cases"] = len(keys)
    return frame, dict(sorted(counts.items()))


# ----------------------------------------------------------------------------------
# Repetition-noise reference
# ----------------------------------------------------------------------------------
def leave_one_run_out_qerror(runs: np.ndarray) -> np.ndarray:
    """q_i = max(t_i / m_i, m_i / t_i), m_i the mean of the other two runs; shape (cases, 3)."""
    runs = np.asarray(runs, dtype="float64")
    if runs.ndim != 2 or runs.shape[1] != 3 or not (np.isfinite(runs).all() and (runs > 0).all()):
        raise Rq1DiagnosticsError("leave-one-run-out q-error needs finite positive runs, three per case")
    others = (runs.sum(axis=1, keepdims=True) - runs) / 2.0
    return np.maximum(runs / others, others / runs)


def coefficient_of_variation(runs: np.ndarray) -> np.ndarray:
    """Per case: sample standard deviation (ddof=1) / mean of the three runs."""
    runs = np.asarray(runs, dtype="float64")
    return runs.std(axis=1, ddof=1) / runs.mean(axis=1)


def _summary(values: np.ndarray, unit: str) -> dict[str, Any]:
    if values.size == 0:
        return {"unit": unit, "n": 0, "median": None, "p90": None, "p95": None, "null_reason": REASON_EMPTY}
    stats = describe(values)
    return {"unit": unit, "n": int(values.size), "median": stats["median"], "p90": stats["p90"], "p95": stats["p95"],
            "null_reason": None}


def noise_document(runs: pd.DataFrame, dataset: DevelopmentDataset, config: Rq1Config,
                   source: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    q = leave_one_run_out_qerror(runs.to_numpy())
    cv = coefficient_of_variation(runs.to_numpy())
    strata = strata_frame(dataset.metadata, dataset.targets["median_ms"], config)

    def summaries(mask: np.ndarray) -> dict[str, Any]:
        return {"leave_one_run_out_qerror": _summary(q[mask].ravel(), "measurement"),
                "coefficient_of_variation": _summary(cv[mask], "case"), "n_cases": int(mask.sum())}
    by = {}
    for column, values in declared_strata(config):
        if column in NOISE_BY:
            labels = strata[column].to_numpy()
            by[column] = {value: summaries(labels == value) for value in values}
            if sum(entry["n_cases"] for entry in by[column].values()) != len(runs):
                raise Rq1DiagnosticsError(f"{column} strata do not cover every development case once")
    spec = config.raw["diagnostics"]["repetition_noise"]
    return {
        "schema": "qcp.rq1_diagnostics_noise", "schema_version": 1, "spec_id": config.spec_id,
        "name": spec["name"], "interpretation": spec["interpretation"],
        "not_a_noise_floor": "descriptive reference for scale; not a proven lower bound on prediction error; the label "
                             "is the median of three runs and differs from any single run",
        "definitions": {
            "leave_one_run_out_qerror": spec["leave_one_run_out_qerror"],
            "coefficient_of_variation": spec["coefficient_of_variation"],
            "aggregation_units": {"leave_one_run_out_qerror": "measurement (three values per development case)",
                                  "coefficient_of_variation": "case (one value per development case)"},
            "percentiles": "linear interpolation (numpy.percentile)",
        },
        "source": source, "counts": counts,
        "overall": summaries(np.ones(len(runs), dtype=bool)),
        "by": by,
    }


# ----------------------------------------------------------------------------------
# Threshold sensitivity
# ----------------------------------------------------------------------------------
def threshold_sets(runs: pd.DataFrame, median_ms: pd.Series, boundary_ms: float = 1000.0) -> tuple[pd.Series, pd.Series]:
    """S (runs straddle the boundary) and B (median in the band), from observed runtimes only."""
    if not runs.index.equals(median_ms.index):
        raise Rq1DiagnosticsError("measured runs and medians are not aligned")
    straddle = (runs.min(axis=1) < boundary_ms) & (boundary_ms <= runs.max(axis=1))
    band = (BAND_LOWER_MS <= median_ms) & (median_ms < BAND_UPPER_MS)
    return straddle.rename("straddle_set"), band.rename("band_set")


def threshold_document(oof: pd.DataFrame, dataset: DevelopmentDataset, config: Rq1Config,
                       runs: pd.DataFrame) -> dict[str, Any]:
    """Every classifier's existing ``fold_group`` OOF outputs scored on the four predeclared evaluations."""
    from query_cost_predictor.rq1_evaluate import PRIMARY_SCHEME, _check_aligned, _model_block
    from query_cost_predictor.rq1_models import classification_model_specs

    keys = _check_aligned(dataset)
    boundary = float(config.raw["targets"]["classification_1s"]["label_boundary_ms"])
    median = dataset.targets["median_ms"]
    labels = dataset.targets["hr_1s"].to_numpy(dtype="int64")
    if not np.array_equal(labels, (median.to_numpy() >= boundary).astype("int64")):
        raise Rq1DiagnosticsError("hr_1s disagrees with median_ms >= 1000 ms; labels are frozen and never recomputed")
    straddle, band = threshold_sets(runs, median, boundary)
    masks = {"all_cases": np.ones(len(keys), dtype=bool), "excluding_straddle_set": ~straddle.to_numpy(),
             "excluding_band_set": ~band.to_numpy(), "band_set_only": band.to_numpy()}
    groups = dataset.metadata["semantic_group_id"].to_numpy(dtype=object)
    rows = []
    for spec in classification_model_specs(config):
        block = _model_block(oof, PRIMARY_SCHEME, spec.model_id, keys)
        y, score = block["y_true"].to_numpy(dtype="int64"), block["score"].to_numpy(dtype="float64")
        if not np.array_equal(y, labels):
            raise Rq1DiagnosticsError(f"{spec.model_id}: OOF labels differ from the frozen hr_1s labels")
        folds = block["fold"].to_numpy()
        for name in EVALUATIONS:
            mask = masks[name]
            values = classification_metric_values(spec.output_type, y[mask], score[mask])
            descriptor = {**describe_rows(dataset.metadata[mask]), **positive_support(y[mask], groups[mask], folds[mask])}
            for metric in CLASSIFICATION_METRICS:
                result = values[metric]
                rows.append(metric_row(task=TASK_CLASSIFICATION, fold_scheme=PRIMARY_SCHEME, model_id=spec.model_id,
                                       scope=SCOPE_POOLED, fold=None, stratum_column=EVALUATION_COLUMN,
                                       stratum_value=name, descriptor=descriptor, metric=metric, result=result,
                                       flag=classification_flags(SCOPE_POOLED, descriptor, result.value is not None)))

    def describe_set(mask: pd.Series, definition: str) -> dict[str, Any]:
        members = mask[mask].index
        return {"definition": definition, "n_cases": int(mask.sum()),
                "n_positive": int(labels[mask.to_numpy()].sum()), "keys": sorted(str(k) for k in members)}
    spec = config.raw["diagnostics"]["threshold_sensitivity"]
    return {
        "schema": "qcp.rq1_diagnostics_threshold", "schema_version": 1, "spec_id": config.spec_id,
        "retrospective": True,
        "interpretation": "retrospective diagnostics defined from observed runtimes; never deployable pre-execution "
                          "filters. No model is refitted and no label, prediction or threshold changes.",
        "source_predictions": spec["source_predictions"], "headline": spec["headline"],
        "label_boundary_ms": boundary, "decision_threshold": 0.5, "labels_changed": False,
        "sets": {"straddle_set": describe_set(straddle, STRADDLE_DEFINITION),
                 "band_set": describe_set(band, BAND_DEFINITION)},
        "evaluations": {name: {"n_cases": int(masks[name].sum()), "n_positive": int(labels[masks[name]].sum())}
                        for name in EVALUATIONS},
        "checks": {"oof_labels_equal_frozen_labels": True, "labels_equal_median_ms_at_boundary": True},
        "columns": list(rows[0]) if rows else [], "rows": rows,
    }


# ----------------------------------------------------------------------------------
# The diagnostics stage of rq1-run
# ----------------------------------------------------------------------------------
def stage_diagnostics(context: Any) -> None:
    from query_cost_predictor import rq1_figures, rq1_report
    from query_cost_predictor.rq1_evaluate import pinned_evidence_file

    dataset = context.artifacts.get("dataset")
    oof = context.artifacts.get("oof", {})
    if not isinstance(dataset, DevelopmentDataset) or TASK_CLASSIFICATION not in oof or "regression" not in oof:
        raise Rq1DiagnosticsError("the diagnostics stage needs the checked dataset and the regression and "
                                  "classification OOF outputs from the earlier stages")
    config = context.config
    check_diagnostics_spec(config)
    path, sha = pinned_evidence_file(config, context.repo_root, context.evidence_dir, EXECUTIONS_FILE)
    runs, counts = load_measured_runs(path, dataset)
    source = {"file": EXECUTIONS_FILE, "sha256": sha, "rows": "development keys; run_purpose measured; status "
              "completed; not censored", "measured_repeat_numbers": list(EXPECTED_MEASURED_REPEATS),
              "probes": "excluded"}
    noise = noise_document(runs, dataset, config, source, counts)
    threshold = threshold_document(oof[TASK_CLASSIFICATION], dataset, config, runs)
    context.write_payload(NOISE_FILE, json_bytes(noise))
    context.write_payload(THRESHOLD_FILE, json_bytes(threshold))
    figures = rq1_figures.write_figures(context)
    context.artifacts.setdefault("report_sections", {})["diagnostics"] = rq1_report.diagnostics_section(
        noise, threshold, figures, config)
    context.provenance["diagnostics"] = {
        "executions_sha256": sha, "counts": counts,
        "straddle_set_cases": threshold["sets"]["straddle_set"]["n_cases"],
        "band_set_cases": threshold["sets"]["band_set"]["n_cases"],
        "figures": [entry["file"] for entry in figures], "models_fitted": 0,
    }
