"""Modeling-key and query-instance identity determinism."""

from __future__ import annotations

import pytest

from query_cost_predictor.hashing import modeling_key, normalize_sql_text, query_instance_id

BASE = ("qi_a", "snap_a", "cfg_a", "warm", "poster-smoke-v1")


def test_modeling_key_is_deterministic() -> None:
    first = modeling_key(*BASE)
    assert first == modeling_key(*BASE)
    assert first.startswith("mk_") and len(first) == 3 + 64


@pytest.mark.parametrize("position", range(5))
def test_every_component_changes_the_key(position: int) -> None:
    changed = list(BASE)
    changed[position] = changed[position] + "_other"
    assert modeling_key(*changed) != modeling_key(*BASE)


def test_empty_component_is_rejected() -> None:
    with pytest.raises(ValueError):
        modeling_key("qi_a", "", "cfg_a", "warm", "v1")


def test_parameter_order_does_not_change_instance_id() -> None:
    a = query_instance_id("tpl", {"a": 1, "b": "1995-01-01"}, "SELECT 1")
    b = query_instance_id("tpl", {"b": "1995-01-01", "a": 1}, "SELECT 1")
    assert a == b


def test_instance_id_normalizes_line_endings_and_trailing_semicolon() -> None:
    assert query_instance_id("tpl", {}, "SELECT 1;\r\n") == query_instance_id("tpl", {}, "SELECT 1")
    assert query_instance_id("tpl", {}, "SELECT 1") != query_instance_id("tpl", {}, "SELECT 2")
    assert normalize_sql_text("  SELECT 1 ;  ") == "SELECT 1"


def test_parameter_value_changes_instance_id() -> None:
    assert query_instance_id("tpl", {"k": 1}, "SELECT 1") != query_instance_id("tpl", {"k": 2}, "SELECT 1")
