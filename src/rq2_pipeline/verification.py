"""Output metadata and exact observed-result checks for RQ2 v2.

Passing means agreement on the supplied snapshot or fixture, never a general
proof. SQL generation and performance decisions are separate components.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from rq2_pipeline.executor import apply_transaction_settings, run_statement
from rq2_pipeline.comparison import compare_results, EQUIVALENT
from rq2_pipeline.safety import validate_read_only_sql


@dataclass(frozen=True)
class OutputSignature:
    names: tuple[str, ...]
    type_oids: tuple[int, ...]

    def __post_init__(self):
        if (type(self.names) is not tuple or type(self.type_oids) is not tuple
                or not self.names or len(self.names) != len(self.type_oids)
                or any(not isinstance(name, str) for name in self.names)
                or any(type(oid) is not int or oid <= 0 for oid in self.type_oids)):
            raise ValueError("Invalid output signature")


def signature_mismatch(original: OutputSignature, candidate: OutputSignature) -> str | None:
    if original.names != candidate.names:
        return "Output column names or order differ"
    if original.type_oids != candidate.type_oids:
        return "Output PostgreSQL type OIDs differ"
    return None


def compare_on_snapshot(conn: Any, original_sql: str, candidate_sql: str, *,
                        settings: Mapping[str, str], ordering_required: bool = False,
                        row_cap: int = 200_000) -> dict:
    """Check metadata and rows in one read-only repeatable-read snapshot.

    The caller must preserve this return value as evidence and stop on any
    mismatch or incomplete execution. No EXPLAIN ANALYZE or model call is made.
    """
    if type(row_cap) is not int or row_cap <= 0:
        raise ValueError("row_cap must be a positive integer")
    statements = {"original": validate_read_only_sql(original_sql).sql,
                  "candidate": validate_read_only_sql(candidate_sql).sql}
    result = {"status": "INCONCLUSIVE", "signatures": {}, "executions": {},
              "detail": "Verification did not complete"}
    with conn.transaction(force_rollback=True):
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        result["transaction_snapshot"] = conn.execute("SELECT pg_current_snapshot()::text").fetchone()[0]
        signatures = {}
        for form, statement in statements.items():
            with conn.cursor() as cur:
                apply_transaction_settings(cur, dict(settings), 15_000, 5_000)
                cur.execute(f"SELECT * FROM ({statement}) AS qcp_v2_metadata LIMIT 0")
                signatures[form] = OutputSignature(tuple(col.name for col in cur.description),
                                                    tuple(col.type_code for col in cur.description))
                result["signatures"][form] = {"names": signatures[form].names,
                                                "type_oids": signatures[form].type_oids}
        mismatch = signature_mismatch(signatures["original"], signatures["candidate"])
        if mismatch:
            result.update(status="MISMATCH", detail=mismatch)
            return result
        outcomes = {}
        for form, statement in statements.items():
            outcome = run_statement(conn, statement, settings=dict(settings), statement_timeout_ms=15_000,
                                    lock_timeout_ms=5_000, watchdog_grace_ms=2_000, fetch_rows_cap=row_cap)
            outcomes[form] = outcome
            result["executions"][form] = vars(outcome)
            if outcome.status != "completed":
                result["detail"] = f"{form} verification execution: {outcome.status}"
                return result
        a, b = outcomes["original"], outcomes["candidate"]
        compared = compare_results(a.rows, b.rows, original_columns=a.column_count,
                                    rewrite_columns=b.column_count, ordering_required=ordering_required,
                                    truncated=a.truncated or b.truncated)
        result["comparison"] = vars(compared)
        if compared.status == EQUIVALENT:
            result.update(status="AGREED_ON_TESTED_INPUTS", detail="Output metadata and exact results agree")
        else:
            result.update(status="INCONCLUSIVE" if compared.status == "INCONCLUSIVE" else "MISMATCH",
                          detail=compared.detail)
    return result
