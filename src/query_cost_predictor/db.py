"""PostgreSQL connections (psycopg 3), local-only, PostgreSQL 16-only.

Role layout (names are fixed; passwords come from ``.env``):

* ``<admin>`` — container superuser, used only by ``db-init``.
* ``qcp_bench_owner`` — owns the TPC-H databases (loading only).
* ``qcp_bench_reader`` — **every measured query**: not a superuser, SELECT-only,
  read-only by default; the collector additionally opens READ ONLY transactions
  and rolls them back.
* ``qcp_evidence_owner`` — owns the evidence schema (migrations only).
* ``qcp_evidence_writer`` — inserts evidence rows; cannot change raw evidence
  (append-only triggers) or the schema.
"""

from __future__ import annotations

from typing import Any

import psycopg

from query_cost_predictor.config import DatabaseSettings, database_settings_from_env

BENCH_OWNER_ROLE = "qcp_bench_owner"
BENCH_READER_ROLE = "qcp_bench_reader"
EVIDENCE_OWNER_ROLE = "qcp_evidence_owner"
EVIDENCE_WRITER_ROLE = "qcp_evidence_writer"
EVIDENCE_DATABASE = "qcp_evidence"
BENCH_DATABASES = {"0.1": "tpch_sf0_1", "1": "tpch_sf1"}
REQUIRED_SERVER_MAJOR = 16


class DatabaseSafetyError(RuntimeError):
    """A connection does not satisfy a safety requirement (role, version, host)."""


def _credentials(kind: str, settings: DatabaseSettings) -> tuple[str, str, int]:
    table = {
        "bench_admin": (settings.bench_admin_user, settings.bench_admin_password, settings.bench_port),
        "bench_owner": (BENCH_OWNER_ROLE, settings.bench_owner_password, settings.bench_port),
        "bench_reader": (BENCH_READER_ROLE, settings.bench_reader_password, settings.bench_port),
        "evidence_admin": (settings.evidence_admin_user, settings.evidence_admin_password, settings.evidence_port),
        "evidence_owner": (EVIDENCE_OWNER_ROLE, settings.evidence_owner_password, settings.evidence_port),
        "evidence_writer": (EVIDENCE_WRITER_ROLE, settings.evidence_writer_password, settings.evidence_port),
    }
    if kind not in table:
        raise ValueError(f"unknown connection kind {kind!r}")
    return table[kind]


def connect(kind: str, dbname: str, *, autocommit: bool = False, application_name: str = "qcp",
            settings: DatabaseSettings | None = None) -> psycopg.Connection[Any]:
    """Open a connection and verify the server is PostgreSQL 16."""
    cfg = settings or database_settings_from_env()
    user, password, port = _credentials(kind, cfg)
    conn = psycopg.connect(
        host=cfg.host, port=port, dbname=dbname, user=user, password=password,
        connect_timeout=10, application_name=application_name[:63], autocommit=autocommit,
    )
    major = conn.info.server_version // 10000
    if major != REQUIRED_SERVER_MAJOR:
        conn.close()
        raise DatabaseSafetyError(f"server at port {port} is PostgreSQL {major}; PostgreSQL 16 is required")
    return conn


def connect_bench_reader(dbname: str, application_name: str) -> psycopg.Connection[Any]:
    """Read-only benchmark connection; refuses a superuser or a non-read-only default.

    The connection is in autocommit mode with ``read_only = True``: every
    measured statement runs inside an explicit ``with conn.transaction(
    force_rollback=True)`` block, which psycopg opens as ``BEGIN READ ONLY``
    and always rolls back. Stand-alone catalog reads run in their own
    (role-default read-only) transactions, so nothing stays open between keys.
    """
    conn = connect("bench_reader", dbname, autocommit=True, application_name=application_name)
    try:
        conn.read_only = True
        with conn.transaction(force_rollback=True):
            row = conn.execute(
                "SELECT r.rolsuper, current_setting('default_transaction_read_only'), current_setting('transaction_read_only') "
                "FROM pg_roles r WHERE r.rolname = current_user"
            ).fetchone()
    except Exception:
        conn.close()
        raise
    if row is None or row[0] or row[1] != "on" or row[2] != "on":
        conn.close()
        raise DatabaseSafetyError(
            f"{BENCH_READER_ROLE} must be a non-superuser with read-only transactions; got {row!r}. Re-run db-init."
        )
    return conn


def connect_evidence_writer(application_name: str) -> psycopg.Connection[Any]:
    return connect("evidence_writer", EVIDENCE_DATABASE, autocommit=False, application_name=application_name)


def server_identity(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    row = conn.execute("SELECT current_setting('server_version'), current_setting('server_version_num')::int").fetchone()
    assert row is not None
    return {"server_version": row[0], "server_version_num": int(row[1])}
