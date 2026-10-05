"""Table-producing helpers for ``notebooks/01_poster_evidence.ipynb``.

Each helper takes loaded evidence (see ``reporting.loaders``) and returns a list
of dictionaries; nothing here generates or substitutes data. Helpers raise
``MissingEvidenceError`` when the evidence they need is absent.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from query_cost_predictor.claims import load_claim_specs, render_claims
from query_cost_predictor.errors import MissingEvidenceError
from query_cost_predictor.hashing import sha256_file
from query_cost_predictor.metrics import q_error
from query_cost_predictor.reporting.loaders import EvidenceBundle, SmokeEvidence

RUNTIME_BINS_MS: list[tuple[str, float, float]] = [
    ("fast (<100 ms)", 0.0, 100.0),
    ("medium (100 ms-1 s)", 100.0, 1000.0),
    ("slow (1-10 s)", 1000.0, 10000.0),
    ("very slow (10-60 s)", 10000.0, 60000.0),
    ("beyond 60 s", 60000.0, math.inf),
]


def runtime_bin(ms: float) -> str:
    for name, low, high in RUNTIME_BINS_MS:
        if low <= ms < high:
            return name
    raise ValueError(f"runtime {ms} outside all bins")


def _file_row(status: str, path: Path, recorded: str | None) -> dict[str, Any]:
    now = sha256_file(path) if path.is_file() else "MISSING"
    return {"evidence_status": status, "file": str(path), "sha256_recorded": recorded or "", "sha256_now": now,
            "matches": (recorded == now) if recorded else None}


def provenance_table(bundle: EvidenceBundle) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if bundle.pilot is not None:
        rows.append(_file_row("RECOMPUTED_PILOT (audit)", bundle.pilot.path, None))
        for info in bundle.pilot.audit["inputs"].values():
            rows.append(_file_row("RECOMPUTED_PILOT (input)", Path(info["path"]), info["sha256"]))
    if bundle.smoke is not None:
        rows.append(_file_row("NEW_POSTER_SMOKE (validation)", bundle.smoke.validation_path, None))
        for name, sha in bundle.smoke.validation["derived_files_sha256"].items():
            rows.append(_file_row("NEW_POSTER_SMOKE (derived)", bundle.smoke.derived_dir / name, sha))
        from query_cost_predictor.smoke_collector import raw_session_dir

        raw_dir = raw_session_dir(bundle.smoke.validation["protocol"]["protocol_version"])
        for info in bundle.smoke.validation["raw_files"]:
            rows.append(_file_row("NEW_POSTER_SMOKE (raw)", raw_dir / info["name"], info["sha256"]))
    if bundle.rewrite is not None:
        rows.append(_file_row("NEW_POSTER_SMOKE (rewrite summary)", bundle.rewrite.path, None))
        raw = bundle.rewrite.summary["raw_file"]
        rows.append(_file_row("NEW_POSTER_SMOKE (rewrite raw)", Path(raw["path"]), raw["sha256"]))
    if not rows:
        raise MissingEvidenceError("no evidence files found; follow POSTER_RUNBOOK.md steps 7-11")
    return rows


def pilot_counts(audit: dict[str, Any]) -> list[dict[str, Any]]:
    r = audit["recomputed"]
    keys = ["counts.instances_raw", "counts.templates", "counts.raw_runs", "counts.instances_with_3_runs",
            "counts.distinct_sql", "diversity.distinct_literal_insensitive_fingerprints", "diversity.distinct_plan_shapes"]
    return [{"quantity": k, "value": r.get(k), "evidence_status": "RECOMPUTED_PILOT"} for k in keys]


def smoke_counts(smoke: SmokeEvidence) -> list[dict[str, Any]]:
    counts = smoke.validation["counts"]
    rows = [{"quantity": k, "value": v, "evidence_status": "NEW_POSTER_SMOKE"} for k, v in counts.items() if not isinstance(v, dict)]
    rows.append({"quantity": "note", "value": "measured executions are child records; they are not counted as modeling keys",
                 "evidence_status": "NEW_POSTER_SMOKE"})
    return rows


def pilot_tail_table(audit: dict[str, Any], split: str = "grouped") -> list[dict[str, Any]]:
    """Out-of-fold q-error by actual-runtime bin, per model, for one split."""
    predictions = audit["models"].get("oof_predictions_ms", {}).get(split)
    if not predictions:
        raise MissingEvidenceError("pilot audit has no out-of-fold predictions; re-run step 7 with the current code")
    actual = {i["query_id"]: i["median_ms"] for i in audit["instances"] if i.get("median_ms") is not None}
    rows = []
    for bin_name, low, high in RUNTIME_BINS_MS:
        ids = [q for q, v in actual.items() if low <= v < high]
        if not ids:
            continue
        row: dict[str, Any] = {"split": split, "runtime_bin": bin_name, "n_keys": len(ids)}
        for model, pred in predictions.items():
            q = q_error([pred[i] for i in ids], [actual[i] for i in ids])
            row[f"{model}_median_q"] = float(np.median(q))
            row[f"{model}_p90_q"] = float(np.percentile(q, 90))
        rows.append(row)
    return rows


def smoke_bin_table(smoke: SmokeEvidence) -> list[dict[str, Any]]:
    counts: Counter = Counter()
    for row in smoke.labels:
        if row["status"] == "complete":
            counts[runtime_bin(float(row["median_ms"]))] += 1
        elif row["status"] == "right_censored":
            counts[f"right-censored at {float(row['censor_lower_bound_ms']) / 1000:.0f} s"] += 1
        else:
            counts[row["status"]] += 1
    return [{"runtime_bin": k, "modeling_keys": v, "evidence_status": "NEW_POSTER_SMOKE"} for k, v in counts.items()]


def operator_table(bundle: EvidenceBundle) -> list[dict[str, Any]]:
    sources = []
    if bundle.pilot is not None:
        sources.append(("RECOMPUTED_PILOT", [i["operators"] for i in bundle.pilot.audit["instances"]]))
    if bundle.smoke is not None:
        sources.append(("NEW_POSTER_SMOKE", [json.loads(r["operators_json"]) for r in bundle.smoke.estimates]))
    if not sources:
        raise MissingEvidenceError("no estimated plans available")
    rows = []
    for status, per_key in sources:
        counts: Counter = Counter()
        for ops in per_key:
            counts.update(set(ops))
        for op, n in counts.most_common():
            rows.append({"evidence_status": status, "operator": op, "keys_with_operator": n, "share": n / len(per_key)})
    return rows


def smoke_resource_table(smoke: SmokeEvidence) -> list[dict[str, Any]]:
    per_key: dict[str, dict[str, Any]] = defaultdict(lambda: {"spill_runs": 0, "read_runs": 0, "worker_mismatch_runs": 0,
                                                              "max_workers_launched": 0, "measured_runs": 0})
    for r in smoke.executions:
        if r["run_purpose"] != "measured":
            continue
        entry = per_key[r["modeling_key"]]
        entry["measured_runs"] += 1
        entry["spill_runs"] += r["spill_detected"] == "true"
        entry["read_runs"] += bool(r["shared_read_blocks"]) and int(r["shared_read_blocks"]) > 0
        if r["workers_launched"]:
            entry["max_workers_launched"] = max(entry["max_workers_launched"], int(r["workers_launched"]))
        if r["workers_planned"] and r["workers_launched"] and int(r["workers_launched"]) < int(r["workers_planned"]):
            entry["worker_mismatch_runs"] += 1
    family = {row["modeling_key"]: (row["family"], row["configuration_name"], row["scale_factor"]) for row in smoke.labels}
    return [{"modeling_key": k, "family": family[k][0], "configuration": family[k][1], "scale_factor": family[k][2], **v}
            for k, v in sorted(per_key.items(), key=lambda kv: family[kv[0]])]


def high_runtime_feasibility(bundle: EvidenceBundle) -> list[dict[str, Any]]:
    rows = []
    if bundle.pilot is not None:
        for item in bundle.pilot.audit["high_runtime_classification"].values():
            rows.append({"evidence_status": "RECOMPUTED_PILOT", "threshold_ms": item["threshold_ms"],
                         "positives": item["n_positive"], "keys": item["n_total"], "status": item["status"],
                         "reason": item["reason"]})
    if bundle.smoke is not None:
        medians = [float(r["median_ms"]) for r in bundle.smoke.labels if r["status"] == "complete"]
        censored = sum(1 for r in bundle.smoke.labels if r["status"] == "right_censored")
        for threshold in bundle.smoke.validation["protocol"]["high_runtime_thresholds_ms"]:
            positives = sum(1 for v in medians if v >= threshold) + censored  # censored keys exceed the timeout
            rows.append({"evidence_status": "NEW_POSTER_SMOKE", "threshold_ms": threshold, "positives": positives,
                         "keys": len(bundle.smoke.labels), "status": "NOT_YET_EVALUABLE",
                         "reason": "smoke run is a protocol demonstration; no classifier is trained or reported"})
    if not rows:
        raise MissingEvidenceError("no pilot or smoke evidence available")
    return rows


def claim_table(bundle: EvidenceBundle) -> list[dict[str, Any]]:
    from query_cost_predictor.reporting.builder import evidence_values

    values = evidence_values(bundle)
    positives = None
    if "pilot.n_at_least_10s" in values or "smoke.n_at_least_10s" in values:
        positives = int(values.get("pilot.n_at_least_10s") or 0) + int(values.get("smoke.n_at_least_10s") or 0)
    return [c.as_dict() for c in render_claims(load_claim_specs(), values, positives_10s=positives)]
