"""Exact timing and verification decisions for the supported RQ2 protocol."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from statistics import median
from typing import Mapping, Sequence

from rq2_pipeline.timing import TimingPair, expected_order


class Gate(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_RUN = "NOT_RUN"


class Outcome(StrEnum):
    GENERATION_FAILED = "GENERATION_FAILED"
    UNSAFE = "UNSAFE"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    NOT_EQUIVALENT = "NOT_EQUIVALENT"
    FIXTURE_REJECTED = "FIXTURE_REJECTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNCHANGED = "UNCHANGED"
    TIMING_NOT_RUN = "TIMING_NOT_RUN"
    INCONCLUSIVE = "INCONCLUSIVE"
    AA_BLOCKED = "AA_BLOCKED"
    MEETS_PRIMARY_CRITERION = "MEETS_PRIMARY_CRITERION"
    CONSISTENTLY_FASTER = "CONSISTENTLY_FASTER"
    NO_CLEAR_DIFFERENCE = "NO_CLEAR_DIFFERENCE"
    CONSISTENTLY_SLOWER = "CONSISTENTLY_SLOWER"


def _integer(value: int, name: str) -> None:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer, not a boolean or float")


def _percent(value: int) -> None:
    _integer(value, "Runtime reduction percent")
    if not 0 <= value < 100:
        raise ValueError("Runtime reduction percent must be in [0, 100)")


@dataclass(frozen=True)
class DecisionPolicy:
    """Practical cutoffs are percentages of runtime, not speedup ratios."""

    primary_reduction_percent: int = 10
    profile_percents: tuple[int, ...] = (0, 5, 10, 15, 20)
    pair_count: int = 11
    required_wins: int = 9

    def __post_init__(self) -> None:
        _percent(self.primary_reduction_percent)
        if self.primary_reduction_percent == 0:
            raise ValueError("The practical primary threshold must be positive")
        if type(self.profile_percents) is not tuple:
            raise ValueError("profile_percents must be an immutable tuple")
        for value in self.profile_percents:
            _percent(value)
        if tuple(sorted(set(self.profile_percents))) != self.profile_percents:
            raise ValueError("Profile thresholds must be unique and sorted")
        if 0 not in self.profile_percents or self.primary_reduction_percent not in self.profile_percents:
            raise ValueError("Profile must include zero and the primary threshold")
        _integer(self.pair_count, "pair_count")
        _integer(self.required_wins, "required_wins")
        if (self.pair_count, self.required_wins) != (11, 9):
            raise ValueError("This rule version supports exactly nine wins in eleven pairs")


@dataclass(frozen=True)
class Verification:
    """Only independently checked stages may be marked PASSED."""

    generation: Gate = Gate.NOT_RUN
    safety: Gate = Gate.NOT_RUN
    preconditions: Gate = Gate.NOT_RUN
    snapshot_results: Gate = Gate.NOT_RUN
    fixtures: Gate = Gate.NOT_RUN

    def __post_init__(self) -> None:
        stopped = False
        for value in (self.generation, self.safety, self.preconditions,
                      self.snapshot_results, self.fixtures):
            if not isinstance(value, Gate):
                raise ValueError("Verification stages require explicit Gate values")
            if stopped and value != Gate.NOT_RUN:
                raise ValueError("A verification stage follows a stage that did not pass")
            stopped = stopped or value != Gate.PASSED


@dataclass(frozen=True)
class ThresholdCheck:
    reduction_percent: int
    strict_wins: int
    meets_rule: bool


@dataclass(frozen=True)
class TimingDecision:
    outcome: Outcome
    reason: str
    aa_wins: int | None = None
    aa_reverse_wins: int | None = None
    strict_faster_pairs: int | None = None
    strict_slower_pairs: int | None = None
    equal_pairs: int | None = None
    median_reduction_percent: Fraction | None = None
    worst_pair_ratio: Fraction | None = None
    profile: tuple[ThresholdCheck, ...] = ()
    primary_wins: int | None = None
    pair_zero_primary_win: bool | None = None
    exactly_nine_with_pair_zero: bool | None = None


@dataclass(frozen=True)
class CaseDecision:
    outcome: Outcome
    reason: str
    timing: TimingDecision | None = None

    @property
    def recommended(self) -> bool:
        return self.outcome == Outcome.MEETS_PRIMARY_CRITERION

    def meets_threshold(self, reduction_percent: int) -> bool:
        """A threshold never overrides failed or incomplete correctness checks."""
        _percent(reduction_percent)
        if self.timing is None:
            return False
        return any(check.reduction_percent == reduction_percent and check.meets_rule
                   for check in self.timing.profile)


def _strict_wins(pairs: Sequence[TimingPair], percent: int, *, reverse: bool = False) -> int:
    _percent(percent)
    wins = 0
    for pair in pairs:
        if not pair.complete:
            raise ValueError("Cannot score an incomplete pair")
        a, b = (pair.b_us, pair.a_us) if reverse else (pair.a_us, pair.b_us)
        # Cross multiplication avoids floating point rounding at the boundary.
        wins += 100 * b < (100 - percent) * a
    return wins


def _series_problem(pairs: Sequence[TimingPair], policy: DecisionPolicy) -> str | None:
    if len(pairs) != policy.pair_count:
        return "requires eleven pairs"
    for index, pair in enumerate(pairs):
        if not isinstance(pair, TimingPair):
            return "contains an invalid observation"
        if pair.pair_index != index or pair.order != expected_order(index):
            return f"observed index/order violates the declared schedule at pair {index}"
        if not pair.complete:
            return f"contains an incomplete execution at pair {index}"
    return None


def evaluate_timing(
    aa_pairs: Sequence[TimingPair],
    ab_pairs: Sequence[TimingPair],
    policy: DecisionPolicy | None = None,
) -> TimingDecision:
    """Apply the proposed A/A gate and minimum-effect sign rule.

    Passing A/A does not prove absence of bias or independence. Conditional
    per-case sign-test error bounds are not family-wide error guarantees.
    """
    policy = policy or DecisionPolicy()
    if len(aa_pairs) == 0:
        return TimingDecision(Outcome.TIMING_NOT_RUN, "No A/A observations were recorded")
    aa_problem = _series_problem(aa_pairs, policy)
    if aa_problem:
        return TimingDecision(Outcome.INCONCLUSIVE, "A/A " + aa_problem)
    aa_wins = _strict_wins(aa_pairs, policy.primary_reduction_percent)
    aa_reverse = _strict_wins(aa_pairs, policy.primary_reduction_percent, reverse=True)
    if max(aa_wins, aa_reverse) >= policy.required_wins:
        return TimingDecision(Outcome.AA_BLOCKED,
                              "Identical SQL meets the primary effect rule in one direction; investigate",
                              aa_wins, aa_reverse)
    if len(ab_pairs) == 0:
        return TimingDecision(Outcome.TIMING_NOT_RUN, "No A/B observations were recorded",
                              aa_wins, aa_reverse)
    ab_problem = _series_problem(ab_pairs, policy)
    if ab_problem:
        return TimingDecision(Outcome.INCONCLUSIVE, "A/B " + ab_problem, aa_wins, aa_reverse)

    profile = tuple(ThresholdCheck(percent, wins, wins >= policy.required_wins)
                    for percent in policy.profile_percents
                    for wins in [_strict_wins(ab_pairs, percent)])
    faster = _strict_wins(ab_pairs, 0)
    slower = _strict_wins(ab_pairs, 0, reverse=True)
    equal = policy.pair_count - faster - slower
    primary = next(check for check in profile
                   if check.reduction_percent == policy.primary_reduction_percent)
    if primary.meets_rule:
        outcome, reason = (Outcome.MEETS_PRIMARY_CRITERION,
                           "At least nine pairs strictly exceed the primary runtime reduction")
    elif faster >= policy.required_wins:
        outcome, reason = (Outcome.CONSISTENTLY_FASTER,
                           "At least nine strict improvements; minimum effect not established")
    elif slower >= policy.required_wins:
        outcome, reason = (Outcome.CONSISTENTLY_SLOWER,
                           "At least nine strictly slower pairs; ties are not losses")
    else:
        outcome, reason = Outcome.NO_CLEAR_DIFFERENCE, "Neither direction has nine strict wins"
    reductions = [Fraction(100 * (p.a_us - p.b_us), p.a_us) for p in ab_pairs]
    ratios = [Fraction(p.a_us, p.b_us) for p in ab_pairs]
    pair_zero_win = bool(_strict_wins(ab_pairs[:1], policy.primary_reduction_percent))
    return TimingDecision(outcome, reason, aa_wins, aa_reverse, faster, slower,
                          equal, median(reductions), min(ratios), profile,
                          primary.strict_wins, pair_zero_win,
                          primary.strict_wins == policy.required_wins and pair_zero_win)


def evaluate_case(
    verification: Verification,
    aa_pairs: Sequence[TimingPair],
    ab_pairs: Sequence[TimingPair],
    *,
    unchanged_sql: bool = False,
    policy: DecisionPolicy | None = None,
) -> CaseDecision:
    """Correctness and review gates precede every timing recommendation."""
    stages = (
        (verification.generation, Outcome.GENERATION_FAILED, "generation"),
        (verification.safety, Outcome.UNSAFE, "safety"),
        (verification.preconditions, Outcome.PRECONDITION_FAILED, "preconditions"),
        (verification.snapshot_results, Outcome.NOT_EQUIVALENT, "snapshot results"),
        (verification.fixtures, Outcome.FIXTURE_REJECTED, "semantic fixtures"),
    )
    # Verification validates that a failed/unperformed gate has no later results.
    for state, outcome, name in stages:
        if state == Gate.FAILED:
            return CaseDecision(outcome, f"Failed {name} check")
    if any(state == Gate.INCONCLUSIVE for state, _, _ in stages):
        return CaseDecision(Outcome.INCONCLUSIVE, "A verification stage could not complete")
    if any(state != Gate.PASSED for state, _, _ in stages):
        return CaseDecision(Outcome.REVIEW_REQUIRED, "Verification coverage is incomplete")
    if type(unchanged_sql) is not bool:
        raise ValueError("unchanged_sql must be a boolean")
    if unchanged_sql:
        return CaseDecision(Outcome.UNCHANGED, "Unchanged SQL cannot be recommended as a rewrite")
    timing = evaluate_timing(aa_pairs, ab_pairs, policy)
    return CaseDecision(timing.outcome, timing.reason, timing)


def recommended_in_all_environments(
    decisions: Mapping[str, CaseDecision], expected_environments: Sequence[str]
) -> bool:
    """Require exactly the four declared environments, with none missing."""
    if (len(expected_environments) != 4 or len(set(expected_environments)) != 4
            or any(not isinstance(name, str) or not name for name in expected_environments)):
        raise ValueError("Declare four distinct, nonempty environment identifiers")
    if set(decisions) - set(expected_environments):
        raise ValueError("Unexpected environment in query decisions")
    return set(decisions) == set(expected_environments) and all(
        decision.recommended for decision in decisions.values())
