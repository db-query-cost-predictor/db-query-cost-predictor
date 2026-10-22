"""RQ1 Slice C2: figures (Agg), calibration plots from the saved CSV only (SYNTHETIC data only)."""

from __future__ import annotations

import csv
import io
import json

import pandas as pd
import pytest

from query_cost_predictor import rq1_evaluate, rq1_figures, rq1_metrics
from query_cost_predictor.rq1_config import load_rq1_config
from query_cost_predictor.rq1_figures import (
    CALIBRATION_SOURCE,
    FIGURE_INDEX_FILE,
    Rq1FigureError,
    _calibration,
    _calibration_figure,
    _pr_curves,
    calibration_figure_path,
    calibration_title_lines,
    read_registered_csv,
    required_figure_paths,
)
from query_cost_predictor.rq1_run import COMPLETE, create_run_context, execute_run, read_complete_run, read_status
from rq1_fixtures import build_release, production_context, small_bootstrap

pytestmark = [pytest.mark.filterwarnings("error::FutureWarning"), pytest.mark.filterwarnings("error::DeprecationWarning"),
              pytest.mark.filterwarnings("error::sklearn.exceptions.ConvergenceWarning")]

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def completed(tmp_path_factory):
    """A complete synthetic production run whose diagnostics stage could not recompute calibration bins."""
    release = build_release(tmp_path_factory.mktemp("release"))
    config = release.reconfigure(small_bootstrap)
    context, stages = production_context(tmp_path_factory.mktemp("runs"), release, config)
    patch = pytest.MonkeyPatch()
    original = dict(stages)

    def diagnostics_without_bin_recomputation(ctx):
        def refuse(*args, **kwargs):
            raise AssertionError("figures must plot the saved calibration_bins.csv, never recompute bins")
        patch.setattr(rq1_metrics, "calibration_bins", refuse)
        patch.setattr(rq1_evaluate, "calibration_table", refuse)
        try:
            original["diagnostics"](ctx)
        finally:
            patch.undo()
    stages = [(n, diagnostics_without_bin_recomputation if n == "diagnostics" else f) for n, f in stages]
    assert context.requirements is not None  # the production inventory is still enforced at finalization
    execute_run(context, stages)
    return context


def test_required_figures_cover_the_plan_and_skip_rank_scores():
    paths = required_figure_paths(load_rq1_config())
    calibration = [p for p in paths if p.startswith("figures/calibration/")]
    assert len(paths) == 9 and len(calibration) == 5
    assert all(p.startswith("figures/calibration/fold_group__clf_") and p.endswith("__pooled_oof__all.png")
               for p in calibration)
    assert not any("clf_raw_cost_rank" in p for p in paths)
    assert calibration_figure_path("fold_group", "clf_hgb_all", "pooled_out_of_fold", "runtime_bin_ms", "<10") == \
        "figures/calibration/fold_group__clf_hgb_all__pooled_oof__runtime_bin_ms=_10.png"
    assert calibration_figure_path("fold_family", "clf_hgb_all", "per_fold", fold=3) == \
        "figures/calibration/fold_family__clf_hgb_all__per_fold3__all.png"


def test_every_required_figure_is_a_registered_png(completed):
    assert read_status(completed.run_dir)["status"] == COMPLETE
    files = read_complete_run(completed.run_dir)["files"]
    for path in required_figure_paths(completed.config):
        assert path in files and (completed.run_dir / path).read_bytes().startswith(PNG_MAGIC)
    stray = [p for p in completed.run_dir.rglob("*") if p.is_file() and p.name.endswith((".tmp", ".partial"))]
    assert stray == []


def test_calibration_figures_record_the_saved_rows_they_plot(completed):
    index = json.loads((completed.run_dir / FIGURE_INDEX_FILE).read_text(encoding="utf-8"))
    saved = list(csv.DictReader(io.StringIO((completed.run_dir / CALIBRATION_SOURCE).read_text(encoding="utf-8"))))
    entries = [e for e in index["figures"] if e["file"].startswith("figures/calibration/")]
    assert len(entries) == 5
    for entry in entries:
        model = entry["models"][0]
        rows = [r for r in saved if (r["fold_scheme"], r["model_id"], r["scope"], r["stratum_column"])
                == ("fold_group", model, "pooled_out_of_fold", "")]
        assert entry["bins_plotted"] == [int(r["bin_index"]) for r in rows if int(r["n_cases"]) > 0]
        assert entry["empty_bins"] == [int(r["bin_index"]) for r in rows if int(r["n_cases"]) == 0]
        assert entry["source_sha256"] == read_complete_run(completed.run_dir)["files"][CALIBRATION_SOURCE]
        assert "calibration_bins.csv rows" in entry["sources"][0]


def test_report_links_every_figure(completed):
    report = (completed.run_dir / "report.md").read_text(encoding="utf-8")
    for path in required_figure_paths(completed.config):
        assert f"]({path})" in report


def test_pr_legend_uses_the_metric_table_ap(completed):
    index = json.loads((completed.run_dir / FIGURE_INDEX_FILE).read_text(encoding="utf-8"))
    pr = next(e for e in index["figures"] if e["file"] == rq1_figures.PR_CURVES)
    rows = json.loads((completed.run_dir / "metrics_classification.json").read_text(encoding="utf-8"))["rows"]
    for model, meta in pr["plotted"].items():
        row = next(r for r in rows if (r["fold_scheme"], r["model_id"], r["scope"], r["stratum_column"], r["metric"])
                   == ("fold_group", model, "pooled_out_of_fold", None, "average_precision"))
        assert meta["average_precision"] == row["value"]


def test_calibration_source_must_be_registered_and_unchanged(tmp_path):
    release = build_release(tmp_path / "release")
    context = create_run_context(tmp_path / "results", "run_fig", "synthetic", release.config)
    with pytest.raises(Rq1FigureError, match="not written in this run"):
        read_registered_csv(context, CALIBRATION_SOURCE)
    context.write_payload(CALIBRATION_SOURCE, b"fold_scheme\nfold_group\n")
    (context.run_dir / CALIBRATION_SOURCE).write_bytes(b"fold_scheme\ntampered\n")
    with pytest.raises(Rq1FigureError, match="changed after it was written"):
        read_registered_csv(context, CALIBRATION_SOURCE)


@pytest.mark.parametrize("model_id", ["clf_train_prevalence", "clf_cost_logistic", "clf_scale_cost_logistic",
                                      "clf_logistic_all", "clf_hgb_all"])
def test_calibration_title_stays_inside_the_exported_image(model_id):
    rows = [{"bin_index": str(i), "n_cases": "1" if i == 0 else "0", "mean_predicted_probability": "0.05" if i == 0 else "",
             "observed_positive_fraction": "0.0" if i == 0 else ""} for i in range(10)]
    title = calibration_title_lines("fold_group", model_id, "pooled_out_of_fold", "all cases")
    assert all(part in title for part in ("fold_group", model_id, "pooled_out_of_fold", "all cases"))
    figure, _ = _calibration_figure(rows, title)
    figure.set_dpi(rq1_figures.DPI)  # measure at the export resolution (550 x 550 px)
    figure.canvas.draw()
    box = figure.axes[0].title.get_window_extent(figure.canvas.get_renderer())
    assert (figure.bbox.width, figure.bbox.height) == (550, 550)
    assert box.x0 >= 0 and box.y0 >= 0 and box.x1 <= figure.bbox.width and box.y1 <= figure.bbox.height


def test_empty_calibration_slice_is_drawn_honestly():
    rows = [{"bin_index": str(i), "n_cases": "0", "mean_predicted_probability": "", "observed_positive_fraction": ""}
            for i in range(10)]
    data, meta = _calibration(rows, "empty slice")
    assert data.startswith(PNG_MAGIC) and meta == {"bins_plotted": [], "empty_bins": list(range(10))}


def test_pr_curves_without_positives_are_marked_undefined():
    oof = pd.DataFrame({"modeling_key": ["a", "b"], "fold_scheme": "fold_group", "model_id": "m", "y_true": [0, 0],
                        "score": [0.2, 0.7]})
    data, plotted = _pr_curves(oof, ["m"], {"m": {"value": None, "null_reason": "no positive cases"}})
    assert data.startswith(PNG_MAGIC) and plotted["m"]["points"] == 0 and "no positive" in plotted["m"]["note"]
