"""Credential isolation and complete report denominators."""
from pathlib import Path
import pytest
from rq2_pipeline.credentials import load_private_env, reader_environment
from rq2_pipeline.reports import write_report
from rq2_pipeline.contract import load_queries
from .test_contract_pipeline import manifest


def test_private_file_does_not_load_admin_credentials_or_override_the_terminal(tmp_path, monkeypatch):
    monkeypatch.delenv("QCP_BENCH_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("QCP_BENCH_READER_PASSWORD", raising=False)
    monkeypatch.delenv("PGPASSWORD", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "TEST_ONLY_EXISTING")
    path = tmp_path / "private.env"
    path.write_text('GROQ_API_KEY=TEST_ONLY_NEW\nQCP_BENCH_READER_PASSWORD="TEST_ONLY_READER"\nQCP_BENCH_ADMIN_PASSWORD=TEST_ONLY_ADMIN\n')
    load_private_env(path); reader_environment()
    import os
    assert os.environ["GROQ_API_KEY"] == "TEST_ONLY_EXISTING"
    assert os.environ["PGPASSWORD"] == "TEST_ONLY_READER"
    assert "QCP_BENCH_ADMIN_PASSWORD" not in os.environ


def test_missing_case_cannot_be_hidden_by_the_report(tmp_path):
    queries = load_queries(manifest())
    with pytest.raises(ValueError, match="reconcile"):
        write_report(tmp_path, [], queries, scope="SYNTHETIC", identity_sha256="synthetic")
