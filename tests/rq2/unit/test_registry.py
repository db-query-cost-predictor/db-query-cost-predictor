"""Synthetic query templates and fixture data; no inference or database calls."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from rq2_pipeline.fixture_registry import (
    Catalog, Column, ForeignKey, GROUPS, TABLES, evaluate_registry,
    family_preconditions, fixtures_for, render_fixture, schema_assertions,
)
from .mutation_controls import handwritten_mutations
from rq2_pipeline.safety import validate_read_only_sql


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
QUERIES = json.loads((FIXTURES / "query_examples.json").read_text(encoding="utf-8"))["queries"]


def catalog_sample():
    data = json.loads((FIXTURES / "catalog.json").read_text(encoding="utf-8"))
    return Catalog(tuple(Column(**row) for row in data["columns"]),
                   tuple((table, tuple(columns)) for table, columns in data["primary_keys"]),
                   tuple(ForeignKey(**{**row, "columns": tuple(row["columns"]),
                                        "parent_columns": tuple(row["parent_columns"])})
                         for row in data["foreign_keys"]))


@pytest.mark.parametrize("query", QUERIES, ids=[q["id"] for q in QUERIES])
def test_registry_provides_parameter_aware_schema_isolated_cases_and_mutations(query):
    catalog = catalog_sample()
    assert family_preconditions(query, catalog)["passed"]
    fixtures = fixtures_for(query, catalog)
    assert fixtures[0].name == "empty_database"
    hazards = {hazard for fixture in fixtures for hazard in fixture.hazards}
    assert "zero_matching_rows" in hazards and "duplicates" in hazards
    for fixture in fixtures:
        assert {table for table, _ in fixture.tables} == TABLES
        sql = "WITH " + render_fixture(fixture, catalog) + " SELECT 1"
        assert validate_read_only_sql(sql).first_word == "with"
    controls = handwritten_mutations(query)
    assert all(control["source"] == "HANDWRITTEN_NEGATIVE_CONTROL" for control in controls)
    assert all(control["sql"] != query["sql"] for control in controls)
    assert {control["hazard"] for control in controls} <= hazards


def test_registry_has_22_fixtures_and_every_schema_constraint_has_an_assertion():
    catalog = catalog_sample()
    assert len(GROUPS) == 5
    assert sum(len(fixtures_for(query, catalog)) for query in QUERIES) == 22
    assertions = schema_assertions(catalog)
    assert len(assertions) == 24
    assert sum(name.startswith("not_null:") for name, _ in assertions) == 8
    assert sum(name.startswith("primary_key:") for name, _ in assertions) == 8
    assert sum(name.startswith("foreign_key:") for name, _ in assertions) == 8


def test_join_removal_checks_exact_fk_columns_and_non_null_child_key():
    catalog = catalog_sample()
    query = QUERIES[3]
    wrong_fk = replace(catalog, foreign_keys=tuple(
        replace(key, columns=("o_orderkey",)) if key.table == "orders" else key
        for key in catalog.foreign_keys))
    assert not family_preconditions(query, wrong_fk)["passed"]
    nullable = replace(catalog, columns=tuple(
        replace(column, not_null=False) if (column.table, column.name) == ("orders", "o_custkey") else column
        for column in catalog.columns))
    assert not family_preconditions(query, nullable)["passed"]


def test_unregistered_family_cannot_trigger_database_work_or_pass():
    query = {**QUERIES[0], "semantic_group_id": "unsupported"}
    result = evaluate_registry(None, query, "SELECT 1", catalog_sample())
    assert result["status"] == "REVIEW_REQUIRED" and result["checks"] == []


@pytest.mark.parametrize("nation", [0, 24])
def test_correlated_sum_parameters_do_not_create_duplicate_nation_keys(nation):
    query = {**QUERIES[1], "parameters": {"nation_key": nation}}
    catalog = catalog_sample()
    for fixture in fixtures_for(query, catalog):
        nations = dict(fixture.tables)["nation"]
        assert len(nations) == len({row[0] for row in nations})


def test_invalid_parameters_refuse_fixture_generation():
    query = {**QUERIES[1], "parameters": {"nation_key": True}}
    with pytest.raises(ValueError):
        fixtures_for(query, catalog_sample())
