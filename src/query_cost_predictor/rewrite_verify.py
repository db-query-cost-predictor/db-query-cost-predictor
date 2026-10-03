"""Manual reference-rewrite verification harness (label: ``MANUAL_REFERENCE_REWRITE``).

No LLM is called. For each hand-written pair from ``config/poster_smoke.yaml``:

1. parse and safety-check both SQL texts (single read-only statement);
2. check the schema preconditions that the written equivalence argument needs;
3. execute both on the same snapshot and compare the results as **exact
   multisets** (duplicates and NULLs retained; ordering compared only when the
   SQL semantics require it);
4. if, and only if, the results are equal: run paired, order-alternated
   ``EXPLAIN ANALYZE`` timings and compute the median speedup
   (original time / rewrite time);
5. recommend only when equivalence passed and the median speedup reaches the
   configured threshold. Anything else is ``DO_NOT_RECOMMEND`` with a reason.

Equal results on a snapshot are evidence, not proof, of equivalence; the
written argument and preconditions are recorded next to every result. This
harness demonstrates the RQ2 verification method; it does not answer RQ2.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import statistics
import sys
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from psycopg.types.json import Jsonb

from query_cost_predictor import __version__
from query_cost_predictor.config import PosterSmokeConfig, RewritePairSpec, load_poster_smoke_config, render_sql
from query_cost_predictor.db import connect_bench_reader, connect_evidence_writer, server_identity
from query_cost_predictor.evidence import RawEvidenceWriter, make_record, utc_now
from query_cost_predictor.evidence_store import insert_raw_file, insert_session
from query_cost_predictor.executor import capture_effective_settings, explain_statement, run_statement
from query_cost_predictor.hashing import config_id, sha256_json, sha256_text
from query_cost_predictor.paths import data_root, ensure_dir, reports_dir
from query_cost_predictor.plans import execution_metrics
from query_cost_predictor.safety import SqlSafetyError, validate_read_only_sql
from query_cost_predictor.smoke_manifest import _read_image_record
from query_cost_predictor.snapshot import latest_snapshot, statistics_sha256

REWRITE_SOURCE = "MANUAL_REFERENCE_REWRITE"
EQUIVALENT = "EQUIVALENT_ON_SNAPSHOT"
NOT_EQUIVALENT = "NOT_EQUIVALENT"
INCONCLUSIVE = "INCONCLUSIVE"
ERROR = "ERROR"


@dataclass(frozen=True)
class MultisetComparison:
    status: str  # EQUIVALENT_ON_SNAPSHOT | NOT_EQUIVALENT | INCONCLUSIVE
    multiset_equal: bool | None
    ordering_status: str
    original_rows: int
    rewrite_rows: int
    detail: str


def _order_key(row: Sequence[Any], columns: Sequence[int]) -> tuple[Any, ...]:
    # PostgreSQL's default ascending order puts NULLs last.
    return tuple((row[c] is None, row[c] if row[c] is not None else 0) for c in columns)


def _is_ordered(rows: list[tuple[Any, ...]], columns: Sequence[int]) -> bool:
    keys = [_order_key(r, columns) for r in rows]
    return all(a <= b for a, b in zip(keys, keys[1:]))


def compare_results(
    original: list[tuple[Any, ...]],
    rewrite: list[tuple[Any, ...]],
    *,
    original_columns: int,
    rewrite_columns: int,
    ordering_required: bool,
    order_by_columns: Sequence[int] = (),
    truncated: bool = False,
) -> MultisetComparison:
    """Exact multiset comparison (duplicates and NULLs retained; values compared with ==)."""
    if truncated:
        return MultisetComparison(INCONCLUSIVE, None, "NOT_CHECKED", len(original), len(rewrite),
                                  "result exceeded the row cap; equivalence not decided")
    if original_columns != rewrite_columns:
        return MultisetComparison(NOT_EQUIVALENT, False, "NOT_CHECKED", len(original), len(rewrite),
                                  f"column count differs ({original_columns} vs {rewrite_columns})")
    equal = Counter(original) == Counter(rewrite)
    if not ordering_required:
        ordering = "NOT_REQUIRED"
    elif order_by_columns:
        ordering = "SATISFIED" if _is_ordered(original, order_by_columns) and _is_ordered(rewrite, order_by_columns) else "VIOLATED"
    else:
        ordering = "SATISFIED" if original == rewrite else "VIOLATED"
    status = EQUIVALENT if equal and ordering in ("NOT_REQUIRED", "SATISFIED") else NOT_EQUIVALENT
    detail = "multisets equal" if equal else "multisets differ (row counts or values)"
    return MultisetComparison(status, equal, ordering, len(original), len(rewrite), detail)


def decide(pair_role: str, equivalence: str, timing_status: str, median_speedup: float | None,
           min_speedup: float) -> tuple[str, str]:
    """Return (recommendation, reason). Refuses unless equivalence passed and the rewrite is faster."""
    if pair_role != "reference_pair":
        return "DO_NOT_RECOMMEND", "negative control: never recommended"
    if equivalence != EQUIVALENT:
        return "DO_NOT_RECOMMEND", f"equivalence check did not pass ({equivalence})"
    if timing_status != "COMPLETE" or median_speedup is None:
        return "DO_NOT_RECOMMEND", f"paired timing incomplete ({timing_status})"
    if median_speedup < min_speedup:
        return "DO_NOT_RECOMMEND", f"median speedup {median_speedup:.3f}x below the {min_speedup:.2f}x threshold"
    return "RECOMMEND", f"equal results on this snapshot and median speedup {median_speedup:.3f}x >= {min_speedup:.2f}x"


class Harness:
    def __init__(self, cfg: PosterSmokeConfig) -> None:
        self.cfg = cfg
        self.vcfg = cfg.rewrite_verification
        self.session_id = uuid.uuid4().hex
        self.writer: RawEvidenceWriter | None = None
        self.ev: Any = None
        self.lock_ms = cfg.protocol.lock_timeout_ms
        self.grace_ms = cfg.protocol.watchdog_grace_ms

    def _run(self, conn: Any, statement: str, settings: dict[str, str], cap: int | None = None) -> Any:
        return run_statement(conn, statement, settings=settings, statement_timeout_ms=self.vcfg.statement_timeout_ms,
                             lock_timeout_ms=self.lock_ms, watchdog_grace_ms=self.grace_ms, fetch_rows_cap=cap)

    def _config(self, conn: Any, name: str) -> tuple[str, dict[str, str]]:
        spec = self.cfg.configurations[name]
        effective = capture_effective_settings(conn, spec.session_settings, self.lock_ms)
        effective_sha = sha256_json(effective)
        server = server_identity(conn)
        cid = config_id(name, effective_sha, server["server_version_num"])
        image, digest, status = _read_image_record()
        self.ev.execute(
            "INSERT INTO qcp.database_configuration (config_id, configuration_name, session_settings, effective_settings, "
            "effective_settings_sha256, server_version, server_version_num, postgres_image, postgres_image_digest, "
            "image_capture_status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (config_id) DO NOTHING",
            (cid, name, Jsonb(spec.session_settings), Jsonb(effective), effective_sha, server["server_version"],
             server["server_version_num"], image, digest, status),
        )
        self.ev.commit()
        return cid, spec.session_settings

    def _timings(self, conn: Any, original_sql: str, rewrite_sql: str, settings: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
        options = self.cfg.protocol.explain_measured_options
        statements = {"original": explain_statement(original_sql, options), "rewrite": explain_statement(rewrite_sql, options)}
        for form in ("original", "rewrite"):
            for _ in range(self.vcfg.warmup_each):
                warm = self._run(conn, statements[form], settings)
                if warm.status != "completed":
                    return f"INCOMPLETE ({form} warm-up {warm.status})", []
        pairs = []
        for index in range(self.vcfg.timing_pairs):
            order = ("original", "rewrite") if index % 2 == 0 else ("rewrite", "original")  # ABBA-style alternation
            times: dict[str, float] = {}
            for form in order:
                outcome = self._run(conn, statements[form], settings)
                if outcome.status != "completed" or outcome.document is None:
                    return f"INCOMPLETE ({form} {outcome.status})", pairs
                times[form] = float(execution_metrics(outcome.document)["execution_time_ms"])
            ratio = times["original"] / max(times["rewrite"], 1e-3)
            pairs.append({"pair_index": index, "order": list(order), "original_ms": times["original"],
                          "rewrite_ms": times["rewrite"], "speedup": ratio})
        return "COMPLETE", pairs

    def verify(self, conn: Any, pair: RewritePairSpec, params: dict[str, Any], snapshot_name: str, snapshot: dict[str, Any],
               cfg_id: str, settings: dict[str, str]) -> dict[str, Any]:
        original_sql = render_sql(pair.original.sql, pair.original.parameter_types, params)
        rewrite_sql = render_sql(pair.rewrite.sql, pair.rewrite.parameter_types, params)
        check: dict[str, Any] = {
            "verification_id": uuid.uuid4().hex, "pair_id": pair.pair_id, "pair_role": pair.role,
            "semantic_group_id": pair.semantic_group_id, "rewrite_source": REWRITE_SOURCE,
            "expected_equivalent": pair.expected_equivalent, "snapshot_name": snapshot_name,
            "scale_factor": self.cfg.snapshots[snapshot_name].scale_factor, "snapshot_id": snapshot["snapshot_id"],
            "config_id": cfg_id, "parameters": params, "original_sql": original_sql, "rewrite_sql": rewrite_sql,
            "original_sql_sha256": sha256_text(original_sql), "rewrite_sql_sha256": sha256_text(rewrite_sql),
            "equivalence_argument": pair.equivalence_argument, "ordering_required": pair.ordering_required,
            "original_row_count": None, "rewrite_row_count": None, "multiset_equal": None, "ordering_status": "NOT_CHECKED",
            "timing_status": "NOT_RUN", "timing_pairs": [], "median_speedup": None,
            "min_speedup_required": self.vcfg.min_speedup_to_recommend,
        }
        try:
            original_sql = validate_read_only_sql(original_sql).sql
            rewrite_sql = validate_read_only_sql(rewrite_sql).sql
            check["safety_status"] = "PASSED_READ_ONLY_SINGLE_STATEMENT"
        except SqlSafetyError as exc:
            check.update(safety_status=f"FAILED: {exc.reason_code}", precondition_status="NOT_CHECKED", equivalence_status=ERROR)
            return self._finish(check)

        failed = []
        for name, sql_text in pair.preconditions:
            outcome = self._run(conn, validate_read_only_sql(sql_text).sql, settings, cap=1)
            if outcome.status != "completed" or not outcome.rows or outcome.rows[0][0] is not True:
                failed.append(name)
        check["precondition_status"] = "PASSED" if not failed else "FAILED: " + ", ".join(failed)
        if failed:
            check["equivalence_status"] = INCONCLUSIVE
            return self._finish(check)

        cap = self.vcfg.max_result_rows
        a = self._run(conn, original_sql, settings, cap=cap)
        b = self._run(conn, rewrite_sql, settings, cap=cap)
        if a.status != "completed" or b.status != "completed":
            check["equivalence_status"] = INCONCLUSIVE if "timeout" in (a.status, b.status) else ERROR
            check["execution_problem"] = {"original": [a.status, a.error_message], "rewrite": [b.status, b.error_message]}
            return self._finish(check)
        comparison = compare_results(a.rows or [], b.rows or [], original_columns=a.column_count or 0,
                                     rewrite_columns=b.column_count or 0, ordering_required=pair.ordering_required,
                                     order_by_columns=pair.order_by_columns, truncated=a.truncated or b.truncated)
        check.update(original_row_count=comparison.original_rows, rewrite_row_count=comparison.rewrite_rows,
                     multiset_equal=comparison.multiset_equal, ordering_status=comparison.ordering_status,
                     equivalence_status=comparison.status, comparison_detail=comparison.detail)
        if comparison.status == EQUIVALENT:
            status, pairs = self._timings(conn, original_sql, rewrite_sql, settings)
            check["timing_status"], check["timing_pairs"] = status, pairs
            if status == "COMPLETE" and pairs:
                check["median_speedup"] = float(statistics.median(p["speedup"] for p in pairs))
        else:
            check["timing_status"] = "SKIPPED_NOT_EQUIVALENT"
        return self._finish(check)

    def _finish(self, check: dict[str, Any]) -> dict[str, Any]:
        recommendation, reason = decide(check["pair_role"], check["equivalence_status"], check["timing_status"],
                                        check["median_speedup"], check["min_speedup_required"])
        check.update(recommendation=recommendation, reason=reason, created_at=utc_now())
        if check["pair_role"] == "negative_control" and check["equivalence_status"] == EQUIVALENT:
            check["method_check"] = "NEGATIVE_CONTROL_NOT_REJECTED"
        record = make_record("rewrite_verification", check, session_id=self.session_id, manifest_id=None)
        assert self.writer is not None
        self.writer.write(record)
        self.ev.execute(
            "INSERT INTO qcp.rewrite_verification (verification_id, session_id, rewrite_pair_id, semantic_group_id, "
            "rewrite_source, pair_role, expected_equivalent, snapshot_id, config_id, parameter_values, original_sql_sha256, "
            "rewrite_sql_sha256, safety_status, precondition_status, original_row_count, rewrite_row_count, multiset_equal, "
            "ordering_required, ordering_status, equivalence_status, timing_status, timing_pairs, median_speedup, "
            "min_speedup_required, recommendation, reason, created_at, raw_record_sha256) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (check["verification_id"], self.session_id, check["pair_id"], check["semantic_group_id"], REWRITE_SOURCE,
             check["pair_role"], check["expected_equivalent"], check["snapshot_id"], check["config_id"],
             Jsonb(check["parameters"]), check["original_sql_sha256"], check["rewrite_sql_sha256"], check["safety_status"],
             check["precondition_status"], check["original_row_count"], check["rewrite_row_count"], check["multiset_equal"],
             check["ordering_required"], check["ordering_status"], check["equivalence_status"], check["timing_status"],
             Jsonb(check["timing_pairs"]), check["median_speedup"], check["min_speedup_required"], check["recommendation"],
             check["reason"], datetime.fromisoformat(check["created_at"]), record["record_sha256"]),
        )
        self.ev.commit()
        print(f"{check['pair_id']} {json.dumps(check['parameters'], sort_keys=True)} SF {check['scale_factor']}: "
              f"{check['equivalence_status']}, timing {check['timing_status']}, "
              f"median speedup {check['median_speedup']}, {check['recommendation']}", flush=True)
        return check

    def run(self) -> dict[str, Any]:
        raw_dir = data_root() / "raw" / "rewrite_verification"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        raw_path = raw_dir / f"session_{stamp}_{self.session_id[:8]}.jsonl"
        checks: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        self.ev = connect_evidence_writer(f"qcp:rewrite:{self.session_id[:8]}")
        try:
            self.writer = RawEvidenceWriter(raw_path)
            start = {"session_id": self.session_id, "session_kind": "rewrite_verification", "manifest_id": None,
                     "started_at": utc_now(), "host_name": socket.gethostname(), "process_id": os.getpid(),
                     "python_version": sys.version.split()[0], "package_version": __version__,
                     "raw_evidence_path": str(raw_path), "order_seed": self.vcfg.order_seed, "platform": platform.platform(),
                     "rewrite_source": REWRITE_SOURCE}
            self.writer.write(make_record("session_start", start, session_id=self.session_id, manifest_id=None))
            insert_session(self.ev, start)
            self.ev.commit()
            for pair in self.cfg.rewrite_pairs:
                for snapshot_name in pair.verification_snapshots:
                    spec = self.cfg.snapshots[snapshot_name]
                    snapshot = latest_snapshot(self.ev, spec.database)
                    self.ev.commit()
                    if snapshot is None:
                        skipped.append({"pair_id": pair.pair_id, "snapshot": snapshot_name, "reason": "snapshot not registered"})
                        continue
                    with connect_bench_reader(spec.database, f"qcp:rewrite:{self.session_id[:8]}") as conn:
                        if statistics_sha256(conn) != snapshot["statistics_sha256"]:
                            raise RuntimeError(f"snapshot drift in {spec.database}; re-register before verifying")
                        cid, settings = self._config(conn, self.vcfg.configuration)
                        for params in pair.original.parameter_sets:
                            checks.append(self.verify(conn, pair, dict(params), snapshot_name, snapshot, cid, settings))
        finally:
            if self.writer is not None:
                self.writer.write(make_record("session_end", {"checks": len(checks)}, session_id=self.session_id, manifest_id=None))
                info = self.writer.close()
                insert_raw_file(self.ev, self.session_id, info, utc_now())
                self.ev.commit()
            self.ev.close()
        negatives = [c for c in checks if c["pair_role"] == "negative_control"]
        errors = [c for c in checks if c["equivalence_status"] == ERROR]
        method_ok = all(c["equivalence_status"] == NOT_EQUIVALENT for c in negatives) and bool(negatives)
        status = "COMPLETED" if checks and not errors and method_ok else "FAILED"
        summary = {
            "evidence_status": "NEW_POSTER_SMOKE", "rewrite_source": REWRITE_SOURCE, "status": status,
            "created_at_utc": utc_now(), "session_id": self.session_id,
            "raw_file": {"path": info["path"], "sha256": info["sha256"]},
            "config": {**asdict(self.vcfg)},
            "checks": [{k: v for k, v in c.items() if k not in ("original_sql", "rewrite_sql")} | {
                "original_sql": c["original_sql"], "rewrite_sql": c["rewrite_sql"]} for c in checks],
            "skipped": skipped,
            "counts": {"checks": len(checks), "equivalence": dict(Counter(c["equivalence_status"] for c in checks)),
                       "recommendations": dict(Counter(c["recommendation"] for c in checks))},
            "method_check": {"negative_controls": len(negatives),
                             "negative_controls_rejected": sum(1 for c in negatives if c["equivalence_status"] == NOT_EQUIVALENT)},
            "note": "Manual reference rewrites demonstrate the verification method. No LLM was used; RQ2 is not answered.",
        }
        out = ensure_dir(reports_dir()) / "rewrite_verification_summary.json"
        out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        summary["summary"] = {"status": status, "report": str(out), **summary["counts"], "method_check": summary["method_check"],
                              "skipped": skipped}
        return summary


def run_verification(config_path: Path | None = None) -> dict[str, Any]:
    return Harness(load_poster_smoke_config(config_path)).run()
