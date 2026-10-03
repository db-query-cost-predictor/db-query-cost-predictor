"""Configuration loading and validation (no database)."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from query_cost_predictor.config import (
    ConfigError,
    database_settings_from_env,
    default_poster_smoke_path,
    load_poster_smoke_config,
    parse_poster_smoke_config,
    render_sql,
)
from query_cost_predictor.envfile import parse_env_text
from query_cost_predictor.final_plan import summarize_final_plan


def _document() -> dict:
    return yaml.safe_load(default_poster_smoke_path().read_text(encoding="utf-8"))


def _parse(document: dict) -> object:
    return parse_poster_smoke_config(document, Path("poster_smoke.yaml"), "0" * 64)


def test_poster_smoke_config_matches_approved_protocol() -> None:
    cfg = load_poster_smoke_config()
    assert cfg.protocol.statement_timeout_ms == 15000
    assert cfg.protocol.planned_final_timeout_ms == 60000
    assert cfg.protocol.measured_repetitions == 3
    assert cfg.protocol.warmup_probes == 1
    assert cfg.protocol.cache_protocol == "warm"
    assert set(cfg.configurations) == {"C1_baseline", "C2_reduced_work_mem"}
    assert all(spec.session_settings["jit"] == "off" for spec in cfg.configurations.values())
    assert sum(1 for pair in cfg.rewrite_pairs if pair.role == "reference_pair") >= 2
    assert all(pair.rewrite_source == "MANUAL_REFERENCE_REWRITE" for pair in cfg.rewrite_pairs)


def test_even_repetitions_rejected() -> None:
    document = _document()
    document["protocol"]["measured_repetitions"] = 4
    with pytest.raises(ConfigError):
        _parse(document)


def test_estimate_must_be_plain_explain() -> None:
    document = _document()
    document["protocol"]["explain_estimate_options"] = "ANALYZE, FORMAT JSON"
    with pytest.raises(ConfigError):
        _parse(document)


def test_measured_options_must_keep_timing_off_and_summary() -> None:
    document = _document()
    document["protocol"]["explain_measured_options"] = "ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON"
    with pytest.raises(ConfigError):
        _parse(document)


def test_llm_rewrite_source_is_rejected_in_poster_config() -> None:
    document = _document()
    document["rewrite_pairs"][0]["rewrite_source"] = "LLM_REWRITE"
    with pytest.raises(ConfigError):
        _parse(document)


def test_session_setting_allowlist() -> None:
    document = _document()
    document["configurations"]["C1_baseline"]["session_settings"]["statement_timeout"] = "0"
    with pytest.raises(ConfigError):
        _parse(document)


def test_jit_must_stay_off() -> None:
    document = copy.deepcopy(_document())
    document["configurations"]["C1_baseline"]["session_settings"]["jit"] = "on"
    with pytest.raises(ConfigError):
        _parse(document)


def test_render_sql_validates_parameter_values() -> None:
    assert render_sql("SELECT {x}", {"x": "int"}, {"x": 5}) == "SELECT 5"
    with pytest.raises(ConfigError):
        render_sql("SELECT {x}", {"x": "int"}, {"x": "5; DROP TABLE t"})
    with pytest.raises(ConfigError):
        render_sql("SELECT DATE '{d}'", {"d": "date"}, {"d": "2020-02-30"})
    with pytest.raises(ConfigError):
        render_sql("SELECT '{w}'", {"w": "word"}, {"w": "a'b"})
    with pytest.raises(ConfigError):
        render_sql("SELECT {x}", {"x": "int"}, {"y": 1})


def test_env_file_parsing() -> None:
    text = "# comment\nA=1\nB='x y'\nC=C:\\qcp_data # trailing comment\nexport D=\"q\"\n"
    assert parse_env_text(text) == {"A": "1", "B": "x y", "C": "C:\\qcp_data", "D": "q"}


def test_database_settings_reject_placeholders_remote_hosts_and_weak_passwords(
        valid_db_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    assert database_settings_from_env().host == "127.0.0.1"
    monkeypatch.setenv("QCP_DB_HOST", "10.0.0.5")
    with pytest.raises(ConfigError):
        database_settings_from_env()
    monkeypatch.setenv("QCP_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("QCP_BENCH_READER_PASSWORD", "CHANGE_ME_BENCH_READER")
    with pytest.raises(ConfigError):
        database_settings_from_env()
    monkeypatch.setenv("QCP_BENCH_READER_PASSWORD", "short")
    with pytest.raises(ConfigError):
        database_settings_from_env()
    monkeypatch.setenv("QCP_BENCH_READER_PASSWORD", valid_db_env["QCP_BENCH_READER_PASSWORD"])
    monkeypatch.setenv("QCP_EVIDENCE_PORT", valid_db_env["QCP_BENCH_PORT"])
    with pytest.raises(ConfigError):
        database_settings_from_env()


def test_env_example_contains_placeholders_only(repo_root: Path) -> None:
    values = parse_env_text((repo_root / ".env.example").read_text(encoding="utf-8"))
    passwords = {k: v for k, v in values.items() if k.endswith("_PASSWORD")}
    assert len(passwords) == 6
    assert all(v.startswith("CHANGE_ME") for v in passwords.values())


def test_compose_publishes_ports_on_localhost_only(repo_root: Path) -> None:
    lines = (repo_root / "docker-compose.yml").read_text(encoding="utf-8").splitlines()
    port_lines = [line for line in lines if line.strip().startswith('- "') and line.strip().endswith(':5432"')]
    assert len(port_lines) == 2
    assert all('"127.0.0.1:' in line for line in port_lines)


def test_final_plan_arithmetic_is_labelled_planned() -> None:
    plan = summarize_final_plan()
    assert plan["evidence_status"] == "PLANNED"
    assert plan["planned_total_keys"] == 4704
    assert plan["planned_holdout_keys"] == 160
    assert plan["planned_development_keys"] == 4544
    assert plan["planned_groups"] >= 150
