"""Runtime labels built from measured repetitions only.

Rules (poster-smoke protocol, three measured repetitions after one probe):

* The unmeasured probe (``repeat_no = 0``) never contributes to a label.
* A completed run has a non-negative runtime. A timeout or an error has a
  **null** runtime plus a status — never a runtime of zero.
* Timed-out runs are right-censored: their runtime exceeds the statement
  timeout, so in sorted order they come after every completed run.
* With ``n`` planned repetitions (``n`` odd) the median sits at 0-based sorted
  position ``k = (n - 1) // 2``.

  - ``failed``: any measured run ended in a non-timeout error.
  - ``right_censored``: at least ``n - k`` runs timed out, so the median is a
    censored value whatever the remaining runs would show. The lower bound is
    the timeout. (This is why collection may stop early.)
  - ``complete``: all ``n`` runs finished or timed out and at least ``k + 1``
    completed, so the median is an observed runtime.
  - ``incomplete``: otherwise (collection not finished).

* MAD, CV, p90, min and max are reported only when no run was censored.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from typing import Iterable

import numpy as np

STATUS_COMPLETE = "complete"
STATUS_CENSORED = "right_censored"
STATUS_FAILED = "failed"
STATUS_INCOMPLETE = "incomplete"
LABEL_STATUSES = (STATUS_COMPLETE, STATUS_CENSORED, STATUS_FAILED, STATUS_INCOMPLETE)

RUN_COMPLETED = "completed"
RUN_TIMEOUT = "timeout"
RUN_ERROR = "error"
RUN_STATUSES = (RUN_COMPLETED, RUN_TIMEOUT, RUN_ERROR)


class LabelError(ValueError):
    """Invalid measured-run input for label construction."""


@dataclass(frozen=True)
class MeasuredRun:
    repeat_no: int
    status: str
    execution_time_ms: float | None

    def __post_init__(self) -> None:
        if self.repeat_no < 1:
            raise LabelError(f"repeat_no {self.repeat_no}: the probe (0) must not be used for labels")
        if self.status not in RUN_STATUSES:
            raise LabelError(f"unknown run status {self.status!r}")
        if self.status == RUN_COMPLETED:
            if self.execution_time_ms is None or not math.isfinite(self.execution_time_ms) or self.execution_time_ms < 0:
                raise LabelError("a completed run needs a finite, non-negative runtime")
        elif self.execution_time_ms is not None:
            raise LabelError("a timeout or error must carry a null runtime, never a value")


@dataclass(frozen=True)
class RuntimeLabel:
    status: str
    measured_repetitions_planned: int
    n_successful: int
    n_censored: int
    n_error: int
    median_ms: float | None
    mad_ms: float | None
    cv: float | None
    p90_ms: float | None
    min_ms: float | None
    max_ms: float | None
    censor_lower_bound_ms: float | None
    timeout_ms: int
    has_censored_runs: bool
    probe_status: str | None
    label_version: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def median_index(n_planned: int) -> int:
    if n_planned < 1 or n_planned % 2 == 0:
        raise LabelError("the number of planned measured repetitions must be odd and >= 1")
    return (n_planned - 1) // 2


def label_state(n_planned: int, n_successful: int, n_censored: int, n_error: int) -> str:
    """Return the label status implied by the run counts (see module docstring)."""
    k = median_index(n_planned)
    if n_successful + n_censored + n_error > n_planned:
        raise LabelError("more measured runs than planned")
    if n_error > 0:
        return STATUS_FAILED
    if n_censored >= n_planned - k:
        return STATUS_CENSORED
    if n_successful + n_censored == n_planned and n_successful >= k + 1:
        return STATUS_COMPLETE
    return STATUS_INCOMPLETE


def is_label_determined(n_planned: int, n_successful: int, n_censored: int, n_error: int) -> bool:
    """True when no further measured run can change the label status."""
    return label_state(n_planned, n_successful, n_censored, n_error) != STATUS_INCOMPLETE


def build_runtime_label(
    runs: Iterable[MeasuredRun],
    *,
    planned_repetitions: int,
    timeout_ms: int,
    label_version: str,
    probe_status: str | None = None,
) -> RuntimeLabel:
    """Construct a :class:`RuntimeLabel` from measured runs (probe excluded)."""
    run_list = sorted(runs, key=lambda r: r.repeat_no)
    repeats = [r.repeat_no for r in run_list]
    if len(repeats) != len(set(repeats)):
        raise LabelError(f"duplicate repeat_no values: {repeats}")
    if any(r > planned_repetitions for r in repeats):
        raise LabelError("repeat_no exceeds the planned number of measured repetitions")

    successes = sorted(float(r.execution_time_ms) for r in run_list if r.status == RUN_COMPLETED)  # type: ignore[arg-type]
    n_censored = sum(1 for r in run_list if r.status == RUN_TIMEOUT)
    n_error = sum(1 for r in run_list if r.status == RUN_ERROR)
    n_successful = len(successes)

    if not run_list and probe_status == RUN_ERROR:
        status = STATUS_FAILED
    else:
        status = label_state(planned_repetitions, n_successful, n_censored, n_error)

    median = mad = cv = p90 = min_ms = max_ms = censor_bound = None
    if status == STATUS_COMPLETE:
        k = median_index(planned_repetitions)
        median = successes[k]
        if n_censored == 0:
            mad = float(statistics.median(abs(x - median) for x in successes))
            mean = statistics.fmean(successes)
            if len(successes) >= 2 and mean > 0:
                cv = float(statistics.stdev(successes) / mean)
            p90 = float(np.percentile(np.asarray(successes), 90))
            min_ms = successes[0]
            max_ms = successes[-1]
    elif status == STATUS_CENSORED:
        censor_bound = float(timeout_ms)

    return RuntimeLabel(
        status=status,
        measured_repetitions_planned=planned_repetitions,
        n_successful=n_successful,
        n_censored=n_censored,
        n_error=n_error,
        median_ms=median,
        mad_ms=mad,
        cv=cv,
        p90_ms=p90,
        min_ms=min_ms,
        max_ms=max_ms,
        censor_lower_bound_ms=censor_bound,
        timeout_ms=int(timeout_ms),
        has_censored_runs=n_censored > 0,
        probe_status=probe_status,
        label_version=label_version,
    )
