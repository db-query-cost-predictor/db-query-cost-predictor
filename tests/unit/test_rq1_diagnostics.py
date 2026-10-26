"""RQ1 Slice C2: repetition-noise reference and near-threshold sensitivity (SYNTHETIC data only)."""

from __future__ import annotations

import csv
import io
import json
import statistics
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from query_cost_predictor import rq1_models
from query_cost_predictor.hashing import sha256_file
from query_cost_predictor.rq1_diagnostics import (
    BAND_DEFINITION,
    EVALUATIONS,
    EXECUTIONS_FILE,
    NOISE_FILE,
    STRADDLE_DEFINITION,
    THRESHOLD_FILE,
    Rq1DiagnosticsError,
    check_diagnostics_spec,
    coefficient_of_variation,
    leave_one_run_out_qerror,
    load_measured_runs,
    threshold_document,
    threshold_sets,
)
from query_cost_predictor.rq1_config import load_rq1_config
from query_cost_predictor.rq1_evaluate import evaluate_classification, load_feature_allowlist, pinned_evidence_file
from query_cost_predictor.rq1_inputs import Rq1InputError, check_inputs
from query_cost_predictor.rq1_metrics import REASON_EMPTY, classification_metric_values
from query_cost_predictor.rq1_run import (
    COMPLETE,
    create_run_context,
    execute_run,
    read_complete_run,
    read_status,
    stage_classification_1s,
    stage_diagnostics,
    stage_input_check,
    stage_regression,
)
from rq1_fixtures import build_release, small_bootstrap

pytestmark = [pytest.mark.filterwarnings("error::FutureWarning"), pytest.mark.filterwarnings("error::DeprecationWarning"),
              pytest.mark.filterwarnings("error::sklearn.exceptions.ConvergenceWarning")]


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    release = build_release(tmp_path_factory.mktemp("release"))
    config = release.reconfigure(small_bootstrap)
    dataset = check_inputs(config, release.evidence_dir, repo_root=release.repo).dataset
    return release, config, dataset


def executions_rows(release) -> list[dict[str, str]]:
    with (release.evidence_dir / EXECUTIONS_FILE).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


# ----------------------------------------------------------------------------- definitions
def test_yaml_diagnostics_definitions_match_the_implementation(tmp_path):
    check_diagnostics_spec(load_rq1_config())
    release = build_release(tmp_path)
    changed = release.reconfigure(lambda d: d["diagnostics"]["threshold_sensitivity"].__setitem__(
        "band_set", "development cases with 750 ms <= median_ms < 1250 ms"))
    with pytest.raises(Rq1DiagnosticsError, match="band"):
        check_diagnostics_spec(changed)


def test_leave_one_run_out_qerror_and_sample_cv_match_hand_calculation():
    runs = np.array([[90.0, 100.0, 110.0], [1.0, 2.0, 4.0]])
    q = leave_one_run_out_qerror(runs)
    assert q[0] == pytest.approx([105 / 90, 1.0, 110 / 95])
    assert q[1] == pytest.approx([3.0, 2.5 / 2.0, 4.0 / 1.5])  # other-run means 3, 2.5 and 1.5
    cv = coefficient_of_variation(runs)
    assert cv[0] == pytest.approx(10.0 / 100.0)
    assert cv[1] == pytest.approx(statistics.stdev([1.0, 2.0, 4.0]) / statistics.fmean([1.0, 2.0, 4.0]))
    with pytest.raises(Rq1DiagnosticsError):
        leave_one_run_out_qerror(np.array([[1.0, 0.0, 2.0]]))


# ----------------------------------------------------------------------------- measured runs
def test_measured_runs_exclude_probes_ignore_heldout_and_reconcile_medians(prepared):
    release, _, dataset = prepared
    runs, counts = load_measured_runs(release.evidence_dir / EXECUTIONS_FILE, dataset)
    assert runs.shape == (40, 3) and list(runs.columns) == ["run_1", "run_2", "run_3"]
    assert counts["measured_runs_used"] == 120 and counts["development_probe_rows_excluded"] == 40
    assert counts["non_development_rows_ignored"] == 15  # 4 heldout keys: 3 x 4 rows + 1 censored key x 3 rows
    assert np.array_equal(np.median(runs.to_numpy(), axis=1), dataset.targets["median_ms"].to_numpy())


@pytest.mark.parametrize("mutation, message", [
    ("drop_run", "lack one of the measured runs"),
    ("duplicate_run", "duplicate measured repeat_no"),
    ("timeout", "not completed and uncensored"),
    ("censored_flag", "not completed and uncensored"),
    ("zero_time", "non-positive"),
    ("nan_time", "non-finite"),
    ("blank_time", "non-numeric"),
    ("repeat_four", "outside"),
    ("unknown_purpose", "unknown run_purpose"),
    ("median_mismatch", "median that differs"),
])
def test_invalid_measurements_fail_clearly(prepared, tmp_path, mutation, message):
    release, _, dataset = prepared
    rows = executions_rows(release)
    target = next(i for i, r in enumerate(rows) if r["modeling_key"] == "mk_03_1_0" and r["repeat_no"] == "2")
    row = rows[target]
    if mutation == "drop_run":
        rows.pop(target)
    elif mutation == "duplicate_run":
        rows.append(dict(row))
    elif mutation == "timeout":
        row["status"] = "timeout"
    elif mutation == "censored_flag":
        row["is_censored"] = "true"
    elif mutation == "zero_time":
        row["execution_time_ms"] = "0.0"
    elif mutation == "nan_time":
        row["execution_time_ms"] = "nan"
    elif mutation == "blank_time":
        row["execution_time_ms"] = ""
    elif mutation == "repeat_four":
        row["repeat_no"] = "4"
    elif mutation == "unknown_purpose":
        row["run_purpose"] = "warmup"
    elif mutation == "median_mismatch":
        row["execution_time_ms"] = repr(float(row["execution_time_ms"]) + 0.5)  # still the middle run
    with pytest.raises(Rq1DiagnosticsError, match=message) as info:
        load_measured_runs(write_rows(tmp_path / "executions.csv", rows), dataset)
    assert "mk_03_1_0" in str(info.value) and "no case is dropped" in str(info.value)


def test_missing_columns_fail(prepared, tmp_path):
    _, _, dataset = prepared
    path = tmp_path / "executions.csv"
    path.write_text("modeling_key,repeat_no,run_purpose\n", encoding="utf-8")
    with pytest.raises(Rq1DiagnosticsError, match="lacks columns"):
        load_measured_runs(path, dataset)


def test_executions_file_is_rehashed_against_the_input_record(tmp_path):
    release = build_release(tmp_path)
    path, sha = pinned_evidence_file(release.config, release.repo, release.evidence_dir, EXECUTIONS_FILE)
    assert path.name == "executions.csv" and len(sha) == 64
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(Rq1InputError, match="differs from the pinned"):
        pinned_evidence_file(release.config, release.repo, release.evidence_dir, EXECUTIONS_FILE)


# ----------------------------------------------------------------------------- S and B
def test_straddle_and_band_boundaries():
    runs = pd.DataFrame({"run_1": [999.999, 1000.0, 900.0, 500.0, 700.0, 799.999, 1249.999, 1250.0],
                         "run_2": [1000.0, 1000.0, 950.0, 999.0, 800.0, 799.999, 1249.999, 1250.0],
                         "run_3": [1001.0, 1200.0, 999.999, 1000.0, 810.0, 799.999, 1249.999, 1250.0]},
                        index=[f"k{i}" for i in range(8)])
    median = runs.median(axis=1)
    straddle, band = threshold_sets(runs, median)
    assert straddle.tolist() == [True, False, False, True, False, False, False, False]
    assert band.tolist() == [True, True, True, True, True, False, True, False]
    assert straddle.index.equals(runs.index)


def test_threshold_sensitivity_uses_existing_oof_and_frozen_labels(prepared):
    release, config, dataset = prepared
    oof = evaluate_classification(dataset, config, load_feature_allowlist(config, release.repo))
    runs, _ = load_measured_runs(release.evidence_dir / EXECUTIONS_FILE, dataset)
    document = threshold_document(oof, dataset, config, runs)
    assert document["sets"]["straddle_set"]["keys"] == ["mk_00_0_1"]  # the fixture's 900/1500/1650 ms case
    assert document["sets"]["straddle_set"]["definition"] == STRADDLE_DEFINITION
    assert document["sets"]["band_set"] == {"definition": BAND_DEFINITION, "n_cases": 0, "n_positive": 0, "keys": []}
    assert document["evaluations"]["excluding_straddle_set"] == {"n_cases": 39, "n_positive": 3}
    assert list(document["evaluations"]) == list(EVALUATIONS) and document["labels_changed"] is False
    rows = {(r["model_id"], r["stratum_value"], r["metric"]): r for r in document["rows"]}
    block = oof[(oof["fold_scheme"] == "fold_group") & (oof["model_id"] == "clf_hgb_all")].set_index("modeling_key")
    keep = block.index != "mk_00_0_1"
    expected = classification_metric_values("probability", block["y_true"][keep], block["score"][keep])
    for metric, value in expected.items():
        assert rows[("clf_hgb_all", "excluding_straddle_set", metric)]["value"] == value.value
    empty = rows[("clf_hgb_all", "band_set_only", "brier")]
    assert empty["n_keys"] == 0 and empty["null_reason"] == REASON_EMPTY
    raw = rows[("clf_raw_cost_rank", "all_cases", "recall")]
    assert raw["null_reason"] == "not applicable to this model's output type"
    assert all(r["scope"] == "pooled_out_of_fold" and r["fold"] is None for r in document["rows"])


def test_inconsistent_frozen_labels_are_refused(prepared):
    release, config, dataset = prepared
    oof = evaluate_classification(dataset, config, load_feature_allowlist(config, release.repo))
    runs, _ = load_measured_runs(release.evidence_dir / EXECUTIONS_FILE, dataset)
    targets = dataset.targets.copy()
    targets.iloc[0, targets.columns.get_loc("hr_1s")] = 1 - targets["hr_1s"].iloc[0]
    altered = type(dataset)(features=dataset.features, baseline_inputs=dataset.baseline_inputs, targets=targets,
                            metadata=dataset.metadata, folds=dataset.folds)
    with pytest.raises(Rq1DiagnosticsError, match="frozen"):
        threshold_document(oof, altered, config, runs)


# ----------------------------------------------------------------------------- the diagnostics stage
def test_diagnostics_stage_writes_both_documents_without_any_refit(prepared, tmp_path, monkeypatch):
    release, config, _ = prepared

    def no_more_fits(context):
        def refuse(spec):
            raise AssertionError(f"diagnostics must never build or fit a model ({spec.model_id})")
        monkeypatch.setattr(rq1_models, "build_regression_model", refuse)
        monkeypatch.setattr(rq1_models, "build_classification_model", refuse)
    context = create_run_context(tmp_path / "results", "run_diag", "synthetic", config,
                                 evidence_dir=release.evidence_dir, repo_root=release.repo)
    execute_run(context, [("input_check", stage_input_check), ("regression", stage_regression),
                          ("classification_1s", stage_classification_1s), ("arm", no_more_fits),
                          ("diagnostics", stage_diagnostics)])
    assert read_status(context.run_dir)["status"] == COMPLETE
    files = read_complete_run(context.run_dir)["files"]
    assert {NOISE_FILE, THRESHOLD_FILE, "figures/figure_index.json"} <= set(files)

    def refuse_constant(token):
        raise AssertionError(token)
    noise = json.loads((context.run_dir / NOISE_FILE).read_text(encoding="utf-8"), parse_constant=refuse_constant)
    assert noise["name"] == "repetition-noise reference" and "not a proven" in noise["interpretation"]
    assert noise["definitions"]["aggregation_units"]["leave_one_run_out_qerror"].startswith("measurement")
    assert noise["definitions"]["aggregation_units"]["coefficient_of_variation"].startswith("case")
    assert noise["overall"]["leave_one_run_out_qerror"]["n"] == 120 and noise["overall"]["coefficient_of_variation"]["n"] == 40
    assert noise["overall"]["coefficient_of_variation"]["median"] == pytest.approx(0.1)
    assert noise["overall"]["leave_one_run_out_qerror"]["median"] == pytest.approx(1.1 / 0.95)
    assert noise["by"]["runtime_bin_ms"]["100-1000"]["leave_one_run_out_qerror"]["null_reason"] == REASON_EMPTY
    assert sum(v["n_cases"] for v in noise["by"]["component"].values()) == 40
    assert noise["source"]["sha256"] == sha256_file(release.evidence_dir / EXECUTIONS_FILE)
    threshold = json.loads((context.run_dir / THRESHOLD_FILE).read_text(encoding="utf-8"))
    assert threshold["retrospective"] is True and "never deployable" in threshold["interpretation"]
    assert context.provenance["diagnostics"]["models_fitted"] == 0
