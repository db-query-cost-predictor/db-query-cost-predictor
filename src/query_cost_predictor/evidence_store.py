"""Evidence-database writes (idempotent) and reconciliation from raw JSONL.

Raw JSONL is the source of truth. Every insert here uses ``ON CONFLICT DO
NOTHING``; after reconciliation the caller compares database hashes with the
raw ledger and stops on any disagreement.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from query_cost_predictor.errors import EvidenceIntegrityError
from query_cost_predictor.evidence import EvidenceLedger, read_raw_file
from query_cost_predictor.hashing import sha256_file


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def insert_session(ev: Any, payload: dict[str, Any]) -> None:
    ev.execute(
        "INSERT INTO qcp.collection_session (session_id, session_kind, manifest_id, started_at, host_name, process_id, "
        "python_version, package_version, raw_evidence_path, order_seed) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (session_id) DO NOTHING",
        (payload["session_id"], payload["session_kind"], payload.get("manifest_id"), _ts(payload["started_at"]),
         payload["host_name"], int(payload["process_id"]), payload["python_version"], payload["package_version"],
         payload["raw_evidence_path"], int(payload["order_seed"])),
    )


def insert_estimate(ev: Any, record: dict[str, Any]) -> None:
    p = record["payload"]
    s = p["scalars"]
    ev.execute(
        "INSERT INTO qcp.estimated_plan (modeling_key, session_id, captured_at, explain_options, plan_json, plan_sha256, "
        "plan_shape_sha256, settings_block, est_total_cost, est_startup_cost, est_plan_rows, est_plan_width, "
        "est_node_count, est_workers_planned, raw_record_sha256) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (modeling_key) DO NOTHING",
        (p["modeling_key"], record["session_id"], _ts(p["captured_at"]), p["explain_options"], Jsonb(p["plan_json"]),
         p["plan_sha256"], p["plan_shape_sha256"], Jsonb(p["settings_block"]), s["est_total_cost"], s["est_startup_cost"],
         s["est_plan_rows"], s["est_plan_width"], s["est_node_count"], s["est_workers_planned"], record["record_sha256"]),
    )


_METRIC_COLUMNS = (
    "actual_rows", "workers_planned", "workers_launched", "shared_hit_blocks", "shared_read_blocks",
    "shared_dirtied_blocks", "shared_written_blocks", "temp_read_blocks", "temp_written_blocks", "io_read_time_ms",
    "io_write_time_ms", "wal_records", "wal_fpi", "wal_bytes", "spill_detected",
)


def insert_execution(ev: Any, record: dict[str, Any]) -> None:
    p = record["payload"]
    metrics = p.get("metrics") or {}
    columns = ["run_id", "modeling_key", "repeat_no", "run_purpose", "session_id", "attempt_no", "started_at",
               "finished_at", "status", "is_censored", "censoring_type", "timeout_ms", "censor_lower_bound_ms",
               "execution_time_ms", "planning_time_ms", "client_elapsed_ms", *_METRIC_COLUMNS, "sqlstate",
               "error_message", "settings_block", "plan_json", "plan_sha256", "plan_shape_sha256", "raw_record_sha256"]
    values = [p["run_id"], p["modeling_key"], int(p["repeat_no"]), p["run_purpose"], record["session_id"],
              int(p["attempt_no"]), _ts(p["started_at"]), _ts(p["finished_at"]), p["status"], bool(p["is_censored"]),
              p.get("censoring_type"), int(p["timeout_ms"]), p.get("censor_lower_bound_ms"), p.get("execution_time_ms"),
              p.get("planning_time_ms"), float(p["client_elapsed_ms"])]
    values += [metrics.get(col) for col in _METRIC_COLUMNS]
    values += [p.get("sqlstate"), p.get("error_message"),
               Jsonb(p["settings_block"]) if p.get("settings_block") is not None else None,
               Jsonb(p["plan_json"]) if p.get("plan_json") is not None else None,
               p.get("plan_sha256"), p.get("plan_shape_sha256"), record["record_sha256"]]
    placeholders = ", ".join(["%s"] * len(columns))
    ev.execute(f"INSERT INTO qcp.query_execution ({', '.join(columns)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
               values)


def insert_error(ev: Any, record: dict[str, Any]) -> None:
    p = record["payload"]
    ev.execute(
        "INSERT INTO qcp.collection_error (error_id, session_id, manifest_id, modeling_key, query_instance_id, repeat_no, "
        "attempt_no, phase, error_class, sqlstate, message, is_retryable, occurred_at, raw_record_sha256) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (error_id) DO NOTHING",
        (p["error_id"], record["session_id"], record.get("manifest_id"), p.get("modeling_key"), p.get("query_instance_id"),
         p.get("repeat_no"), p.get("attempt_no"), p["phase"], p["error_class"], p.get("sqlstate"), p["message"],
         bool(p["is_retryable"]), _ts(p["occurred_at"]), record["record_sha256"]),
    )


def insert_heartbeat(ev: Any, record: dict[str, Any]) -> None:
    p = record["payload"]
    ev.execute(
        "INSERT INTO qcp.collection_heartbeat (session_id, emitted_at, state, keys_total, keys_done, keys_complete, "
        "keys_censored, keys_failed, current_modeling_key, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (record["session_id"], _ts(record["recorded_at"]), p["state"], p["keys_total"], p["keys_done"], p["keys_complete"],
         p["keys_censored"], p["keys_failed"], p.get("current_modeling_key"), p.get("note")),
    )


def insert_raw_file(ev: Any, session_id: str, info: dict[str, Any], closed_at: str) -> None:
    ev.execute(
        "INSERT INTO qcp.raw_evidence_file (session_id, file_path, file_sha256, byte_count, record_count, closed_at) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (session_id, file_path) DO NOTHING",
        (session_id, info["path"], info["sha256"], int(info["bytes"]), int(info["record_count"]), _ts(closed_at)),
    )


def registered_raw_files(ev: Any, session_ids: list[str]) -> dict[str, str]:
    rows = ev.execute("SELECT file_path, file_sha256 FROM qcp.raw_evidence_file WHERE session_id = ANY(%s)",
                      (session_ids,)).fetchall()
    return {row[0]: row[1] for row in rows}


def reconcile_raw_files(ev: Any, files: list[Path], closed_at: str) -> dict[str, Any]:
    """Ingest raw records missing from the database; register files a crashed session left unclosed."""
    all_records: list[dict[str, Any]] = []
    closed_by_reconciliation = []
    for path in files:
        records = read_raw_file(path)
        start = next((r for r in records if r["record_type"] == "session_start"), None)
        if start is None:
            raise EvidenceIntegrityError(f"{path.name} has no session_start record")
        insert_session(ev, start["payload"])
        for record in records:
            if record["record_type"] == "estimate":
                insert_estimate(ev, record)
            elif record["record_type"] == "execution":
                insert_execution(ev, record)
            elif record["record_type"] == "error":
                insert_error(ev, record)
        session_id = start["payload"]["session_id"]
        known = registered_raw_files(ev, [session_id])
        if str(path) not in known:
            info = {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size,
                    "record_count": len(records)}
            insert_raw_file(ev, session_id, info, closed_at)
            closed_by_reconciliation.append(path.name)
        elif known[str(path)] != sha256_file(path):
            raise EvidenceIntegrityError(f"{path.name} changed after it was closed (SHA-256 differs from the database)")
        all_records.extend(records)
    ev.commit()
    return {"records": all_records, "closed_by_reconciliation": closed_by_reconciliation}


def verify_database_matches_ledger(ev: Any, ledger: EvidenceLedger, modeling_keys: list[str]) -> None:
    """Every raw terminal execution/estimate must be in the database with the same hash, and vice versa."""
    rows = ev.execute("SELECT modeling_key, repeat_no, raw_record_sha256 FROM qcp.query_execution "
                      "WHERE modeling_key = ANY(%s)", (modeling_keys,)).fetchall()
    db_runs = {(r[0], int(r[1])): r[2] for r in rows}
    raw_runs = {k: v["record_sha256"] for k, v in ledger.executions.items() if k[0] in set(modeling_keys)}
    if db_runs != raw_runs:
        only_db = sorted(set(db_runs) - set(raw_runs))[:3]
        only_raw = sorted(set(raw_runs) - set(db_runs))[:3]
        differing = [k for k in set(db_runs) & set(raw_runs) if db_runs[k] != raw_runs[k]][:3]
        raise EvidenceIntegrityError(f"database/raw execution mismatch: only_db={only_db} only_raw={only_raw} differing={differing}")
    rows = ev.execute("SELECT modeling_key, raw_record_sha256 FROM qcp.estimated_plan WHERE modeling_key = ANY(%s)",
                      (modeling_keys,)).fetchall()
    db_est = {r[0]: r[1] for r in rows}
    raw_est = {k: v["record_sha256"] for k, v in ledger.estimates.items() if k in set(modeling_keys)}
    if db_est != raw_est:
        raise EvidenceIntegrityError("database/raw estimate mismatch")
    ev.commit()
