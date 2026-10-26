"""RQ1 metric definedness, null reasons, flags, calibration bins and long-format metric tables.

Regression values come from the shared helpers wherever the definitions match:
``metrics.regression_metrics`` (floor ``EPSILON_MS`` = 0.001 ms) for the runtime
metrics and ``metrics.rank_correlations`` for Spearman and Kendall. This module
adds the approved RQ1 rules on top:

* a metric is null **only** when its own rule fails, with the YAML reason; a
  defined zero stays zero;
* R² needs at least 2 cases and non-constant actual values; constant
  predictions alone never make it undefined;
* Spearman and Kendall need at least 2 cases and non-constant values on both
  sides. ``metrics.rank_correlations`` refuses fewer than 3 pairs (the pilot and
  poster rely on that), so the 2-pair case calls the same SciPy functions here
  and the legacy helper is unchanged;
* rank-score models get the output-type reason for every runtime metric.

Rank correlations compare the actual ``median_ms`` with the model's native
output (the predicted ln runtime, or the raw cost score); they are invariant to
the monotone ``exp`` back-transform.

1 s classification (``hr_1s``: positive 1, negative 0):

* average precision is ``sklearn.metrics.average_precision_score`` (the
  non-interpolated step sum), never trapezoidal PR-AUC; it needs a positive, and
  an all-positive set gives AP = 1 with ``trivial_single_class``;
* ROC-AUC needs both classes; recall and the false-negative rate need a
  positive; precision needs a case predicted positive (probability >= 0.5); the
  false-positive rate needs a negative; Brier score (mean (p - y)^2) and the
  warning rate need a non-empty set;
* rank-score models get AP and ROC-AUC only; every probability or threshold
  metric carries the output-type reason;
* flags: ``trivial_single_class`` on defined values of a one-class set,
  ``thin_positive_support`` (< 30 positives or < 5 positive groups) and
  ``single_fold_positives`` (pooled rows only, positives from one test fold);
* calibration bins are computed directly with the fixed edges (bin i holds
  lower <= p < upper, the last bin also holds p = 1); empty bins have zero
  counts and null rates.

No NaN or infinity is ever exported: :func:`check_finite_document` guards every
table before it is written.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from query_cost_predictor.metrics import EPSILON_MS, rank_correlations, regression_metrics
from query_cost_predictor.rq1_config import DECISION_THRESHOLD, Rq1Config

TASK_REGRESSION, TASK_CLASSIFICATION = "regression", "classification_1s"
SCOPE_POOLED, SCOPE_PER_FOLD = "pooled_out_of_fold", "per_fold"
RUNTIME_METRICS = ("log_mae", "median_qerror", "p90_qerror", "p95_qerror", "mae_ms", "rmse_ms", "r2_log", "r2_ms")
RANK_METRICS = ("spearman", "kendall")
REGRESSION_METRICS = RUNTIME_METRICS + RANK_METRICS
NONEMPTY_METRICS = ("log_mae", "median_qerror", "p90_qerror", "p95_qerror", "mae_ms", "rmse_ms")
R2_METRICS = ("r2_log", "r2_ms")

REASON_EMPTY = "no valid observations"
REASON_MIN_CASES_2 = "fewer than 2 cases"
REASON_CONSTANT_ACTUALS = "actual values are constant"
REASON_CONSTANT_SCORES = "scores are constant"
REASON_OUTPUT_TYPE = "not applicable to this model's output type"
REASON_NO_POSITIVES = "no positive cases"
REASON_NO_NEGATIVES = "no negative cases"
REASON_NO_PREDICTED_POSITIVES = "no case predicted positive at the fixed threshold"
NULL_REASONS = {
    "empty": REASON_EMPTY,
    "min_cases_2": REASON_MIN_CASES_2,
    "nonconstant_actuals": REASON_CONSTANT_ACTUALS,
    "nonconstant_scores": REASON_CONSTANT_SCORES,
    "has_positives": REASON_NO_POSITIVES,
    "has_negatives": REASON_NO_NEGATIVES,
    "has_predicted_positives": REASON_NO_PREDICTED_POSITIVES,
    "output_type": REASON_OUTPUT_TYPE,
}

CLASSIFICATION_RANKING_METRICS = ("average_precision", "roc_auc")
THRESHOLD_METRICS = ("recall", "false_negative_rate", "precision", "false_positive_rate", "warning_rate_per_100")
PROBABILITY_METRICS = ("brier",)
CLASSIFICATION_METRICS = CLASSIFICATION_RANKING_METRICS + THRESHOLD_METRICS + PROBABILITY_METRICS
CALIBRATION_BINS = "calibration_bins"
DEFINEDNESS = {
    **{name: "nonempty_valid" for name in NONEMPTY_METRICS},
    "brier": "nonempty_valid",
    "warning_rate_per_100": "nonempty_valid",
    "r2_log": ["min_cases_2", "nonconstant_actuals"],
    "r2_ms": ["min_cases_2", "nonconstant_actuals"],
    "spearman": ["min_cases_2", "nonconstant_actuals", "nonconstant_scores"],
    "kendall": ["min_cases_2", "nonconstant_actuals", "nonconstant_scores"],
    "average_precision": ["has_positives"],
    "roc_auc": ["has_positives", "has_negatives"],
    "recall": ["has_positives"],
    "false_negative_rate": ["has_positives"],
    "precision": ["has_predicted_positives"],
    "false_positive_rate": ["has_negatives"],
    CALIBRATION_BINS: "nonempty_valid",
}
RANK_SCORE_EXCLUDED_REGRESSION = RUNTIME_METRICS
RANK_SCORE_EXCLUDED = RUNTIME_METRICS + THRESHOLD_METRICS + PROBABILITY_METRICS + (CALIBRATION_BINS,)

FLAG_TRIVIAL_SINGLE_CLASS = "trivial_single_class"
FLAG_THIN_POSITIVE_SUPPORT = "thin_positive_support"
FLAG_SINGLE_FOLD_POSITIVES = "single_fold_positives"
FLAGS = (FLAG_TRIVIAL_SINGLE_CLASS, FLAG_THIN_POSITIVE_SUPPORT, FLAG_SINGLE_FOLD_POSITIVES)
THIN_SUPPORT_THRESHOLDS = {"min_positives": 30, "min_positive_groups": 5}
CALIBRATION_EDGES = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
CALIBRATION_COLUMNS = ("fold_scheme", "model_id", "scope", "fold", "stratum_column", "stratum_value", "bin_index",
                       "bin_lower", "bin_upper", "n_cases", "n_positive", "mean_predicted_probability",
                       "observed_positive_fraction")
METRIC_TABLE_COLUMNS = ("task", "fold_scheme", "model_id", "scope", "fold", "stratum_column", "stratum_value",
                        "n_keys", "n_groups", "keys_per_group", "n_query_instances", "n_positive",
                        "n_positive_groups", "n_contributing_positive_folds", "metric", "value", "ci_lower",
                        "ci_upper", "null_reason", "flag")
RUNTIME_BIN_COLUMN = "runtime_bin_ms"


class Rq1MetricError(ValueError):
    """A metric input or table violates the approved RQ1 metric rules."""


@dataclass(frozen=True)
class MetricValue:
    """A metric value, or ``None`` with the reason its definedness rule failed."""

    value: float | None
    null_reason: str | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.null_reason is None):
            raise Rq1MetricError("a metric has either a finite value or a null reason, never both or neither")
        if self.value is not None and not math.isfinite(self.value):
            raise Rq1MetricError(f"a defined metric must be finite, got {self.value!r}")

    @classmethod
    def null(cls, reason: str) -> MetricValue:
        return cls(None, reason)


def check_metric_spec(config: Rq1Config) -> None:
    """Fail unless the YAML metric rules equal the rules implemented here (no silent drift)."""
    spec = config.metrics_spec
    regression = spec["regression"]
    declared = tuple(regression["primary"]) + tuple(regression["operational"]) + tuple(regression["secondary"])
    if declared != RUNTIME_METRICS or tuple(regression["rank"]) != RANK_METRICS:
        raise Rq1MetricError(f"YAML regression metrics {declared} + {regression['rank']} differ from the implementation")
    for name, rule in DEFINEDNESS.items():
        if spec["definedness"].get(name) != rule:
            raise Rq1MetricError(f"YAML definedness for {name} is {spec['definedness'].get(name)!r}, implemented {rule!r}")
    if set(spec["definedness"]) != set(DEFINEDNESS):
        raise Rq1MetricError(f"YAML definedness covers {sorted(spec['definedness'])}, implemented {sorted(DEFINEDNESS)}")
    for key, reason in NULL_REASONS.items():
        if spec["null_reasons"].get(key) != reason:
            raise Rq1MetricError(f"YAML null reason {key!r} differs from the implementation")
    excluded = set(spec["output_type_exclusions"]["rank_score"])
    if excluded != set(RANK_SCORE_EXCLUDED):
        raise Rq1MetricError("YAML output-type exclusions for rank scores differ from the implementation")
    if spec.get("undefined_value") is not None:
        raise Rq1MetricError("undefined metric values must be null")
    if tuple(config.metric_table_columns) != METRIC_TABLE_COLUMNS:
        raise Rq1MetricError("YAML metric_table_columns differ from the implementation")
    classification = spec["classification"]
    declared = (tuple(k for k in classification["ranking"] if k != "note")
                + tuple(classification["at_fixed_threshold"]) + tuple(k for k in classification["probability"]
                                                                       if k != CALIBRATION_BINS))
    if declared != CLASSIFICATION_METRICS:
        raise Rq1MetricError(f"YAML classification metrics {declared} differ from {CLASSIFICATION_METRICS}")
    ap = classification["ranking"]["average_precision"]
    if ap.get("function") != "sklearn.metrics.average_precision_score" or ap.get("pos_label") != 1:
        raise Rq1MetricError("average precision must be sklearn.metrics.average_precision_score with pos_label 1")
    if classification["probability"]["brier"].get("scale_by_half") is not True:
        raise Rq1MetricError("the Brier score is declared with scale_by_half=True (mean (p - y)^2)")
    bins = classification["probability"][CALIBRATION_BINS]
    if int(bins["n_bins"]) != 10 or tuple(float(e) for e in bins["edges"]) != CALIBRATION_EDGES:
        raise Rq1MetricError("calibration bins must be the 10 fixed bins with edges 0.0, 0.1, ..., 1.0")
    if tuple(spec["flags"]) != FLAGS or {k: int(v) for k, v in spec["thin_support_thresholds"].items()} != \
            THIN_SUPPORT_THRESHOLDS:
        raise Rq1MetricError("YAML flags or thin-support thresholds differ from the implementation")
    if float(config.raw["fixed"]["decision_threshold"]["probability"]) != DECISION_THRESHOLD:
        raise Rq1MetricError("the decision threshold is fixed at 0.5")
    if tuple(config.raw["outputs"]["calibration_bins_columns"]) != CALIBRATION_COLUMNS:
        raise Rq1MetricError("YAML calibration_bins_columns differ from the implementation")


# ----------------------------------------------------------------------------------
# Metric values with definedness
# ----------------------------------------------------------------------------------
def _vector(values: Iterable[float], name: str) -> np.ndarray:
    array = np.asarray(values if isinstance(values, np.ndarray) else list(values), dtype="float64")
    if array.ndim != 1:
        raise Rq1MetricError(f"{name} must be one-dimensional")
    if not np.isfinite(array).all():
        raise Rq1MetricError(f"{name} contains non-finite values; invalid outputs are rejected, never repaired")
    return array


def _constant(array: np.ndarray) -> bool:
    return bool(array.size) and bool(np.all(array == array[0]))


def runtime_metric_values(actual_ms: Iterable[float], pred_ms: Iterable[float]) -> dict[str, MetricValue]:
    """The eight runtime metrics of one set of rows, each with its own definedness rule."""
    actual, pred = _vector(actual_ms, "actual_ms"), _vector(pred_ms, "pred_ms")
    if actual.shape != pred.shape:
        raise Rq1MetricError("actual and predicted runtimes differ in length")
    if actual.size == 0:
        return {name: MetricValue.null(REASON_EMPTY if name in NONEMPTY_METRICS else REASON_MIN_CASES_2)
                for name in RUNTIME_METRICS}
    values = regression_metrics(actual, pred)
    out = {name: MetricValue(float(values[name])) for name in NONEMPTY_METRICS}
    r2_actuals = {"r2_log": np.log(np.maximum(actual, EPSILON_MS)), "r2_ms": actual}
    for name in R2_METRICS:
        if actual.size < 2:
            out[name] = MetricValue.null(REASON_MIN_CASES_2)
        elif _constant(r2_actuals[name]):
            out[name] = MetricValue.null(REASON_CONSTANT_ACTUALS)
        else:
            out[name] = MetricValue(float(values[name]))
    return out


def rank_metric_values(actual: Iterable[float], score: Iterable[float]) -> dict[str, MetricValue]:
    """Spearman and Kendall with the RQ1 rules (2 non-constant pairs are enough)."""
    a, s = _vector(actual, "actual"), _vector(score, "score")
    if a.shape != s.shape:
        raise Rq1MetricError("actual values and scores differ in length")
    if a.size < 2:
        reason = REASON_MIN_CASES_2
    elif _constant(a):
        reason = REASON_CONSTANT_ACTUALS
    elif _constant(s):
        reason = REASON_CONSTANT_SCORES
    else:
        reason = None
    if reason is not None:
        return {name: MetricValue.null(reason) for name in RANK_METRICS}
    if a.size >= 3:
        values = rank_correlations(s, a)
        spearman, kendall = values["spearman"], values["kendall"]
    else:  # RQ1 adapter: the legacy helper refuses 2 pairs; same SciPy definitions
        spearman, kendall = stats.spearmanr(s, a).statistic, stats.kendalltau(s, a).statistic
    return {"spearman": MetricValue(float(spearman)), "kendall": MetricValue(float(kendall))}


def regression_metric_values(output_type: str, actual_ms: Iterable[float], pred_ms: Iterable[float] | None,
                             native: Iterable[float]) -> dict[str, MetricValue]:
    """Every regression metric for one model on one set of rows.

    ``native`` is the model's own output used for ranking: the predicted ln
    runtime for runtime models, the raw score for rank-score models.
    """
    actual = _vector(actual_ms, "actual_ms")
    if output_type == "rank_score":
        out = {name: MetricValue.null(REASON_OUTPUT_TYPE) for name in RUNTIME_METRICS}
    elif output_type == "runtime_prediction":
        if pred_ms is None:
            raise Rq1MetricError("a runtime model needs predicted runtimes")
        out = runtime_metric_values(actual, pred_ms)
    else:
        raise Rq1MetricError(f"unknown output type {output_type!r}")
    out.update(rank_metric_values(actual, native))
    return out


# ----------------------------------------------------------------------------------
# 1 s classification values with definedness
# ----------------------------------------------------------------------------------
def _labels(values: Iterable[int]) -> np.ndarray:
    array = np.asarray(values if isinstance(values, np.ndarray) else list(values))
    if array.ndim != 1 or not np.isin(array, (0, 1)).all():
        raise Rq1MetricError("class labels must be a one-dimensional vector of 0 (negative) and 1 (positive)")
    return array.astype("int64")


def check_probabilities(probabilities: Iterable[float]) -> np.ndarray:
    """Finite probabilities in [0, 1], or :class:`Rq1MetricError`; never clipped."""
    array = np.asarray(probabilities if isinstance(probabilities, np.ndarray) else list(probabilities), dtype="float64")
    if array.ndim != 1 or not np.isfinite(array).all() or ((array < 0.0) | (array > 1.0)).any():
        raise Rq1MetricError("probabilities must be finite and within [0, 1]; invalid values fail and are never clipped")
    return array


def predicted_positive(probabilities: Iterable[float], threshold: float = DECISION_THRESHOLD) -> np.ndarray:
    """The fixed operating point: positive when the probability is at least ``threshold`` (0.5)."""
    return check_probabilities(probabilities) >= threshold


def average_precision_value(y: Iterable[int], score: Iterable[float]) -> MetricValue:
    """``average_precision_score`` (step sum, pos_label 1); null without positives; all-positive gives 1."""
    labels, scores = _labels(y), _vector(score, "score")
    if labels.shape != scores.shape:
        raise Rq1MetricError("labels and scores differ in length")
    if not labels.any():
        return MetricValue.null(REASON_NO_POSITIVES)
    return MetricValue(float(average_precision_score(labels, scores, pos_label=1)))


def classification_metric_values(output_type: str, y: Iterable[int], score: Iterable[float],
                                 threshold: float = DECISION_THRESHOLD) -> dict[str, MetricValue]:
    """Every 1 s classification metric of one model on one set of rows, each with its own rule."""
    labels, scores = _labels(y), _vector(score, "score")
    if labels.shape != scores.shape:
        raise Rq1MetricError("labels and scores differ in length")
    positives, negatives = int(labels.sum()), int((labels == 0).sum())
    out = {"average_precision": average_precision_value(labels, scores)}
    if not positives:
        out["roc_auc"] = MetricValue.null(REASON_NO_POSITIVES)
    elif not negatives:
        out["roc_auc"] = MetricValue.null(REASON_NO_NEGATIVES)
    else:
        out["roc_auc"] = MetricValue(float(roc_auc_score(labels, scores)))
    if output_type == "rank_score":
        out.update({name: MetricValue.null(REASON_OUTPUT_TYPE) for name in THRESHOLD_METRICS + PROBABILITY_METRICS})
        return out
    if output_type != "probability":
        raise Rq1MetricError(f"unknown output type {output_type!r}")
    predicted = predicted_positive(scores, threshold)
    true_positive = int((predicted & (labels == 1)).sum())
    false_positive = int((predicted & (labels == 0)).sum())
    if positives:
        out["recall"] = MetricValue(true_positive / positives)
        out["false_negative_rate"] = MetricValue((positives - true_positive) / positives)
    else:
        out["recall"] = out["false_negative_rate"] = MetricValue.null(REASON_NO_POSITIVES)
    n_predicted = int(predicted.sum())
    out["precision"] = (MetricValue(true_positive / n_predicted) if n_predicted
                        else MetricValue.null(REASON_NO_PREDICTED_POSITIVES))
    out["false_positive_rate"] = (MetricValue(false_positive / negatives) if negatives
                                  else MetricValue.null(REASON_NO_NEGATIVES))
    if labels.size:
        out["warning_rate_per_100"] = MetricValue(100.0 * n_predicted / labels.size)
        out["brier"] = MetricValue(float(brier_score_loss(labels, scores, pos_label=1, labels=[0, 1],
                                                          scale_by_half=True)))
    else:
        out["warning_rate_per_100"] = out["brier"] = MetricValue.null(REASON_EMPTY)
    return {name: out[name] for name in CLASSIFICATION_METRICS}


def positive_support(y: Iterable[int], groups: Sequence[str], folds: Sequence[int]) -> dict[str, int]:
    """Positives, positive semantic groups and distinct test folds supplying the positives of one set of rows."""
    labels = _labels(y).astype(bool)
    groups, folds = np.asarray(groups, dtype=object), np.asarray(folds)
    return {"n_positive": int(labels.sum()), "n_positive_groups": int(len(set(groups[labels].tolist()))),
            "n_contributing_positive_folds": int(len(set(folds[labels].tolist())))}


def classification_flags(scope: str, descriptor: dict[str, Any], defined: bool) -> str | None:
    """Flags of one classification row, in the declared order, joined by ';' (``None`` when none apply).

    * ``trivial_single_class``: the rows hold one class only and the value is defined;
    * ``thin_positive_support``: fewer than 30 positives or fewer than 5 positive groups;
    * ``single_fold_positives``: pooled rows whose positives all come from one test fold
      (never applied automatically to per-fold rows).
    """
    n_keys, n_positive = int(descriptor["n_keys"]), int(descriptor["n_positive"])
    flags = []
    if defined and n_keys and n_positive in (0, n_keys):
        flags.append(FLAG_TRIVIAL_SINGLE_CLASS)
    if (n_positive < THIN_SUPPORT_THRESHOLDS["min_positives"]
            or int(descriptor["n_positive_groups"]) < THIN_SUPPORT_THRESHOLDS["min_positive_groups"]):
        flags.append(FLAG_THIN_POSITIVE_SUPPORT)
    if scope == SCOPE_POOLED and int(descriptor["n_contributing_positive_folds"]) == 1:
        flags.append(FLAG_SINGLE_FOLD_POSITIVES)
    return ";".join(flags) or None


def calibration_bin_index(probabilities: Iterable[float], edges: Sequence[float] = CALIBRATION_EDGES) -> np.ndarray:
    """Bin of each probability: lower <= p < upper, and the last bin also holds p = 1."""
    p = check_probabilities(probabilities)
    edges_array = np.asarray(edges, dtype="float64")
    index = np.searchsorted(edges_array, p, side="right") - 1
    return np.minimum(index, edges_array.size - 2)


def calibration_bins(y: Iterable[int], probabilities: Iterable[float],
                     edges: Sequence[float] = CALIBRATION_EDGES) -> list[dict[str, Any]]:
    """The complete fixed-bin table of one set of rows (empty bins: zero counts, null rates)."""
    labels, p = _labels(y), check_probabilities(probabilities)
    if labels.shape != p.shape:
        raise Rq1MetricError("labels and probabilities differ in length")
    index = calibration_bin_index(p, edges)
    rows = []
    for bin_index in range(len(edges) - 1):
        mask = index == bin_index
        n_cases, n_positive = int(mask.sum()), int(labels[mask].sum())
        rows.append({"bin_index": bin_index, "bin_lower": float(edges[bin_index]), "bin_upper": float(edges[bin_index + 1]),
                     "n_cases": n_cases, "n_positive": n_positive,
                     "mean_predicted_probability": float(p[mask].mean()) if n_cases else None,
                     "observed_positive_fraction": n_positive / n_cases if n_cases else None})
    if sum(row["n_cases"] for row in rows) != labels.size:
        raise Rq1MetricError("calibration-bin counts do not sum to the evaluated rows")
    return rows


# ----------------------------------------------------------------------------------
# Strata and row descriptors
# ----------------------------------------------------------------------------------
def runtime_bin_labels(median_ms: pd.Series, edges: Sequence[float], labels: Sequence[str]) -> pd.Series:
    """Assign each actual ``median_ms`` to a reporting bin (lower <= x < upper); never drops a case."""
    edges = [float(e) for e in edges]
    if len(labels) != len(edges) - 1 or edges != sorted(edges):
        raise Rq1MetricError("runtime-bin edges must be increasing with one label per bin")
    values = median_ms.to_numpy(dtype="float64")
    outside = (values < edges[0]) | (values >= edges[-1]) | ~np.isfinite(values)
    if outside.any():
        raise Rq1MetricError(f"{int(outside.sum())} case(s) fall outside the runtime bins {edges}; no case is dropped")
    positions = np.searchsorted(np.asarray(edges), values, side="right") - 1
    assigned = pd.Series(np.asarray(labels, dtype=object)[positions], index=median_ms.index, name=RUNTIME_BIN_COLUMN)
    counts = assigned.value_counts()
    if int(counts.sum()) != len(values):
        raise Rq1MetricError("runtime-bin counts do not sum to the evaluated cases")
    return assigned


def strata_frame(metadata: pd.DataFrame, median_ms: pd.Series, config: Rq1Config) -> pd.DataFrame:
    """The declared reporting strata per development case; unexpected values are errors."""
    strata = config.reporting["strata"]
    frame = pd.DataFrame(index=metadata.index)
    for column, declared in strata.items():
        if column == RUNTIME_BIN_COLUMN:
            frame[column] = runtime_bin_labels(median_ms.reindex(metadata.index), declared["edges"], declared["labels"])
            continue
        unexpected = sorted(set(metadata[column]) - {str(v) for v in declared})
        if unexpected:
            raise Rq1MetricError(f"{column} has values {unexpected} outside the declared strata {declared}")
        frame[column] = metadata[column].astype(str)
    return frame


def declared_strata(config: Rq1Config) -> list[tuple[str, list[str]]]:
    out = []
    for column, declared in config.reporting["strata"].items():
        values = declared["labels"] if column == RUNTIME_BIN_COLUMN else declared
        out.append((str(column), [str(v) for v in values]))
    return out


def describe_rows(metadata: pd.DataFrame) -> dict[str, Any]:
    n_keys = int(len(metadata))
    n_groups = int(metadata["semantic_group_id"].nunique())
    return {"n_keys": n_keys, "n_groups": n_groups, "keys_per_group": (n_keys / n_groups) if n_groups else None,
            "n_query_instances": int(metadata["query_instance_id"].nunique())}


def metric_row(*, task: str, fold_scheme: str, model_id: str, scope: str, fold: int | None,
               stratum_column: str | None, stratum_value: str | None, descriptor: dict[str, Any], metric: str,
               result: MetricValue, ci: tuple[float | None, float | None] = (None, None),
               flag: str | None = None) -> dict[str, Any]:
    if (scope == SCOPE_PER_FOLD) != (fold is not None):
        raise Rq1MetricError("fold is an integer on per_fold rows and null on pooled rows")
    if (stratum_column is None) != (stratum_value is None):
        raise Rq1MetricError("stratum_column and stratum_value are both set or both null")
    return {
        "task": task, "fold_scheme": fold_scheme, "model_id": model_id, "scope": scope,
        "fold": None if fold is None else int(fold), "stratum_column": stratum_column, "stratum_value": stratum_value,
        "n_keys": descriptor["n_keys"], "n_groups": descriptor["n_groups"],
        "keys_per_group": descriptor["keys_per_group"], "n_query_instances": descriptor["n_query_instances"],
        "n_positive": descriptor.get("n_positive"), "n_positive_groups": descriptor.get("n_positive_groups"),
        "n_contributing_positive_folds": descriptor.get("n_contributing_positive_folds"),
        "metric": metric, "value": result.value, "ci_lower": ci[0], "ci_upper": ci[1],
        "null_reason": result.null_reason, "flag": flag,
    }


# ----------------------------------------------------------------------------------
# Serialization (no NaN or infinity, deterministic bytes)
# ----------------------------------------------------------------------------------
def check_finite_document(node: Any, trail: str = "$") -> None:
    """Raise if any float anywhere in ``node`` is NaN or infinite."""
    if isinstance(node, float) and not math.isfinite(node):
        raise Rq1MetricError(f"{trail} is {node!r}; NaN and infinity are never exported")
    if isinstance(node, dict):
        for key, value in node.items():
            check_finite_document(value, f"{trail}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            check_finite_document(value, f"{trail}[{index}]")


def format_cell(value: Any) -> str:
    """CSV text: empty for null, ``repr`` for floats (shortest round-trip), ``str`` otherwise."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (float, np.floating)):
        value = float(value)
        if not math.isfinite(value):
            raise Rq1MetricError(f"{value!r} cannot be exported")
        return repr(value)
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    return str(value)


def csv_bytes(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> bytes:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        if set(row) != set(columns):
            raise Rq1MetricError(f"row columns {sorted(row)} differ from {list(columns)}")
        writer.writerow([format_cell(row[c]) for c in columns])
    return buffer.getvalue().encode("utf-8")


def json_bytes(document: Any) -> bytes:
    import json

    check_finite_document(document)
    return (json.dumps(document, indent=2, allow_nan=False) + "\n").encode("utf-8")
