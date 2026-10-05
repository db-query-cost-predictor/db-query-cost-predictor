"""Regression and rank metrics used in the pilot audit and the poster.

Definitions (also printed next to every metric in reports):

* q-error = max(pred / actual, actual / pred) with both values floored at
  ``EPSILON_MS`` = 0.001 ms. Lower is better; 1 is perfect.
* log-runtime MAE = mean |ln(pred) − ln(actual)| (natural log, same floor).
  It equals the mean of ln(q-error).
* MAE / RMSE in milliseconds on the original scale.
* R² is reported as a **secondary** statistic on both log and raw scales. It
  is never described as accuracy.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np
from scipy import stats

EPSILON_MS = 1e-3

METRIC_DEFINITIONS: dict[str, str] = {
    "log_mae": "mean |ln(pred_ms) - ln(actual_ms)| (floor 0.001 ms)",
    "median_qerror": "median of max(pred/actual, actual/pred)",
    "p90_qerror": "90th percentile of q-error (linear interpolation)",
    "p95_qerror": "95th percentile of q-error (linear interpolation)",
    "mae_ms": "mean |pred_ms - actual_ms|",
    "rmse_ms": "sqrt(mean((pred_ms - actual_ms)^2))",
    "r2_log": "secondary: 1 - SS_res/SS_tot on ln(ms); not accuracy",
    "r2_ms": "secondary: 1 - SS_res/SS_tot on ms; not accuracy",
    "spearman": "Spearman rank correlation (no calibration required)",
    "kendall": "Kendall tau-b rank correlation",
}


def _as_array(values: Iterable[float]) -> np.ndarray:
    array = np.asarray(list(values) if not isinstance(values, np.ndarray) else values, dtype=float)
    if array.ndim != 1:
        raise ValueError("expected a one-dimensional sequence")
    if not np.all(np.isfinite(array)):
        raise ValueError("metrics require finite values (censored or failed runs must be excluded first)")
    return array


def q_error(pred_ms: Iterable[float], actual_ms: Iterable[float], eps: float = EPSILON_MS) -> np.ndarray:
    pred = np.maximum(_as_array(pred_ms), eps)
    actual = np.maximum(_as_array(actual_ms), eps)
    if pred.shape != actual.shape:
        raise ValueError("prediction and actual arrays differ in length")
    return np.maximum(pred / actual, actual / pred)


def r2_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
    if ss_tot == 0.0:
        return float("nan")
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    return 1.0 - ss_res / ss_tot


def regression_metrics(actual_ms: Sequence[float], pred_ms: Sequence[float]) -> dict[str, float]:
    actual = _as_array(actual_ms)
    pred = _as_array(pred_ms)
    if actual.shape != pred.shape or actual.size == 0:
        raise ValueError("regression_metrics needs two equal-length, non-empty arrays")
    q = q_error(pred, actual)
    log_actual = np.log(np.maximum(actual, EPSILON_MS))
    log_pred = np.log(np.maximum(pred, EPSILON_MS))
    return {
        "n": float(actual.size),
        "log_mae": float(np.mean(np.abs(log_pred - log_actual))),
        "median_qerror": float(np.median(q)),
        "p90_qerror": float(np.percentile(q, 90)),
        "p95_qerror": float(np.percentile(q, 95)),
        "mae_ms": float(np.mean(np.abs(pred - actual))),
        "rmse_ms": float(math.sqrt(np.mean((pred - actual) ** 2))),
        "r2_log": r2_score(log_actual, log_pred),
        "r2_ms": r2_score(actual, pred),
    }


def rank_correlations(x: Sequence[float], y: Sequence[float]) -> dict[str, float]:
    xa = _as_array(x)
    ya = _as_array(y)
    if xa.shape != ya.shape or xa.size < 3:
        raise ValueError("rank correlations need at least three paired values")
    spearman, spearman_p = stats.spearmanr(xa, ya)
    kendall, kendall_p = stats.kendalltau(xa, ya)
    return {
        "spearman": float(spearman),
        "spearman_p": float(spearman_p),
        "kendall": float(kendall),
        "kendall_p": float(kendall_p),
        "n": float(xa.size),
    }


def describe(values: Iterable[float]) -> dict[str, float]:
    """Distribution summary; percentiles use linear interpolation."""
    array = _as_array(values)
    if array.size == 0:
        raise ValueError("cannot describe an empty sequence")
    return {
        "n": float(array.size),
        "min": float(np.min(array)),
        "median": float(np.median(array)),
        "p90": float(np.percentile(array, 90)),
        "p95": float(np.percentile(array, 95)),
        "max": float(np.max(array)),
        "mean": float(np.mean(array)),
        "std_sample": float(np.std(array, ddof=1)) if array.size > 1 else float("nan"),
    }


def quantiles(values: Iterable[float], qs: Sequence[float] = (50, 90, 95, 99)) -> dict[str, float]:
    array = _as_array(values)
    return {f"p{int(q)}": float(np.percentile(array, q)) for q in qs}
