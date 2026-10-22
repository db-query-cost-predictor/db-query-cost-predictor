"""Paired cluster bootstrap over development semantic groups (shared by regression and classification).

One :class:`BootstrapPlan` serves every task of a run:

* the ordered group list is the sorted list of development
  ``semantic_group_id`` values (194 in the release);
* each replicate draws that many group positions with replacement from
  ``numpy.random.default_rng(seed)`` (seed 20261021, 2,000 replicates);
* a replicate keeps every OOF row of each drawn group once per occurrence, so a
  group drawn twice contributes its rows twice;
* every model and every learned-minus-baseline difference of every task uses
  the same indices (paired);
* a replicate statistic is the same case-weighted pooled metric as the point
  estimate; an undefined replicate is counted as invalid with its reason and
  never imputed; the interval is the 95 % percentile interval of the valid
  replicates.

Each task adds one :class:`BootstrapContribution` to the run context
(:func:`add_contribution`). The four shared files (``bootstrap_groups.csv``,
``bootstrap_indices.npz``, ``bootstrap_replicates.csv``,
``bootstrap_summary.json``) are written once, by :func:`write_bootstrap_outputs`,
after every required task has contributed; nothing is appended or rewritten.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np

from query_cost_predictor.rq1_config import Rq1Config
from query_cost_predictor.rq1_metrics import MetricValue, csv_bytes, json_bytes

ARTIFACT_KEY = "bootstrap"
GROUPS_FILE = "bootstrap_groups.csv"
INDICES_FILE = "bootstrap_indices.npz"
REPLICATES_FILE = "bootstrap_replicates.csv"
SUMMARY_FILE = "bootstrap_summary.json"
OUTPUT_FILES = (GROUPS_FILE, INDICES_FILE, REPLICATES_FILE, SUMMARY_FILE)
REPLICATE_COLUMNS = ("task", "statistic_kind", "model_id", "baseline_model_id", "metric", "replicate", "value",
                     "invalid_reason")
MODEL, DIFFERENCE = "model", "difference"
TASK_ORDER = ("regression", "classification_1s")
# ``uncertainty.applies_to`` names headline tables; contributions are keyed by task, never by table name.
HEADLINE_TABLE_TASKS = {"headline_regression": "regression", "headline_classification": "classification_1s"}


class Rq1BootstrapError(ValueError):
    """The bootstrap plan, a contribution or the shared outputs violate the approved design."""


@dataclass(frozen=True)
class BootstrapPlan:
    groups: tuple[str, ...]
    indices: np.ndarray  # (replicates, n_groups) positions into ``groups``
    seed: int
    level: float
    cluster_column: str

    @property
    def replicates(self) -> int:
        return int(self.indices.shape[0])

    def positions_by_group(self, row_groups: Sequence[str]) -> list[np.ndarray]:
        """Row positions of each planned group, in plan order, for one fixed row order."""
        row_groups = np.asarray([str(g) for g in row_groups])
        observed = set(row_groups.tolist())
        if observed != set(self.groups):
            raise Rq1BootstrapError(f"the rows hold {len(observed)} groups that differ from the {len(self.groups)} "
                                    "planned groups")
        return [np.flatnonzero(row_groups == group) for group in self.groups]

    def replicate_rows(self, replicate: int, positions: Sequence[np.ndarray]) -> np.ndarray:
        """Row positions of one replicate (repeated draws repeat every row of that group)."""
        return np.concatenate([positions[g] for g in self.indices[replicate]])


def make_bootstrap_plan(row_groups: Sequence[str], config: Rq1Config) -> BootstrapPlan:
    spec = config.uncertainty
    groups = tuple(sorted({str(g) for g in row_groups}))
    if len(groups) != int(spec["n_clusters"]):
        raise Rq1BootstrapError(f"{len(groups)} development groups, but the YAML declares {spec['n_clusters']}")
    interval = spec["interval"]
    if interval.get("type") != "percentile":
        raise Rq1BootstrapError("only the percentile interval is declared")
    rng = np.random.default_rng(int(spec["seed"]))
    indices = rng.integers(0, len(groups), size=(int(spec["replicates"]), len(groups)), dtype=np.int64)
    return BootstrapPlan(groups=groups, indices=indices, seed=int(spec["seed"]), level=float(interval["level"]),
                         cluster_column=str(spec["cluster_column"]))


def percentile_interval(values: Sequence[float], level: float) -> tuple[float | None, float | None]:
    if not len(values):
        return None, None
    tail = (1.0 - level) / 2.0 * 100.0
    lower, upper = np.percentile(np.asarray(values, dtype="float64"), [tail, 100.0 - tail])
    return float(lower), float(upper)


@dataclass
class BootstrapContribution:
    """One task's replicate statistics and summary, held in the run context until final assembly."""

    task: str
    replicate_rows: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def interval(self, model_id: str, metric: str) -> tuple[float | None, float | None]:
        for entry in self.summary["models"]:
            if (entry["model_id"], entry["metric"]) == (model_id, metric):
                return entry["ci_lower"], entry["ci_upper"]
        raise KeyError((model_id, metric))


def _summary_entry(kind: str, model_id: str, baseline: str | None, metric: str, point: MetricValue,
                   values: list[float | None], reasons: list[str | None], level: float) -> dict[str, Any]:
    valid = [v for v in values if v is not None]
    counts: dict[str, int] = {}
    for reason in reasons:
        if reason is not None:
            counts[reason] = counts.get(reason, 0) + 1
    lower, upper = percentile_interval(valid, level)
    return {"statistic_kind": kind, "model_id": model_id, "baseline_model_id": baseline, "metric": metric,
            "point_estimate": point.value, "point_null_reason": point.null_reason,
            "ci_lower": lower, "ci_upper": upper, "n_valid": len(valid), "n_invalid": len(values) - len(valid),
            "invalid_reasons": dict(sorted(counts.items()))}


def paired_bootstrap(task: str, plan: BootstrapPlan, row_groups: Sequence[str],
                     statistics: dict[str, Callable[[np.ndarray], dict[str, MetricValue]]], metrics: Sequence[str],
                     differences: Sequence[tuple[str, str]], direction: str) -> BootstrapContribution:
    """Recompute every model's pooled metrics on identical resampled rows; derive paired differences.

    ``statistics`` maps a model id to a function of row positions returning its
    metric values (the same function gives the point estimate on all rows).
    ``differences`` lists ``(learned, baseline)`` pairs, reported learned minus
    baseline for every metric.
    """
    positions = plan.positions_by_group(row_groups)
    all_rows = np.arange(len(row_groups))
    models = list(statistics)
    for learned, baseline in differences:
        if learned not in statistics or baseline not in statistics:
            raise Rq1BootstrapError(f"difference {learned} - {baseline} names a model without statistics")
    points = {m: statistics[m](all_rows) for m in models}
    series: dict[tuple[str, str, str | None], tuple[list[float | None], list[str | None]]] = {}
    for key in [(m, metric, None) for m in models for metric in metrics] + \
               [(l, metric, b) for l, b in differences for metric in metrics]:
        series[key] = ([], [])
    rows: list[dict[str, Any]] = []
    for replicate in range(plan.replicates):
        sample = plan.replicate_rows(replicate, positions)
        values = {m: statistics[m](sample) for m in models}
        for m in models:
            for metric in metrics:
                result = values[m][metric]
                series[(m, metric, None)][0].append(result.value)
                series[(m, metric, None)][1].append(result.null_reason)
                rows.append({"task": task, "statistic_kind": MODEL, "model_id": m, "baseline_model_id": None,
                             "metric": metric, "replicate": replicate, "value": result.value,
                             "invalid_reason": result.null_reason})
        for learned, baseline in differences:
            for metric in metrics:
                left, right = values[learned][metric], values[baseline][metric]
                if left.value is not None and right.value is not None:
                    value, reason = left.value - right.value, None
                else:
                    value = None
                    reason = "; ".join(f"{side} {result.null_reason}" for side, result in
                                       (("learned:", left), ("baseline:", right)) if result.value is None)
                series[(learned, metric, baseline)][0].append(value)
                series[(learned, metric, baseline)][1].append(reason)
                rows.append({"task": task, "statistic_kind": DIFFERENCE, "model_id": learned,
                             "baseline_model_id": baseline, "metric": metric, "replicate": replicate,
                             "value": value, "invalid_reason": reason})
    summary_models = [_summary_entry(MODEL, m, None, metric, points[m][metric], *series[(m, metric, None)], plan.level)
                      for m in models for metric in metrics]
    summary_differences = []
    for learned, baseline in differences:
        for metric in metrics:
            left, right = points[learned][metric], points[baseline][metric]
            point = (MetricValue(left.value - right.value) if left.value is not None and right.value is not None
                     else MetricValue.null("point estimate undefined for one side"))
            summary_differences.append(_summary_entry(DIFFERENCE, learned, baseline, metric, point,
                                                      *series[(learned, metric, baseline)], plan.level))
    summary = {"task": task, "metrics": list(metrics), "direction": direction, "replicates": plan.replicates,
               "rows_per_full_sample": int(len(row_groups)), "models": summary_models,
               "differences": summary_differences}
    return BootstrapContribution(task=task, replicate_rows=rows, summary=summary)


# ----------------------------------------------------------------------------------
# Run-context integration: one plan, one contribution per task, one final write
# ----------------------------------------------------------------------------------
def _state(context: Any) -> dict[str, Any]:
    return context.artifacts.setdefault(ARTIFACT_KEY, {"plan": None, "contributions": {}, "written": False})


def get_or_create_plan(context: Any, row_groups: Sequence[str]) -> BootstrapPlan:
    """The run's single plan (created on first use from the development groups)."""
    state = _state(context)
    if state["plan"] is None:
        state["plan"] = make_bootstrap_plan(row_groups, context.config)
    elif set(state["plan"].groups) != {str(g) for g in row_groups}:
        raise Rq1BootstrapError("a later task resampled a different group list; every task must share one plan")
    return state["plan"]


def add_contribution(context: Any, contribution: BootstrapContribution) -> None:
    state = _state(context)
    if state["written"]:
        raise Rq1BootstrapError("the shared bootstrap files were already written; no contribution can be added")
    if contribution.task in state["contributions"]:
        raise Rq1BootstrapError(f"the {contribution.task} bootstrap contribution already exists")
    state["contributions"][contribution.task] = contribution


def _npz_bytes(arrays: dict[str, np.ndarray]) -> bytes:
    """A NumPy ``.npz`` archive with fixed timestamps, so identical arrays give identical bytes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(arrays):
            payload = io.BytesIO()
            np.lib.format.write_array(payload, np.array(arrays[name], order="C"), allow_pickle=False)
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            archive.writestr(info, payload.getvalue())
    return buffer.getvalue()


def bootstrap_output_bytes(plan: BootstrapPlan, contributions: dict[str, BootstrapContribution],
                           config: Rq1Config) -> dict[str, bytes]:
    """The four shared files for the given contributions (pure; nothing is written)."""
    spec = config.uncertainty
    ordered = [contributions[t] for t in TASK_ORDER if t in contributions]
    groups_rows = [{"group_position": i, plan.cluster_column: g} for i, g in enumerate(plan.groups)]
    replicate_rows = [row for c in ordered for row in c.replicate_rows]
    summary = {
        "schema": "qcp.rq1_bootstrap_summary", "schema_version": 1,
        "method": spec["method"], "cluster_column": plan.cluster_column, "n_clusters": len(plan.groups),
        "replicates": plan.replicates, "seed": plan.seed,
        "interval": {"type": "percentile", "level": plan.level}, "paired": spec["paired"],
        "resampling": spec["resampling"], "statistic": spec["statistic"],
        "invalid_replicate_rule": spec["invalid_replicate"], "interpretation": spec["interpretation"],
        "files": {"ordered_group_list": GROUPS_FILE, "resample_indices": INDICES_FILE,
                  "replicate_statistics": REPLICATES_FILE},
        "tasks": {c.task: c.summary for c in ordered},
    }
    return {
        GROUPS_FILE: csv_bytes(groups_rows, ("group_position", plan.cluster_column)),
        INDICES_FILE: _npz_bytes({"indices": plan.indices.astype(np.int64),
                                  "groups": np.asarray(plan.groups, dtype=str),
                                  "seed": np.asarray(plan.seed, dtype=np.int64)}),
        REPLICATES_FILE: csv_bytes(replicate_rows, REPLICATE_COLUMNS),
        SUMMARY_FILE: json_bytes(summary),
    }


def required_bootstrap_tasks(config: Rq1Config) -> tuple[str, ...]:
    """The contribution task ids required by ``uncertainty.applies_to`` (headline table names mapped to tasks)."""
    tables = [str(name) for name in config.uncertainty["applies_to"]]
    unknown = sorted(set(tables) - set(HEADLINE_TABLE_TASKS))
    if unknown:
        raise Rq1BootstrapError(f"uncertainty.applies_to names unknown headline tables {unknown}")
    tasks = {HEADLINE_TABLE_TASKS[name] for name in tables}
    return tuple(task for task in TASK_ORDER if task in tasks)


def write_bootstrap_outputs(context: Any, required_tasks: Sequence[str]) -> None:
    """Write the four shared bootstrap files once, after every required task has contributed.

    The production pipeline passes :func:`required_bootstrap_tasks` (regression
    and 1 s classification); the files are never appended to or rewritten, so a
    missing or extra contribution is an error.
    """
    unknown = sorted(set(required_tasks) - set(TASK_ORDER))
    if unknown:
        raise Rq1BootstrapError(f"{unknown} are not contribution task ids {TASK_ORDER} (headline table names are "
                                "mapped by required_bootstrap_tasks)")
    state = _state(context)
    if state["written"]:
        raise Rq1BootstrapError("the shared bootstrap files are written once per run")
    present = set(state["contributions"])
    if state["plan"] is None or present != set(required_tasks):
        raise Rq1BootstrapError(f"bootstrap contributions {sorted(present)} differ from the required {sorted(required_tasks)}")
    for name, data in bootstrap_output_bytes(state["plan"], state["contributions"], context.config).items():
        context.write_payload(name, data)
    state["written"] = True
