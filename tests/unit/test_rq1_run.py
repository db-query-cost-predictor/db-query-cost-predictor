"""RQ1 Slice A: run lifecycle, output-manifest finalization and refusal (synthetic stages, temp folders)."""

from __future__ import annotations

import json
import warnings
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from query_cost_predictor import cli
from query_cost_predictor import rq1_run
from query_cost_predictor.rq1_run import (
    COMPLETE,
    EXCLUDED_FROM_MANIFEST,
    FAILED,
    MANIFEST_FILE,
    REPORT_FILE,
    RUN_RECORD_FILE,
    STATUS_FILE,
    RunDirectoryExistsError,
    RunLifecycleError,
    RunRefusedError,
    create_run_context,
    execute_run,
    make_run_id,
    production_stages,
    read_complete_run,
    read_status,
    stage_input_check,
    unimplemented_stages,
)
from rq1_fixtures import build_release


def new_context(tmp_path: Path, run_id: str = "run_a"):
    return create_run_context(tmp_path / "results", run_id, "test", config=None)


def write_payloads(context):
    context.write_json_payload("metrics_regression.json", {"synthetic": True})
    context.write_payload("figures/plot.txt", b"synthetic figure")


def write_report(context):
    context.write_payload(REPORT_FILE, b"# synthetic report\n")


def test_run_id_format():
    when = datetime(2026, 10, 22, 1, 2, 3, tzinfo=timezone.utc)
    assert make_run_id(when, "a" * 64, "b" * 40) == f"20261022T010203Z_{'a' * 16}_{'b' * 12}"


def test_run_directory_is_created_exclusively(tmp_path):
    new_context(tmp_path)
    with pytest.raises(RunDirectoryExistsError):
        new_context(tmp_path)


def test_running_is_written_first(tmp_path):
    context = new_context(tmp_path)
    assert read_status(context.run_dir)["status"] == "RUNNING"


def test_complete_run_manifest_covers_payloads_and_excludes_lifecycle_files(tmp_path):
    context = execute_run(new_context(tmp_path), [("payloads", write_payloads), ("report", write_report)])
    status = read_status(context.run_dir)
    assert status["status"] == COMPLETE and status["completed_stages"] == ["payloads", "report"]
    manifest = read_complete_run(context.run_dir)
    assert set(manifest["files"]) == {"metrics_regression.json", "figures/plot.txt", REPORT_FILE, RUN_RECORD_FILE}
    assert not set(EXCLUDED_FROM_MANIFEST) & set(manifest["files"])
    assert tuple(manifest["excluded"]) == (STATUS_FILE, MANIFEST_FILE)
    record = json.loads((context.run_dir / RUN_RECORD_FILE).read_text(encoding="utf-8"))
    assert record["threadpool"]["requested_limit"] == 1
    assert [entry["status"] for entry in record["status_history"]] == ["RUNNING", "RUNNING", "RUNNING"]


def test_stage_failure_leaves_failed_and_no_success_looking_outputs(tmp_path):
    def boom(context):
        raise ValueError("synthetic failure")
    context = new_context(tmp_path)
    with pytest.raises(ValueError):
        execute_run(context, [("payloads", write_payloads), ("report", write_report), ("boom", boom)])
    status = read_status(context.run_dir)
    assert status["status"] == FAILED and status["failed_stage"] == "boom"
    assert status["last_completed_stage"] == "report" and status["error"]["type"] == "ValueError"
    assert not (context.run_dir / REPORT_FILE).exists() and (context.run_dir / (REPORT_FILE + ".FAILED")).exists()
    assert not (context.run_dir / MANIFEST_FILE).exists()
    with pytest.raises(RunLifecycleError, match="not COMPLETE"):
        read_complete_run(context.run_dir)


def test_unregistered_file_fails_finalization(tmp_path):
    def stray(context):
        (context.run_dir / "stray.csv").write_text("not registered", encoding="utf-8")
    context = new_context(tmp_path)
    with pytest.raises(RunLifecycleError, match="unregistered"):
        execute_run(context, [("report", write_report), ("stray", stray)])
    status = read_status(context.run_dir)
    assert status["status"] == FAILED and status["failed_stage"] == "finalization"
    assert not (context.run_dir / REPORT_FILE).exists() and not (context.run_dir / MANIFEST_FILE).exists()


def test_payload_changed_before_manifest_verification_fails(tmp_path, monkeypatch):
    original = rq1_run.sha256_file

    def tampering_hash(path):
        if Path(path).name == "metrics_regression.json":
            return "0" * 64
        return original(path)
    monkeypatch.setattr(rq1_run, "sha256_file", tampering_hash)
    context = new_context(tmp_path)
    with pytest.raises(RunLifecycleError, match="changed after it was written"):
        execute_run(context, [("payloads", write_payloads), ("report", write_report)])
    assert read_status(context.run_dir)["status"] == FAILED
    assert (context.run_dir / (MANIFEST_FILE + ".FAILED")).exists() and not (context.run_dir / MANIFEST_FILE).exists()


def test_payloads_are_immutable_and_lifecycle_names_are_reserved(tmp_path):
    context = new_context(tmp_path)
    context.write_payload("a.txt", b"x")
    with pytest.raises(RunLifecycleError):
        context.write_payload("a.txt", b"y")
    for reserved in (STATUS_FILE, MANIFEST_FILE, "../escape.txt"):
        with pytest.raises(RunLifecycleError):
            context.write_payload(reserved, b"x")


def test_tampering_after_complete_is_detected(tmp_path):
    context = execute_run(new_context(tmp_path), [("payloads", write_payloads), ("report", write_report)])
    (context.run_dir / "metrics_regression.json").write_text('{"tampered": true}', encoding="utf-8")
    with pytest.raises(RunLifecycleError, match="differs from the output manifest"):
        read_complete_run(context.run_dir)


def test_convergence_warning_fails_the_run(tmp_path):
    from sklearn.exceptions import ConvergenceWarning

    def warns(context):
        warnings.warn("synthetic", ConvergenceWarning)
    context = new_context(tmp_path)
    with pytest.raises(ConvergenceWarning):
        execute_run(context, [("fit", warns)])
    assert read_status(context.run_dir)["status"] == FAILED


def test_production_pipeline_refuses_missing_stages():
    stages = ("input_check", "regression", "classification_1s", "diagnostics", "handoff")
    assert unimplemented_stages(stages) == []
    assert [name for name, _ in production_stages(stages)] == list(stages)
    assert unimplemented_stages((*stages, "not_a_stage")) == ["not_a_stage"]
    with pytest.raises(RunRefusedError, match="not implemented"):  # an undeclared stage is never skipped
        production_stages((*stages, "not_a_stage"))


def test_rq1_run_cli_refuses_without_creating_a_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QCP_DATA_ROOT", str(tmp_path / "data_root"))
    (tmp_path / "data_root").mkdir()
    monkeypatch.setattr(cli, "_rq1_source", lambda repo, config: (None, ["tracked tree is not clean (1 change(s))"]))
    code = cli.cmd_rq1_run(Namespace(evidence_dir=str(tmp_path), archive=None, run_label="test"))
    assert code == 3
    assert "RQ1-RUN: REFUSED" in capsys.readouterr().out
    assert list((tmp_path / "data_root").iterdir()) == []


def test_input_check_stage_runs_inside_a_complete_synthetic_run(tmp_path):
    release = build_release(tmp_path / "release")
    context = create_run_context(tmp_path / "results", "run_b", "test", release.config,
                                 evidence_dir=release.evidence_dir, repo_root=release.repo)
    execute_run(context, [("input_check", stage_input_check), ("report", write_report)])
    manifest = read_complete_run(context.run_dir)
    assert "input_check.json" in manifest["files"]
    assert context.artifacts["dataset"].summary()["development_cases"] == 40
    check = json.loads((context.run_dir / "input_check.json").read_text(encoding="utf-8"))
    assert check["original_release_validation"]["status"] == "STOP"
    assert check["collection_control_annotations"]["in_sample"]["used_for_rq1_selection_fitting_or_prediction"] is False
