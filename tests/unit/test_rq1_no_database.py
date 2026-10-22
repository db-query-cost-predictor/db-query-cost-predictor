"""RQ1 Slice A commands never connect to a database or execute SQL, and write no run directory."""

from __future__ import annotations

import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from query_cost_predictor import cli
from rq1_fixtures import REPO_ROOT, build_release

RQ1_MODULES = ("rq1_config", "rq1_inputs", "rq1_provenance", "rq1_run")


def test_rq1_modules_contain_no_database_or_sql_access():
    for name in RQ1_MODULES:
        source = (REPO_ROOT / "src" / "query_cost_predictor" / f"{name}.py").read_text(encoding="utf-8")
        for forbidden in ("psycopg", "query_cost_predictor.db", "connect_bench", "connect_evidence", "cursor(", "SELECT "):
            assert forbidden not in source, f"{name}.py mentions {forbidden!r}"


def test_importing_the_rq1_commands_loads_no_database_driver():
    code = ("import sys; import query_cost_predictor.cli, query_cost_predictor.rq1_config, "
            "query_cost_predictor.rq1_inputs, query_cost_predictor.rq1_provenance, query_cost_predictor.rq1_run; "
            "print(sorted(m for m in ('psycopg', 'query_cost_predictor.db') if m in sys.modules))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """Any database connection attempt fails the test."""
    def refuse(*args, **kwargs):
        raise AssertionError("RQ1 Slice A must not connect to a database")
    import psycopg

    from query_cost_predictor import db
    monkeypatch.setattr(psycopg, "connect", refuse)
    for name in ("connect_evidence_writer", "connect_bench_reader"):
        if hasattr(db, name):
            monkeypatch.setattr(db, name, refuse)
    monkeypatch.setattr(cli, "load_repo_env", lambda: None)
    return tmp_path


def test_check_inputs_command_runs_offline_and_writes_no_run_directory(offline, monkeypatch, capsys):
    release = build_release(offline / "release")
    from query_cost_predictor import paths, rq1_config

    monkeypatch.setattr(rq1_config, "load_rq1_config", lambda path=None: release.config)
    monkeypatch.setattr(paths, "repo_root", lambda: release.repo)
    monkeypatch.delenv("QCP_DATA_ROOT", raising=False)
    before = sorted(p.relative_to(offline) for p in offline.rglob("*"))
    code = cli.cmd_rq1_check_inputs(Namespace(evidence_dir=str(release.evidence_dir), archive=str(release.archive),
                                              json_out=None))
    output = capsys.readouterr().out
    assert code == 0
    assert "RQ1-CHECK-INPUTS: PASS" in output and "STOP (G-08)" in output
    assert "NOT_ELIGIBLE" in output  # the synthetic repository is not a git checkout: data PASS is not eligibility
    assert sorted(p.relative_to(offline) for p in offline.rglob("*")) == before


def test_json_out_inside_the_evidence_directory_is_refused(offline, monkeypatch):
    release = build_release(offline / "release")
    from query_cost_predictor import paths, rq1_config

    monkeypatch.setattr(rq1_config, "load_rq1_config", lambda path=None: release.config)
    monkeypatch.setattr(paths, "repo_root", lambda: release.repo)
    code = cli.cmd_rq1_check_inputs(Namespace(evidence_dir=str(release.evidence_dir), archive=None,
                                              json_out=str(release.evidence_dir / "check.json")))
    assert code == 2 and not (release.evidence_dir / "check.json").exists()
