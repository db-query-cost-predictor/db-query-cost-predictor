"""Matplotlib figure functions with mandatory provenance footers.

Figures are built with the object-oriented API (``matplotlib.figure.Figure``),
so no GUI backend is needed. Each function raises ``MissingEvidenceError``
instead of drawing an empty or placeholder chart.
"""

from __future__ import annotations

import math
import textwrap
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
from matplotlib.figure import Figure

from query_cost_predictor.errors import MissingEvidenceError
from query_cost_predictor.hashing import sha256_file
from query_cost_predictor.paths import ensure_dir

THRESHOLD_LINES_MS = (100.0, 1000.0, 10000.0)
PANEL_COLORS = {"RECOMPUTED_PILOT": "#4C72B0", "NEW_POSTER_SMOKE": "#C44E52"}


@dataclass(frozen=True)
class Provenance:
    source: str
    status: str
    n_keys: int
    n_executions: int
    n_groups: int
    configuration: str
    scale_factor: str
    created_at_utc: str
    metric_definition: str

    def text(self, label: str) -> str:
        line = (
            f"[{label}] status={self.status}; source={self.source}; keys={self.n_keys}; "
            f"raw executions={self.n_executions}; groups={self.n_groups}; configuration={self.configuration}; "
            f"SF={self.scale_factor}; created={self.created_at_utc}; metric: {self.metric_definition}"
        )
        return "\n".join(textwrap.wrap(line, width=170, subsequent_indent="    "))

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimePanel:
    label: str
    completed_ms: Sequence[float]
    censored_bounds_ms: Sequence[float]
    provenance: Provenance


@dataclass(frozen=True)
class ScatterPanel:
    label: str
    cost: Sequence[float]
    runtime_ms: Sequence[float]
    censored: Sequence[bool]
    provenance: Provenance
    spearman: float | None


@dataclass(frozen=True)
class OperatorPanel:
    label: str
    operators_per_key: Sequence[Sequence[str]]
    provenance: Provenance


@dataclass(frozen=True)
class GroupPanel:
    label: str
    completed: dict[str, list[float]]
    censored: dict[str, list[float]]
    provenance: Provenance


@dataclass(frozen=True)
class RewriteTiming:
    label: str
    original_ms: Sequence[float]
    rewrite_ms: Sequence[float]
    median_speedup: float


def _footer(fig: Figure, entries: list[tuple[str, Provenance]]) -> None:
    text = "\n".join(prov.text(label) for label, prov in entries)
    n_lines = text.count("\n") + 1
    fig.text(0.01, 0.005, text, fontsize=6.5, family="monospace", va="bottom", ha="left")
    fig.subplots_adjust(bottom=min(0.12 + 0.03 * n_lines, 0.6))


def _panels_grid(n_panels: int, width_per_panel: float = 6.0, height: float = 4.8) -> tuple[Figure, list]:
    fig = Figure(figsize=(max(width_per_panel * n_panels, 7.0), height + 1.6))
    axes = [fig.add_subplot(1, n_panels, i + 1) for i in range(n_panels)]
    return fig, axes


def runtime_distribution_figure(panels: list[RuntimePanel]) -> Figure:
    usable = [p for p in panels if len(p.completed_ms) + len(p.censored_bounds_ms) > 0]
    if not usable:
        raise MissingEvidenceError("runtime distribution: no labelled keys available")
    fig, axes = _panels_grid(len(usable))
    for ax, panel in zip(axes, usable):
        color = PANEL_COLORS.get(panel.provenance.status, "#555555")
        values = [v for v in panel.completed_ms if v > 0]
        all_values = values + [v for v in panel.censored_bounds_ms if v > 0]
        if not all_values:
            raise MissingEvidenceError(f"runtime distribution: panel {panel.label!r} has no positive runtimes")
        lo = math.floor(math.log10(min(all_values)))
        hi = math.ceil(math.log10(max(all_values)))
        if hi <= lo:
            hi = lo + 1
        bins = np.logspace(lo, hi, max(4 * (hi - lo), 4) + 1)
        if values:
            ax.hist(values, bins=bins, color=color, alpha=0.8, label=f"complete (n={len(values)})")
        if panel.censored_bounds_ms:
            ax.hist(list(panel.censored_bounds_ms), bins=bins, color="#222222", hatch="//", alpha=0.6,
                    label=f"right-censored at timeout (n={len(panel.censored_bounds_ms)})")
        for threshold in THRESHOLD_LINES_MS:
            if 10 ** lo <= threshold <= 10 ** hi:
                ax.axvline(threshold, color="#888888", linestyle="--", linewidth=0.8)
                ax.text(threshold, ax.get_ylim()[1] * 0.95, f"{threshold:g} ms", rotation=90, fontsize=7, va="top")
        ax.set_xscale("log")
        ax.set_xlabel("per-key median runtime (ms, log scale)")
        ax.set_ylabel("modeling keys")
        ax.set_title(panel.label, fontsize=10)
        ax.legend(fontsize=7)
    fig.suptitle("Runtime distribution (instrumented EXPLAIN ANALYZE server time)", fontsize=11)
    _footer(fig, [(p.label, p.provenance) for p in usable])
    return fig


def cost_vs_runtime_figure(panels: list[ScatterPanel]) -> Figure:
    usable = [p for p in panels if len(p.cost) >= 3]
    if not usable:
        raise MissingEvidenceError("cost vs runtime: fewer than three keys with plain-plan cost and runtime")
    fig, axes = _panels_grid(len(usable))
    for ax, panel in zip(axes, usable):
        color = PANEL_COLORS.get(panel.provenance.status, "#555555")
        cost = np.asarray(panel.cost, dtype=float)
        runtime = np.asarray(panel.runtime_ms, dtype=float)
        censored = np.asarray(panel.censored, dtype=bool)
        ax.scatter(cost[~censored], runtime[~censored], s=14, color=color, alpha=0.75, label="complete")
        if censored.any():
            ax.scatter(cost[censored], runtime[censored], s=30, marker="^", color="#222222",
                       label="censored (lower bound = timeout)")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("PostgreSQL estimated total cost (planner units, log)")
        ax.set_ylabel("median runtime (ms, log)")
        title = panel.label if panel.spearman is None else f"{panel.label}\nSpearman rho (complete keys) = {panel.spearman:.3f}"
        ax.set_title(title, fontsize=10)
        ax.legend(fontsize=7)
    fig.suptitle("Plain-plan estimated cost versus measured runtime", fontsize=11)
    _footer(fig, [(p.label, p.provenance) for p in usable])
    return fig


def random_vs_grouped_figure(rows: list[dict], provenance: Provenance) -> Figure:
    pooled = [r for r in rows if r.get("scope") == "pooled_out_of_fold" and r.get("median_qerror") not in (None, "")]
    if not pooled:
        raise MissingEvidenceError("random vs grouped: no pooled out-of-fold metrics in the pilot audit")
    models = [m for m in ("global_median", "calibrated_postgres_cost", "hgb") if any(r["model"] == m for r in pooled)]
    fig, axes = _panels_grid(2, width_per_panel=5.5)
    width = 0.38
    positions = np.arange(len(models))
    for ax, metric, ylabel in ((axes[0], "median_qerror", "median q-error (lower is better)"),
                               (axes[1], "log_mae", "log-runtime MAE (lower is better)")):
        for offset, split, color, label in ((-width / 2, "random", "#BBBBBB", "random 5-fold (leakage diagnostic)"),
                                            (width / 2, "grouped", "#4C72B0", "template-grouped 5-fold (primary)")):
            values = [next((float(r[metric]) for r in pooled if r["model"] == m and r["split"] == split), float("nan"))
                      for m in models]
            ax.bar(positions + offset, values, width=width, color=color, label=label)
        ax.set_xticks(positions)
        ax.set_xticklabels(models, fontsize=8)
        ax.set_ylabel(ylabel)
        ax.legend(fontsize=7)
    fig.suptitle("Pilot: identical folds for every model; random split shown only to expose leakage", fontsize=11)
    _footer(fig, [("pilot models", provenance)])
    return fig


def operator_frequency_figure(panels: list[OperatorPanel], top_n: int = 18) -> Figure:
    usable = [p for p in panels if len(p.operators_per_key) > 0]
    if not usable:
        raise MissingEvidenceError("operator frequency: no estimated plans available")
    fig, axes = _panels_grid(len(usable), height=6.0)
    for ax, panel in zip(axes, usable):
        n_keys = len(panel.operators_per_key)
        counts: dict[str, int] = {}
        for ops in panel.operators_per_key:
            for op in set(ops):
                counts[op] = counts.get(op, 0) + 1
        items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
        names = [k for k, _ in items][::-1]
        shares = [v / n_keys for _, v in items][::-1]
        ax.barh(names, shares, color=PANEL_COLORS.get(panel.provenance.status, "#555555"))
        ax.set_xlim(0, 1)
        ax.set_xlabel("share of modeling keys whose estimated plan contains the operator")
        ax.set_title(panel.label, fontsize=10)
        ax.tick_params(axis="y", labelsize=7)
    fig.suptitle("Operator coverage in plain (estimated) plans", fontsize=11)
    _footer(fig, [(p.label, p.provenance) for p in usable])
    return fig


def runtime_by_group_figure(panels: list[GroupPanel]) -> Figure:
    usable = [p for p in panels if any(p.completed.values()) or any(p.censored.values())]
    if not usable:
        raise MissingEvidenceError("runtime by group: no labelled keys available")
    fig, axes = _panels_grid(len(usable), width_per_panel=7.0, height=5.5)
    rng = np.random.default_rng(0)  # deterministic jitter
    for ax, panel in zip(axes, usable):
        groups = sorted(set(panel.completed) | set(panel.censored))
        color = PANEL_COLORS.get(panel.provenance.status, "#555555")
        for idx, group in enumerate(groups):
            done = panel.completed.get(group, [])
            cens = panel.censored.get(group, [])
            if done:
                ax.scatter(idx + rng.uniform(-0.2, 0.2, len(done)), done, s=10, color=color, alpha=0.7)
                ax.plot([idx - 0.3, idx + 0.3], [float(np.median(done))] * 2, color="black", linewidth=1)
            if cens:
                ax.scatter(idx + rng.uniform(-0.2, 0.2, len(cens)), cens, s=28, marker="^", color="#222222")
        ax.set_yscale("log")
        ax.set_xticks(range(len(groups)))
        ax.set_xticklabels(groups, rotation=70, fontsize=6)
        ax.set_ylabel("per-key median runtime (ms, log); triangles = censored lower bound")
        ax.set_title(panel.label, fontsize=10)
    fig.suptitle("Runtime by query family / template (group identity used for reporting only)", fontsize=11)
    _footer(fig, [(p.label, p.provenance) for p in usable])
    return fig


def noise_ecdf_figure(qerrors: Sequence[float], provenance: Provenance) -> Figure:
    values = np.sort(np.asarray([q for q in qerrors if q is not None], dtype=float))
    if values.size == 0:
        raise MissingEvidenceError("noise ECDF: no repeated-run q-errors available")
    fig, axes = _panels_grid(1, width_per_panel=7.0)
    ax = axes[0]
    ax.step(values, np.arange(1, values.size + 1) / values.size, where="post", color="#4C72B0")
    for pct in (50, 90, 95):
        q = float(np.percentile(values, pct))
        ax.axvline(q, color="#888888", linestyle="--", linewidth=0.8)
        ax.text(q, 0.05 + pct / 250, f"p{pct}={q:.3f}", fontsize=7, rotation=90)
    ax.set_xlabel("leave-one-run-out q-error (run vs mean of the other runs)")
    ax.set_ylabel("cumulative share of runs")
    fig.suptitle("Repeated-run measurement noise floor", fontsize=11)
    _footer(fig, [("noise", provenance)])
    return fig


def rewrite_speedup_figure(timings: list[RewriteTiming], provenance: Provenance) -> Figure:
    usable = [t for t in timings if len(t.original_ms) > 0 and len(t.original_ms) == len(t.rewrite_ms)]
    if not usable:
        raise MissingEvidenceError("rewrite speedup: no equivalent pair with complete paired timings")
    fig, axes = _panels_grid(len(usable), width_per_panel=4.5)
    for ax, timing in zip(axes, usable):
        for orig, rew in zip(timing.original_ms, timing.rewrite_ms):
            ax.plot([0, 1], [orig, rew], color="#999999", linewidth=0.8)
        ax.scatter([0] * len(timing.original_ms), timing.original_ms, color="#C44E52", label="original")
        ax.scatter([1] * len(timing.rewrite_ms), timing.rewrite_ms, color="#55A868", label="manual reference rewrite")
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["original", "rewrite"])
        ax.set_yscale("log")
        ax.set_ylabel("instrumented execution time (ms, log)")
        ax.set_title(f"{timing.label}\nmedian paired speedup {timing.median_speedup:.2f}x", fontsize=9)
        ax.legend(fontsize=7)
    fig.suptitle("MANUAL_REFERENCE_REWRITE (not LLM): paired timings after exact multiset equivalence check", fontsize=10)
    _footer(fig, [("rewrite harness", provenance)])
    return fig


def save_figure(fig: Figure, path: Path) -> dict[str, str]:
    """Save ``fig`` as PNG and return its path and SHA-256."""
    ensure_dir(path.parent)
    fig.savefig(path, dpi=200)
    return {"path": str(path), "sha256": sha256_file(path)}
