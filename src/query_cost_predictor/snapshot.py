"""Benchmark snapshot identity and registration.

A snapshot identity is the SHA-256 of: workload, database, scale factor,
server version, schema hash, index-manifest hash, planner-statistics hash and
data hash (per-table SHA-256 of the loaded files, recorded by the loader).
Re-running ANALYZE changes the statistics hash and therefore the snapshot; the
collector re-checks the statistics hash during collection to detect drift.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

from query_cost_predictor.config import load_poster_smoke_config
from query_cost_predictor.db import BENCH_DATABASES, connect_bench_reader, connect_evidence_writer, server_identity
from query_cost_predictor.hashing import sha256_json, snapshot_id
from query_cost_predictor.paths import environment_reports_dir, ensure_dir

TPCH_TABLES = ("region", "nation", "part", "supplier", "partsupp", "customer", "orders", "lineitem")


def _rows(conn: Any, query: str) -> list[list[Any]]:
    return [[None if v is None else (float(v) if isinstance(v, float) else v) for v in row] for row in conn.execute(query)]


def schema_sha256(conn: Any) -> str:
    columns = _rows(conn, "SELECT table_name, column_name, data_type, is_nullable, ordinal_position "
                          "FROM information_schema.columns WHERE table_schema = 'public' "
                          "ORDER BY table_name, ordinal_position")
    constraints = _rows(conn, "SELECT conrelid::regclass::text, conname, contype::text, pg_get_constraintdef(oid) "
                              "FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2")
    return sha256_json({"columns": columns, "constraints": constraints})


def index_manifest_sha256(conn: Any) -> tuple[str, list[list[Any]]]:
    indexes = _rows(conn, "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' "
                          "ORDER BY tablename, indexname")
    return sha256_json(indexes), indexes


def statistics_sha256(conn: Any) -> str:
    stats = _rows(conn, "SELECT tablename, attname, null_frac, avg_width, n_distinct, most_common_vals::text, "
                        "most_common_freqs::text, histogram_bounds::text, correlation "
                        "FROM pg_stats WHERE schemaname = 'public' ORDER BY tablename, attname")
    return sha256_json(stats)


def statistics_coverage(conn: Any) -> dict[str, int]:
    counts = {t: 0 for t in TPCH_TABLES}
    for table, n in conn.execute("SELECT tablename, count(*) FROM pg_stats WHERE schemaname = 'public' GROUP BY tablename"):
        if table in counts:
            counts[table] = int(n)
    return counts


def autovacuum_disabled(conn: Any) -> dict[str, bool]:
    result = {t: False for t in TPCH_TABLES}
    for relname, options in conn.execute(
        "SELECT relname, coalesce(reloptions, '{}') FROM pg_class "
        "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
    ):
        if relname in result:
            result[relname] = "autovacuum_enabled=false" in list(options)
    return result


def row_counts(conn: Any) -> dict[str, int]:
    counts = {}
    for table in TPCH_TABLES:
        row = conn.execute(sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(table))).fetchone()
        counts[table] = int(row[0]) if row else -1
    return counts


def load_manifest_rows(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT table_name, row_count, sha256, scale_factor, dbgen_repo, dbgen_commit "
                        "FROM qcp_meta.load_manifest ORDER BY table_name").fetchall()
    return [{"table_name": r[0], "row_count": int(r[1]), "sha256": r[2], "scale_factor": r[3],
             "dbgen_repo": r[4], "dbgen_commit": r[5]} for r in rows]


def capture_identity(conn: Any, *, workload: str, database_name: str, scale_factor: str) -> dict[str, Any]:
    """Compute every component of a snapshot identity from a read-only connection."""
    server = server_identity(conn)
    index_hash, indexes = index_manifest_sha256(conn)
    manifest = load_manifest_rows(conn)
    identity = {
        "workload": workload,
        "database_name": database_name,
        "scale_factor": scale_factor,
        "server_version_num": server["server_version_num"],
        "schema_sha256": schema_sha256(conn),
        "index_manifest_sha256": index_hash,
        "statistics_sha256": statistics_sha256(conn),
        "data_sha256": sha256_json(manifest),
    }
    conn.rollback()
    return {
        "snapshot_id": snapshot_id(identity),
        "identity": identity,
        "server_version": server["server_version"],
        "indexes": indexes,
        "load_manifest": manifest,
    }


def register_snapshot(scale_factor: str) -> dict[str, Any]:
    cfg = load_poster_smoke_config()
    spec = next((s for s in cfg.snapshots.values() if s.scale_factor == scale_factor), None)
    if spec is None or spec.database != BENCH_DATABASES[scale_factor]:
        raise RuntimeError(f"no snapshot for scale factor {scale_factor} in config/poster_smoke.yaml")
    checks: dict[str, Any] = {}
    with connect_bench_reader(spec.database, "qcp:register-snapshot") as conn:
        counts = row_counts(conn)
        coverage = statistics_coverage(conn)
        autovacuum = autovacuum_disabled(conn)
        conn.rollback()
        captured = capture_identity(conn, workload=spec.workload, database_name=spec.database, scale_factor=scale_factor)
    expected = cfg.expected_row_counts.get(scale_factor, {})
    manifest_counts = {row["table_name"]: row["row_count"] for row in captured["load_manifest"]}
    checks["row_counts_match_expected"] = bool(expected) and counts == expected
    checks["row_counts_match_load_manifest"] = counts == manifest_counts
    checks["all_tables_analyzed"] = all(n > 0 for n in coverage.values())
    checks["autovacuum_disabled_on_all_tables"] = all(autovacuum.values())
    status = "PASS" if all(checks.values()) else "FAIL"
    identity = captured["identity"]
    registered = False
    if status == "PASS":
        with connect_evidence_writer("qcp:register-snapshot") as ev:
            ev.execute(
                "INSERT INTO qcp.benchmark_snapshot (snapshot_id, workload, database_name, scale_factor, server_version, "
                "server_version_num, schema_sha256, index_manifest_sha256, statistics_sha256, data_sha256, "
                "table_row_counts, load_manifest) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (snapshot_id) DO NOTHING",
                (captured["snapshot_id"], identity["workload"], identity["database_name"], scale_factor,
                 captured["server_version"], identity["server_version_num"], identity["schema_sha256"],
                 identity["index_manifest_sha256"], identity["statistics_sha256"], identity["data_sha256"],
                 Jsonb(counts), Jsonb(captured["load_manifest"])),
            )
            ev.commit()
            registered = True
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "snapshot_id": captured["snapshot_id"],
        "registered": registered,
        "identity": identity,
        "server_version": captured["server_version"],
        "row_counts": counts,
        "expected_row_counts": expected,
        "statistics_rows_per_table": coverage,
        "autovacuum_disabled": autovacuum,
        "indexes": captured["indexes"],
        "load_manifest": captured["load_manifest"],
        "checks": checks,
    }
    out = ensure_dir(environment_reports_dir()) / f"snapshot_{spec.database}.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["summary"] = {"status": status, "snapshot_id": captured["snapshot_id"], "database": spec.database,
                         "row_counts": counts, "checks": checks, "report": str(out)}
    return report


def latest_snapshot(ev_conn: Any, database_name: str) -> dict[str, Any] | None:
    row = ev_conn.execute(
        "SELECT snapshot_id, workload, database_name, scale_factor::text, statistics_sha256, captured_at "
        "FROM qcp.benchmark_snapshot WHERE database_name = %s ORDER BY captured_at DESC LIMIT 1",
        (database_name,),
    ).fetchone()
    if row is None:
        return None
    return {"snapshot_id": row[0], "workload": row[1], "database_name": row[2], "scale_factor": row[3],
            "statistics_sha256": row[4], "captured_at": str(row[5])}
