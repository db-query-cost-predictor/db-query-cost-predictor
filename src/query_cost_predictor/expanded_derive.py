"""Rebuild expanded-release labels, features and splits from raw evidence.

Reuses :func:`query_cost_predictor.smoke_derive.build_tables` (measured runs
only, A-D features through the availability contract) and adds the release
columns:

* ``observation_status``: complete | right_censored | failed | incomplete | not_attempted;
* the execution-time censoring bound from :func:`labels.exec_censor_lower_bound`
  (the statement timeout also counts parsing and planning) next to the raw
  statement-level bound (= the timeout);
* frozen high-runtime classes at 1 s and 10 s (positive / negative /
  indeterminate / unlabeled; a failure is never a positive);
* the failure class of failed keys; the split assignment from the manifest.

Outputs are content-addressed under
``QCP_DATA_ROOT/derived/expanded/<manifest>/<label_version>/<inputs_hash>/`` and a
rebuild from unchanged raw evidence must be byte-identical.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from query_cost_predictor import EXPANDED_FEATURE_VERSION, EXPANDED_LABEL_VERSION
from query_cost_predictor.contract import assert_feature_matrix_allowed, default_registry
from query_cost_predictor.errors import EvidenceIntegrityError
from query_cost_predictor.evidence import EvidenceLedger
from query_cost_predictor.expanded_manifest import KIND, MANIFEST_KIND
from query_cost_predictor.hashing import canonical_json, sha256_bytes, sha256_file, sha256_json
from query_cost_predictor.labels import (
    FAILURE_NONE,
    HR_POSITIVE,
    RUN_ERROR,
    STATUS_CENSORED,
    STATUS_COMPLETE,
    STATUS_FAILED,
    exec_censor_lower_bound,
    failure_class,
    high_runtime_class,
)
from query_cost_predictor.smoke_derive import (
    ESTIMATE_COLUMNS,
    EXECUTION_COLUMNS,
    FEATURE_COLUMNS,
    KEY_COLUMNS,
    LABEL_COLUMNS,
    build_tables,
    csv_bytes,
    derived_root,
    latest_derived_dir,
    load_ledger_for,
    write_outputs,
)
from query_cost_predictor.smoke_manifest import current_manifest_id, load_manifest

RELEASE_COLUMNS = ["component", "structure_family", "output_shape", "index_variant", "parameter_regime",
                   "split_role", "fold_group", "fold_family", "fold_seen_template"]
EXPANDED_KEY_COLUMNS = KEY_COLUMNS + RELEASE_COLUMNS + ["in_sample"]
EXPANDED_LABEL_COLUMNS = LABEL_COLUMNS + ["snapshot_name", "query_instance_id"] + RELEASE_COLUMNS + [
    "observation_status", "statement_censor_bound_ms", "exec_censor_lower_bound_ms", "censor_bound_basis",
    "hr_1s_class", "hr_10s_class", "failure_class", "first_error_sqlstate", "estimate_planning_time_ms"]
SPLIT_COLUMNS = ["modeling_key", "semantic_group_id", "structure_family", "query_instance_id", "template_id",
                 "split_role", "fold_group", "fold_family", "fold_seen_template"]
OUTPUT_FILES = ("keys.csv", "executions.csv", "labels.csv", "estimates.csv", "features.csv", "splits.csv",
                "derived_manifest.json")
THRESHOLD_COLUMNS = {1000.0: "hr_1s_class", 10000.0: "hr_10s_class"}


def _planning_times(ledger: EvidenceLedger, modeling_key: str) -> list[float | None]:
    values: list[float | None] = []
    estimate = ledger.estimates.get(modeling_key)
    if estimate is not None:
        values.append(estimate["payload"].get("planning_time_ms"))
    values += [run.get("planning_time_ms") for run in ledger.runs_for(modeling_key)]
    return values


def _first_error(ledger: EvidenceLedger, modeling_key: str) -> dict[str, Any] | None:
    return next((run for run in ledger.runs_for(modeling_key) if run["status"] == RUN_ERROR), None)


def build_expanded_tables(manifest: dict[str, Any], ledger: EvidenceLedger) -> dict[str, list[dict[str, Any]]]:
    """Pure transformation: manifest + raw ledger -> expanded derived tables."""
    if manifest.get("manifest_kind") != MANIFEST_KIND:
        raise EvidenceIntegrityError("not an expanded-release manifest")
    release = manifest["release"]
    thresholds = tuple(float(v) for v in release["frozen_high_runtime_thresholds_ms"])
    if thresholds != tuple(float(v) for v in manifest["protocol"]["high_runtime_thresholds_ms"]) or \
            set(thresholds) != set(THRESHOLD_COLUMNS):
        raise EvidenceIntegrityError("high-runtime thresholds differ from the frozen 1 s / 10 s definition")
    timeout_ms = int(manifest["protocol"]["statement_timeout_ms"])
    margin = float(release["censor_margin_ms"])
    fallback = float(release["planning_fallback_ms"])
    tables = build_tables(manifest, ledger, label_version=EXPANDED_LABEL_VERSION, feature_version=EXPANDED_FEATURE_VERSION)
    entries = {e["modeling_key"]: e for e in manifest["entries"]}
    sample = set(manifest["sample"]["keys"])

    for row in tables["keys"]:
        entry = entries[row["modeling_key"]]
        row.update({c: entry.get(c) for c in RELEASE_COLUMNS})
        row["in_sample"] = row["modeling_key"] in sample
    key_state = {row["modeling_key"]: row["key_state"] for row in tables["keys"]}

    for row in tables["labels"]:
        mk = row["modeling_key"]
        entry = entries[mk]
        row.update({c: entry.get(c) for c in RELEASE_COLUMNS})
        row["snapshot_name"] = entry["snapshot_name"]
        row["query_instance_id"] = entry["query_instance_id"]
        status = row["status"]
        row["observation_status"] = "not_attempted" if key_state[mk] == "not_started" else status
        row["statement_censor_bound_ms"] = None
        row["exec_censor_lower_bound_ms"] = None
        row["censor_bound_basis"] = None
        if status == STATUS_CENSORED:
            bound, basis = exec_censor_lower_bound(timeout_ms, _planning_times(ledger, mk), margin_ms=margin,
                                                   fallback_planning_ms=fallback)
            row["statement_censor_bound_ms"] = float(timeout_ms)
            row["censor_lower_bound_ms"] = bound  # the label's bound refers to execution time
            row["exec_censor_lower_bound_ms"] = bound
            row["censor_bound_basis"] = basis
        for threshold, column in THRESHOLD_COLUMNS.items():
            row[column] = high_runtime_class(status, row["median_ms"], row["censor_lower_bound_ms"], threshold)
        error = _first_error(ledger, mk)
        if status == STATUS_FAILED:
            row["failure_class"] = failure_class(error.get("sqlstate"), error.get("error_class")) if error else "other_error"
            row["first_error_sqlstate"] = error.get("sqlstate") if error else None
        else:
            row["failure_class"] = FAILURE_NONE
            row["first_error_sqlstate"] = None
        estimate = ledger.estimates.get(mk)
        row["estimate_planning_time_ms"] = estimate["payload"].get("planning_time_ms") if estimate else None

    tables["splits"] = [{c: entries[mk].get(c) for c in SPLIT_COLUMNS}
                        for mk in sorted(entries)]
    return tables


def expanded_counts(tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    labels = tables["labels"]
    observation = Counter(row["observation_status"] for row in labels)
    usable = [row for row in labels if row["status"] in (STATUS_COMPLETE, STATUS_CENSORED)]
    development_usable = [row for row in usable if row["split_role"] == "development"]
    counts: dict[str, Any] = {
        "proposed_keys": len(labels),
        "observation_status": dict(sorted(observation.items())),
        "usable_keys": len(usable),
        "development_usable_keys": len(development_usable),
        "semantic_groups_with_usable_keys": len({row["semantic_group_id"] for row in usable}),
        "templates_with_usable_keys": len({row["template_id"] for row in usable}),
        "measured_executions": sum(1 for row in tables["executions"] if row["run_purpose"] == "measured"),
        "probe_executions": sum(1 for row in tables["executions"] if row["run_purpose"] == "probe"),
        "estimates": len(tables["estimates"]),
        "failure_classes": dict(sorted(Counter(row["failure_class"] for row in labels
                                               if row["status"] == STATUS_FAILED).items())),
    }
    for column in THRESHOLD_COLUMNS.values():
        counts[column] = dict(sorted(Counter(row[column] for row in labels).items()))
        counts[f"{column}_development_positive_groups"] = len(
            {row["semantic_group_id"] for row in development_usable if row[column] == HR_POSITIVE})
    return counts


def render_expanded_outputs(tables: dict[str, list[dict[str, Any]]], inputs: dict[str, Any],
                            manifest: dict[str, Any]) -> dict[str, bytes]:
    """All derived files as bytes (deterministic: fixed columns, sorted rows, no timestamps)."""
    assert_feature_matrix_allowed(list(FEATURE_COLUMNS))
    files = {
        "keys.csv": csv_bytes(EXPANDED_KEY_COLUMNS, tables["keys"]),
        "executions.csv": csv_bytes(EXECUTION_COLUMNS, tables["executions"]),
        "labels.csv": csv_bytes(EXPANDED_LABEL_COLUMNS, tables["labels"]),
        "estimates.csv": csv_bytes(ESTIMATE_COLUMNS, tables["estimates"]),
        "features.csv": csv_bytes(["modeling_key", *FEATURE_COLUMNS], tables["features"]),
        "splits.csv": csv_bytes(SPLIT_COLUMNS, tables["splits"]),
    }
    derived_manifest = {
        "release_name": manifest["release"]["release_name"],
        "label_version": EXPANDED_LABEL_VERSION,
        "feature_version": EXPANDED_FEATURE_VERSION,
        "feature_columns": list(FEATURE_COLUMNS),
        "frozen_high_runtime_thresholds_ms": manifest["release"]["frozen_high_runtime_thresholds_ms"],
        "censoring": {"statement_timeout_ms": manifest["protocol"]["statement_timeout_ms"],
                      "censor_margin_ms": manifest["release"]["censor_margin_ms"],
                      "planning_fallback_ms": manifest["release"]["planning_fallback_ms"],
                      "rule": "execution-time lower bound = timeout - max observed planning time - margin"},
        "inputs": inputs,
        "counts": expanded_counts(tables),
        "outputs": {name: sha256_bytes(data) for name, data in sorted(files.items())},
        "note": "Derived from raw evidence only; rebuild with expanded-derive. Labels use measured runs only; "
                "a proposed key is not a usable label until its observation_status is complete or right_censored.",
    }
    files["derived_manifest.json"] = (json.dumps(derived_manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return files


def derive_expanded(manifest_id: str | None, *, write_db: bool = True) -> dict[str, Any]:
    mid = current_manifest_id(manifest_id, KIND)
    manifest, manifest_path = load_manifest(mid, KIND)
    ledger, _, files = load_ledger_for(manifest["protocol"]["protocol_version"], KIND)
    inputs = {
        "manifest": {"manifest_id": mid, "sha256": sha256_file(manifest_path)},
        "raw_files": [{"name": p.name, "sha256": sha256_file(p)} for p in files],
    }
    inputs_sha = sha256_json(inputs)
    tables = build_expanded_tables(manifest, ledger)
    outputs = render_expanded_outputs(tables, inputs, manifest)
    root = derived_root(mid, EXPANDED_LABEL_VERSION, KIND)
    target = root / inputs_sha[:16]
    write_status = write_outputs(outputs, target)
    (root / "LATEST.txt").write_text(inputs_sha[:16] + "\n", encoding="utf-8")
    (target.parent / f"{target.name}.build_log.json").write_text(json.dumps({
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "write_status": write_status,
        "inputs_sha256": inputs_sha}, indent=2), encoding="utf-8")
    counts = expanded_counts(tables)
    release_id = f"expanded:{mid[3:19]}:{EXPANDED_LABEL_VERSION}:{inputs_sha[:16]}"
    if write_db:
        _write_database(mid, tables, inputs_sha, release_id, target, counts)
    return {"status": "PASS", "derived_dir": str(target),
            "summary": {"derived_dir": str(target), "write_status": write_status, "release_id": release_id,
                        "database_written": write_db, **counts}}


def _write_database(mid: str, tables: dict[str, list[dict[str, Any]]], inputs_sha: str, release_id: str,
                    target: Path, counts: dict[str, Any]) -> None:
    from psycopg.types.json import Jsonb

    from query_cost_predictor.db import connect_evidence_writer

    registry = default_registry()
    keys = [row["modeling_key"] for row in tables["keys"]]
    now = datetime.now(timezone.utc)
    label_rows = [
        (row["modeling_key"], EXPANDED_LABEL_VERSION, row["status"], row["measured_repetitions_planned"],
         row["n_successful"], row["n_censored"], row["n_error"], row["median_ms"], row["mad_ms"], row["cv"],
         row["p90_ms"], row["min_ms"], row["max_ms"], row["censor_lower_bound_ms"], row["timeout_ms"],
         row["has_censored_runs"], row["probe_status"], inputs_sha, now)
        for row in tables["labels"]]
    feature_rows = []
    for row in tables["features"]:
        for name in FEATURE_COLUMNS:
            spec = registry.get(name)
            if spec is None or not spec.is_model_input:
                raise EvidenceIntegrityError(f"feature {name} is not a registered A-D model input")
            feature_rows.append((row["modeling_key"], EXPANDED_FEATURE_VERSION, name, spec.classes[0],
                                 float(row[name]), inputs_sha, now))
    with connect_evidence_writer("qcp:expanded-derive") as ev:
        ev.execute("DELETE FROM qcp.runtime_label WHERE label_version = %s AND modeling_key = ANY(%s)",
                   (EXPANDED_LABEL_VERSION, keys))
        with ev.cursor() as cur:
            cur.executemany(
                "INSERT INTO qcp.runtime_label (modeling_key, label_version, status, measured_repetitions_planned, "
                "n_successful, n_censored, n_error, median_ms, mad_ms, cv, p90_ms, min_ms, max_ms, censor_lower_bound_ms, "
                "timeout_ms, has_censored_runs, probe_status, inputs_sha256, built_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", label_rows)
        ev.execute("DELETE FROM qcp.feature_record WHERE feature_version = %s AND modeling_key = ANY(%s)",
                   (EXPANDED_FEATURE_VERSION, keys))
        with ev.cursor() as cur:
            cur.executemany(
                "INSERT INTO qcp.feature_record (modeling_key, feature_version, feature_name, availability_class, "
                "value_numeric, inputs_sha256, built_at) VALUES (%s, %s, %s, %s, %s, %s, %s)", feature_rows)
        ev.execute(
            "INSERT INTO qcp.dataset_release (release_id, release_kind, manifest_id, label_version, feature_version, "
            "inputs_sha256, key_count, labeled_key_count, group_count, template_count, measured_execution_count, status, "
            "files, created_at, updated_at) VALUES (%s, 'expanded_release', %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft', "
            "%s, %s, %s) ON CONFLICT (release_id) DO UPDATE SET files = EXCLUDED.files, updated_at = EXCLUDED.updated_at",
            (release_id, mid, EXPANDED_LABEL_VERSION, EXPANDED_FEATURE_VERSION, inputs_sha, counts["proposed_keys"],
             counts["usable_keys"], counts["semantic_groups_with_usable_keys"], counts["templates_with_usable_keys"],
             counts["measured_executions"], Jsonb({name: sha256_file(target / name) for name in OUTPUT_FILES}), now, now),
        )
        ev.commit()


def latest_expanded_derived_dir(manifest_id: str) -> Path:
    return latest_derived_dir(manifest_id, EXPANDED_LABEL_VERSION, KIND)


def labels_by_key(tables: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    return {row["modeling_key"]: row for row in tables["labels"]}


def canonical_label_view(row: dict[str, Any]) -> str:
    """Comparable projection of one label row (used by validation to prove identical recomputation)."""
    fields = ("status", "median_ms", "censor_lower_bound_ms", "hr_1s_class", "hr_10s_class", "failure_class",
              "observation_status")
    return canonical_json({f: row.get(f) for f in fields})
