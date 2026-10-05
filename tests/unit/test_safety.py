"""Single read-only statement validation (defense in depth; the role and READ ONLY transactions are the other layers)."""

from __future__ import annotations

import pytest

from query_cost_predictor.safety import SAFETY_PASSED, SqlSafetyError, validate_read_only_sql

ALLOWED = [
    "SELECT 1",
    "select * from orders where o_orderkey = 1;",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "SELECT 'drop table; insert into x' AS s",
    "SELECT count(*) FROM part WHERE p_name LIKE '%green%'",
    "SELECT 1 -- DELETE FROM t",
    "SELECT o_orderkey FROM orders ORDER BY o_orderkey FETCH FIRST 5 ROWS ONLY",
    "SELECT $$quoted; DROP TABLE t$$ AS s",
]

REJECTED = [
    ("", "EMPTY"),
    ("DELETE FROM orders", "NOT_SELECT"),
    ("SELECT 1; SELECT 2", "MULTIPLE_STATEMENTS"),
    ("SELECT 1; DROP TABLE orders", "MULTIPLE_STATEMENTS"),
    ("BEGIN; SELECT 1; COMMIT", "MULTIPLE_STATEMENTS"),
    ("WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", "FORBIDDEN_KEYWORD"),
    ("SELECT * INTO new_table FROM orders", "FORBIDDEN_KEYWORD"),
    ("SELECT * FROM orders FOR UPDATE", "FORBIDDEN_KEYWORD"),
    ("SELECT * FROM orders FOR SHARE", "FORBIDDEN_KEYWORD"),
    ("SELECT pg_sleep(100)", "FORBIDDEN_FUNCTION"),
    ("SELECT set_config('work_mem', '1GB', false)", "FORBIDDEN_FUNCTION"),
    ("SELECT pg_advisory_lock(1)", "FORBIDDEN_FUNCTION"),
    ("SELECT pg_catalog.pg_read_file('x')", "FORBIDDEN_FUNCTION"),
    ("SELECT lo_import('x')", "FORBIDDEN_FUNCTION"),
    ("SELECT query_to_xml('delete from t', true, true, '')", "FORBIDDEN_FUNCTION"),
    ("EXPLAIN ANALYZE SELECT 1", "NOT_SELECT"),
    ("COPY orders TO STDOUT", "NOT_SELECT"),
    ("SELECT $1", "POSITIONAL_PARAMETER"),
    ("SELECT 'unterminated", "UNPARSEABLE"),
    ("SELECT 1\x00", "CONTROL_CHARACTER"),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_read_only_single_statements_pass(sql: str) -> None:
    result = validate_read_only_sql(sql)
    assert result.status == SAFETY_PASSED
    assert not result.sql.endswith(";")


@pytest.mark.parametrize(("sql", "reason"), REJECTED)
def test_unsafe_statements_are_rejected(sql: str, reason: str) -> None:
    with pytest.raises(SqlSafetyError) as info:
        validate_read_only_sql(sql)
    assert info.value.reason_code == reason
