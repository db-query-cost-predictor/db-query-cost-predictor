"""Exact timing observations and the declared, zero-based pair schedule."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from fractions import Fraction
import json


class PairOrder(StrEnum):
    A_THEN_B = "A_THEN_B"
    B_THEN_A = "B_THEN_A"


def expected_order(pair_index: int) -> PairOrder:
    if type(pair_index) is not int or pair_index < 0:
        raise ValueError("Pair index must be a nonnegative integer")
    return PairOrder.A_THEN_B if pair_index % 2 == 0 else PairOrder.B_THEN_A


@dataclass(frozen=True)
class TimingPair:
    """An observed pair, not a requested schedule.

    Record the actual index and execution order. A is original and B is candidate;
    in A/A both are separately labelled copies of the original. None means an
    incomplete execution, whose original status must also be retained by a runner.
    """

    a_us: int | None
    b_us: int | None
    pair_index: int
    order: PairOrder

    def __post_init__(self) -> None:
        expected_order(self.pair_index)
        if not isinstance(self.order, PairOrder):
            raise ValueError("Record the actual order as a PairOrder value")
        for value in (self.a_us, self.b_us):
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError("Times must be positive integer microseconds or None")

    @property
    def complete(self) -> bool:
        return self.a_us is not None and self.b_us is not None


def milliseconds_to_microseconds(value: Decimal | str | int) -> int:
    """Convert original decimal milliseconds without rounding or float recovery.

    Raw JSON must be decoded with Decimal. Converting an already decoded float
    to text cannot recover a lost original representation, so floats are refused.
    Zero is below timing resolution for this ratio-based protocol and is refused.
    """
    if type(value) not in (Decimal, str, int):
        raise ValueError("Use an original decimal value, never a binary float")
    try:
        decimal = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid decimal milliseconds") from None
    if not decimal.is_finite() or decimal <= 0:
        raise ValueError("Milliseconds must be positive and finite")
    microseconds = Fraction(decimal) * 1000
    if microseconds.denominator != 1:
        raise ValueError("Millisecond value has precision finer than one microsecond")
    return microseconds.numerator


def load_explain_json(raw: str | bytes | bytearray) -> dict:
    """Decode raw EXPLAIN JSON before any binary-float conversion."""
    def reject_constant(value):
        raise ValueError(f"Nonfinite JSON value: {value}")

    value = json.loads(raw, parse_float=Decimal, parse_constant=reject_constant)
    if isinstance(value, list):
        if len(value) != 1:
            raise ValueError("Expected exactly one EXPLAIN document")
        value = value[0]
    if not isinstance(value, dict) or not isinstance(value.get("Plan"), dict):
        raise ValueError("EXPLAIN must contain a Plan object")
    return value
