"""Synthetic boundary and failure tests; never database runtime observations."""

from dataclasses import replace
from fractions import Fraction
from itertools import product
import socket
import subprocess

import pytest

from rq2_pipeline.decisions import (
    DecisionPolicy, Gate, Outcome, TimingPair, Verification, evaluate_case,
    evaluate_timing, recommended_in_all_environments,
)
from rq2_pipeline.timing import PairOrder, expected_order


@pytest.fixture(autouse=True)
def forbid_external_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Decision tests must not open connections or launch processes")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def pairs(candidate_us, n=11, original_us=100_000, *, start_index=0):
    return [TimingPair(original_us, candidate_us, i, expected_order(i))
            for i in range(start_index, start_index + n)]


def passed_checks():
    return Verification(*(Gate.PASSED for _ in range(5)))


def stopped_checks(stage, value=Gate.FAILED):
    names = ("generation", "safety", "preconditions", "snapshot_results", "fixtures")
    index = names.index(stage)
    return Verification(**{name: Gate.PASSED if i < index else value if i == index else Gate.NOT_RUN
                            for i, name in enumerate(names)})


@pytest.mark.parametrize("candidate_us,expected", [
    (89_999, Outcome.MEETS_PRIMARY_CRITERION),
    (90_000, Outcome.CONSISTENTLY_FASTER),
    (90_001, Outcome.CONSISTENTLY_FASTER),
    (99_999, Outcome.CONSISTENTLY_FASTER),
    (100_000, Outcome.NO_CLEAR_DIFFERENCE),
    (100_001, Outcome.CONSISTENTLY_SLOWER),
])
def test_exact_primary_boundary(candidate_us, expected):
    result = evaluate_case(passed_checks(), pairs(100_000), pairs(candidate_us))
    assert result.outcome == expected
    assert result.recommended is (expected == Outcome.MEETS_PRIMARY_CRITERION)


def test_nine_minimum_effect_wins_pass_but_eight_do_not():
    nine = pairs(89_000, 9) + pairs(110_000, 2, start_index=9)
    eight = pairs(89_000, 8) + pairs(95_000, 3, start_index=8)
    assert evaluate_timing(pairs(100_000), nine).outcome == Outcome.MEETS_PRIMARY_CRITERION
    assert evaluate_timing(pairs(100_000), eight).outcome == Outcome.CONSISTENTLY_FASTER


def test_large_median_does_not_replace_nine_minimum_effect_wins():
    result = evaluate_timing(pairs(100_000), pairs(80_000, 6) + pairs(99_000, 5, start_index=6))
    assert result.median_reduction_percent == 20
    assert result.outcome == Outcome.CONSISTENTLY_FASTER


@pytest.mark.parametrize("ab,expected,counts", [
    (pairs(100_000), Outcome.NO_CLEAR_DIFFERENCE, (0, 0, 11)),
    (pairs(120_000, 8) + pairs(100_000, 3, start_index=8), Outcome.NO_CLEAR_DIFFERENCE, (0, 8, 3)),
    (pairs(120_000, 9) + pairs(100_000, 2, start_index=9), Outcome.CONSISTENTLY_SLOWER, (0, 9, 2)),
    (pairs(80_000, 8) + pairs(100_000, 3, start_index=8), Outcome.NO_CLEAR_DIFFERENCE, (8, 0, 3)),
])
def test_ties_are_neither_improvements_nor_slowdowns(ab, expected, counts):
    result = evaluate_timing(pairs(100_000), ab)
    assert result.outcome == expected
    assert (result.strict_faster_pairs, result.strict_slower_pairs, result.equal_pairs) == counts


@pytest.mark.parametrize("aa", [
    pairs(89_000, 9) + pairs(100_000, 2, start_index=9),
    pairs(100_000, 9, original_us=89_000) + pairs(100_000, 2, start_index=9),
])
def test_identical_sql_effect_in_either_direction_blocks_ab(aa):
    result = evaluate_case(passed_checks(), aa, pairs(10_000))
    assert result.outcome == Outcome.AA_BLOCKED
    assert not result.recommended
    assert not result.meets_threshold(0)


def test_high_aa_spread_alone_does_not_block():
    aa = pairs(10_000, 6) + pairs(1_000_000, 5, start_index=6)
    assert evaluate_timing(aa, pairs(50_000)).outcome == Outcome.MEETS_PRIMARY_CRITERION


@pytest.mark.parametrize("n", [0, 5, 10, 12])
def test_wrong_pair_count_never_receives_a_primary_timing_label(n):
    expected = Outcome.TIMING_NOT_RUN if n == 0 else Outcome.INCONCLUSIVE
    assert evaluate_timing(pairs(100_000), pairs(50_000, n)).outcome == expected
    assert evaluate_timing(pairs(100_000, n), pairs(50_000)).outcome == expected


@pytest.mark.parametrize("incomplete", [TimingPair(None, None, 10, expected_order(10)),
                                       TimingPair(100_000, None, 10, expected_order(10)),
                                       TimingPair(None, 50_000, 10, expected_order(10))])
def test_any_incomplete_execution_remains_inconclusive(incomplete):
    aa = pairs(100_000, 10) + [incomplete]
    ab = pairs(50_000, 10) + [incomplete]
    assert evaluate_timing(aa, pairs(50_000)).outcome == Outcome.INCONCLUSIVE
    assert evaluate_timing(pairs(100_000), ab).outcome == Outcome.INCONCLUSIVE


@pytest.mark.parametrize("value", [0, -1, 1.0, True, "1000"])
def test_invalid_time_units_are_refused(value):
    with pytest.raises(ValueError):
        TimingPair(value, 1, 0, expected_order(0))


@pytest.mark.parametrize("changes", [
    {"pair_count": 5}, {"required_wins": 8}, {"pair_count": True},
    {"primary_reduction_percent": 0}, {"primary_reduction_percent": 100},
    {"primary_reduction_percent": 10.0}, {"profile_percents": [0, 10]},
    {"profile_percents": (0, 10, 10)}, {"profile_percents": (10, 0)},
    {"profile_percents": (0, 5)},
])
def test_unsupported_or_ambiguous_policies_are_refused(changes):
    with pytest.raises(ValueError):
        DecisionPolicy(**changes)


@pytest.mark.parametrize("stage,outcome", [
    ("generation", Outcome.GENERATION_FAILED), ("safety", Outcome.UNSAFE),
    ("preconditions", Outcome.PRECONDITION_FAILED),
    ("snapshot_results", Outcome.NOT_EQUIVALENT), ("fixtures", Outcome.FIXTURE_REJECTED),
])
def test_failed_verification_never_accesses_timing(stage, outcome):
    class ForbiddenTiming:
        def __len__(self):
            raise AssertionError("A failed correctness stage must not be timed")
    result = evaluate_case(stopped_checks(stage),
                           ForbiddenTiming(), ForbiddenTiming())
    assert result.outcome == outcome
    assert result.timing is None
    assert not result.meets_threshold(0)


def test_missing_coverage_and_incomplete_verification_do_not_pass():
    assert evaluate_case(Verification(), [], []).outcome == Outcome.REVIEW_REQUIRED
    incomplete = stopped_checks("snapshot_results", Gate.INCONCLUSIVE)
    assert evaluate_case(incomplete, [], []).outcome == Outcome.INCONCLUSIVE
    with pytest.raises(ValueError):
        Verification(generation=True)


def test_demonstrated_mismatch_has_priority_over_missing_later_coverage():
    checks = replace(passed_checks(), snapshot_results=Gate.FAILED, fixtures=Gate.NOT_RUN)
    assert evaluate_case(checks, [], []).outcome == Outcome.NOT_EQUIVALENT


def test_unchanged_sql_never_becomes_a_recommended_rewrite():
    result = evaluate_case(passed_checks(), pairs(100_000), pairs(10_000), unchanged_sql=True)
    assert result.outcome == Outcome.UNCHANGED
    assert not result.recommended
    assert not result.meets_threshold(0)


def test_profile_uses_nine_individual_wins_at_each_runtime_threshold():
    result = evaluate_case(passed_checks(), pairs(100_000), pairs(88_000))
    assert [(x.reduction_percent, x.strict_wins, x.meets_rule) for x in result.timing.profile] == [
        (0, 11, True), (5, 11, True), (10, 11, True), (15, 0, False), (20, 0, False)]
    assert result.timing.median_reduction_percent == 12
    assert result.timing.worst_pair_ratio == Fraction(25, 22)
    assert [result.meets_threshold(x) for x in (0, 5, 10, 15, 20)] == [True, True, True, False, False]


def test_integer_comparison_remains_exact_above_float_integer_precision():
    original = 10**20
    aa = pairs(original, original_us=original)
    at_boundary = pairs(9 * original // 10, original_us=original)
    below_boundary = pairs(9 * original // 10 - 1, original_us=original)
    assert evaluate_timing(aa, at_boundary).outcome == Outcome.CONSISTENTLY_FASTER
    assert evaluate_timing(aa, below_boundary).outcome == Outcome.MEETS_PRIMARY_CRITERION


def test_nine_of_eleven_rule_has_67_passing_binary_patterns():
    passing = 0
    for signs in product((False, True), repeat=11):
        ab = [TimingPair(100_000, 89_000 if sign else 90_000, i, expected_order(i))
              for i, sign in enumerate(signs)]
        passing += evaluate_timing(pairs(100_000), ab).outcome == Outcome.MEETS_PRIMARY_CRITERION
    # This combinatorial count does not establish independence of actual runs.
    assert passing == 67


def test_query_recommendation_requires_four_distinct_declared_environments():
    expected = ("small/default", "small/low_memory", "large/default", "large/low_memory")
    passed = evaluate_case(passed_checks(), pairs(100_000), pairs(50_000))
    rows = dict.fromkeys(expected, passed)
    assert recommended_in_all_environments(rows, expected)
    assert not recommended_in_all_environments({}, expected)
    assert not recommended_in_all_environments(dict(list(rows.items())[:3]), expected)
    rows[expected[-1]] = evaluate_case(passed_checks(), pairs(100_000), [])
    assert not recommended_in_all_environments(rows, expected)
    with pytest.raises(ValueError):
        recommended_in_all_environments(rows, ("same",) * 4)
    with pytest.raises(ValueError):
        recommended_in_all_environments({"unexpected": passed}, expected)


@pytest.mark.parametrize("first,second", [(Gate.NOT_RUN, Gate.FAILED),
                                         (Gate.FAILED, Gate.PASSED),
                                         (Gate.INCONCLUSIVE, Gate.PASSED)])
def test_later_results_cannot_follow_an_unpassed_stage(first, second):
    with pytest.raises(ValueError, match="follows"):
        Verification(generation=Gate.PASSED, safety=first, snapshot_results=second)


def test_unbalanced_observed_order_cannot_receive_a_speed_recommendation():
    ab = [TimingPair(100_000, 80_000, i, PairOrder.A_THEN_B) for i in range(11)]
    result = evaluate_timing(pairs(100_000), ab)
    assert result.outcome == Outcome.INCONCLUSIVE
    assert "schedule" in result.reason


def test_missing_or_repeated_pair_indices_cannot_be_silently_relabelled():
    aa = pairs(100_000)
    aa[-1] = replace(aa[-1], pair_index=0)
    assert evaluate_timing(aa, pairs(50_000)).outcome == Outcome.INCONCLUSIVE


def test_observation_requires_explicit_order_and_valid_index():
    with pytest.raises(ValueError):
        TimingPair(100, 80, 0, "A_THEN_B")
    with pytest.raises(ValueError):
        TimingPair(100, 80, -1, PairOrder.A_THEN_B)


@pytest.mark.parametrize("values,expected", [
    ([89_000] * 9 + [95_000] * 2, True),
    ([95_000] + [89_000] * 9 + [95_000], False),
    ([89_000] * 10 + [95_000], False),
])
def test_pair_zero_diagnostic_is_predeclared_and_does_not_change_acceptance(values, expected):
    ab = [TimingPair(100_000, value, i, expected_order(i)) for i, value in enumerate(values)]
    result = evaluate_timing(pairs(100_000), ab)
    assert result.outcome == Outcome.MEETS_PRIMARY_CRITERION
    assert result.exactly_nine_with_pair_zero is expected
