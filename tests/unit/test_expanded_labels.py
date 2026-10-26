"""Timeout handling, censoring bounds, frozen high-runtime classes and failure taxonomy.

Written but not run by Claude Code. All runtimes are synthetic test values.
"""

from __future__ import annotations

import pytest
from psycopg import errors

from query_cost_predictor.executor import StatementOutcome, _classify_error
from query_cost_predictor.labels import (
    BOUND_FROM_FALLBACK,
    BOUND_FROM_PLANNING,
    HR_INDETERMINATE,
    HR_NEGATIVE,
    HR_POSITIVE,
    HR_UNLABELED,
    STATUS_CENSORED,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    LabelError,
    MeasuredRun,
    build_runtime_label,
    exec_censor_lower_bound,
    failure_class,
    high_runtime_class,
)
from query_cost_predictor.smoke_collector import execution_payload

TIMEOUT_MS = 15000


def test_execution_bound_subtracts_the_largest_planning_time_and_the_margin() -> None:
    bound, basis = exec_censor_lower_bound(TIMEOUT_MS, [0.2, None, 1.5], margin_ms=100, fallback_planning_ms=1000)
    assert bound == pytest.approx(15000 - 1.5 - 100)
    assert basis == BOUND_FROM_PLANNING


def test_execution_bound_uses_the_flagged_fallback_without_planning_time() -> None:
    bound, basis = exec_censor_lower_bound(TIMEOUT_MS, [None], margin_ms=100, fallback_planning_ms=1000)
    assert bound == pytest.approx(13900.0)
    assert basis == BOUND_FROM_FALLBACK
    assert exec_censor_lower_bound(100, [500.0], margin_ms=100, fallback_planning_ms=0)[0] == 0.0


def test_censored_label_never_has_a_median_and_carries_the_execution_bound() -> None:
    runs = [MeasuredRun(1, "timeout", None), MeasuredRun(2, "timeout", None)]
    label = build_runtime_label(runs, planned_repetitions=3, timeout_ms=TIMEOUT_MS, label_version="t",
                                censor_lower_bound_ms=14898.5)
    assert label.status == STATUS_CENSORED
    assert label.median_ms is None
    assert label.censor_lower_bound_ms == 14898.5
    default = build_runtime_label(runs, planned_repetitions=3, timeout_ms=TIMEOUT_MS, label_version="t")
    assert default.censor_lower_bound_ms == float(TIMEOUT_MS)  # poster-smoke behaviour unchanged


def test_censoring_bound_cannot_exceed_the_timeout() -> None:
    runs = [MeasuredRun(1, "timeout", None), MeasuredRun(2, "timeout", None)]
    with pytest.raises(LabelError):
        build_runtime_label(runs, planned_repetitions=3, timeout_ms=TIMEOUT_MS, label_version="t",
                            censor_lower_bound_ms=16000.0)


def test_a_timeout_is_never_a_completed_runtime() -> None:
    with pytest.raises(LabelError):
        MeasuredRun(1, "timeout", float(TIMEOUT_MS))
    one_timeout = build_runtime_label([MeasuredRun(1, "completed", 900.0), MeasuredRun(2, "timeout", None),
                                       MeasuredRun(3, "completed", 1100.0)],
                                      planned_repetitions=3, timeout_ms=TIMEOUT_MS, label_version="t")
    assert one_timeout.status == STATUS_COMPLETE and one_timeout.median_ms == 1100.0  # timeout sorts last


def test_timeout_payload_keeps_runtime_null_and_records_the_statement_bound() -> None:
    entry = {"modeling_key": "mk_synthetic"}
    outcome = StatementOutcome("timeout", "2000-01-01T00:00:00+00:00", "2000-01-01T00:00:15+00:00", 15003.0,
                               sqlstate="57014", error_class="QueryCanceled",
                               error_message="canceling statement due to statement timeout")
    payload = execution_payload(entry, 2, 1, outcome, TIMEOUT_MS)
    assert payload["status"] == "timeout" and payload["is_censored"]
    assert payload["execution_time_ms"] is None
    assert payload["censor_lower_bound_ms"] == float(TIMEOUT_MS)
    assert payload["censoring_type"] == "right_statement_timeout"


def test_only_a_server_statement_timeout_is_censored() -> None:
    timeout = errors.QueryCanceled("canceling statement due to statement timeout")
    assert _classify_error(timeout, fired=False)[0] == "timeout"
    watchdog = _classify_error(timeout, fired=True)
    assert watchdog[0] == "error" and watchdog[2] == "WatchdogCancelled"
    user_cancel = errors.QueryCanceled("canceling statement due to user request")
    assert _classify_error(user_cancel, fired=False)[0] == "error"


@pytest.mark.parametrize(
    ("status", "median", "bound", "expected_1s", "expected_10s"),
    [
        (STATUS_COMPLETE, 1200.0, None, HR_POSITIVE, HR_NEGATIVE),
        (STATUS_COMPLETE, 999.9, None, HR_NEGATIVE, HR_NEGATIVE),
        (STATUS_COMPLETE, 10000.0, None, HR_POSITIVE, HR_POSITIVE),
        (STATUS_CENSORED, None, 14898.5, HR_POSITIVE, HR_POSITIVE),
        (STATUS_CENSORED, None, 900.0, HR_INDETERMINATE, HR_INDETERMINATE),
        (STATUS_FAILED, None, None, HR_UNLABELED, HR_UNLABELED),
        (STATUS_INCOMPLETE, None, None, HR_UNLABELED, HR_UNLABELED),
    ],
)
def test_frozen_high_runtime_classes(status, median, bound, expected_1s, expected_10s) -> None:
    assert high_runtime_class(status, median, bound, 1000.0) == expected_1s
    assert high_runtime_class(status, median, bound, 10000.0) == expected_10s


@pytest.mark.parametrize(
    ("sqlstate", "error_class", "expected"),
    [
        ("53400", "ConfigurationLimitExceeded", "resource_limit"),   # temp_file_limit
        ("53200", "OutOfMemory", "resource_limit"),
        (None, "WatchdogCancelled", "watchdog_cancel"),
        ("57014", "WatchdogCancelled", "watchdog_cancel"),
        ("42601", "SyntaxError", "sql_error"),
        ("42883", "UndefinedFunction", "sql_error"),
        ("22012", "DivisionByZero", "sql_error"),
        ("55P03", "LockNotAvailable", "lock_timeout"),
        ("57014", "QueryCanceled", "other_error"),
        (None, "MissingExecutionTime", "protocol_error"),
    ],
)
def test_failure_classes(sqlstate, error_class, expected) -> None:
    assert failure_class(sqlstate, error_class) == expected
