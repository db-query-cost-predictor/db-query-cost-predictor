"""Rebuild runtime labels and A–D features from raw evidence.

Derived outputs are versioned and content-addressed:

    QCP_DATA_ROOT/derived/poster_smoke/<manifest>/<label_version>/<inputs_hash>/

``inputs_hash`` covers the manifest file and every raw JSONL file. Rebuilding
from unchanged raw evidence must reproduce byte-identical files; if an output
folder already exists, the rebuild is written to a temporary folder and
compared file by file (a difference is an integrity failure). Raw evidence is
only read, never modified.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from query_cost_predictor import POSTER_FEATURE_VERSION, POSTER_LABEL_VERSION
from query_cost_predictor.contract import assert_feature_matrix_allowed, default_registry
from query_cost_predictor.errors import EvidenceIntegrityError, MissingEvidenceError
from query_cost_predictor.evidence import EvidenceLedger, raw_files, read_raw_file
from query_cost_predictor.hashing import canonical_json, sha256_bytes, sha256_file, sha256_json
from query_cost_predictor.labels import MeasuredRun, build_runtime_label
from query_cost_predictor.paths import data_root, ensure_dir
from query_cost_predictor.plans import ESTIMATE_FEATURE_NAMES, estimate_features, operator_list
from query_cost_predictor.smoke_collector import key_state, raw_session_dir
from query_cost_predictor.smoke_manifest import current_manifest_id, load_manifest
from query_cost_predictor.sqltext import SQL_FEATURE_NAMES, sql_structure_features

FEATURE_COLUMNS: tuple[str, ...] = tuple(SQL_FEATURE_NAMES) + tuple(ESTIMATE_FEATURE_NAMES) + ("snap_scale_factor", "cfg_work_mem_kb")
KEY_COLUMNS = ["modeling_key", "execution_order", "template_id", "semantic_group_id", "family", "form", "rewrite_source",
               "rewrite_pair_id", "workload", "snapshot_name", "scale_factor", "configuration_name", "snapshot_id",
               "config_id", "query_instance_id", "sql_sha256", "literal_fingerprint", "parameters_json", "key_state"]
EXECUTION_COLUMNS = ["modeling_key", "repeat_no", "run_purpose", "status", "is_censored", "censor_lower_bound_ms",
                     "timeout_ms", "execution_time_ms", "planning_time_ms", "client_elapsed_ms", "actual_rows",
                     "workers_planned", "workers_launched", "shared_hit_blocks", "shared_read_blocks",
                     "temp_read_blocks", "temp_written_blocks", "io_read_time_ms", "spill_detected",
                     "plan_shape_sha256", "settings_json", "sqlstate", "error_class", "run_id", "session_id",
                     "raw_record_sha256"]
LABEL_COLUMNS = ["modeling_key", "template_id", "semantic_group_id", "family", "configuration_name", "scale_factor",
                 "status", "measured_repetitions_planned", "n_successful", "n_censored", "n_error", "median_ms",
                 "mad_ms", "cv", "p90_ms", "min_ms", "max_ms", "censor_lower_bound_ms", "timeout_ms",
                 "has_censored_runs", "probe_status", "label_version"]
ESTIMATE_COLUMNS = ["modeling_key", "est_total_cost", "est_startup_cost", "est_plan_rows", "est_plan_width",
                    "est_node_count", "est_workers_planned", "plan_sha256", "plan_shape_sha256", "operators_json",
                    "settings_json"]
OUTPUT_FILES = ("keys.csv", "executions.csv", "labels.csv", "estimates.csv", "features.csv", "derived_manifest.json")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def csv_bytes(columns: list[str], rows: Iterable[dict[str, Any]]) -> bytes:
    """Deterministic CSV (fixed column order, repr floats, LF line endings)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(col)) for col in columns])
    return buffer.getvalue().encode("utf-8")


def _work_mem_kb(manifest: dict[str, Any], configuration: str) -> float:
    entry = manifest["configurations"][configuration]["work_mem"]
    if entry["unit"] != "kB":
        raise EvidenceIntegrityError(f"unexpected work_mem unit {entry['unit']!r}")
    return float(entry["setting"])


def build_tables(manifest: dict[str, Any], ledger: EvidenceLedger, *, label_version: str = POSTER_LABEL_VERSION,
                 feature_version: str = POSTER_FEATURE_VERSION) -> dict[str, list[dict[str, Any]]]:
    """Pure transformation from manifest + raw ledger to derived tables."""
    protocol = manifest["protocol"]
    planned = int(protocol["measured_repetitions"])
    timeout_ms = int(protocol["statement_timeout_ms"])
    entries = sorted(manifest["entries"], key=lambda e: e["modeling_key"])
    keys, executions, labels, estimates, features = [], [], [], [], []
    for entry in entries:
        mk = entry["modeling_key"]
        keys.append({**{c: entry.get(c) for c in KEY_COLUMNS if c in entry},
                     "parameters_json": canonical_json(entry["parameters"]),
                     "key_state": key_state(ledger, mk, planned)})
        run_records = sorted((r for (k, _), r in ledger.executions.items() if k == mk),
                             key=lambda r: int(r["payload"]["repeat_no"]))
        for record in run_records:
            p = record["payload"]
            metrics = p.get("metrics") or {}
            executions.append({
                **{c: p.get(c) for c in ("modeling_key", "repeat_no", "run_purpose", "status", "is_censored",
                                         "censor_lower_bound_ms", "timeout_ms", "execution_time_ms", "planning_time_ms",
                                         "client_elapsed_ms", "plan_shape_sha256", "sqlstate", "error_class", "run_id")},
                **{c: metrics.get(c) for c in ("actual_rows", "workers_planned", "workers_launched", "shared_hit_blocks",
                                               "shared_read_blocks", "temp_read_blocks", "temp_written_blocks",
                                               "io_read_time_ms", "spill_detected")},
                "settings_json": canonical_json(p.get("settings_block")) if p.get("settings_block") is not None else None,
                "session_id": record["session_id"],
                "raw_record_sha256": record["record_sha256"],
            })
        measured = [MeasuredRun(int(p["repeat_no"]), p["status"], p.get("execution_time_ms"))
                    for p in ledger.measured_runs(mk)]
        probe = ledger.probe(mk)
        label = build_runtime_label(measured, planned_repetitions=planned, timeout_ms=timeout_ms,
                                    label_version=label_version, probe_status=probe["status"] if probe else None)
        labels.append({**label.as_dict(), "modeling_key": mk, "template_id": entry["template_id"],
                       "semantic_group_id": entry["semantic_group_id"], "family": entry["family"],
                       "configuration_name": entry["configuration_name"], "scale_factor": entry["scale_factor"]})
        estimate_record = ledger.estimates.get(mk)
        if estimate_record is not None:
            p = estimate_record["payload"]
            estimates.append({"modeling_key": mk, **p["scalars"], "plan_sha256": p["plan_sha256"],
                              "plan_shape_sha256": p["plan_shape_sha256"],
                              "operators_json": canonical_json(operator_list(p["plan_json"])),
                              "settings_json": canonical_json(p["settings_block"])})
            row = {"modeling_key": mk}
            row.update(sql_structure_features(entry["sql_text"]))
            row.update(estimate_features(p["plan_json"]))
            row["snap_scale_factor"] = float(entry["scale_factor"])
            row["cfg_work_mem_kb"] = _work_mem_kb(manifest, entry["configuration_name"])
            features.append(row)
    return {"keys": keys, "executions": executions, "labels": labels, "estimates": estimates, "features": features}


def summarize(tables: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    labels = tables["labels"]
    statuses = [row["status"] for row in labels]
    return {
        "keys": len(tables["keys"]),
        "labeled_keys": statuses.count("complete") + statuses.count("right_censored"),
        "complete": statuses.count("complete"),
        "right_censored": statuses.count("right_censored"),
        "failed": statuses.count("failed"),
        "incomplete": statuses.count("incomplete"),
        "semantic_groups": len({row["semantic_group_id"] for row in labels}),
        "templates": len({row["template_id"] for row in labels}),
        "measured_executions": sum(1 for row in tables["executions"] if row["run_purpose"] == "measured"),
        "probe_executions": sum(1 for row in tables["executions"] if row["run_purpose"] == "probe"),
        "estimates": len(tables["estimates"]),
    }


def render_outputs(tables: dict[str, list[dict[str, Any]]], inputs: dict[str, Any], label_version: str,
                   feature_version: str) -> dict[str, bytes]:
    """All derived files as bytes (deterministic; no timestamps)."""
    assert_feature_matrix_allowed(list(FEATURE_COLUMNS))
    files = {
        "keys.csv": csv_bytes(KEY_COLUMNS, tables["keys"]),
        "executions.csv": csv_bytes(EXECUTION_COLUMNS, tables["executions"]),
        "labels.csv": csv_bytes(LABEL_COLUMNS, tables["labels"]),
        "estimates.csv": csv_bytes(ESTIMATE_COLUMNS, tables["estimates"]),
        "features.csv": csv_bytes(["modeling_key", *FEATURE_COLUMNS], tables["features"]),
    }
    manifest = {
        "label_version": label_version,
        "feature_version": feature_version,
        "feature_columns": list(FEATURE_COLUMNS),
        "inputs": inputs,
        "counts": summarize(tables),
        "outputs": {name: sha256_bytes(data) for name, data in sorted(files.items())},
        "note": "Derived from raw evidence only; rebuild with smoke-derive. Labels use measured runs only.",
    }
    files["derived_manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return files


def write_outputs(files: dict[str, bytes], target: Path) -> str:
    """Write into ``target`` (new) or verify a byte-identical rebuild (existing)."""
    ensure_dir(target.parent)
    temp = target.parent / f"{target.name}.tmp-{uuid.uuid4().hex[:8]}"
    temp.mkdir()
    for name, data in files.items():
        (temp / name).write_bytes(data)
    if not target.exists():
        temp.rename(target)
        return "written"
    differing = [n for n in files if not (target / n).is_file() or sha256_file(target / n) != sha256_file(temp / n)]
    if differing:
        raise EvidenceIntegrityError(f"non-deterministic rebuild: {differing} differ (kept {temp} for inspection)")
    shutil.rmtree(temp)
    return "identical_rebuild_verified"


def derived_root(manifest_id: str, label_version: str = POSTER_LABEL_VERSION) -> Path:
    return data_root() / "derived" / "poster_smoke" / manifest_id[3:19] / label_version


def load_ledger_for(protocol_version: str) -> tuple[EvidenceLedger, list[dict[str, Any]], list[Path]]:
    files = raw_files(raw_session_dir(protocol_version))
    if not files:
        raise MissingEvidenceError("no raw poster-smoke evidence; run 05_run_poster_smoke.ps1 -Step Collect")
    records = [record for path in files for record in read_raw_file(path)]
    return EvidenceLedger.from_records(records), records, files


def derive(manifest_id: str | None, *, write_db: bool = True, label_version: str = POSTER_LABEL_VERSION,
           feature_version: str = POSTER_FEATURE_VERSION) -> dict[str, Any]:
    mid = current_manifest_id(manifest_id)
    manifest, manifest_path = load_manifest(mid)
    ledger, _, files = load_ledger_for(manifest["protocol"]["protocol_version"])
    inputs = {
        "manifest": {"manifest_id": mid, "sha256": sha256_file(manifest_path)},
        "raw_files": [{"name": p.name, "sha256": sha256_file(p)} for p in files],
    }
    inputs_sha = sha256_json(inputs)
    tables = build_tables(manifest, ledger, label_version=label_version, feature_version=feature_version)
    outputs = render_outputs(tables, inputs, label_version, feature_version)
    root = derived_root(mid, label_version)
    target = root / inputs_sha[:16]
    write_status = write_outputs(outputs, target)
    (root / "LATEST.txt").write_text(inputs_sha[:16] + "\n", encoding="utf-8")
    (target.parent / f"{target.name}.build_log.json").write_text(json.dumps({
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "write_status": write_status,
        "inputs_sha256": inputs_sha}, indent=2), encoding="utf-8")

    counts = summarize(tables)
    release_id = f"poster_smoke:{mid[3:19]}:{label_version}:{inputs_sha[:16]}"
    if write_db:
        _write_database(mid, tables, inputs_sha, release_id, label_version, feature_version, target, counts)
    return {
        "status": "PASS",
        "derived_dir": str(target),
        "summary": {"derived_dir": str(target), "write_status": write_status, "release_id": release_id,
                    "database_written": write_db, **counts},
    }


def _write_database(mid: str, tables: dict[str, list[dict[str, Any]]], inputs_sha: str, release_id: str,
                    label_version: str, feature_version: str, target: Path, counts: dict[str, int]) -> None:
    from psycopg.types.json import Jsonb

    from query_cost_predictor.db import connect_evidence_writer

    registry = default_registry()
    keys = [row["modeling_key"] for row in tables["keys"]]
    now = datetime.now(timezone.utc)
    with connect_evidence_writer("qcp:smoke-derive") as ev:
        ev.execute("DELETE FROM qcp.runtime_label WHERE label_version = %s AND modeling_key = ANY(%s)", (label_version, keys))
        for row in tables["labels"]:
            ev.execute(
                "INSERT INTO qcp.runtime_label (modeling_key, label_version, status, measured_repetitions_planned, "
                "n_successful, n_censored, n_error, median_ms, mad_ms, cv, p90_ms, min_ms, max_ms, censor_lower_bound_ms, "
                "timeout_ms, has_censored_runs, probe_status, inputs_sha256, built_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (row["modeling_key"], label_version, row["status"], row["measured_repetitions_planned"],
                 row["n_successful"], row["n_censored"], row["n_error"], row["median_ms"], row["mad_ms"], row["cv"],
                 row["p90_ms"], row["min_ms"], row["max_ms"], row["censor_lower_bound_ms"], row["timeout_ms"],
                 row["has_censored_runs"], row["probe_status"], inputs_sha, now),
            )
        ev.execute("DELETE FROM qcp.feature_record WHERE feature_version = %s AND modeling_key = ANY(%s)", (feature_version, keys))
        for row in tables["features"]:
            for name in FEATURE_COLUMNS:
                spec = registry.get(name)
                assert spec is not None and spec.is_model_input
                ev.execute(
                    "INSERT INTO qcp.feature_record (modeling_key, feature_version, feature_name, availability_class, "
                    "value_numeric, inputs_sha256, built_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (row["modeling_key"], feature_version, name, spec.classes[0], float(row[name]), inputs_sha, now),
                )
        ev.execute(
            "INSERT INTO qcp.dataset_release (release_id, release_kind, manifest_id, label_version, feature_version, "
            "inputs_sha256, key_count, labeled_key_count, group_count, template_count, measured_execution_count, status, "
            "files, created_at, updated_at) VALUES (%s, 'poster_smoke', %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s) "
            "ON CONFLICT (release_id) DO UPDATE SET files = EXCLUDED.files, updated_at = EXCLUDED.updated_at",
            (release_id, mid, label_version, feature_version, inputs_sha, counts["keys"], counts["labeled_keys"],
             counts["semantic_groups"], counts["templates"], counts["measured_executions"],
             Jsonb({name: sha256_file(target / name) for name in OUTPUT_FILES}), now, now),
        )
        ev.commit()


def latest_derived_dir(manifest_id: str, label_version: str = POSTER_LABEL_VERSION) -> Path:
    root = derived_root(manifest_id, label_version)
    pointer = root / "LATEST.txt"
    if not pointer.is_file():
        raise MissingEvidenceError("no derived smoke evidence; run 05_run_poster_smoke.ps1 -Step Derive")
    target = root / pointer.read_text(encoding="utf-8").strip()
    if not target.is_dir():
        raise MissingEvidenceError(f"derived folder {target} is missing; re-run -Step Derive")
    return target
