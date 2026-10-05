"""Independent audit of the historical pilot (evidence status ``RECOMPUTED_PILOT``).

Reads ``reference_pilot/output/`` read-only and recomputes, from the raw plans:
counts and repetition integrity, runtime statistics, variance decomposition,
disk-read / spill / parallel-worker evidence, SQL, plan and parameter
diversity, repeated-run noise q-error, the raw and calibrated PostgreSQL-cost
baselines and one dependency-light nonlinear model (scikit-learn
``HistGradientBoostingRegressor`` with fixed, untuned hyperparameters) under
identical random (leakage diagnostic) and template-grouped (primary) folds.

Important caveats written into every output:

* The pilot captured only ``EXPLAIN ANALYZE`` plans. D-class features are
  computed from the *estimate view* of those plans (execution-only keys
  removed), which is what a plain ``EXPLAIN`` would contain under the same
  statistics and settings — but no plain ``EXPLAIN`` was captured.
* Template identifiers, seeds and query identities are never model inputs.
* High-runtime classifiers are reported as ``NOT_YET_EVALUABLE`` when the
  pilot has no (or too few) positive examples.
"""

from __future__ import annotations

import csv
import json
import math
import platform
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from query_cost_predictor import PILOT_AUDIT_VERSION, __version__
from query_cost_predictor.contract import assert_feature_matrix_allowed
from query_cost_predictor.hashing import sha256_file
from query_cost_predictor.labels import LabelError, MeasuredRun, build_runtime_label
from query_cost_predictor.metrics import (
    EPSILON_MS,
    METRIC_DEFINITIONS,
    describe,
    quantiles,
    r2_score,
    rank_correlations,
    regression_metrics,
)
from query_cost_predictor.paths import config_dir, ensure_dir, refuse_reference_write
from query_cost_predictor.plans import (
    ESTIMATE_FEATURE_NAMES,
    estimate_features,
    execution_metrics,
    iter_nodes,
    operator_list,
    physical_plan_hash,
    plan_document,
    plan_shape_hash,
    settings_block,
)
from query_cost_predictor.splits import assert_groups_disjoint, fold_summary, group_kfold, iter_folds, random_kfold
from query_cost_predictor.sqltext import SQL_FEATURE_NAMES, extract_literals, literal_insensitive_fingerprint, sql_structure_features

DEFAULT_SEED = 20260923
N_FOLDS = 5
PLANNED_REPETITIONS = 3
HIGH_RUNTIME_THRESHOLDS_MS = (1000.0, 10000.0)
MIN_POSITIVES_FOR_CLASSIFIER = 10
EVIDENCE_STATUS = "RECOMPUTED_PILOT"
REQUIRED_FILES = ("query_runs.jsonl", "query_dataset.csv", "validation_summary.json")
OPTIONAL_FILES = ("errors.jsonl", "sql_validation.json", "setup_verification.json", "DATASET_VALIDATION_REPORT.md",
                  "requirements.freeze.txt")
HGB_PARAMS = {
    "max_iter": 300,
    "learning_rate": 0.05,
    "max_leaf_nodes": 15,
    "min_samples_leaf": 5,
    "l2_regularization": 1.0,
    "early_stopping": False,
}
MODEL_FEATURES: tuple[str, ...] = tuple(SQL_FEATURE_NAMES) + tuple(ESTIMATE_FEATURE_NAMES)
INDEX_SCAN_TYPES = ("Index Scan", "Index Only Scan", "Bitmap Index Scan")


class AuditError(RuntimeError):
    """The audit cannot run (for example a required input file is missing)."""


@dataclass
class QualityCheck:
    check_id: str
    check: str
    status: str  # PASS | FAIL | WARN | INFO
    observed: str
    expected: str
    detail: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _builtin(value: Any) -> Any:
    """Convert numpy scalars/arrays and NaN into JSON-safe builtins."""
    if isinstance(value, dict):
        return {str(k): _builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_builtin(v) for v in value]
    if isinstance(value, np.ndarray):
        return [_builtin(v) for v in value.tolist()]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return None if not math.isfinite(number) else number
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append({"line": line_no, "error": str(exc)})
                continue
            if not isinstance(obj, dict):
                errors.append({"line": line_no, "error": "record is not a JSON object"})
                continue
            records.append(obj)
    return records, errors


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return rows, list(reader.fieldnames or [])


def _describe_inputs(pilot_dir: Path) -> dict[str, dict[str, Any]]:
    missing = [name for name in REQUIRED_FILES if not (pilot_dir / name).is_file()]
    if missing:
        raise AuditError(f"required pilot files missing in {pilot_dir}: {missing}")
    described: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_FILES + OPTIONAL_FILES:
        path = pilot_dir / name
        if path.is_file():
            described[name] = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
    return described


def _cv(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    return None if mean <= 0 else statistics.stdev(values) / mean


def _template_key(value: Any) -> str:
    return str(int(value))


def _build_instances(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Counter, list[float]]:
    """Group raw runs into query instances and compute per-instance evidence."""
    required = ("query_id", "template_id", "seed", "run_number", "sql", "plan")
    problems: Counter = Counter()
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        if any(key not in rec for key in required):
            problems["malformed_records"] += 1
            continue
        by_id[str(rec["query_id"])].append(rec)

    instances: list[dict[str, Any]] = []
    loo_qerrors: list[float] = []
    for query_id in sorted(by_id):
        recs = sorted(by_id[query_id], key=lambda r: int(r["run_number"]))
        run_numbers = [int(r["run_number"]) for r in recs]
        if len(run_numbers) != len(set(run_numbers)):
            problems["duplicate_run_numbers"] += 1
        if sorted(set(run_numbers)) != [1, 2, 3]:
            problems["run_numbers_not_1_2_3"] += 1
        if len({r["sql"] for r in recs}) != 1:
            problems["sql_differs_across_runs"] += 1
        if len({(str(r["template_id"]), str(r["seed"])) for r in recs}) != 1:
            problems["identity_differs_across_runs"] += 1

        docs = [plan_document(r["plan"]) for r in recs]
        metrics = [execution_metrics(d) for d in docs]
        times = [m["execution_time_ms"] for m in metrics]
        valid_times = [t for t in times if t is not None and math.isfinite(t) and t > 0]
        if len(valid_times) != len(times):
            problems["missing_or_nonpositive_execution_time"] += 1

        try:
            label = build_runtime_label(
                [MeasuredRun(n, "completed", t) for n, t in zip(run_numbers, times) if t is not None],
                planned_repetitions=PLANNED_REPETITIONS,
                timeout_ms=300_000,
                label_version="pilot-audit-label-v1",
            )
            label_status, median_ms = label.status, label.median_ms
        except LabelError as exc:
            problems["label_error"] += 1
            label_status, median_ms = f"invalid: {exc}", None

        for idx, t in enumerate(valid_times):
            others = [x for j, x in enumerate(valid_times) if j != idx]
            if others:
                reference = statistics.fmean(others)
                loo_qerrors.append(max(t / reference, reference / t))

        run3 = next((m for r, m in zip(recs, metrics) if int(r["run_number"]) == 3), None)
        first_doc = docs[0]
        est = estimate_features(first_doc)
        sqlf = sql_structure_features(str(recs[0]["sql"]))
        operators = operator_list(first_doc)
        est_nodes = [n for n, _ in iter_nodes(first_doc["Plan"])]
        instance: dict[str, Any] = {
            "query_id": query_id,
            "template_id": _template_key(recs[0]["template_id"]),
            "seed": str(recs[0]["seed"]),
            "n_runs": len(recs),
            "sql": str(recs[0]["sql"]),
            "sql_fingerprint": literal_insensitive_fingerprint(str(recs[0]["sql"])),
            "literals": extract_literals(str(recs[0]["sql"])),
            "run_times_ms": times,
            "mean_ms": statistics.fmean(valid_times) if valid_times else None,
            "median_ms": median_ms,
            "label_status": label_status,
            "cv": _cv(valid_times),
            "est_total_cost_run1": float(first_doc["Plan"].get("Total Cost") or 0.0),
            "est_total_cost_run3": float(docs[run_numbers.index(3)]["Plan"].get("Total Cost") or 0.0) if 3 in run_numbers else None,
            "total_cost_stable": len({float(d["Plan"].get("Total Cost") or 0.0) for d in docs}) == 1,
            "plan_shape": plan_shape_hash(first_doc),
            "physical_plan": physical_plan_hash(first_doc),
            "plan_shape_stable": len({plan_shape_hash(d) for d in docs}) == 1,
            "operators": operators,
            "has_aggregate": any(n.get("Node Type") == "Aggregate" for n in est_nodes),
            "has_index_scan": any(n.get("Node Type") in INDEX_SCAN_TYPES for n in est_nodes),
            "has_subplan": any(n.get("Parent Relationship") in ("SubPlan", "InitPlan") for n in est_nodes),
            "shared_read_blocks_run3": None if run3 is None else run3["shared_read_blocks"],
            "temp_written_blocks_run3": None if run3 is None else run3["temp_written_blocks"],
            "any_run_shared_read": any((m["shared_read_blocks"] or 0) > 0 for m in metrics),
            "any_run_temp_written": any((m["temp_written_blocks"] or 0) > 0 for m in metrics),
            "any_run_spill": any(m["spill_detected"] for m in metrics),
            "runs_with_shared_read": sum(1 for m in metrics if (m["shared_read_blocks"] or 0) > 0),
            "runs_with_temp_written": sum(1 for m in metrics if (m["temp_written_blocks"] or 0) > 0),
            "runs_with_workers_launched": sum(1 for m in metrics if m["workers_launched"] > 0),
            "runs_workers_mismatch": sum(1 for m in metrics if m["workers_launched"] < m["workers_planned"]),
            "actual_rows_run1": metrics[0]["actual_rows"],
            "runs_with_jit_off": sum(1 for d in docs if str(settings_block(d).get("jit", "")).lower() == "off"),
        }
        instance.update(est)
        instance.update(sqlf)
        instances.append(instance)
    return instances, problems, loo_qerrors


def _variance_decomposition(values: np.ndarray, groups: list[str]) -> dict[str, float]:
    grand = float(np.mean(values))
    ss_total = float(np.sum((values - grand) ** 2))
    ss_between = 0.0
    for group in sorted(set(groups)):
        member = values[[g == group for g in groups]]
        ss_between += len(member) * (float(np.mean(member)) - grand) ** 2
    return {
        "ss_total": ss_total,
        "ss_between": ss_between,
        "ss_within": ss_total - ss_between,
        "between_share": ss_between / ss_total if ss_total > 0 else float("nan"),
    }


def _fit_loglinear(cost: np.ndarray, runtime_ms: np.ndarray) -> tuple[float, float]:
    slope, intercept = np.polyfit(np.log(np.maximum(cost, 1e-6)), np.log(np.maximum(runtime_ms, EPSILON_MS)), 1)
    return float(slope), float(intercept)


def _evaluate_models(instances: list[dict[str, Any]], seed: int, n_folds: int) -> dict[str, Any]:
    """Run baselines and the nonlinear model under identical random and grouped folds."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    feature_columns = list(MODEL_FEATURES)
    assert_feature_matrix_allowed(feature_columns)  # rejects E-class and identity columns
    usable = [inst for inst in instances if inst["median_ms"] is not None]
    x = np.array([[float(inst[col]) for col in feature_columns] for inst in usable], dtype=float)
    y_ms = np.array([float(inst["median_ms"]) for inst in usable], dtype=float)
    y_log = np.log(np.maximum(y_ms, EPSILON_MS))
    cost = np.array([float(inst["est_total_cost_run1"]) for inst in usable], dtype=float)
    groups = [inst["template_id"] for inst in usable]

    fold_sets = {
        "random": random_kfold(len(usable), n_folds, seed),
        "grouped": group_kfold(groups, n_folds, seed),
    }
    assert_groups_disjoint(fold_sets["grouped"], groups)

    rows: list[dict[str, Any]] = []
    pooled: dict[str, dict[str, dict[str, float]]] = {}
    oof_predictions: dict[str, dict[str, dict[str, float]]] = {}
    for split_name, folds in fold_sets.items():
        predictions = {name: np.full(len(usable), np.nan) for name in ("global_median", "calibrated_postgres_cost", "hgb")}
        for fold, train, test in iter_folds(folds):
            predictions["global_median"][test] = float(np.median(y_ms[train]))
            slope, intercept = _fit_loglinear(cost[train], y_ms[train])
            predictions["calibrated_postgres_cost"][test] = np.exp(intercept + slope * np.log(np.maximum(cost[test], 1e-6)))
            model = HistGradientBoostingRegressor(random_state=seed, **HGB_PARAMS)
            model.fit(x[train], y_log[train])
            predictions["hgb"][test] = np.exp(model.predict(x[test]))
            test_groups = [groups[i] for i in test]
            for name, pred in predictions.items():
                metrics = regression_metrics(y_ms[test], pred[test])
                rows.append({
                    "model": name, "split": split_name, "scope": f"fold_{fold}",
                    "n_keys": len(test), "n_groups": len(set(test_groups)),
                    "keys_per_group": len(test) / max(len(set(test_groups)), 1), **metrics,
                })
        pooled[split_name] = {}
        oof_predictions[split_name] = {
            name: {inst["query_id"]: float(pred[i]) for i, inst in enumerate(usable)} for name, pred in predictions.items()
        }
        for name, pred in predictions.items():
            metrics = regression_metrics(y_ms, pred)
            pooled[split_name][name] = metrics
            rows.append({
                "model": name, "split": split_name, "scope": "pooled_out_of_fold",
                "n_keys": len(usable), "n_groups": len(set(groups)),
                "keys_per_group": len(usable) / max(len(set(groups)), 1), **metrics,
            })
    raw_cost = rank_correlations(cost, y_ms)
    rows.append({
        "model": "raw_postgres_cost", "split": "none (rank correlation, no fitting)", "scope": "all_keys",
        "n_keys": len(usable), "n_groups": len(set(groups)),
        "keys_per_group": len(usable) / max(len(set(groups)), 1),
        "spearman": raw_cost["spearman"], "kendall": raw_cost["kendall"],
    })
    return {
        "feature_columns": feature_columns,
        "target": "ln(median of three measured runs, ms)",
        "hyperparameters": {"hgb": HGB_PARAMS, "note": "fixed a priori; no tuning, so no nested selection is needed"},
        "folds": {
            name: {"assignment": {inst["query_id"]: int(f) for inst, f in zip(usable, ids)},
                   "summary": fold_summary(ids, groups)}
            for name, ids in fold_sets.items()
        },
        "rows": rows,
        "pooled": pooled,
        "oof_predictions_ms": oof_predictions,
        "raw_postgres_cost": raw_cost,
        "n_keys": len(usable),
        "n_groups": len(set(groups)),
    }


def _high_runtime_feasibility(instances: list[dict[str, Any]]) -> dict[str, Any]:
    labels = [inst["median_ms"] for inst in instances if inst["median_ms"] is not None]
    result: dict[str, Any] = {}
    for threshold in HIGH_RUNTIME_THRESHOLDS_MS:
        positives = sum(1 for v in labels if v >= threshold)
        if positives == 0:
            status, reason = "NOT_YET_EVALUABLE", "no positive examples; no classifier trained or reported"
        elif positives < MIN_POSITIVES_FOR_CLASSIFIER:
            status, reason = "NOT_YET_EVALUABLE", f"only {positives} positives (< {MIN_POSITIVES_FOR_CLASSIFIER})"
        else:
            status, reason = "EVALUABLE_NOT_RUN", "enough positives, but classification is outside this audit"
        result[f"threshold_{int(threshold)}ms"] = {
            "threshold_ms": threshold, "n_positive": positives, "n_total": len(labels),
            "status": status, "reason": reason,
        }
    return result


def _per_template(instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table = []
    by_template: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for inst in instances:
        by_template[inst["template_id"]].append(inst)
    for template in sorted(by_template, key=int):
        members = by_template[template]
        sql_counts = Counter(m["sql"] for m in members)
        literal_lists = [m["literals"] for m in members]
        n_positions = {len(lits) for lits in literal_lists}
        varying = []
        if len(n_positions) == 1:
            width = n_positions.pop()
            varying = [i for i in range(width) if len({lits[i] for lits in literal_lists}) > 1]
        means = [m["mean_ms"] for m in members if m["mean_ms"] is not None]
        rows_actual = [m["actual_rows_run1"] for m in members if m["actual_rows_run1"] is not None]
        table.append({
            "template_id": template,
            "n_instances": len(members),
            "distinct_sql": len(sql_counts),
            "largest_identical_group": max(sql_counts.values()),
            "distinct_literal_tuples": len({tuple(lits) for lits in literal_lists}),
            "literal_positions": sorted({len(lits) for lits in literal_lists}),
            "varying_literal_positions": varying,
            "distinct_plan_shapes": len({m["plan_shape"] for m in members}),
            "mean_of_mean_ms": statistics.fmean(means) if means else None,
            "var_sample_mean_ms": statistics.variance(means) if len(means) > 1 else None,
            "cv_mean_ms": _cv(means),
            "max_join_nodes": max(int(m["est_join_count"]) for m in members),
            "has_index_scan": any(m["has_index_scan"] for m in members),
            "has_subplan": any(m["has_subplan"] for m in members),
            "median_actual_rows_run1": float(np.median(rows_actual)) if rows_actual else None,
        })
    return table


def _largest_group_cv(instances: list[dict[str, Any]], template: str) -> float | None:
    members = [m for m in instances if m["template_id"] == template]
    if not members:
        return None
    groups: dict[str, list[float]] = defaultdict(list)
    for m in members:
        if m["mean_ms"] is not None:
            groups[m["sql"]].append(m["mean_ms"])
    if not groups:
        return None
    largest = max(sorted(groups), key=lambda sql: len(groups[sql]))
    return _cv(groups[largest])


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), item, out)
    else:
        out[prefix] = value


def compare_claim(claim: dict[str, Any], recomputed: dict[str, Any]) -> dict[str, Any]:
    """Compare one reported value with the recomputed value; never overwrite either."""
    rule = str(claim.get("rule"))
    metric = str(claim.get("metric", "none"))
    reported = claim.get("reported")
    value = recomputed.get(metric) if metric != "none" else None
    status = "MATCH"
    if rule in ("not_recomputable", "not_recomputed"):
        status = rule.upper()
    elif metric not in recomputed:
        status = "MISSING_METRIC"
    elif rule == "not_comparable":
        status = "NOT_COMPARABLE"
    elif value is None:
        status = "MISSING_METRIC"
    elif rule == "exact":
        if isinstance(reported, str) or isinstance(value, str):
            status = "MATCH" if str(reported) == str(value) else "MISMATCH"
        else:
            status = "MATCH" if float(reported) == float(value) else "MISMATCH"
    elif rule == "abs":
        status = "MATCH" if abs(float(value) - float(reported)) <= float(claim["tolerance"]) else "MISMATCH"
    elif rule == "rel":
        denominator = max(abs(float(reported)), 1e-12)
        status = "MATCH" if abs(float(value) - float(reported)) / denominator <= float(claim["tolerance"]) else "MISMATCH"
    else:
        status = "INVALID_RULE"
    return {
        "id": claim.get("id"), "source": claim.get("source"), "metric": metric, "rule": rule,
        "tolerance": claim.get("tolerance"), "reported": reported, "recomputed": value,
        "status": status, "note": claim.get("note", ""),
    }


_STATUS_ORDER = ["MISMATCH", "MISSING_METRIC", "INVALID_RULE", "NOT_COMPARABLE", "NOT_RECOMPUTABLE", "NOT_RECOMPUTED", "MATCH"]


def _claim_check_markdown(results: list[dict[str, Any]], created_at: str) -> str:
    counts = Counter(r["status"] for r in results)
    ordered = sorted(results, key=lambda r: (_STATUS_ORDER.index(r["status"]) if r["status"] in _STATUS_ORDER else 99, str(r["id"])))
    lines = [
        "# Pilot claim check",
        "",
        f"Evidence status: `{EVIDENCE_STATUS}`. Generated {created_at} by `pilot-audit` ({PILOT_AUDIT_VERSION}).",
        "Reported values are copied from `config/pilot_reported_claims.yaml`; recomputed values come from",
        "`reference_pilot/output/` raw files. Neither is overwritten. Mismatches are listed first.",
        "",
        "| Status | Count |", "|---|---:|",
    ]
    lines += [f"| {status} | {counts.get(status, 0)} |" for status in _STATUS_ORDER]
    lines += ["", "| Status | ID | Source | Metric | Rule | Reported | Recomputed | Note |", "|---|---|---|---|---|---|---|---|"]
    for r in ordered:
        rule = r["rule"] + (f" ±{r['tolerance']}" if r.get("tolerance") is not None else "")
        recomputed = r["recomputed"]
        if isinstance(recomputed, float):
            recomputed = f"{recomputed:.6g}"
        lines.append(f"| {r['status']} | {r['id']} | {r['source']} | `{r['metric']}` | {rule} | {r['reported']} | {recomputed} | {r['note']} |")
    lines += ["", "NOT_COMPARABLE rows show a value computed with a different model or definition; do not present them as reproductions."]
    return "\n".join(lines) + "\n"


def _package_versions() -> dict[str, str]:
    versions = {"python": sys.version.split()[0], "platform": platform.platform(), "query_cost_predictor": __version__}
    for dist in ("numpy", "scipy", "scikit-learn", "pandas", "matplotlib", "PyYAML"):
        try:
            versions[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            versions[dist] = "not installed"
    return versions


def run_pilot_audit(
    pilot_dir: Path,
    out_dir: Path,
    *,
    seed: int = DEFAULT_SEED,
    n_folds: int = N_FOLDS,
    claims_path: Path | None = None,
    make_figures: bool = True,
) -> dict[str, Any]:
    """Run the audit and write its outputs into ``out_dir``. Returns the audit dictionary."""
    refuse_reference_write(out_dir)
    created_at = _utc_now()
    inputs = _describe_inputs(pilot_dir)
    records, parse_errors = _read_jsonl(pilot_dir / "query_runs.jsonl")
    csv_rows, csv_fields = _read_csv(pilot_dir / "query_dataset.csv")
    summary = json.loads((pilot_dir / "validation_summary.json").read_text(encoding="utf-8"))
    error_records, _ = _read_jsonl(pilot_dir / "errors.jsonl") if (pilot_dir / "errors.jsonl").is_file() else ([], [])
    sql_validation = json.loads((pilot_dir / "sql_validation.json").read_text(encoding="utf-8")) \
        if (pilot_dir / "sql_validation.json").is_file() else None

    instances, problems, loo_qerrors = _build_instances(records)
    by_id = {inst["query_id"]: inst for inst in instances}
    templates = sorted({inst["template_id"] for inst in instances}, key=int)
    checks: list[QualityCheck] = []

    def add(check_id: str, check: str, ok: bool, observed: Any, expected: Any, detail: str = "", severity: str = "FAIL") -> None:
        checks.append(QualityCheck(check_id, check, "PASS" if ok else severity, str(observed), str(expected), detail))

    add("DQ-01", "required input files present", True, ", ".join(REQUIRED_FILES), "all present")
    add("DQ-02", "raw JSONL records parse", not parse_errors, len(parse_errors), 0, json.dumps(parse_errors[:5]))
    add("DQ-03", "malformed raw records", problems["malformed_records"] == 0, problems["malformed_records"], 0)
    n3 = sum(1 for inst in instances if inst["n_runs"] == PLANNED_REPETITIONS)
    add("DQ-04", "instances with exactly three runs", n3 == len(instances), n3, len(instances))
    add("DQ-05", "duplicate or missing run numbers", problems["duplicate_run_numbers"] + problems["run_numbers_not_1_2_3"] == 0,
        problems["duplicate_run_numbers"] + problems["run_numbers_not_1_2_3"], 0)
    add("DQ-06", "SQL and identity identical across repetitions",
        problems["sql_differs_across_runs"] + problems["identity_differs_across_runs"] == 0,
        problems["sql_differs_across_runs"] + problems["identity_differs_across_runs"], 0)
    add("DQ-07", "execution times present and positive", problems["missing_or_nonpositive_execution_time"] == 0,
        problems["missing_or_nonpositive_execution_time"], 0)

    csv_ids = [row.get("query_id", "") for row in csv_rows]
    add("DQ-08", "CSV rows equal raw instances", len(csv_rows) == len(instances), len(csv_rows), len(instances))
    add("DQ-09", "CSV query_ids equal raw query_ids", set(csv_ids) == set(by_id), len(set(csv_ids) ^ set(by_id)), 0,
        "count of ids present in only one source")
    mean_mismatches = 0
    cost_mismatches = 0
    for row in csv_rows:
        inst = by_id.get(row.get("query_id", ""))
        if inst is None or inst["mean_ms"] is None:
            mean_mismatches += 1
            continue
        reported_mean = float(row["execution_time_mean_ms"])
        if not math.isclose(reported_mean, inst["mean_ms"], rel_tol=1e-9):
            mean_mismatches += 1
        if inst["est_total_cost_run3"] is None or float(row["optimizer_total_cost"]) != inst["est_total_cost_run3"]:
            cost_mismatches += 1
    add("DQ-10", "CSV execution_time_mean_ms equals mean of raw runs", mean_mismatches == 0, mean_mismatches, 0)
    add("DQ-11", "CSV optimizer_total_cost equals run-3 plan Total Cost", cost_mismatches == 0, cost_mismatches, 0)
    missing_total = sum(1 for row in csv_rows for value in row.values() if value == "")
    add("DQ-12", "CSV missing values", missing_total == 0, missing_total, 0, severity="WARN")
    recorded_sha = summary.get("output_sha256", {})
    for name in ("query_dataset.csv", "query_runs.jsonl", "errors.jsonl"):
        if name in recorded_sha and name in inputs:
            add(f"DQ-13-{name}", f"SHA-256 of {name} matches validation_summary.json",
                recorded_sha[name] == inputs[name]["sha256"], inputs[name]["sha256"], recorded_sha[name])
    add("DQ-14", "plan shape identical across repetitions", all(i["plan_shape_stable"] for i in instances),
        sum(1 for i in instances if not i["plan_shape_stable"]), 0, "instances whose plan shape changed", severity="WARN")
    add("DQ-15", "estimated total cost identical across repetitions", all(i["total_cost_stable"] for i in instances),
        sum(1 for i in instances if not i["total_cost_stable"]), 0, severity="WARN")
    jit_off_runs = sum(i["runs_with_jit_off"] for i in instances)
    add("DQ-16", "JIT off in every run (plan Settings block)", jit_off_runs == len(records), jit_off_runs, len(records), severity="WARN")
    checks.append(QualityCheck("DQ-17", "timeouts retained as censored labels", "WARN", f"{len(error_records)} error records",
                               "censored rows in the dataset",
                               "The pilot collector wrote failures and timeouts to errors.jsonl, not to the dataset; zero error "
                               "records means no timeouts occurred, but the protocol could not have retained them as labels."))
    checks.append(QualityCheck("DQ-18", "plain EXPLAIN captured before execution", "WARN", "no", "yes",
                               "Only EXPLAIN ANALYZE plans exist; D-class features use the estimate view of those plans."))
    try:
        assert_feature_matrix_allowed(list(MODEL_FEATURES))
        add("DQ-19", "model feature columns pass the availability contract", True, len(MODEL_FEATURES), "A-D features only")
    except Exception as exc:  # the audit still records the failure before stopping
        add("DQ-19", "model feature columns pass the availability contract", False, "rejected", "A-D features only", str(exc))
        raise

    # --- Recomputed values -------------------------------------------------------------
    mean_labels = np.array([i["mean_ms"] for i in instances if i["mean_ms"] is not None], dtype=float)
    median_labels = np.array([i["median_ms"] for i in instances if i["median_ms"] is not None], dtype=float)
    all_runs = np.array([t for i in instances for t in i["run_times_ms"] if t is not None], dtype=float)
    mean_groups = [i["template_id"] for i in instances if i["mean_ms"] is not None]
    median_groups = [i["template_id"] for i in instances if i["median_ms"] is not None]
    per_template = _per_template(instances)
    cvs = [i["cv"] for i in instances if i["cv"] is not None]
    median_cv = float(np.median(cvs)) if cvs else float("nan")
    high_variance = sum(1 for c in cvs if c > 0.20)
    template_cvs = [t["cv_mean_ms"] for t in per_template if t["cv_mean_ms"] is not None]
    csv_sql = [row.get("sql", "") for row in csv_rows]

    runtime_mean = describe(mean_labels)
    runtime_mean["max_over_min"] = runtime_mean["max"] / runtime_mean["min"]
    runtime_median = describe(median_labels)
    runtime_median["max_over_min"] = runtime_median["max"] / runtime_median["min"]
    runtime_median["orders_of_magnitude"] = math.log10(runtime_median["max_over_min"])

    def template_stat(template: str, key: str) -> Any:
        return next((t[key] for t in per_template if t["template_id"] == template), None)

    cost_run3 = [(i["est_total_cost_run3"], i["mean_ms"]) for i in instances
                 if i["est_total_cost_run3"] is not None and i["mean_ms"] is not None]
    model_results = _evaluate_models(instances, seed, n_folds)
    insample_pred = np.full(mean_labels.shape, float(np.mean(mean_labels)))

    recomputed_tree: dict[str, Any] = {
        "counts": {
            "instances_raw": len(instances),
            "instances_csv": len(csv_rows),
            "templates": len(templates),
            "raw_runs": len(records),
            "error_records": len(error_records),
            "csv_columns": len(csv_fields),
            "duplicate_query_ids": len(csv_ids) - len(set(csv_ids)),
            "duplicate_csv_rows": len(csv_rows) - len({tuple(row.items()) for row in csv_rows}),
            "distinct_sql": len(set(csv_sql)),
            "duplicate_sql_rows": len(csv_sql) - len(set(csv_sql)),
            "missing_values_csv_total": missing_total,
            "raw_record_parse_errors": len(parse_errors),
            "instances_with_3_runs": n3,
            "sql_validation_valid": None if sql_validation is None else sum(1 for e in sql_validation if e.get("valid") is True),
        },
        "integrity": {
            "csv_mean_mismatches": mean_mismatches,
            "csv_cost_mismatches": cost_mismatches,
            "runs_with_jit_off_setting": jit_off_runs,
            "sha256": {name: meta["sha256"] for name, meta in inputs.items()},
        },
        "runtime": {"mean_label": runtime_mean, "median_label": runtime_median, "raw_runs": describe(all_runs)},
        "quality": {"high_variance_instances": high_variance, "ok_instances": len(cvs) - high_variance},
        "noise": {
            "loo_qerror": quantiles(loo_qerrors),
            "median_instance_cv": median_cv,
            "sem_fraction_3_runs": median_cv / math.sqrt(3),
            "sem_fraction_5_runs": median_cv / math.sqrt(5),
            "n_runs": len(loo_qerrors),
        },
        "variance": {
            "between_template_share_mean_ms": _variance_decomposition(mean_labels, mean_groups)["between_share"],
            "between_template_share_log_median": _variance_decomposition(np.log(median_labels), median_groups)["between_share"],
            "within_template_cv_min": min(template_cvs) if template_cvs else None,
            "within_template_cv_max": max(template_cvs) if template_cvs else None,
        },
        "resources": {
            "instances_zero_shared_reads_run3": sum(1 for i in instances if i["shared_read_blocks_run3"] == 0),
            "instances_temp_written_run3": sum(1 for i in instances if (i["temp_written_blocks_run3"] or 0) > 0),
            "runs_with_shared_reads": sum(i["runs_with_shared_read"] for i in instances),
            "instances_any_shared_read": sum(1 for i in instances if i["any_run_shared_read"]),
            "runs_with_temp_written": sum(i["runs_with_temp_written"] for i in instances),
            "instances_any_spill": sum(1 for i in instances if i["any_run_spill"]),
            "runs_with_workers_launched": sum(i["runs_with_workers_launched"] for i in instances),
            "instances_any_workers_launched": sum(1 for i in instances if i["runs_with_workers_launched"] > 0),
            "runs_workers_mismatch": sum(i["runs_workers_mismatch"] for i in instances),
            "timeouts_recorded": sum(1 for e in error_records if "timeout" in json.dumps(e).lower()),
        },
        "diversity": {
            "q06_distinct_sql": template_stat("6", "distinct_sql"),
            "q13_distinct_sql": template_stat("13", "distinct_sql"),
            "q18_distinct_sql": template_stat("18", "distinct_sql"),
            "q06_largest_identical_group": template_stat("6", "largest_identical_group"),
            "q13_largest_identical_group": template_stat("13", "largest_identical_group"),
            "q18_largest_identical_group": template_stat("18", "largest_identical_group"),
            "q13_identical_group_cv": _largest_group_cv(instances, "13"),
            "q18_identical_group_cv": _largest_group_cv(instances, "18"),
            "node_count_min": int(min(i["est_node_count"] for i in instances)),
            "node_count_max": int(max(i["est_node_count"] for i in instances)),
            "join_count_min": int(min(i["est_join_count"] for i in instances)),
            "join_count_max": int(max(i["est_join_count"] for i in instances)),
            "instances_with_aggregate": sum(1 for i in instances if i["has_aggregate"]),
            "templates_with_index_scan": sum(1 for t in per_template if t["has_index_scan"]),
            "templates_result_lt_10_rows": sum(1 for t in per_template if t["median_actual_rows_run1"] is not None and t["median_actual_rows_run1"] < 10),
            "templates_result_gt_1000_rows": sum(1 for t in per_template if t["median_actual_rows_run1"] is not None and t["median_actual_rows_run1"] > 1000),
            "templates_with_4plus_joins": sum(1 for t in per_template if t["max_join_nodes"] >= 4),
            "templates_with_1to3_joins": sum(1 for t in per_template if 1 <= t["max_join_nodes"] <= 3),
            "templates_with_subplan": sum(1 for t in per_template if t["has_subplan"]),
            "distinct_literal_insensitive_fingerprints": len({i["sql_fingerprint"] for i in instances}),
            "distinct_plan_shapes": len({i["plan_shape"] for i in instances}),
            "distinct_physical_plans": len({i["physical_plan"] for i in instances}),
            "distinct_literal_tuples": len({(i["template_id"], tuple(i["literals"])) for i in instances}),
        },
        "cost": {
            "spearman_total_cost_vs_mean_label": rank_correlations([c for c, _ in cost_run3], [m for _, m in cost_run3])["spearman"],
            "spearman_total_cost_vs_median_label": model_results["raw_postgres_cost"]["spearman"],
            "kendall_total_cost_vs_median_label": model_results["raw_postgres_cost"]["kendall"],
        },
        "models": {
            "global_mean_insample_mae_ms": float(np.mean(np.abs(mean_labels - insample_pred))),
            "global_mean_insample_r2": r2_score(mean_labels, insample_pred),
        },
    }
    for split_name, per_model in model_results["pooled"].items():
        for model_name, metrics in per_model.items():
            recomputed_tree["models"].setdefault(model_name, {}).setdefault(split_name, {})["pooled"] = metrics
    recomputed: dict[str, Any] = {}
    _flatten("", _builtin(recomputed_tree), recomputed)

    claims_file = claims_path or (config_dir() / "pilot_reported_claims.yaml")
    claims_doc = yaml.safe_load(claims_file.read_text(encoding="utf-8"))
    claim_results = [compare_claim(claim, recomputed) for claim in claims_doc.get("claims", [])]
    high_runtime = _high_runtime_feasibility(instances)

    audit: dict[str, Any] = {
        "audit_version": PILOT_AUDIT_VERSION,
        "evidence_status": EVIDENCE_STATUS,
        "created_at_utc": created_at,
        "seed": seed,
        "n_folds": n_folds,
        "inputs": inputs,
        "environment": _package_versions(),
        "pilot_settings_reported": summary.get("postgres_settings", {}),
        "pilot_collector_settings_reported": summary.get("collector_settings", {}),
        "caveats": [
            "Pilot plans were captured with EXPLAIN ANALYZE only; D-class features use the estimate view of those plans.",
            "The pilot label definition was the mean of three runs; this audit also reports the median label used by the new protocol.",
            "Random-split results are a leakage diagnostic, not performance on unseen query structures.",
            "Template identifiers, seeds and query identities are never model inputs.",
            "Single machine, single session, SF 0.1, warm cache; timings do not transfer to other hardware.",
        ],
        "data_quality": [asdict(c) for c in checks],
        "problems": dict(problems),
        "recomputed": recomputed,
        "per_template": per_template,
        "models": {k: v for k, v in model_results.items() if k != "pooled"},
        "high_runtime_classification": high_runtime,
        "metric_definitions": METRIC_DEFINITIONS,
        "claim_check": claim_results,
        "instances": [
            {k: inst[k] for k in ("query_id", "template_id", "n_runs", "run_times_ms", "mean_ms", "median_ms", "cv",
                                  "est_total_cost_run1", "operators", "any_run_spill", "any_run_shared_read",
                                  "runs_with_workers_launched", "plan_shape")}
            for inst in instances
        ],
        "noise_loo_qerrors": loo_qerrors,
    }
    audit = _builtin(audit)

    ensure_dir(out_dir)
    (out_dir / "pilot_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    _write_metrics_csv(out_dir / "pilot_metrics.csv", audit["models"]["rows"])
    _write_quality_csv(out_dir / "pilot_data_quality.csv", checks)
    (out_dir / "pilot_claim_check.md").write_text(_claim_check_markdown(claim_results, created_at), encoding="utf-8")
    if make_figures:
        from query_cost_predictor.reporting.pilot_figures import write_pilot_figures

        audit["figures"] = write_pilot_figures(audit, out_dir / "figures" / "pilot")
        (out_dir / "pilot_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit


_METRIC_COLUMNS = ["model", "split", "scope", "n_keys", "n_groups", "keys_per_group", "log_mae", "median_qerror",
                   "p90_qerror", "p95_qerror", "mae_ms", "rmse_ms", "r2_log", "r2_ms", "spearman", "kendall"]


def _write_metrics_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_METRIC_COLUMNS + ["evidence_status"], extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({**{col: row.get(col, "") for col in _METRIC_COLUMNS}, "evidence_status": EVIDENCE_STATUS})


def _write_quality_csv(path: Path, checks: list[QualityCheck]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["check_id", "check", "status", "observed", "expected", "detail"])
        writer.writeheader()
        for check in checks:
            writer.writerow(asdict(check))
