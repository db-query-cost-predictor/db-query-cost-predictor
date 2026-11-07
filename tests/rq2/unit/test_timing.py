"""Original decimal timings must never be truncated through binary floats."""

from decimal import Decimal, localcontext

import pytest

from rq2_pipeline.timing import load_explain_json, milliseconds_to_microseconds


@pytest.mark.parametrize("value,expected", [("1.005", 1005), (Decimal("1.005"), 1005),
                                           ("0.001", 1), ("15000.000", 15_000_000),
                                           (1, 1000), ("1.0050", 1005)])
def test_decimal_conversion_is_exact(value, expected):
    assert milliseconds_to_microseconds(value) == expected


@pytest.mark.parametrize("value", [1.005, True, 0, "-1", "NaN", "Infinity", "0.0001", "invalid"])
def test_rounded_nonpositive_or_invalid_inputs_are_refused(value):
    with pytest.raises(ValueError):
        milliseconds_to_microseconds(value)


def test_conversion_does_not_depend_on_decimal_arithmetic_context():
    with localcontext() as context:
        context.prec = 2
        assert milliseconds_to_microseconds("12345.678") == 12_345_678


def test_raw_explain_json_is_parsed_before_float_conversion():
    plan = load_explain_json('[{"Plan":{"Node Type":"Result"},"Execution Time":1.005}]')
    assert isinstance(plan["Execution Time"], Decimal)
    assert milliseconds_to_microseconds(plan["Execution Time"]) == 1005


@pytest.mark.parametrize("raw", ['[]', '[{"Plan":{}},{"Plan":{}}]', '{}',
                                 '{"Plan":{},"Execution Time":NaN}'])
def test_invalid_or_nonfinite_explain_documents_are_refused(raw):
    with pytest.raises(ValueError):
        load_explain_json(raw)
