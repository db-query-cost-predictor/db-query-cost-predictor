"""RQ1 Slice A safety regressions: --json-out destinations, payload containment, terminal run states.

Synthetic fixtures and temporary directories only; the real evidence is never touched.
"""

from __future__ import annotations

import json
import os
from argparse import Namespace
from pathlib import Path

import pytest

from query_cost_predictor import cli, paths, rq1_config
from query_cost_predictor.rq1_run import (
    COMPLETE,
    FAILED,
    MANIFEST_FILE,
    REPORT_FILE,
    STATUS_FILE,
    RunContext,
    RunLifecycleError,
    create_run_context,
    execute_run,
    finalize_run,
    read_complete_run,
    read_status,
    validate_payload_path,
)
from rq1_fixtures import build_release


def make_directory_alias(link: Path, target: Path) -> None:
    """Directory symlink, or a junction where Windows refuses unprivileged symlinks."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("cannot create a directory symlink here")
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


def snapshot(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


# ----------------------------------------------------------------------------- 1. rq1-check-inputs --json-out
@pytest.fixture
def checker(tmp_path, monkeypatch):
    release = build_release(tmp_path / "release")
    monkeypatch.setattr(rq1_config, "load_rq1_config", lambda path=None: release.config)
    monkeypatch.setattr(paths, "repo_root", lambda: release.repo)
    monkeypatch.setattr(cli, "load_repo_env", lambda: None)
    monkeypatch.delenv("QCP_DATA_ROOT", raising=False)
    return release


def run_check(release, json_out):
    return cli.cmd_rq1_check_inputs(Namespace(evidence_dir=str(release.evidence_dir), archive=None,
                                              json_out=None if json_out is None else str(json_out)))


@pytest.mark.parametrize("target", ["record", "config_yaml", "registry"])
def test_json_out_cannot_overwrite_repository_files(checker, capsys, target):
    path = {"record": checker.record_path, "config_yaml": checker.repo / "config" / "expanded_release_v1.yaml",
            "registry": checker.repo / "config" / "feature_registry.yaml"}[target]
    before = path.read_bytes()
    assert run_check(checker, path) == 2
    output = capsys.readouterr().out
    assert "RQ1-CHECK-INPUTS: PASS" not in output and "FAIL" in output
    assert path.read_bytes() == before


def test_json_out_cannot_create_a_new_file_inside_the_repository(checker):
    target = checker.repo / "reports" / "new_report.json"
    assert run_check(checker, target) == 2
    assert not target.exists() and not target.parent.exists()


def test_json_out_cannot_write_inside_the_evidence_directory(checker):
    before = snapshot(checker.evidence_dir)
    assert run_check(checker, checker.evidence_dir / "derived" / "keys.csv") == 2
    assert run_check(checker, checker.evidence_dir / "new.json") == 2
    assert snapshot(checker.evidence_dir) == before


def test_json_out_cannot_overwrite_an_existing_outside_file(checker, tmp_path):
    existing = tmp_path / "outside" / "existing.json"
    existing.parent.mkdir()
    existing.write_text("keep me", encoding="utf-8")
    assert run_check(checker, existing) == 2
    assert existing.read_text(encoding="utf-8") == "keep me"


def test_json_out_through_a_directory_alias_into_the_repository_is_refused(checker, tmp_path):
    alias = tmp_path / "alias_to_repo_config"
    make_directory_alias(alias, checker.repo / "config")
    assert run_check(checker, alias / "new_report.json") == 2
    assert not (checker.repo / "config" / "new_report.json").exists()
    assert run_check(checker, alias / "rq1_modeling_input_v1.json") == 2


def test_json_out_is_created_exclusively_even_if_a_file_appears_after_validation(checker, tmp_path, monkeypatch, capsys):
    racing = tmp_path / "outside" / "race.json"
    racing.parent.mkdir()
    racing.write_text("created by someone else", encoding="utf-8")
    monkeypatch.setattr(cli, "_rq1_report_destination", lambda raw, protected: racing)  # bypass the early check
    assert run_check(checker, racing) == 2
    output = capsys.readouterr().out
    assert "RQ1-CHECK-INPUTS: PASS" not in output
    assert racing.read_text(encoding="utf-8") == "created by someone else"


def test_json_out_success_writes_one_new_report_and_no_run_directory(checker, tmp_path, capsys):
    target = tmp_path / "outside" / "nested" / "input_check.json"
    before = {p for p in tmp_path.rglob("*")}
    assert run_check(checker, target) == 0
    assert "RQ1-CHECK-INPUTS: PASS" in capsys.readouterr().out
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "PASS"
    created = {p for p in tmp_path.rglob("*")} - before
    assert created == {target, target.parent, target.parent.parent}


# ----------------------------------------------------------------------------- 2. payload containment
def new_context(tmp_path: Path, run_id: str = "run_a") -> RunContext:
    return create_run_context(tmp_path / "results", run_id, "test", config=None)


@pytest.mark.parametrize("relative", [
    "/etc/escape.txt", "C:\\escape.txt", "C:escape.txt", "c:/escape.txt", "\\\\server\\share\\escape.txt",
    "\\rooted.txt", "//server/share/escape.txt", "a\\b.txt", "../escape.txt", "a/../../escape.txt", "a/../b.txt",
    "a//b.txt", "./a.txt", "a/./b.txt", "", ".hidden", "a/.hidden", "trailing.", "trailing ", "CON", "nul.txt",
    "figures/COM1.png", "data.csv:stream", "RUN_STATUS.json", "run_status.JSON", "output_manifest.json",
    "report.md.FAILED", "partial.tmp", "a\0b",
])
def test_unsafe_payload_paths_are_rejected_before_anything_is_created(tmp_path, relative):
    context = new_context(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(RunLifecycleError):
        context.write_payload(relative, b"x")
    assert snapshot(tmp_path) == before and context.payloads == {}


def test_absolute_native_path_and_non_string_are_rejected(tmp_path):
    context = new_context(tmp_path)
    outside = tmp_path / "outside.txt"
    for value in (str(outside), outside, b"a.txt", None):
        with pytest.raises(RunLifecycleError):
            context.write_payload(value, b"x")  # type: ignore[arg-type]
    assert not outside.exists()


def test_parent_alias_escaping_the_run_directory_is_rejected(tmp_path):
    context = new_context(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    make_directory_alias(context.run_dir / "escape", outside)
    with pytest.raises(RunLifecycleError, match="leave the run directory"):
        context.write_payload("escape/x.txt", b"x")
    with pytest.raises(RunLifecycleError):
        context.write_payload("escape/deeper/x.txt", b"x")
    assert list(outside.iterdir()) == [] and context.payloads == {}


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions only")
def test_windows_junctions_cannot_bypass_either_boundary(tmp_path, checker):
    import _winapi

    context = new_context(tmp_path, "run_junction")
    outside = tmp_path / "outside_junction_target"
    outside.mkdir()
    _winapi.CreateJunction(str(outside), str(context.run_dir / "junction"))
    with pytest.raises(RunLifecycleError):
        context.write_payload("junction/x.txt", b"x")
    assert list(outside.iterdir()) == []
    _winapi.CreateJunction(str(checker.repo / "config"), str(tmp_path / "junction_to_repo"))
    assert run_check(checker, tmp_path / "junction_to_repo" / "new_report.json") == 2
    assert not (checker.repo / "config" / "new_report.json").exists()


def test_final_component_symlink_is_rejected(tmp_path):
    context = new_context(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    try:
        os.symlink(outside, context.run_dir / "link.txt")
    except OSError:
        pytest.skip("file symlinks need extra privileges on this machine")
    with pytest.raises(RunLifecycleError):
        context.write_payload("link.txt", b"x")
    assert outside.read_text(encoding="utf-8") == "keep"


def test_case_variant_duplicates_are_rejected(tmp_path):
    context = new_context(tmp_path)
    context.write_payload("metrics.json", b"{}")
    with pytest.raises(RunLifecycleError):
        context.write_payload("METRICS.json", b"{}")


def test_valid_nested_payloads_register_the_same_paths_as_on_disk(tmp_path):
    context = new_context(tmp_path)
    for name in ("figures/sub/pr_curve.png", "metrics_regression.csv", "a-b_c.d/e.txt"):
        assert validate_payload_path(name) == name
    def stage(ctx):
        ctx.write_payload("figures/sub/pr_curve.png", b"png")
        ctx.write_payload("metrics_regression.csv", b"a,b\n")
        ctx.write_payload(REPORT_FILE, b"# report\n")
    execute_run(context, [("stage", stage)])
    manifest = read_complete_run(context.run_dir)
    assert "figures/sub/pr_curve.png" in manifest["files"]


# ----------------------------------------------------------------------------- 3. terminal run states
def complete_run(tmp_path: Path) -> RunContext:
    def stage(ctx):
        ctx.write_payload("metrics.json", b"{}")
        ctx.write_payload(REPORT_FILE, b"# report\n")
    return execute_run(new_context(tmp_path, "run_done"), [("stage", stage)])


def test_complete_run_refuses_writes_execution_and_finalization_without_changes(tmp_path):
    context = complete_run(tmp_path)
    assert context.state == COMPLETE
    before = snapshot(context.run_dir)
    with pytest.raises(RunLifecycleError, match="COMPLETE"):
        context.write_payload("late.json", b"{}")
    with pytest.raises(RunLifecycleError, match="COMPLETE"):
        context.write_json_payload("late2.json", {})
    with pytest.raises(RunLifecycleError, match="COMPLETE"):
        execute_run(context, [("again", lambda ctx: ctx.write_payload("x.json", b"{}"))])
    with pytest.raises(RunLifecycleError, match="COMPLETE"):
        finalize_run(context)
    assert snapshot(context.run_dir) == before
    assert read_status(context.run_dir)["status"] == COMPLETE
    read_complete_run(context.run_dir)  # the manifest is still valid


def test_failed_run_refuses_writes_execution_and_finalization_without_changes(tmp_path):
    def boom(ctx):
        ctx.write_payload("partial.json", b"{}")
        raise ValueError("synthetic")
    context = new_context(tmp_path, "run_failed")
    with pytest.raises(ValueError):
        execute_run(context, [("boom", boom)])
    assert context.state == FAILED
    before = snapshot(context.run_dir)
    for operation in (lambda: context.write_payload("late.json", b"{}"),
                      lambda: execute_run(context, [("again", lambda ctx: None)]),
                      lambda: finalize_run(context)):
        with pytest.raises(RunLifecycleError, match="FAILED"):
            operation()
    assert snapshot(context.run_dir) == before
    assert read_status(context.run_dir)["status"] == FAILED and not (context.run_dir / MANIFEST_FILE).exists()


def test_terminal_status_on_disk_is_respected_by_another_context_object(tmp_path):
    context = complete_run(tmp_path)
    before = snapshot(context.run_dir)
    other = RunContext(run_id=context.run_id, run_label="other", run_dir=context.run_dir, config=None)
    with pytest.raises(RunLifecycleError, match="COMPLETE"):
        other.write_payload("late.json", b"{}")
    with pytest.raises(RunLifecycleError, match="COMPLETE"):
        execute_run(other, [("again", lambda ctx: None)])
    assert snapshot(context.run_dir) == before


def test_finalization_from_inside_a_stage_is_refused_and_the_run_fails(tmp_path):
    def finalize_early(ctx):
        ctx.write_payload(REPORT_FILE, b"# report\n")
        finalize_run(ctx)  # a misbehaving stage tries to publish COMPLETE itself
    context = new_context(tmp_path, "run_early")
    with pytest.raises(RunLifecycleError, match="inside the active stage 'early'"):
        execute_run(context, [("early", finalize_early)])
    status = read_status(context.run_dir)
    assert status["status"] == FAILED and status["failed_stage"] == "early" and status["last_completed_stage"] is None
    assert not (context.run_dir / "run_record.json").exists() and not (context.run_dir / MANIFEST_FILE).exists()
    assert not (context.run_dir / REPORT_FILE).exists() and (context.run_dir / (REPORT_FILE + ".FAILED")).exists()
    assert REPORT_FILE in status["quarantined"]
    with pytest.raises(RunLifecycleError, match="not COMPLETE"):
        read_complete_run(context.run_dir)


def test_premature_finalization_in_a_multi_stage_run_stops_later_stages(tmp_path):
    calls: list[str] = []

    def first(ctx):
        calls.append("first")
        ctx.write_payload("first.json", b"{}")
        ctx.write_payload(REPORT_FILE, b"# premature report\n")
        finalize_run(ctx)

    def later(name):
        def stage(ctx):
            calls.append(name)
            ctx.write_payload(f"{name}.json", b"{}")
        return stage

    context = new_context(tmp_path, "run_multi")
    with pytest.raises(RunLifecycleError):
        execute_run(context, [("first", first), ("second", later("second")), ("third", later("third"))])
    assert calls == ["first"]  # later stages were never called
    status = read_status(context.run_dir)
    assert status["status"] == FAILED and status["failed_stage"] == "first"
    assert status["planned_stages"] == ["first", "second", "third"] and status["completed_stages"] == []
    files = set(snapshot(context.run_dir))
    assert {"second.json", "third.json", "run_record.json", MANIFEST_FILE, REPORT_FILE}.isdisjoint(files)
    with pytest.raises(RunLifecycleError, match="not COMPLETE"):
        read_complete_run(context.run_dir)
    with pytest.raises(RunLifecycleError, match="FAILED"):
        execute_run(context, [("retry", later("retry"))])  # the failed run stays immutable


def test_finalization_needs_the_full_planned_sequence(tmp_path):
    context = new_context(tmp_path, "run_direct")
    with pytest.raises(RunLifecycleError, match="planned stage sequence"):
        finalize_run(context)  # never orchestrated: nothing planned, nothing completed
    assert set(snapshot(context.run_dir)) == {STATUS_FILE} and read_status(context.run_dir)["status"] == "RUNNING"


def test_nested_execution_from_inside_a_stage_is_refused(tmp_path):
    def nested(ctx):
        execute_run(ctx, [("inner", lambda inner: None)])
    context = new_context(tmp_path, "run_nested")
    with pytest.raises(RunLifecycleError, match="once per run"):
        execute_run(context, [("outer", nested)])
    assert read_status(context.run_dir)["status"] == FAILED


def test_reader_rejects_a_complete_status_with_an_unfinished_stage_sequence(tmp_path):
    context = complete_run(tmp_path)
    status_path = context.run_dir / STATUS_FILE
    document = json.loads(status_path.read_text(encoding="utf-8"))
    document["planned_stages"] = ["stage", "never_ran"]  # simulate a COMPLETE published too early
    status_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RunLifecycleError, match="planned stages"):
        read_complete_run(context.run_dir)


def test_full_sequence_finalizes_and_records_the_plan(tmp_path):
    context = execute_run(new_context(tmp_path, "run_full"), [
        ("a", lambda ctx: ctx.write_payload("a.json", b"{}")),
        ("b", lambda ctx: ctx.write_payload(REPORT_FILE, b"# report\n"))])
    status = read_status(context.run_dir)
    assert status["status"] == COMPLETE and status["planned_stages"] == status["completed_stages"] == ["a", "b"]
    assert context.active_stage is None
    read_complete_run(context.run_dir)


def test_running_writes_and_failure_recording_still_work(tmp_path):
    context = new_context(tmp_path)
    context.write_payload("ok.json", b"{}")
    assert context.state == "RUNNING" and read_status(context.run_dir)["status"] == "RUNNING"
    with pytest.raises(KeyError):
        execute_run(context, [("fail", lambda ctx: {}["missing"])])
    status = read_status(context.run_dir)
    assert status["status"] == FAILED and status["error"]["type"] == "KeyError" and STATUS_FILE == "RUN_STATUS.json"
