"""Resumable, idempotent poster-smoke collector.

For every modeling key, in the manifest's seeded order:

1. re-validate the SQL as a single read-only statement;
2. plain ``EXPLAIN (SETTINGS, FORMAT JSON)`` → ``estimated_plan`` (class D);
3. one unmeasured probe (``repeat_no = 0``) with the measured EXPLAIN options;
4. up to three measured executions (``repeat_no = 1..3``), stopping early only
   when the label is already determined to be right-censored;
5. every terminal outcome (completed, timeout, error) is written to the
   session's raw JSONL first, then to the evidence database.

Resume: previous session files are re-read, checksum-verified, reconciled into
the database, and completed ``modeling_key + repeat_no`` pairs are skipped. A
resumed key gets an extra warm-up (``resume_warmup``, raw JSONL only) because
the cache state after an interruption is unknown.
"""

from __future__ import annotations

import os
import platform
import socket
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psycopg

from query_cost_predictor import __version__
from query_cost_predictor.db import connect_bench_reader, connect_evidence_writer
from query_cost_predictor.errors import EvidenceIntegrityError
from query_cost_predictor.evidence import EvidenceLedger, RawEvidenceWriter, make_record, raw_files, utc_now
from query_cost_predictor.evidence_store import (
    insert_error,
    insert_estimate,
    insert_execution,
    insert_heartbeat,
    insert_raw_file,
    insert_session,
    reconcile_raw_files,
    verify_database_matches_ledger,
)
from query_cost_predictor.executor import (
    StatementOutcome,
    TransientDatabaseError,
    capture_effective_settings,
    explain_statement,
    run_statement,
)
from query_cost_predictor.hashing import sha256_file, sha256_json
from query_cost_predictor.labels import RUN_COMPLETED, RUN_ERROR, RUN_TIMEOUT, STATUS_INCOMPLETE, label_state
from query_cost_predictor.paths import data_root
from query_cost_predictor.plans import estimate_scalars, execution_metrics, plan_json_hash, plan_shape_hash, settings_block
from query_cost_predictor.safety import validate_read_only_sql
from query_cost_predictor.smoke_manifest import current_manifest_id, load_manifest
from query_cost_predictor.snapshot import statistics_sha256

EXIT_FINISHED, EXIT_FAILED, EXIT_PARTIAL, EXIT_INTERRUPTED = 0, 1, 3, 130


class CollectorError(RuntimeError):
    """Collection must stop (drift, exhausted retries, integrity problem)."""


def raw_session_dir(protocol_version: str) -> Path:
    """One raw directory per protocol version, shared by every poster-smoke manifest.

    Modeling keys do not depend on the manifest, so a rebuilt manifest (for
    example after SF 1 is loaded) reuses the evidence of keys it shares with an
    earlier manifest instead of re-collecting them.
    """
    return data_root() / "raw" / "poster_smoke" / protocol_version


def key_state(ledger: EvidenceLedger, modeling_key: str, planned: int) -> str:
    """not_started | failed | complete | right_censored | incomplete (from raw evidence only)."""
    probe = ledger.probe(modeling_key)
    runs = ledger.measured_runs(modeling_key)
    if probe is None and not runs:
        return "not_started"
    if probe is not None and probe["status"] == RUN_ERROR and not runs:
        return "failed"
    counts = Counter(r["status"] for r in runs)
    return label_state(planned, counts[RUN_COMPLETED], counts[RUN_TIMEOUT], counts[RUN_ERROR])


def execution_payload(entry: dict[str, Any], repeat_no: int, attempt_no: int, outcome: StatementOutcome,
                      timeout_ms: int) -> dict[str, Any]:
    """Build the raw payload for one terminal execution (probe or measured)."""
    payload: dict[str, Any] = {
        "modeling_key": entry["modeling_key"],
        "repeat_no": repeat_no,
        "run_purpose": "probe" if repeat_no == 0 else "measured",
        "run_id": uuid.uuid4().hex,
        "attempt_no": attempt_no,
        "started_at": outcome.started_at,
        "finished_at": outcome.finished_at,
        "status": outcome.status,
        "is_censored": outcome.status == RUN_TIMEOUT,
        "censoring_type": "right_statement_timeout" if outcome.status == RUN_TIMEOUT else None,
        "timeout_ms": timeout_ms,
        "censor_lower_bound_ms": float(timeout_ms) if outcome.status == RUN_TIMEOUT else None,
        "execution_time_ms": None,
        "planning_time_ms": None,
        "client_elapsed_ms": outcome.client_elapsed_ms,
        "metrics": None,
        "settings_block": None,
        "plan_json": None,
        "plan_sha256": None,
        "plan_shape_sha256": None,
        "sqlstate": outcome.sqlstate,
        "error_class": outcome.error_class,
        "error_message": outcome.error_message,
        "watchdog_fired": outcome.watchdog_fired,
    }
    if outcome.status == RUN_COMPLETED and outcome.document is not None:
        metrics = execution_metrics(outcome.document)
        if metrics["execution_time_ms"] is None:
            payload.update(status=RUN_ERROR, error_class="MissingExecutionTime",
                           error_message="EXPLAIN ANALYZE document has no 'Execution Time' (SUMMARY must be ON)")
            return payload
        payload.update(
            execution_time_ms=metrics["execution_time_ms"],
            planning_time_ms=metrics["planning_time_ms"],
            metrics={k: v for k, v in metrics.items() if k not in ("execution_time_ms", "planning_time_ms", "sort_space_types")},
            settings_block=settings_block(outcome.document),
            plan_json=outcome.document,
            plan_sha256=plan_json_hash(outcome.document),
            plan_shape_sha256=plan_shape_hash(outcome.document),
        )
    elif outcome.status == RUN_ERROR and not outcome.error_message:
        payload["error_message"] = "unknown error"
    return payload


class Collector:
    def __init__(self, manifest: dict[str, Any], manifest_path: Path, max_keys: int | None) -> None:
        self.manifest = manifest
        self.manifest_path = manifest_path
        self.manifest_id = manifest["manifest_id"]
        self.protocol = manifest["protocol"]
        self.entries = sorted(manifest["entries"], key=lambda e: e["execution_order"])
        self.max_keys = max_keys
        self.planned = int(self.protocol["measured_repetitions"])
        self.timeout_ms = int(self.protocol["statement_timeout_ms"])
        self.lock_ms = int(self.protocol["lock_timeout_ms"])
        self.grace_ms = int(self.protocol["watchdog_grace_ms"])
        self.max_retries = int(self.protocol.get("max_transient_retries", 3))
        self.session_id = uuid.uuid4().hex
        self.bench: dict[str, Any] = {}
        self.ev: Any = None
        self.writer: RawEvidenceWriter | None = None
        self.ledger = EvidenceLedger()

    # -------------------------------------------------------------------- helpers
    def _record(self, record_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        record = make_record(record_type, payload, session_id=self.session_id, manifest_id=self.manifest_id)
        assert self.writer is not None
        self.writer.write(record)  # evidence first
        return record

    def _bench(self, database: str) -> Any:
        conn = self.bench.get(database)
        if conn is None or conn.closed or conn.broken:
            conn = connect_bench_reader(database, f"qcp:collect:{self.session_id[:8]}")
            self.bench[database] = conn
        return conn

    def _reset_bench(self, database: str) -> None:
        conn = self.bench.pop(database, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _error(self, entry: dict[str, Any] | None, phase: str, error_class: str, message: str, *, retryable: bool,
               repeat_no: int | None = None, attempt_no: int | None = None, sqlstate: str | None = None) -> None:
        payload = {
            "error_id": uuid.uuid4().hex, "phase": phase, "error_class": error_class, "message": message,
            "sqlstate": sqlstate, "is_retryable": retryable, "occurred_at": utc_now(), "repeat_no": repeat_no,
            "attempt_no": attempt_no, "modeling_key": entry["modeling_key"] if entry else None,
            "query_instance_id": entry["query_instance_id"] if entry else None,
        }
        record = self._record("error", payload)
        insert_error(self.ev, record)
        self.ev.commit()

    def _counts(self) -> Counter:
        return Counter(key_state(self.ledger, e["modeling_key"], self.planned) for e in self.entries)

    def _heartbeat(self, state: str, current: str | None = None, note: str | None = None) -> None:
        counts = self._counts()
        payload = {
            "state": state, "keys_total": len(self.entries),
            "keys_done": counts["complete"] + counts["right_censored"] + counts["failed"],
            "keys_complete": counts["complete"], "keys_censored": counts["right_censored"],
            "keys_failed": counts["failed"], "current_modeling_key": current, "note": note,
        }
        record = self._record("heartbeat", payload)
        insert_heartbeat(self.ev, record)
        self.ev.commit()
        print(f"[{record['recorded_at']}] {state}: done {payload['keys_done']}/{payload['keys_total']} "
              f"(complete {payload['keys_complete']}, censored {payload['keys_censored']}, failed {payload['keys_failed']})"
              + (f" - {note}" if note else ""), flush=True)

    def _with_retries(self, entry: dict[str, Any], phase: str, repeat_no: int | None,
                      action: Callable[[Any], StatementOutcome]) -> tuple[int, StatementOutcome]:
        for attempt in range(1, self.max_retries + 1):
            try:
                return attempt, action(self._bench(entry["database"]))
            except (TransientDatabaseError, psycopg.OperationalError) as exc:  # includes failed reconnects
                self._error(entry, phase, "TransientDatabaseError", str(exc), retryable=True,
                            repeat_no=repeat_no, attempt_no=attempt)
                self._reset_bench(entry["database"])
                time.sleep(2 ** attempt)
        raise CollectorError(f"{phase} for {entry['modeling_key']}: transient failures exhausted ({self.max_retries} attempts)")

    # -------------------------------------------------------------------- drift checks
    def check_drift(self, databases: set[str]) -> None:
        snapshots = self.manifest["snapshots"]
        for name, snap in snapshots.items():
            if snap["database"] not in databases:
                continue
            live = statistics_sha256(self._bench(snap["database"]))
            if live != snap["statistics_sha256"]:
                raise CollectorError(f"snapshot drift in {snap['database']}: planner statistics changed; stop and share this message")

    def check_configurations(self) -> None:
        for name, info in self.manifest["configurations"].items():
            conn = self._bench(info["captured_on_database"])
            effective = capture_effective_settings(conn, info["session_settings"], self.lock_ms)
            if sha256_json(effective) != info["effective_settings_sha256"]:
                raise CollectorError(f"configuration drift for {name}: effective settings differ from the manifest")

    # -------------------------------------------------------------------- one key
    def _execute(self, entry: dict[str, Any], options: str) -> Callable[[Any], StatementOutcome]:
        statement = explain_statement(entry["sql_text"], options)

        def action(conn: Any) -> StatementOutcome:
            return run_statement(conn, statement, settings=entry["session_settings"], statement_timeout_ms=self.timeout_ms,
                                 lock_timeout_ms=self.lock_ms, watchdog_grace_ms=self.grace_ms)
        return action

    def _store_execution(self, entry: dict[str, Any], repeat_no: int) -> str:
        attempt, outcome = self._with_retries(entry, "probe" if repeat_no == 0 else "measured", repeat_no,
                                              self._execute(entry, self.protocol["explain_measured_options"]))
        payload = execution_payload(entry, repeat_no, attempt, outcome, self.timeout_ms)
        record = self._record("execution", payload)
        self.ledger.add_execution(record)
        insert_execution(self.ev, record)
        self.ev.commit()
        if payload["status"] == RUN_ERROR:
            self._error(entry, "probe" if repeat_no == 0 else "measured", payload.get("error_class") or "Error",
                        payload["error_message"], retryable=False, repeat_no=repeat_no, attempt_no=attempt,
                        sqlstate=payload.get("sqlstate"))
        return str(payload["status"])

    def process_key(self, entry: dict[str, Any]) -> None:
        key = entry["modeling_key"]
        safety = validate_read_only_sql(entry["sql_text"])
        if safety.sql != entry["sql_text"]:
            raise CollectorError(f"{key}: SQL text changed after normalization; manifest is inconsistent")
        started = time.monotonic()

        if key not in self.ledger.estimates:
            attempt, outcome = self._with_retries(entry, "estimate", None,
                                                  self._execute(entry, self.protocol["explain_estimate_options"]))
            if outcome.status == RUN_COMPLETED and outcome.document is not None:
                payload = {
                    "modeling_key": key, "captured_at": outcome.finished_at,
                    "explain_options": self.protocol["explain_estimate_options"], "plan_json": outcome.document,
                    "plan_sha256": plan_json_hash(outcome.document), "plan_shape_sha256": plan_shape_hash(outcome.document),
                    "settings_block": settings_block(outcome.document), "scalars": estimate_scalars(outcome.document),
                }
                record = self._record("estimate", payload)
                self.ledger.add_estimate(record)
                insert_estimate(self.ev, record)
                self.ev.commit()
            else:
                self._error(entry, "estimate", outcome.error_class or "Error", outcome.error_message or outcome.status,
                            retryable=False, attempt_no=attempt, sqlstate=outcome.sqlstate)

        if not self.ledger.has_run(key, 0):
            if self._store_execution(entry, 0) == RUN_ERROR:
                return
        else:
            attempt, outcome = self._with_retries(entry, "probe", None, self._execute(entry, self.protocol["explain_measured_options"]))
            self._record("resume_warmup", {"modeling_key": key, "status": outcome.status,
                                           "client_elapsed_ms": outcome.client_elapsed_ms, "attempt_no": attempt,
                                           "note": "unmeasured warm-up after resume; never used for labels"})

        for repeat_no in range(1, self.planned + 1):
            if key_state(self.ledger, key, self.planned) != STATUS_INCOMPLETE:
                break
            if self.ledger.has_run(key, repeat_no):
                continue
            if self._store_execution(entry, repeat_no) == RUN_ERROR:
                break

        budget_s = (self.planned + 2) * (self.timeout_ms + self.grace_ms) / 1000.0 + 30.0
        if time.monotonic() - started > budget_s:
            self._heartbeat("stalled", key, f"key exceeded its time budget of {budget_s:.0f}s")

    # -------------------------------------------------------------------- session
    def run(self) -> int:
        raw_dir = raw_session_dir(self.protocol["protocol_version"])
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        raw_path = raw_dir / f"session_{stamp}_{self.session_id[:8]}.jsonl"
        self.ev = connect_evidence_writer(f"qcp:collect:{self.session_id[:8]}")
        exit_code = EXIT_FAILED
        try:
            row = self.ev.execute("SELECT manifest_file_sha256 FROM qcp.collection_manifest WHERE manifest_id = %s",
                                  (self.manifest_id,)).fetchone()
            self.ev.commit()
            if row is None:
                raise CollectorError("manifest is not registered; run 05_run_poster_smoke.ps1 -Step Manifest")
            if row[0] != sha256_file(self.manifest_path):
                raise CollectorError("manifest file differs from the registered manifest (edited?)")

            previous = raw_files(raw_dir)
            reconciled = reconcile_raw_files(self.ev, previous, utc_now())
            self.ledger = EvidenceLedger.from_records(reconciled["records"])
            keys = [e["modeling_key"] for e in self.entries]
            verify_database_matches_ledger(self.ev, self.ledger, keys)

            self.writer = RawEvidenceWriter(raw_path)
            start_payload = {
                "session_id": self.session_id, "session_kind": "poster_smoke_collection", "manifest_id": self.manifest_id,
                "started_at": utc_now(), "host_name": socket.gethostname(), "process_id": os.getpid(),
                "python_version": sys.version.split()[0], "package_version": __version__,
                "raw_evidence_path": str(raw_path), "order_seed": int(self.protocol["order_seed"]),
                "platform": platform.platform(), "previous_session_files": [p.name for p in previous],
                "closed_by_reconciliation": reconciled["closed_by_reconciliation"],
            }
            self._record("session_start", start_payload)
            insert_session(self.ev, start_payload)
            self.ev.commit()

            databases = {e["database"] for e in self.entries}
            self.check_drift(databases)
            self.check_configurations()
            self._heartbeat("started", note=f"resuming with {len(previous)} earlier session file(s)" if previous else None)

            processed = 0
            check_every = int(self.protocol.get("snapshot_check_every_n_keys", 10) or 10)
            for entry in self.entries:
                if key_state(self.ledger, entry["modeling_key"], self.planned) not in ("not_started", STATUS_INCOMPLETE):
                    continue
                if self.max_keys is not None and processed >= self.max_keys:
                    break
                self.process_key(entry)
                processed += 1
                self._heartbeat("running", entry["modeling_key"])
                if processed % check_every == 0:
                    self.check_drift(databases)

            counts = self._counts()
            remaining = counts["not_started"] + counts[STATUS_INCOMPLETE]
            self._heartbeat("finished", note=f"{remaining} key(s) still not determined" if remaining else None)
            exit_code = EXIT_FINISHED if remaining == 0 else EXIT_PARTIAL
        except KeyboardInterrupt:
            if self.writer is not None:
                self._record("interruption", {"note": "interrupted by user; the in-flight execution was not recorded"})
                try:
                    self.ev.rollback()  # the interrupt may have aborted an evidence-database statement
                    self._heartbeat("interrupted")
                except Exception as exc:  # noqa: BLE001 - raw JSONL already holds the interruption record
                    print(f"WARNING: could not record the interruption heartbeat ({exc})", file=sys.stderr)
            exit_code = EXIT_INTERRUPTED
        except Exception as exc:
            if self.writer is not None:
                try:
                    self._heartbeat("failed", note=f"{exc.__class__.__name__}: {exc}")
                except Exception:  # noqa: BLE001 - keep the original failure
                    pass
            print(f"ERROR: {exc.__class__.__name__}: {exc}", file=sys.stderr)
            exit_code = EXIT_FAILED
        finally:
            if self.writer is not None:
                self._record("session_end", {"exit_code": exit_code})
                info = self.writer.close()
                try:
                    insert_raw_file(self.ev, self.session_id, info, utc_now())
                    self.ev.commit()
                except Exception as exc:  # noqa: BLE001 - reconciliation will register the file next time
                    print(f"WARNING: raw file not registered ({exc}); the next session will register it", file=sys.stderr)
                print(f"raw evidence: {info['path']} ({info['record_count']} records, sha256 {info['sha256']})")
            for database in list(self.bench):
                self._reset_bench(database)
            try:
                self.ev.close()
            except Exception:  # noqa: BLE001
                pass
        counts = self._counts()
        print("key states: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        label = {EXIT_FINISHED: "FINISHED", EXIT_PARTIAL: "PARTIAL (re-run to continue)",
                 EXIT_INTERRUPTED: "INTERRUPTED (re-run to resume)", EXIT_FAILED: "FAILED"}[exit_code]
        print(f"SMOKE-COLLECT: {label}")
        return exit_code


def run_collection(manifest_id: str | None, *, max_keys: int | None = None) -> int:
    mid = current_manifest_id(manifest_id)
    manifest, path = load_manifest(mid)
    if manifest.get("manifest_kind") != "poster_smoke":
        raise EvidenceIntegrityError("not a poster-smoke manifest")
    return Collector(manifest, path, max_keys).run()
