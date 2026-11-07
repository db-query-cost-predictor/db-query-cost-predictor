"""Schema-compatible empty-database fixture primitive, not full family coverage.

Empty relations satisfy primary keys, foreign keys and NOT NULL declarations
vacuously. They do not test a zero-match subquery inside nonempty tables. Those
fixtures and family-specific boundary/multiplicity checks remain necessary.
"""

from dataclasses import dataclass
import re
from typing import Sequence

from rq2_pipeline.context import TABLES, TYPES
from rq2_pipeline.safety import validate_read_only_sql
from rq2_pipeline.sqltext import tokenize


@dataclass(frozen=True)
class EmptyDatabaseFixture:
    ctes: str
    name: str = "empty_database"


def empty_database_fixture(columns: Sequence[Sequence[str]]) -> EmptyDatabaseFixture:
    relations = {table: [] for table in TABLES}
    seen = set()
    for row in columns:
        if not isinstance(row, (list, tuple)) or len(row) != 4:
            raise ValueError("Expected table, column, type and nullability catalog values")
        table, column, kind, nullable = row
        if (not isinstance(table, str) or table not in TABLES or not isinstance(column, str)
                or not re.fullmatch(r"[a-z_][a-z0-9_]*", column)
                or not isinstance(kind, str) or kind not in TYPES or nullable not in {"YES", "NO"}):
            raise ValueError("Invalid fixture catalog declaration")
        if (table, column) in seen:
            raise ValueError("Duplicate fixture column")
        seen.add((table, column))
        relations[table].append((column, kind))
    if any(not columns for columns in relations.values()):
        raise ValueError("Empty fixture must shadow all eight TPC-H base tables")
    ctes = []
    for table, definitions in sorted(relations.items()):
        names = ",".join(column for column, _ in definitions)
        types = ",".join(f"NULL::{kind}" for _, kind in definitions)
        ctes.append(f"{table}({names}) AS (SELECT {types} WHERE false)")
    return EmptyDatabaseFixture(",".join(ctes))


def bind_empty_database(statement: str, fixture: EmptyDatabaseFixture) -> str:
    normalized = validate_read_only_sql(statement).sql
    tokens = [token for token in tokenize(normalized) if token.kind not in {"ws", "comment"}]
    for index, token in enumerate(tokens):
        if (token.kind in {"word", "qident"} and token.value.lower() in TABLES
                and index and tokens[index - 1].text == "."):
            raise ValueError("Schema-qualified base relations could bypass the fixture")
    return f"WITH {fixture.ctes} SELECT * FROM ({normalized}) AS qcp_empty_fixture_output"


def compare_empty_database(conn, original_sql: str, candidate_sql: str,
                           columns: Sequence[Sequence[str]]) -> dict:
    """Run one fixture in a restricted search path; this is not family acceptance."""
    from rq2_pipeline.verification import compare_on_snapshot

    fixture = empty_database_fixture(columns)
    original = bind_empty_database(original_sql, fixture)
    candidate = bind_empty_database(candidate_sql, fixture)
    result = compare_on_snapshot(conn, original, candidate,
                                  settings={"jit": "off", "work_mem": "4MB", "search_path": "pg_catalog"})
    return {"fixture": fixture.name, "coverage": "ONE_EMPTY_DATABASE_FIXTURE_ONLY", **result}
