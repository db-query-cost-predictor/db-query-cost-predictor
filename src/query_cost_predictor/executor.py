"""Database execution primitives shared by the smoke collector and the rewrite harness.

Every statement runs inside ``with conn.transaction(force_rollback=True)`` on a
read-only connection, so it is always rolled back. Per-transaction settings
(statement timeout, lock timeout, configuration settings) are applied with
``set_config(name, value, true)``. A client-side watchdog cancels a statement
that outlives the server timeout plus a grace period.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import errors

from query_cost_predictor.evidence import utc_now
from query_cost_predictor.plans import plan_document

EFFECTIVE_SETTING_NAMES = (
    "server_version_num", "jit", "track_io_timing", "work_mem", "hash_mem_multiplier", "shared_buffers",
    "effective_cache_size", "maintenance_work_mem", "max_parallel_workers_per_gather", "max_parallel_workers",
    "max_worker_processes", "parallel_setup_cost", "parallel_tuple_cost", "min_parallel_table_scan_size",
    "min_parallel_index_scan_size", "random_page_cost", "seq_page_cost", "cpu_tuple_cost",
    "cpu_index_tuple_cost", "cpu_operator_cost", "effective_io_concurrency", "default_statistics_target",
    "geqo", "geqo_threshold", "join_collapse_limit", "from_collapse_limit", "plan_cache_mode",
    "temp_file_limit", "default_transaction_read_only", "huge_pages",
)


class TransientDatabaseError(RuntimeError):
    """Connection-level failure; the caller may reconnect and retry (not a measurement)."""


class SafetyViolation(RuntimeError):
    """The server returned something a single read-only statement cannot produce."""


@dataclass
class StatementOutcome:
    status: str  # completed | timeout | error
    started_at: str
    finished_at: str
    client_elapsed_ms: float
    document: dict[str, Any] | None = None
    rows: list[tuple[Any, ...]] | None = None
    column_count: int | None = None
    truncated: bool = False
    sqlstate: str | None = None
    error_class: str | None = None
    error_message: str | None = None
    watchdog_fired: bool = False


def apply_transaction_settings(cur: psycopg.Cursor[Any], settings: dict[str, str], statement_timeout_ms: int,
                               lock_timeout_ms: int) -> None:
    items = [("statement_timeout", f"{int(statement_timeout_ms)}ms"), ("lock_timeout", f"{int(lock_timeout_ms)}ms")]
    items += sorted(settings.items())
    for name, value in items:
        cur.execute("SELECT set_config(%s, %s, true)", (name, value))


def _start_watchdog(conn: psycopg.Connection[Any], seconds: float, fired: dict[str, bool]) -> threading.Timer:
    def cancel() -> None:
        fired["value"] = True
        try:
            cancel_fn = getattr(conn, "cancel_safe", None) or conn.cancel
            cancel_fn()
        except Exception:  # noqa: BLE001 - best effort; the server timeout remains the primary guard
            pass

    timer = threading.Timer(seconds, cancel)
    timer.daemon = True
    timer.start()
    return timer


def _classify_error(exc: psycopg.Error, fired: bool) -> tuple[str, str | None, str, str]:
    sqlstate = getattr(exc, "sqlstate", None)
    message = str(exc).strip()
    if isinstance(exc, errors.QueryCanceled):
        if fired:
            return "error", sqlstate, "WatchdogCancelled", message
        if "statement timeout" in message.lower():
            return "timeout", sqlstate, "QueryCanceled", message
        return "error", sqlstate, "QueryCanceled", message
    return "error", sqlstate, exc.__class__.__name__, message


def run_statement(
    conn: psycopg.Connection[Any],
    statement: str,
    *,
    settings: dict[str, str],
    statement_timeout_ms: int,
    lock_timeout_ms: int,
    watchdog_grace_ms: int,
    fetch_rows_cap: int | None = None,
) -> StatementOutcome:
    """Execute one already-validated statement in a rolled-back read-only transaction.

    If ``fetch_rows_cap`` is None the statement must be an EXPLAIN returning one
    JSON document. Otherwise rows are fetched through a server-side cursor (which
    PostgreSQL only allows for a single SELECT) up to ``cap + 1`` rows.
    """
    started = utc_now()
    t0 = time.perf_counter()
    fired = {"value": False}
    timer: threading.Timer | None = None
    try:
        with conn.transaction(force_rollback=True):
            with conn.cursor() as setup:
                apply_transaction_settings(setup, settings, statement_timeout_ms, lock_timeout_ms)
                read_only = setup.execute("SELECT current_setting('transaction_read_only')").fetchone()
                if read_only is None or read_only[0] != "on":
                    raise SafetyViolation("transaction is not read-only; refusing to execute")
            timer = _start_watchdog(conn, (statement_timeout_ms + watchdog_grace_ms) / 1000.0, fired)
            if fetch_rows_cap is None:
                with conn.cursor() as cur:
                    cur.execute(statement)
                    row = cur.fetchone()
                    if cur.nextset():
                        raise SafetyViolation("more than one result set returned")
                document = plan_document(row[0]) if row else None
                if document is None:
                    raise SafetyViolation("EXPLAIN returned no plan")
                outcome = StatementOutcome("completed", started, utc_now(), (time.perf_counter() - t0) * 1000.0,
                                           document=document)
            else:
                with conn.cursor(name="qcp_fetch") as cur:
                    cur.execute(statement)
                    rows = cur.fetchmany(fetch_rows_cap + 1)
                    column_count = len(cur.description or [])
                truncated = len(rows) > fetch_rows_cap
                outcome = StatementOutcome("completed", started, utc_now(), (time.perf_counter() - t0) * 1000.0,
                                           rows=[tuple(r) for r in rows[:fetch_rows_cap]], column_count=column_count,
                                           truncated=truncated)
    except psycopg.Error as exc:
        # Server-reported errors (they carry a SQLSTATE, e.g. statement timeout 57014 or
        # temp_file_limit exceeded 53400) are terminal outcomes of this execution.
        # Connection-level failures (no SQLSTATE, or a broken connection) are transient:
        # the caller reconnects and retries without consuming a repeat number.
        if conn.broken or getattr(exc, "sqlstate", None) is None:
            raise TransientDatabaseError(f"{exc.__class__.__name__}: {exc}") from exc
        status, sqlstate, error_class, message = _classify_error(exc, fired["value"])
        outcome = StatementOutcome(status, started, utc_now(), (time.perf_counter() - t0) * 1000.0,
                                   sqlstate=sqlstate, error_class=error_class, error_message=message,
                                   watchdog_fired=fired["value"])
    finally:
        if timer is not None:
            timer.cancel()
    return outcome


def explain_statement(sql_text: str, options: str) -> str:
    return f"EXPLAIN ({options}) {sql_text}"


def capture_effective_settings(conn: psycopg.Connection[Any], session_settings: dict[str, str],
                               lock_timeout_ms: int = 5000) -> dict[str, dict[str, str]]:
    """Effective planner/executor settings after applying a configuration's session settings."""
    with conn.transaction(force_rollback=True):
        with conn.cursor() as cur:
            apply_transaction_settings(cur, session_settings, 60_000, lock_timeout_ms)
            cur.execute(
                "SELECT name, setting, coalesce(unit, '') FROM pg_settings "
                "WHERE name = ANY(%s) OR name LIKE 'enable%%' ORDER BY name",
                (list(EFFECTIVE_SETTING_NAMES),),
            )
            return {name: {"setting": setting, "unit": unit} for name, setting, unit in cur.fetchall()}


def work_mem_kb(effective: dict[str, dict[str, str]]) -> float:
    entry = effective.get("work_mem", {})
    if entry.get("unit") != "kB":
        raise ValueError(f"unexpected work_mem unit {entry.get('unit')!r}")
    return float(entry["setting"])
