"""Synthetic output signatures and isolated snapshot-comparison orchestration."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from rq2_pipeline import verification
from rq2_pipeline.verification import OutputSignature, signature_mismatch


@pytest.mark.parametrize("candidate,expected", [
    (OutputSignature(("n", "revenue"), (20, 1700)), None),
    (OutputSignature(("n", "amount"), (20, 1700)), "names"),
    (OutputSignature(("revenue", "n"), (1700, 20)), "names"),
    (OutputSignature(("n", "revenue"), (23, 1700)), "OIDs"),
])
def test_column_names_order_and_types_are_independent_requirements(candidate, expected):
    mismatch = signature_mismatch(OutputSignature(("n", "revenue"), (20, 1700)), candidate)
    assert mismatch is None if expected is None else expected in mismatch


def test_invalid_output_metadata_is_refused():
    with pytest.raises(ValueError):
        OutputSignature(("n",), ())


class FakeConnection:
    def __init__(self, names=("n", "revenue")):
        self.names = names
        self.forms = 0
        self.commands = []

    def transaction(self, **kwargs):
        assert kwargs == {"force_rollback": True}
        return nullcontext()

    def execute(self, statement):
        self.commands.append(statement)
        return SimpleNamespace(fetchone=lambda: ("synthetic_snapshot",))

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute_metadata(self, statement):
        self.forms += 1
        names = ("n", "revenue") if self.forms == 1 else self.names
        self.description = [SimpleNamespace(name=name, type_code=oid)
                            for name, oid in zip(names, (20, 1700))]


def _connection(monkeypatch, names=("n", "revenue")):
    conn = FakeConnection(names)
    metadata = SimpleNamespace(execute=conn.execute_metadata)
    class Cursor:
        def __enter__(self):
            return metadata

        def __exit__(self, *args):
            return False
    def execute(statement):
        conn.execute_metadata(statement)
        metadata.description = conn.description
    metadata.execute = execute
    monkeypatch.setattr(conn, "cursor", lambda: Cursor())
    monkeypatch.setattr(verification, "apply_transaction_settings", lambda *args: None)
    return conn


def test_name_mismatch_stops_before_row_execution(monkeypatch):
    conn = _connection(monkeypatch, ("n", "changed"))
    def forbidden(*args, **kwargs):
        raise AssertionError("No row execution is needed after metadata mismatch")
    monkeypatch.setattr(verification, "run_statement", forbidden)
    result = verification.compare_on_snapshot(conn, "SELECT 1 AS n, 2 AS revenue",
                                               "SELECT 1 AS n, 2 AS changed", settings={})
    assert result["status"] == "MISMATCH"
    assert conn.commands[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"


@pytest.mark.parametrize("candidate_rows,expected", [
    ([(0, None)], "AGREED_ON_TESTED_INPUTS"),
    ([(0, 0)], "MISMATCH"),
])
def test_zero_count_does_not_hide_null_to_zero_mismatch(monkeypatch, candidate_rows, expected):
    conn = _connection(monkeypatch)
    rows = iter([[(0, None)], candidate_rows])
    def execute(*args, **kwargs):
        return SimpleNamespace(status="completed", rows=next(rows), column_count=2, truncated=False)
    monkeypatch.setattr(verification, "run_statement", execute)
    result = verification.compare_on_snapshot(conn, "SELECT 0 AS n, NULL::numeric AS revenue",
                                               "SELECT 0 AS n, 0::numeric AS revenue", settings={})
    assert result["status"] == expected
