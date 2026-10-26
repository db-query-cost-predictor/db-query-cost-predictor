"""RQ1 figures (Matplotlib, Agg canvas): primary ``fold_group`` out-of-fold results only.

Every figure is rendered in memory and written through
:meth:`RunContext.write_payload` (exclusive, registered, hashed); no temporary
file is created in the run directory. ``figures/figure_index.json`` records
each figure's source rows.

* ``regression_pred_vs_actual_ln__fold_group.png``: predicted vs actual
  ln runtime for the five runtime-predicting models;
* ``regression_qerror_ecdf__fold_group.png``: empirical CDF of q-error;
* ``regression_abs_log_error_by_runtime_bin__fold_group.png``: |ln pred -
  ln actual| by actual-runtime bin (reporting bins on the actual ``median_ms``);
* ``classification_pr_curves__fold_group.png``: step-wise precision-recall
  curves of all six classifiers, with average precision (from
  ``metrics_classification``) in the legend;
* ``calibration/<scheme>__<model>__<scope tag>__<stratum>.png`` (for example
  ``fold_group__clf_hgb_all__pooled_oof__all.png``): one per probabilistic
  classifier (pooled, unstratified ``fold_group``), drawn
  only from the saved ``calibration_bins.csv`` rows, which are re-hashed
  against the registered payload before use. Rank-score models get none.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

import numpy as np

from query_cost_predictor.metrics import EPSILON_MS, q_error
from query_cost_predictor.rq1_config import Rq1Config
from query_cost_predictor.rq1_metrics import (
    CALIBRATION_COLUMNS,
    SCOPE_POOLED,
    TASK_CLASSIFICATION,
    TASK_REGRESSION,
    json_bytes,
    strata_frame,
)

PRIMARY_SCHEME = "fold_group"
FIGURE_INDEX_FILE = "figures/figure_index.json"
PRED_VS_ACTUAL = "figures/regression_pred_vs_actual_ln__fold_group.png"
QERROR_ECDF = "figures/regression_qerror_ecdf__fold_group.png"
ERROR_BY_BIN = "figures/regression_abs_log_error_by_runtime_bin__fold_group.png"
PR_CURVES = "figures/classification_pr_curves__fold_group.png"
CALIBRATION_SOURCE = "calibration_bins.csv"
DPI = 110


class Rq1FigureError(ValueError):
    """A figure source is missing, unregistered or changed."""


SCOPE_TAGS = {"pooled_out_of_fold": "pooled_oof", "per_fold": "per_fold"}  # short tags keep Windows paths short


def _component(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", text) or "_"


def calibration_figure_path(scheme: str, model_id: str, scope: str, column: str | None = None,
                            value: str | None = None, fold: int | None = None) -> str:
    """``figures/calibration/<scheme>__<model>__<scope tag>__<stratum>.png`` (scope tag ``pooled_oof`` = pooled OOF)."""
    stratum = "all" if column is None else f"{column}={value}"
    tag = SCOPE_TAGS[scope] + ("" if fold is None else str(int(fold)))
    return f"figures/calibration/{_component(scheme)}__{_component(model_id)}__{tag}__{_component(stratum)}.png"


def _probabilistic_models(config: Rq1Config) -> list[str]:
    from query_cost_predictor.rq1_models import PROBABILITY, classification_model_specs

    return [spec.model_id for spec in classification_model_specs(config) if spec.output_type == PROBABILITY]


def required_figure_paths(config: Rq1Config) -> list[str]:
    calibration = [calibration_figure_path(PRIMARY_SCHEME, m, SCOPE_POOLED) for m in _probabilistic_models(config)]
    return [PRED_VS_ACTUAL, QERROR_ECDF, ERROR_BY_BIN, PR_CURVES, *calibration]


def _new_figure(width: float, height: float):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(width, height), layout="constrained")
    FigureCanvasAgg(figure)
    return figure


def _png(figure: Any) -> bytes:
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=DPI, metadata={"Software": None})
    return buffer.getvalue()


def read_registered_csv(context: Any, name: str) -> list[dict[str, str]]:
    """Rows of a payload already written in this run, after checking its bytes against the registered hash."""
    from query_cost_predictor.hashing import sha256_bytes

    if name not in context.payloads:
        raise Rq1FigureError(f"{name} was not written in this run; figures plot saved outputs only")
    data = (context.run_dir / name).read_bytes()
    if sha256_bytes(data) != context.payloads[name]:
        raise Rq1FigureError(f"{name} changed after it was written")
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


def _block(oof: Any, model_id: str) -> Any:
    block = oof[(oof["fold_scheme"] == PRIMARY_SCHEME) & (oof["model_id"] == model_id)]
    if block.empty:
        raise Rq1FigureError(f"no {PRIMARY_SCHEME} OOF rows for {model_id}")
    return block.sort_values("modeling_key", kind="mergesort")


def _grid(n: int, columns: int = 3) -> tuple[int, int]:
    return (n + columns - 1) // columns, min(n, columns)


# ----------------------------------------------------------------------------------
# Regression figures
# ----------------------------------------------------------------------------------
def _pred_vs_actual(oof: Any, models: list[str]) -> bytes:
    rows, cols = _grid(len(models))
    figure = _new_figure(4.2 * cols, 4.0 * rows)
    axes = figure.subplots(rows, cols, squeeze=False).ravel()
    for ax, model_id in zip(axes, models):
        block = _block(oof, model_id)
        actual, predicted = block["y_true_ln"].to_numpy(), block["pred_ln"].to_numpy()
        low, high = float(min(actual.min(), predicted.min())), float(max(actual.max(), predicted.max()))
        ax.scatter(actual, predicted, s=6, alpha=0.4)
        ax.plot([low, high], [low, high], color="black", linewidth=0.8, label="y = x")
        ax.set_title(f"{model_id} (n = {len(block)})", fontsize=9)
        ax.set_xlabel("actual ln(median_ms) [ln ms]")
        ax.set_ylabel("predicted ln runtime [ln ms]")
        ax.legend(loc="upper left", fontsize=7)
    for ax in axes[len(models):]:
        ax.set_axis_off()
    figure.suptitle("Predicted vs actual ln runtime: fold_group pooled out-of-fold (primary)", fontsize=10)
    return _png(figure)


def _qerror_ecdf(oof: Any, models: list[str]) -> bytes:
    figure = _new_figure(7.0, 4.5)
    ax = figure.subplots()
    for model_id in models:
        block = _block(oof, model_id)
        q = np.sort(q_error(block["pred_ms"].to_numpy(), block["y_true_ms"].to_numpy()))
        ax.step(q, np.arange(1, q.size + 1) / q.size, where="post", label=f"{model_id} (n = {q.size})")
    ax.set_xscale("log")
    ax.set_xlabel("q-error = max(pred/actual, actual/pred), runtimes floored at 0.001 ms (log scale)")
    ax.set_ylabel("fraction of cases with q-error <= x")
    ax.set_title("Q-error ECDF: fold_group pooled out-of-fold (primary)", fontsize=10)
    ax.legend(fontsize=7)
    return _png(figure)


def _error_by_bin(oof: Any, models: list[str], bins: Any, labels: list[str]) -> bytes:
    rows, cols = _grid(len(models))
    figure = _new_figure(4.2 * cols, 3.8 * rows)
    axes = figure.subplots(rows, cols, squeeze=False).ravel()
    for ax, model_id in zip(axes, models):
        block = _block(oof, model_id).set_index("modeling_key")
        error = np.abs(np.log(np.maximum(block["pred_ms"].to_numpy(), EPSILON_MS))
                       - np.log(np.maximum(block["y_true_ms"].to_numpy(), EPSILON_MS)))
        assigned = bins.reindex(block.index).to_numpy()
        data = [error[assigned == label] for label in labels]
        positions = [i for i, values in enumerate(data) if values.size]
        if positions:
            ax.boxplot([data[i] for i in positions], positions=positions, widths=0.6, showfliers=False)
        ticks = [f"{label}\n(n={values.size})" if values.size else f"{label}\n(no cases)"
                 for label, values in zip(labels, data)]
        ax.set_xticks(range(len(labels)), ticks, fontsize=7)
        ax.set_xlim(-0.6, len(labels) - 0.4)
        ax.set_xlabel("actual median_ms bin [ms]")
        ax.set_ylabel("|ln pred - ln actual| (= ln q-error)")
        ax.set_title(model_id, fontsize=9)
    for ax in axes[len(models):]:
        ax.set_axis_off()
    figure.suptitle("Absolute log error by actual-runtime bin (boxes without outliers): fold_group pooled "
                    "out-of-fold", fontsize=10)
    return _png(figure)


# ----------------------------------------------------------------------------------
# Classification figures
# ----------------------------------------------------------------------------------
def _pr_curves(oof: Any, models: list[str], ap_rows: dict[str, dict[str, Any]]) -> tuple[bytes, dict[str, Any]]:
    from sklearn.metrics import precision_recall_curve

    figure = _new_figure(7.0, 5.0)
    ax = figure.subplots()
    plotted: dict[str, Any] = {}
    prevalence = None
    for model_id in models:
        block = _block(oof, model_id)
        y, score = block["y_true"].to_numpy(dtype="int64"), block["score"].to_numpy(dtype="float64")
        row = ap_rows[model_id]
        ap_text = f"AP {row['value']:.3f}" if row["value"] is not None else f"AP null ({row['null_reason']})"
        if y.any():
            precision, recall, _ = precision_recall_curve(y, score, pos_label=1)
            ax.step(recall, precision, where="post", label=f"{model_id} ({ap_text})")
            prevalence = float(y.mean())
            plotted[model_id] = {"average_precision": row["value"], "points": int(recall.size)}
        else:
            plotted[model_id] = {"average_precision": None, "points": 0, "note": "no positive cases; no curve"}
    if prevalence is not None:
        ax.axhline(prevalence, color="grey", linestyle=":", linewidth=0.8, label=f"prevalence {prevalence:.3f}")
    else:
        ax.text(0.5, 0.5, "no positive cases: precision-recall curves are undefined", ha="center",
                transform=ax.transAxes)
    ax.set_xlim(0, 1.0)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("recall (1 s positives found)")
    ax.set_ylabel("precision")
    ax.set_title("Step-wise precision-recall, 1 s classification: fold_group pooled out-of-fold (primary)",
                 fontsize=10)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=7, loc="upper right")
    return _png(figure), plotted


def calibration_title_lines(scheme: str, model_id: str, scope: str, stratum: str) -> str:
    """The calibration title on three short lines, so every part stays inside the 5 x 5 inch image."""
    return (f"Calibration (10 fixed bins, saved calibration_bins.csv)\n"
            f"scheme: {scheme}   model: {model_id}\n"
            f"scope: {scope}   stratum: {stratum}")


def _calibration_figure(rows: list[dict[str, str]], title: str) -> tuple[Any, dict[str, Any]]:
    figure = _new_figure(5.0, 5.0)
    ax = figure.subplots()
    ax.plot([0, 1], [0, 1], color="black", linewidth=0.8, linestyle="--", label="perfect calibration")
    filled = [r for r in rows if int(r["n_cases"]) > 0]
    empty = [int(r["bin_index"]) for r in rows if int(r["n_cases"]) == 0]
    if filled:
        x = [float(r["mean_predicted_probability"]) for r in filled]
        y = [float(r["observed_positive_fraction"]) for r in filled]
        n = [int(r["n_cases"]) for r in filled]
        ax.plot(x, y, marker="o", linewidth=1, label="non-empty bins")
        for xi, yi, ni in zip(x, y, n):
            ax.annotate(f"n={ni}", (xi, yi), xytext=(4, 4), textcoords="offset points", fontsize=7)
    else:
        ax.text(0.5, 0.5, "no cases in this slice", ha="center", transform=ax.transAxes)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("mean predicted probability in bin")
    ax.set_ylabel("observed positive fraction in bin")
    ax.set_title(title, fontsize=8)
    ax.text(0.02, 0.97, f"empty bins (no cases; not drawn): {empty or 'none'}", transform=ax.transAxes,
            fontsize=7, va="top")
    ax.legend(loc="upper left", bbox_to_anchor=(0.0, 0.93), fontsize=7)
    return figure, {"bins_plotted": [int(r["bin_index"]) for r in filled], "empty_bins": empty}


def _calibration(rows: list[dict[str, str]], title: str) -> tuple[bytes, dict[str, Any]]:
    figure, meta = _calibration_figure(rows, title)
    return _png(figure), meta


# ----------------------------------------------------------------------------------
# Writer
# ----------------------------------------------------------------------------------
def write_figures(context: Any) -> list[dict[str, Any]]:
    """Render and register every required figure plus ``figures/figure_index.json``; return the index entries."""
    config = context.config
    oof = context.artifacts["oof"]
    dataset = context.artifacts["dataset"]
    tables = config.reporting["tables"]
    runtime_models = [str(m) for m in tables["headline_regression"]["models"]]
    classifiers = [str(m) for m in tables["headline_classification"]["models"]]
    strata = config.reporting["strata"]["runtime_bin_ms"]
    bins = strata_frame(dataset.metadata, dataset.targets["median_ms"], config)["runtime_bin_ms"]
    ap_rows = {r["model_id"]: r for r in context.artifacts["metric_rows"][TASK_CLASSIFICATION]
               if (r["fold_scheme"], r["scope"], r["stratum_column"], r["metric"])
               == (PRIMARY_SCHEME, SCOPE_POOLED, None, "average_precision")}
    regression_source = "oof_regression.csv (fold_scheme = fold_group)"
    index: list[dict[str, Any]] = []

    def register(path: str, data: bytes, title: str, sources: list[str], **extra: Any) -> None:
        context.write_payload(path, data)
        index.append({"file": path, "title": title, "sources": sources, "fold_scheme": PRIMARY_SCHEME, **extra})

    register(PRED_VS_ACTUAL, _pred_vs_actual(oof[TASK_REGRESSION], runtime_models),
             "Predicted vs actual ln runtime", [regression_source], models=runtime_models)
    register(QERROR_ECDF, _qerror_ecdf(oof[TASK_REGRESSION], runtime_models), "Q-error ECDF", [regression_source],
             models=runtime_models)
    register(ERROR_BY_BIN, _error_by_bin(oof[TASK_REGRESSION], runtime_models, bins, [str(v) for v in strata["labels"]]),
             "Absolute log error by actual-runtime bin", [regression_source, "actual median_ms (reporting bins)"],
             models=runtime_models)
    pr_png, pr_meta = _pr_curves(oof[TASK_CLASSIFICATION], classifiers, ap_rows)
    register(PR_CURVES, pr_png, "Step-wise precision-recall curves (AP in legend)",
             ["oof_classification.csv (fold_scheme = fold_group)",
              "metrics_classification.json (average_precision, fold_group, pooled_out_of_fold)"],
             models=classifiers, plotted=pr_meta)

    saved = read_registered_csv(context, CALIBRATION_SOURCE)
    if saved and tuple(saved[0]) != CALIBRATION_COLUMNS:
        raise Rq1FigureError("calibration_bins.csv columns differ from the approved schema")
    for model_id in _probabilistic_models(config):
        rows = [r for r in saved if (r["fold_scheme"], r["model_id"], r["scope"], r["stratum_column"])
                == (PRIMARY_SCHEME, model_id, SCOPE_POOLED, "")]
        if [int(r["bin_index"]) for r in rows] != list(range(10)):
            raise Rq1FigureError(f"calibration_bins.csv lacks the ten pooled fold_group bins of {model_id}")
        title = f"Calibration (10 fixed bins): {PRIMARY_SCHEME} / {model_id} / {SCOPE_POOLED} / all cases"
        data, meta = _calibration(rows, calibration_title_lines(PRIMARY_SCHEME, model_id, SCOPE_POOLED, "all cases"))
        register(calibration_figure_path(PRIMARY_SCHEME, model_id, SCOPE_POOLED), data, title,
                 [f"{CALIBRATION_SOURCE} rows: fold_scheme={PRIMARY_SCHEME}, model_id={model_id}, "
                  f"scope={SCOPE_POOLED}, stratum=all"],
                 models=[model_id], scope=SCOPE_POOLED, stratum="all",
                 source_sha256=context.payloads[CALIBRATION_SOURCE], **meta)
    context.write_payload(FIGURE_INDEX_FILE, json_bytes({
        "schema": "qcp.rq1_figure_index", "schema_version": 1, "backend": "Agg",
        "note": "figures show primary fold_group pooled out-of-fold results; calibration figures are drawn only from "
                "the saved calibration_bins.csv rows",
        "figures": index}))
    return index
