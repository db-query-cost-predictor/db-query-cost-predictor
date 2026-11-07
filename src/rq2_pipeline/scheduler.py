"""Sequential warm-ups, eleven A/A pairs, then eleven A/B pairs."""

from dataclasses import asdict

from rq2_pipeline.decisions import DecisionPolicy, Outcome, evaluate_timing
from rq2_pipeline.timing import PairOrder, TimingPair, expected_order


def measure(original, candidate, execute, *, policy=None):
    """execute(sql, label) persists raw evidence and returns integer us or None.

    A failure stops the case immediately. There are no retries or replacement
    pairs. A/A is scored before candidate warm-ups or A/B measurement starts.
    """
    policy = policy or DecisionPolicy()
    aa, ab = [], []
    result = {"aa_pairs": aa, "ab_pairs": ab, "status": "INCONCLUSIVE", "reason": "Not completed"}
    for index in range(2):
        if execute(original, f"warmup-original-{index}") is None:
            result["reason"] = "Original warm-up incomplete"
            return result

    def series(phase, a_sql, b_sql, target):
        for index in range(policy.pair_count):
            order = expected_order(index)
            forms = ("A", "B") if order == PairOrder.A_THEN_B else ("B", "A")
            values = {"A": None, "B": None}
            for form in forms:
                values[form] = execute(a_sql if form == "A" else b_sql, f"{phase}-{index:02d}-{form}")
                if values[form] is None:
                    target.append(asdict(TimingPair(values["A"], values["B"], index, order)))
                    return False
            target.append(asdict(TimingPair(values["A"], values["B"], index, order)))
        return True

    if not series("aa", original, original, aa):
        result["reason"] = "A/A execution incomplete"
        return result
    aa_decision = evaluate_timing(restore_pairs(aa), (), policy)
    if aa_decision.outcome == Outcome.AA_BLOCKED:
        result.update(status="AA_BLOCKED", reason=aa_decision.reason)
        return result
    for index in range(2):
        if execute(candidate, f"warmup-candidate-{index}") is None:
            result["reason"] = "Candidate warm-up incomplete"
            return result
    if not series("ab", original, candidate, ab):
        result["reason"] = "A/B execution incomplete"
        return result
    result.update(status="COMPLETE", reason="All declared measurements completed")
    return result


def restore_pairs(rows):
    return tuple(TimingPair(row["a_us"], row["b_us"], row["pair_index"], PairOrder(row["order"])) for row in rows)
