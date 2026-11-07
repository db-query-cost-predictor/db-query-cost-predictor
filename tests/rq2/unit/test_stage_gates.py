"""Correctness gates and the real scheduler, with synthetic database outcomes."""
from hashlib import sha256
from types import SimpleNamespace
import pytest

from rq2_pipeline import stages
from rq2_pipeline.contract import default_config, load_queries, original_template
from rq2_pipeline.storage import read_json
from rq2_pipeline.executor import StatementOutcome


def setup(monkeypatch):
    p = {"start_date": "1994-04-13", "days": 17}
    q = load_queries({"version": "rq2-input-v1", "queries": [{"id": "synthetic", "family": "date_arithmetic", "parameters": p,
                    "sql": original_template("date_arithmetic", p)}]})[0]
    case = q["cases"][0]
    identity = {"identity_sha256": "synthetic", "config": default_config(), "environment": {"windows_power": {"synthetic": True}},
                "database_identities": {case["database"]: {}}, "effective_settings": {case["database"] + "/" + case["configuration_name"]: {}}}
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    for name, function in {"connect": lambda *a: Conn(), "probe": lambda *a: {}, "assert_environment_unchanged": lambda *a: None,
                           "frozen_identity": lambda *a: True, "capture_effective_settings": lambda *a: {},
                           "capture_catalog": lambda *a: object(), "family_preconditions": lambda *a: {"passed": True},
                           "compare_on_snapshot": lambda *a, **k: {"status": "AGREED_ON_TESTED_INPUTS"},
                           "evaluate_registry": lambda *a: {"status": "AGREED_ON_TESTED_FIXTURES"},
                           "power_record": lambda: {"synthetic": True}}.items(): monkeypatch.setattr(stages, name, function)
    sql = "SELECT count(*) AS n_lines,sum(l_extendedprice) AS revenue FROM lineitem WHERE l_shipdate = DATE '1994-03-27'"
    candidate = {"sql": sql, "raw_sql_sha256": sha256(sql.encode()).hexdigest()}
    return identity, q, case, candidate, {"docker": "unused", "desktop": "unused", "container": "unused"}


@pytest.mark.parametrize("stage,status,outcome", [("snapshot", "MISMATCH", "NOT_EQUIVALENT"),
    ("snapshot", "INCONCLUSIVE", "INCONCLUSIVE"), ("fixtures", "REJECTED_COUNTEREXAMPLE", "FIXTURE_REJECTED")])
def test_wrong_or_incomplete_results_never_reach_timing(tmp_path, monkeypatch, stage, status, outcome):
    identity,q,case,candidate,paths = setup(monkeypatch)
    monkeypatch.setattr(stages, "compare_on_snapshot" if stage == "snapshot" else "evaluate_registry", lambda *a, **k: {"status": status})
    monkeypatch.setattr(stages, "measure", lambda *a, **k: pytest.fail("Rejected correctness must not reach timing"))
    assert stages.run_case(identity,q,case,candidate,tmp_path,paths)["status"] == outcome


def test_read_only_safety_blocks_before_connecting(tmp_path, monkeypatch):
    identity,q,case,candidate,paths = setup(monkeypatch)
    candidate["sql"] = "DELETE FROM lineitem"
    monkeypatch.setattr(stages, "connect", lambda *a: pytest.fail("Unsafe SQL must not connect"))
    assert stages.run_case(identity,q,case,candidate,tmp_path,paths)["status"] == "UNSAFE"


def test_successful_gate_path_runs_exactly_the_declared_schedule(tmp_path, monkeypatch):
    identity,q,case,candidate,paths = setup(monkeypatch)
    executed = []
    def execute(conn, sql, settings, **kwargs):
        executed.append(sql)
        return StatementOutcome("completed", "synthetic-start", "synthetic-end", 0,
                                document={"Execution Time": 100 if sql == q["sql"] else 80})
    monkeypatch.setattr(stages, "execute", execute)
    row = stages.run_case(identity,q,case,candidate,tmp_path,paths)
    assert row["status"] == "MEETS_PRIMARY_CRITERION"
    assert row["original_median_ms"] == 100 and row["rewrite_median_ms"] == 80
    assert len(executed) == 48
    timing = read_json(tmp_path / "timing.json")
    assert len(timing["aa_pairs"]) == len(timing["ab_pairs"]) == 11
    assert all(p["a_us"] == p["b_us"] for p in timing["aa_pairs"])
