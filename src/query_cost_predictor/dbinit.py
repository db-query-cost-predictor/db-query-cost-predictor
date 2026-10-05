"""Idempotent initialization: roles, databases, evidence migrations, grants.

The container administrator is used here only. Every later command connects
as a restricted role. Migrations are tracked in ``qcp.schema_migration`` with
their SHA-256; an applied migration whose file has changed stops ``db-init``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg import sql

from query_cost_predictor.config import DatabaseSettings, database_settings_from_env
from query_cost_predictor.db import (
    BENCH_DATABASES,
    BENCH_OWNER_ROLE,
    BENCH_READER_ROLE,
    EVIDENCE_DATABASE,
    EVIDENCE_OWNER_ROLE,
    EVIDENCE_WRITER_ROLE,
    connect,
)
from query_cost_predictor.hashing import sha256_file
from query_cost_predictor.paths import environment_reports_dir, ensure_dir, repo_root

MIGRATION_PATTERN = re.compile(r"^V(\d{3})__[a-z0-9_]+\.sql$")
BOOTSTRAP_DDL = """
CREATE SCHEMA IF NOT EXISTS qcp;
CREATE TABLE IF NOT EXISTS qcp.schema_migration (
    version    text PRIMARY KEY,
    filename   text NOT NULL,
    sha256     text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
"""
# Role-level defaults for the read-only benchmark role (set by the administrator).
READER_ROLE_SETTINGS = {
    "default_transaction_read_only": "on",
    "statement_timeout": "15s",
    "lock_timeout": "5s",
    "idle_in_transaction_session_timeout": "60s",
    "jit": "off",
}


class MigrationError(RuntimeError):
    """A migration file is malformed or changed after being applied."""


def migration_directory() -> Path:
    return repo_root() / "sql" / "evidence"


def migration_files(directory: Path | None = None) -> list[Path]:
    folder = directory or migration_directory()
    files = sorted(p for p in folder.glob("V*.sql"))
    versions: set[str] = set()
    for path in files:
        match = MIGRATION_PATTERN.match(path.name)
        if not match:
            raise MigrationError(f"bad migration file name {path.name} (expected V001__name.sql)")
        if match.group(1) in versions:
            raise MigrationError(f"duplicate migration version {match.group(1)}")
        versions.add(match.group(1))
    if not files:
        raise MigrationError(f"no migrations found in {folder}")
    return files


def _ensure_role(conn: Any, role: str, password: str) -> str:
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is not None
    verb = "ALTER" if exists else "CREATE"
    conn.execute(
        sql.SQL(verb + " ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(
            sql.Identifier(role), sql.Literal(password)
        )
    )
    return "updated" if exists else "created"


def _ensure_database(conn: Any, dbname: str, owner: str) -> str:
    if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone():
        owner_row = conn.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s", (dbname,)
        ).fetchone()
        if owner_row is None or owner_row[0] != owner:
            raise RuntimeError(f"database {dbname} exists but is owned by {owner_row[0] if owner_row else '?'}, not {owner}")
        return "exists"
    conn.execute(
        sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'").format(
            sql.Identifier(dbname), sql.Identifier(owner)
        )
    )
    return "created"


def _role_report(conn: Any, role: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, coalesce(rolconfig, '{}') "
        "FROM pg_roles WHERE rolname = %s",
        (role,),
    ).fetchone()
    if row is None:
        return {"exists": False}
    config = dict(item.split("=", 1) for item in row[5])
    return {"exists": True, "rolsuper": row[0], "rolcreatedb": row[1], "rolcreaterole": row[2],
            "rolreplication": row[3], "rolbypassrls": row[4], "role_settings": config}


def _init_bench(settings: DatabaseSettings) -> dict[str, Any]:
    report: dict[str, Any] = {"roles": {}, "databases": {}}
    with connect("bench_admin", "postgres", autocommit=True, application_name="qcp:db-init", settings=settings) as conn:
        report["roles"][BENCH_OWNER_ROLE] = _ensure_role(conn, BENCH_OWNER_ROLE, settings.bench_owner_password)
        report["roles"][BENCH_READER_ROLE] = _ensure_role(conn, BENCH_READER_ROLE, settings.bench_reader_password)
        role_settings = dict(READER_ROLE_SETTINGS, temp_file_limit=settings.reader_temp_file_limit)
        for name, value in role_settings.items():
            conn.execute(sql.SQL("ALTER ROLE {} SET {} = {}").format(
                sql.Identifier(BENCH_READER_ROLE), sql.SQL(name), sql.Literal(value)))
        for dbname in BENCH_DATABASES.values():
            report["databases"][dbname] = _ensure_database(conn, dbname, BENCH_OWNER_ROLE)
            conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(dbname)))
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname), sql.Identifier(BENCH_READER_ROLE)))
        report["reader_role"] = _role_report(conn, BENCH_READER_ROLE)
        report["owner_role"] = _role_report(conn, BENCH_OWNER_ROLE)
    for dbname in BENCH_DATABASES.values():
        with connect("bench_admin", dbname, autocommit=True, application_name="qcp:db-init", settings=settings) as conn:
            conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            conn.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(sql.Identifier(BENCH_OWNER_ROLE)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(BENCH_READER_ROLE)))
            conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} GRANT SELECT ON TABLES TO {}").format(
                sql.Identifier(BENCH_OWNER_ROLE), sql.Identifier(BENCH_READER_ROLE)))
    return report


def _apply_migrations(conn: Any) -> list[dict[str, Any]]:
    conn.execute(BOOTSTRAP_DDL)
    conn.commit()
    applied = {row[0]: (row[1], row[2]) for row in conn.execute("SELECT version, filename, sha256 FROM qcp.schema_migration")}
    conn.commit()
    results = []
    for path in migration_files():
        version = MIGRATION_PATTERN.match(path.name).group(1)  # type: ignore[union-attr]
        digest = sha256_file(path)
        if version in applied:
            if applied[version][1] != digest:
                raise MigrationError(f"{path.name} changed after it was applied (checksum_mismatch); refusing to continue")
            results.append({"version": version, "file": path.name, "sha256": digest, "status": "already_applied"})
            continue
        conn.execute(path.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO qcp.schema_migration (version, filename, sha256) VALUES (%s, %s, %s)",
                     (version, path.name, digest))
        conn.commit()
        results.append({"version": version, "file": path.name, "sha256": digest, "status": "applied"})
    return results


def _init_evidence(settings: DatabaseSettings) -> dict[str, Any]:
    report: dict[str, Any] = {"roles": {}}
    with connect("evidence_admin", "postgres", autocommit=True, application_name="qcp:db-init", settings=settings) as conn:
        report["roles"][EVIDENCE_OWNER_ROLE] = _ensure_role(conn, EVIDENCE_OWNER_ROLE, settings.evidence_owner_password)
        report["roles"][EVIDENCE_WRITER_ROLE] = _ensure_role(conn, EVIDENCE_WRITER_ROLE, settings.evidence_writer_password)
        report["database"] = _ensure_database(conn, EVIDENCE_DATABASE, EVIDENCE_OWNER_ROLE)
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(EVIDENCE_DATABASE)))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(EVIDENCE_DATABASE), sql.Identifier(EVIDENCE_WRITER_ROLE)))
        report["writer_role"] = _role_report(conn, EVIDENCE_WRITER_ROLE)
    with connect("evidence_admin", EVIDENCE_DATABASE, autocommit=True, application_name="qcp:db-init", settings=settings) as conn:
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    with connect("evidence_owner", EVIDENCE_DATABASE, autocommit=False, application_name="qcp:db-init", settings=settings) as conn:
        report["migrations"] = _apply_migrations(conn)
        checks = {}
        for table in ("query_execution", "estimated_plan", "query_instance", "collection_error"):
            row = conn.execute("SELECT has_table_privilege(%s, %s, 'UPDATE'), has_table_privilege(%s, %s, 'DELETE')",
                               (EVIDENCE_WRITER_ROLE, f"qcp.{table}", EVIDENCE_WRITER_ROLE, f"qcp.{table}")).fetchone()
            checks[f"writer_cannot_modify_{table}"] = bool(row is not None and not row[0] and not row[1])
        conn.commit()
        report["privilege_checks"] = checks
    return report


def run_db_init() -> dict[str, Any]:
    settings = database_settings_from_env()
    bench = _init_bench(settings)
    evidence = _init_evidence(settings)
    reader = bench["reader_role"]
    checks = {
        "reader_not_superuser": reader.get("rolsuper") is False,
        "reader_default_read_only": reader.get("role_settings", {}).get("default_transaction_read_only") == "on",
        "reader_temp_file_limit_set": "temp_file_limit" in reader.get("role_settings", {}),
        "owner_not_superuser": bench["owner_role"].get("rolsuper") is False,
        "writer_not_superuser": evidence["writer_role"].get("rolsuper") is False,
        **evidence["privilege_checks"],
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "bench": bench,
        "evidence": evidence,
        "checks": checks,
    }
    out = ensure_dir(environment_reports_dir()) / "db_init.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["summary"] = {
        "status": status,
        "migrations": [f"{m['file']}: {m['status']}" for m in evidence["migrations"]],
        "checks": checks,
        "report": str(out),
    }
    return report
