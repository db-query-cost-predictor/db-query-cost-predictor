"""Synthetic fixture-construction tests; these do not execute PostgreSQL."""

import pytest

from rq2_pipeline.context import TABLES
from rq2_pipeline.fixtures import bind_empty_database, empty_database_fixture


def columns():
    return [[table, "synthetic_key", "integer", "NO"] for table in sorted(TABLES)]


def test_empty_fixture_shadows_every_base_relation():
    fixture = empty_database_fixture(columns())
    statement = bind_empty_database("SELECT count(*) AS n FROM lineitem;", fixture)
    assert statement.count("WHERE false") == 8
    assert "NULL::integer" in statement
    assert "SELECT count(*) AS n FROM lineitem" in statement


@pytest.mark.parametrize("statement", ["SELECT count(*) FROM public.lineitem",
                                       'SELECT count(*) FROM public."lineitem"'])
def test_qualified_relations_cannot_escape_empty_fixture(statement):
    with pytest.raises(ValueError, match="bypass"):
        bind_empty_database(statement, empty_database_fixture(columns()))


def test_incomplete_or_unsafe_catalog_cannot_define_a_fixture():
    with pytest.raises(ValueError, match="eight"):
        empty_database_fixture(columns()[:1])
    bad = columns()
    bad[0][1] = "x) AS (SELECT 1); --"
    with pytest.raises(ValueError):
        empty_database_fixture(bad)


def test_fixture_execution_forces_catalog_only_search_path(monkeypatch):
    from rq2_pipeline import fixtures, verification
    def compare(conn, original, candidate, *, settings):
        assert settings["search_path"] == "pg_catalog"
        assert original.startswith("WITH ") and candidate.startswith("WITH ")
        return {"status": "AGREED_ON_TESTED_INPUTS"}
    monkeypatch.setattr(verification, "compare_on_snapshot", compare)
    result = fixtures.compare_empty_database(None, "SELECT count(*) FROM lineitem",
                                             "SELECT count(*) FROM lineitem", columns())
    assert result["coverage"] == "ONE_EMPTY_DATABASE_FIXTURE_ONLY"
