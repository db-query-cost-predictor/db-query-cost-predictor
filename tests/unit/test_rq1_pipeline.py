"""RQ1 Slice C2: the complete production pipeline, its inventory checks, the report copy and the CLI.

SYNTHETIC package and temporary directories only; the real evidence is never read here.
"""

from __future__ import annotations

import json
import os
from argparse import Namespace
from pathlib import Path

import pytest

from query_cost_predictor import cli, paths, rq1_config, rq1_report, rq1_run
from query_cost_predictor.rq1_run import (
    COMPLETE,
    FAILED,
    MANIFEST_FILE,
    PRODUCTION_STAGES,
    REPORT_FILE,
    RUN_RECORD_FILE,
    RunLifecycleError,
    RunRefusedError,
    RunRequirements,
    assert_output_location_safe,
    assert_paths_fit,
    copy_report_after_complete,
    create_run_context,
    execute_run,
    is_production_pipeline,
    production_requirements,
    production_stages,
    read_complete_run,
    read_status,
    unimplemented_stages,
)
from rq1_fixtures import SYNTHETIC_SOURCE_STATE, build_release, production_context, small_bootstrap

pytestmark = [pytest.mark.filterwarnings("error::FutureWarning"), pytest.mark.filterwarnings("error::DeprecationWarning"),
              pytest.mark.filterwarnings("error::sklearn.exceptions.ConvergenceWarning")]

STAGE_ORDER = ["input_check", "regression", "classification_1s", "diagnostics", "handoff"]


@pytest.fixture(scope="module")
def release(tmp_path_factory):
    return build_release(tmp_path_factory.mktemp("release"))


@pytest.fixture(scope="module")
def config(release):
    return release.reconfigure(small_bootstrap)


@pytest.fixture(scope="module")
def completed(tmp_path_factory, release, config):
    context, stages = production_context(tmp_path_factory.mktemp("runs"), release, config)
    return execute_run(context, stages)


# ----------------------------------------------------------------------------- registration
def test_every_production_stage_is_registered_in_the_declared_order():
    config = rq1_config.load_rq1_config()
    assert list(config.stages) == STAGE_ORDER == list(PRODUCTION_STAGES)
    assert unimplemented_stages(config.stages) == []
    stages = production_stages(config.stages)
    assert [name for name, _ in stages] == STAGE_ORDER and is_production_pipeline(stages)
    assert all(function is PRODUCTION_STAGES[name] for name, function in stages)
    with pytest.raises(RunRefusedError, match="not implemented"):
        production_stages([*STAGE_ORDER, "unknown_stage"])
    assert config.uncertainty["replicates"] == 2000 and config.uncertainty["seed"] == 20261021


def test_production_requirements_cover_the_declared_inventory():
    config = rq1_config.load_rq1_config()
    requirements = production_requirements(config)
    outputs = set(requirements.outputs)
    for name in ("run_record.json", "input_check.json", "fold_membership.csv", "oof_regression.csv",
                 "oof_classification.csv", "metrics_regression.json", "metrics_regression.csv",
                 "metrics_classification.json", "metrics_classification.csv", "calibration_bins.csv",
                 "bootstrap_groups.csv", "bootstrap_indices.npz", "bootstrap_replicates.csv", "bootstrap_summary.json",
                 "diagnostics_noise.json", "diagnostics_threshold.json", "report.md", "figures/figure_index.json",
                 "handoff/handoff_manifest.json"):
        assert name in outputs, name
    assert sum(p.startswith("figures/") and p.endswith(".png") for p in outputs) == 9
    assert sum(p.endswith(".joblib") for p in outputs) == 8
    assert "RUN_STATUS.json" not in outputs and "output_manifest.json" not in outputs
    assert set(config.raw["run_record"]["fields"]) <= set(requirements.record_fields)
    assert requirements.complete_report_text == config.limitations_text


def test_production_stages_without_requirements_are_refused_and_fail(tmp_path, release, config):
    context = create_run_context(tmp_path / "results", "run_noreq", "synthetic", config,
                                 evidence_dir=release.evidence_dir, repo_root=release.repo)
    with pytest.raises(RunRefusedError, match="RunRequirements"):
        execute_run(context, production_stages(config.stages))
    status = read_status(context.run_dir)
    assert status["status"] == FAILED and status["completed_stages"] == []
    assert not (context.run_dir / REPORT_FILE).exists() and not (context.run_dir / MANIFEST_FILE).exists()


# ----------------------------------------------------------------------------- a complete synthetic production run
def test_complete_run_has_every_required_output_and_provenance_field(completed, config):
    status = read_status(completed.run_dir)
    assert status["status"] == COMPLETE and status["completed_stages"] == STAGE_ORDER
    files = read_complete_run(completed.run_dir)["files"]
    assert set(production_requirements(config).outputs) <= set(files)
    record = json.loads((completed.run_dir / RUN_RECORD_FILE).read_text(encoding="utf-8"))
    for field in config.raw["run_record"]["fields"]:
        assert record.get(field) is not None, field
    assert record["production_inventory_checked"] is True
    assert record["supplementary_approval_status"] == "verified_this_run"  # the fixture's approval matches
    assert record["historical_source_checkpoint"]["provenance"] == "copied_from_input_record"
    assert record["threadpool_info"]["requested_limit"] == 1
    assert record["source_cleanliness"]["tracked_tree_clean"] is True
    assert record["collection_control_annotations"]["in_sample"]["used_for_rq1_selection_fitting_or_prediction"] is False


def test_complete_report_has_every_part_and_no_partial_banner(completed, config):
    report = (completed.run_dir / REPORT_FILE).read_text(encoding="utf-8")
    assert "PARTIAL REPORT" not in report and config.limitations_text in report
    for text in ("STOP (G-08)", "NOT_YET_EVALUABLE", "## Runtime regression", "## High-runtime classification at 1 s",
                 "## Diagnostics (descriptive; labels never change)", "## Hand-off refits (not evaluated)",
                 "### Robustness check: `fold_family`", "### Seen-template diagnostic: `fold_seen_template`",
                 "paired differences", "one predeclared operating point", "Repetition-noise reference",
                 "Near-threshold sensitivity", "](figures/", "Source revision: `" + "f" * 40):
        assert text in report, text
    assert "winner" not in report.replace("No model is named a winner", "")


# ----------------------------------------------------------------------------- omissions block COMPLETE
def test_a_required_output_that_was_never_registered_blocks_complete(tmp_path, release, config):
    context, stages = production_context(tmp_path, release, config, run_id="run_missing")
    base = production_requirements(config)
    context.requirements = RunRequirements(outputs=(*base.outputs, "figures/never_written.png"),
                                           record_fields=base.record_fields,
                                           complete_report_text=base.complete_report_text)
    with pytest.raises(RunLifecycleError, match="never written and registered"):
        execute_run(context, stages)
    status = read_status(context.run_dir)
    assert status["status"] == FAILED and status["failed_stage"] == "finalization"
    assert not (context.run_dir / REPORT_FILE).exists() and (context.run_dir / (REPORT_FILE + ".FAILED")).exists()
    assert not (context.run_dir / MANIFEST_FILE).exists()


def test_a_missing_provenance_field_blocks_complete(tmp_path, release, config, monkeypatch):
    original = rq1_report.assemble_final_outputs

    def drop_approval(context):
        context.provenance.pop("supplementary_approval_status")
        original(context)
    monkeypatch.setattr(rq1_report, "assemble_final_outputs", drop_approval)
    context, stages = production_context(tmp_path, release, config, run_id="run_noprov")
    with pytest.raises(RunLifecycleError, match="supplementary_approval_status"):
        execute_run(context, stages)
    assert read_status(context.run_dir)["status"] == FAILED
    assert not (context.run_dir / RUN_RECORD_FILE).exists()


def test_a_partial_report_blocks_complete(tmp_path, release, config, monkeypatch):
    from query_cost_predictor.rq1_bootstrap import required_bootstrap_tasks, write_bootstrap_outputs

    def partial(context):
        write_bootstrap_outputs(context, required_bootstrap_tasks(context.config))
        sections = context.artifacts["report_sections"]
        context.artifacts["report_sections"] = {"regression": sections["regression"]}
        rq1_report.write_report(context, ["regression"])  # renders with the PARTIAL banner
    monkeypatch.setattr(rq1_report, "assemble_final_outputs", partial)
    context, stages = production_context(tmp_path, release, config, run_id="run_partial")
    with pytest.raises(RunLifecycleError, match="partial"):
        execute_run(context, stages)
    assert read_status(context.run_dir)["status"] == FAILED


def test_production_assembly_refuses_a_missing_section(tmp_path, release, config):
    context = create_run_context(tmp_path / "results", "run_sections", "synthetic", config)
    context.artifacts["report_sections"] = {"regression": "## Runtime regression\n"}
    with pytest.raises(rq1_report.Rq1ReportError, match="missing"):
        rq1_report.write_report(context)


# ----------------------------------------------------------------------------- report copy after COMPLETE only
def test_report_copy_only_after_verified_complete(completed, tmp_path):
    destination = copy_report_after_complete(completed.run_dir, tmp_path / "reports" / "rq1")
    assert destination == tmp_path / "reports" / "rq1" / completed.run_dir.name
    assert (destination / REPORT_FILE).read_bytes() == (completed.run_dir / REPORT_FILE).read_bytes()
    figures = sorted(p.relative_to(destination).as_posix() for p in (destination / "figures").rglob("*") if p.is_file())
    assert figures == sorted(n for n in read_complete_run(completed.run_dir)["files"] if n.startswith("figures/"))
    with pytest.raises(FileExistsError):  # never overwritten
        copy_report_after_complete(completed.run_dir, tmp_path / "reports" / "rq1")
    assert read_status(completed.run_dir)["status"] == COMPLETE


def test_no_copy_from_running_failed_or_tampered_runs(tmp_path):
    running = create_run_context(tmp_path / "results", "run_running", "synthetic", None)
    running.write_payload(REPORT_FILE, b"# not a study\n")
    with pytest.raises(RunLifecycleError, match="not COMPLETE"):
        copy_report_after_complete(running.run_dir, tmp_path / "copies")
    failed = create_run_context(tmp_path / "results", "run_failed", "synthetic", None)

    def boom(context):
        context.write_payload(REPORT_FILE, b"# looks finished\n")
        raise ValueError("synthetic failure")
    with pytest.raises(ValueError):
        execute_run(failed, [("boom", boom)])
    with pytest.raises(RunLifecycleError, match="not COMPLETE"):
        copy_report_after_complete(failed.run_dir, tmp_path / "copies")
    done = execute_run(create_run_context(tmp_path / "results", "run_done", "synthetic", None),
                       [("report", lambda c: c.write_payload(REPORT_FILE, b"# synthetic\n"))])
    (done.run_dir / REPORT_FILE).write_bytes(b"# tampered\n")
    with pytest.raises(RunLifecycleError, match="differs"):
        copy_report_after_complete(done.run_dir, tmp_path / "copies")
    assert not (tmp_path / "copies").exists()


# ----------------------------------------------------------------------------- output locations
def test_output_locations_may_not_overlap_inputs_but_siblings_are_fine(tmp_path):
    data_root = tmp_path / "data_root"
    evidence = tmp_path / "evidence"
    approval = data_root / "manifests" / "expanded" / "approvals" / "FULL.json"
    for folder in (evidence / "derived", approval.parent):
        folder.mkdir(parents=True)
    approval.write_text("{}", encoding="utf-8")
    protected = {"evidence directory": evidence, "supplementary approval record": approval, "archive": None}
    assert_output_location_safe(data_root / "results" / "rq1" / "rq1-modeling-v1", protected)  # sibling: allowed
    for unsafe in (evidence / "results", evidence, tmp_path, approval.parent, approval):
        with pytest.raises(RunRefusedError, match="overlaps"):
            assert_output_location_safe(unsafe, protected)


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions only")
def test_output_location_through_a_junction_into_the_evidence_is_refused(tmp_path):
    import _winapi

    evidence = tmp_path / "evidence"
    (evidence / "derived").mkdir(parents=True)
    alias = tmp_path / "alias"
    _winapi.CreateJunction(str(evidence), str(alias))
    with pytest.raises(RunRefusedError, match="overlaps"):
        assert_output_location_safe(alias / "results", {"evidence directory": evidence})


# ----------------------------------------------------------------------------- the CLI end to end (synthetic)
@pytest.mark.skipif(os.name != "nt", reason="the MAX_PATH guard applies on Windows only")
def test_overlong_output_paths_are_refused_before_anything_is_created(tmp_path):
    deep = tmp_path / ("d" * 120) / ("e" * 60)
    with pytest.raises(RunRefusedError, match="shorter QCP_DATA_ROOT"):
        assert_paths_fit(deep, ["figures/calibration/fold_group__clf_scale_cost_logistic__pooled_oof__all.png"])
    assert not deep.exists()
    assert_paths_fit(tmp_path, ["report.md"])


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    release = build_release(tmp_path / "r")  # short: Windows MAX_PATH applies to the nested run outputs
    config = release.reconfigure(small_bootstrap)
    monkeypatch.setattr(rq1_config, "load_rq1_config", lambda path=None: config)
    monkeypatch.setattr(paths, "repo_root", lambda: release.repo)
    monkeypatch.setattr(cli, "_rq1_source", lambda repo, cfg: (dict(SYNTHETIC_SOURCE_STATE), []))
    monkeypatch.setenv("QCP_DATA_ROOT", str(release.data_root))
    return release


def run_cli(release, label="synthetic"):
    return cli.cmd_rq1_run(Namespace(evidence_dir=str(release.evidence_dir), archive=str(release.archive),
                                     run_label=label))


def test_cli_completes_verifies_and_then_copies_the_report(cli_env, capsys):
    release = cli_env
    code = run_cli(release)
    output = capsys.readouterr().out
    assert code == 0 and "RQ1-RUN: COMPLETE (verified" in output and "report copy:" in output
    runs = list((release.data_root / "results" / "rq1" / "rq1-modeling-v1").iterdir())
    assert len(runs) == 1 and read_status(runs[0])["status"] == COMPLETE
    manifest = read_complete_run(runs[0])
    copy = release.repo / "reports" / "rq1" / runs[0].name
    assert (copy / REPORT_FILE).read_bytes() == (runs[0] / REPORT_FILE).read_bytes()
    assert all((copy / n).is_file() for n in manifest["files"] if n.startswith("figures/"))
    record = json.loads((runs[0] / RUN_RECORD_FILE).read_text(encoding="utf-8"))
    assert record["archive_status"] == "verified_this_run" and record["supplementary_approval_status"] == "verified_this_run"


def test_cli_copy_failure_keeps_the_completed_run(cli_env, capsys, monkeypatch):
    release = cli_env

    def failing_copy(*args, **kwargs):
        raise OSError("synthetic copy failure")
    monkeypatch.setattr(rq1_run, "copy_report_after_complete", failing_copy)
    code = run_cli(release)
    output = capsys.readouterr().out
    assert code == 4 and "REPORT-COPY: FAILED" in output and "RQ1-RUN: COMPLETE" in output
    run_dir = next((release.data_root / "results" / "rq1" / "rq1-modeling-v1").iterdir())
    assert read_status(run_dir)["status"] == COMPLETE
    read_complete_run(run_dir)
    assert not (release.repo / "reports" / "rq1").exists()


def test_cli_refuses_an_output_location_inside_the_evidence(cli_env, capsys, monkeypatch):
    release = cli_env
    inside = release.evidence_dir / "nested_data_root"
    inside.mkdir()
    monkeypatch.setenv("QCP_DATA_ROOT", str(inside))
    before = sorted(p.relative_to(release.evidence_dir).as_posix() for p in release.evidence_dir.rglob("*"))
    assert run_cli(release) == 3
    assert "unsafe output location" in capsys.readouterr().out
    assert sorted(p.relative_to(release.evidence_dir).as_posix() for p in release.evidence_dir.rglob("*")) == before


def test_cli_refuses_an_ineligible_source_without_a_run_directory(cli_env, capsys, monkeypatch):
    release = cli_env
    monkeypatch.setattr(cli, "_rq1_source", lambda repo, cfg: (None, ["tracked tree is not clean (1 change(s))"]))
    assert run_cli(release) == 3
    assert "source not eligible" in capsys.readouterr().out
    assert not (release.data_root / "results").exists()
