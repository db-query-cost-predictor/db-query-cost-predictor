"""Median label construction and timeout censoring (synthetic runtimes, not measurements)."""

from __future__ import annotations

import pytest

from query_cost_predictor.labels import (
    STATUS_CENSORED,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    LabelError,
    MeasuredRun,
    build_runtime_label,
    is_label_determined,
    label_state,
)

TIMEOUT_MS = 15000


def label(runs, *, planned: int = 3, probe_status: str | None = "completed"):
    return build_runtime_label(runs, planned_repetitions=planned, timeout_ms=TIMEOUT_MS, label_version="test",
                               probe_status=probe_status)


def test_median_label_from_three_completed_runs() -> None:
    result = label([MeasuredRun(1, "completed", 30.0), MeasuredRun(2, "completed", 10.0), MeasuredRun(3, "completed", 20.0)])
    assert result.status == STATUS_COMPLETE
    assert result.median_ms == 20.0
    assert result.mad_ms == 10.0
    assert result.cv == pytest.approx(0.5)
    assert result.p90_ms == pytest.approx(28.0)
    assert (result.min_ms, result.max_ms) == (10.0, 30.0)
    assert result.n_successful == 3 and result.n_censored == 0 and not result.has_censored_runs


def test_probe_is_never_a_measured_run() -> None:
    with pytest.raises(LabelError):
        MeasuredRun(0, "completed", 5.0)


def test_one_timeout_of_three_keeps_an_observed_median() -> None:
    result = label([MeasuredRun(1, "completed", 10.0), MeasuredRun(2, "completed", 12.0), MeasuredRun(3, "timeout", None)])
    assert result.status == STATUS_COMPLETE
    assert result.median_ms == 12.0
    assert result.has_censored_runs and result.n_censored == 1
    assert result.mad_ms is None and result.cv is None and result.p90_ms is None


def test_two_timeouts_are_right_censored_at_the_timeout() -> None:
    result = label([MeasuredRun(1, "timeout", None), MeasuredRun(2, "timeout", None)])
    assert result.status == STATUS_CENSORED
    assert result.median_ms is None
    assert result.censor_lower_bound_ms == float(TIMEOUT_MS)
    assert result.n_censored == 2
    assert is_label_determined(3, 0, 2, 0)


def test_failed_execution_is_null_plus_status_never_zero() -> None:
    with pytest.raises(LabelError):
        MeasuredRun(1, "error", 0.0)
    with pytest.raises(LabelError):
        MeasuredRun(1, "timeout", 0.0)
    result = label([MeasuredRun(1, "error", None)])
    assert result.status == STATUS_FAILED and result.median_ms is None


def test_completed_run_requires_a_runtime() -> None:
    with pytest.raises(LabelError):
        MeasuredRun(1, "completed", None)


def test_missing_runs_leave_the_label_incomplete() -> None:
    result = label([MeasuredRun(1, "completed", 10.0), MeasuredRun(2, "completed", 11.0)])
    assert result.status == STATUS_INCOMPLETE and result.median_ms is None


def test_duplicate_repeat_numbers_are_rejected() -> None:
    with pytest.raises(LabelError):
        label([MeasuredRun(1, "completed", 10.0), MeasuredRun(1, "completed", 11.0)])


def test_probe_error_without_measured_runs_is_failed() -> None:
    assert label([], probe_status="error").status == STATUS_FAILED


def test_even_number_of_planned_repetitions_is_rejected() -> None:
    with pytest.raises(LabelError):
        label_state(4, 2, 0, 0)
