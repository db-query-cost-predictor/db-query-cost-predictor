"""Read-only snapshot identity checks; no database initialization or writes."""
from __future__ import annotations
from typing import Any
from psycopg import sql
from rq2_pipeline.database import server_identity
from rq2_pipeline.hashing import sha256_json, snapshot_id

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
