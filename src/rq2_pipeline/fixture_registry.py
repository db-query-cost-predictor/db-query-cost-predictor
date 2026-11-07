"""Versioned, parameter-aware fixtures with executed schema assertions.

All data is synthetic and exists only in read-only CTEs. No model is called.
Passing finite fixtures means tested agreement, not a semantic proof.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
import re
from typing import Any

from rq2_pipeline.executor import run_statement
from rq2_pipeline.hashing import sha256_json, sha256_text
from rq2_pipeline.context import TABLES
from rq2_pipeline.fixtures import EmptyDatabaseFixture, bind_empty_database
from rq2_pipeline.verification import compare_on_snapshot
from rq2_pipeline.safety import validate_read_only_sql


VERSION = "rq2-fixture-registry-v2"
GROUPS = {
    "sg_x1_rw_rw04_date_arithmetic": "date_arithmetic",
    "sg_x1_rw_rw10_correlated_sum_unindexed": "correlated_sum",
    "sg_x1_rw_rw12_not_in_to_not_exists": "anti_subquery",
    "sg_x1_rw_rw21_inner_join_elimination": "join_removal",
    "sg_x1_rw_rw23_cte_materialized": "materialized_cte",
}
SETTINGS = {"jit": "off", "work_mem": "4MB", "search_path": "pg_catalog"}
TYPE_SQL = re.compile(r"(?:integer|bigint|smallint|date|numeric\([1-9][0-9]*,[0-9]+\)|character(?: varying)?\([1-9][0-9]*\))\Z")
IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*\Z")


@dataclass(frozen=True)
class Column:
    table: str
    name: str
    type_sql: str
    not_null: bool


@dataclass(frozen=True)
class ForeignKey:
    name: str
    table: str
    columns: tuple[str, ...]
    parent: str
    parent_columns: tuple[str, ...]


@dataclass(frozen=True)
class Catalog:
    columns: tuple[Column, ...]
    primary_keys: tuple[tuple[str, tuple[str, ...]], ...]
    foreign_keys: tuple[ForeignKey, ...]

    def table_columns(self, table):
        return tuple(column for column in self.columns if column.table == table)

    @property
    def identity(self):
        return sha256_json(asdict(self))


@dataclass(frozen=True)
class Fixture:
    name: str
    tables: tuple[tuple[str, tuple[tuple[Any, ...], ...]], ...]
    hazards: tuple[str, ...]


def capture_catalog(conn) -> Catalog:
    rows = conn.execute(
        "SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull "
        "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum").fetchall()
    columns = []
    lookup = {}
    for table, number, name, kind, not_null in rows:
        if table not in TABLES or not IDENTIFIER.fullmatch(name) or not TYPE_SQL.fullmatch(kind):
            raise ValueError("Unsupported table, column or type in fixture catalog")
        columns.append(Column(table, name, kind, not_null))
        lookup[(table, number)] = name
    if {column.table for column in columns} != TABLES:
        raise ValueError("Require all eight declared TPC-H tables")
    constraints = conn.execute(
        "SELECT con.conname,con.contype,c.relname,f.relname,con.conkey,con.confkey,"
        "con.convalidated,con.confmatchtype FROM pg_constraint con "
        "JOIN pg_class c ON c.oid=con.conrelid LEFT JOIN pg_class f ON f.oid=con.confrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "ORDER BY c.relname,con.conname").fetchall()
    primary_keys, foreign_keys = [], []
    for name, kind, table, parent, keys, parent_keys, validated, match_type in constraints:
        if kind not in {"p", "f"} or not validated:
            raise ValueError("Fixture validation requires validated PK/FK constraints only")
        names = tuple(lookup[(table, key)] for key in keys)
        if kind == "p":
            primary_keys.append((table, names))
        else:
            if parent not in TABLES or match_type != "s":
                raise ValueError("Unsupported foreign key target or match type")
            foreign_keys.append(ForeignKey(name, table, names, parent,
                                           tuple(lookup[(parent, key)] for key in parent_keys)))
    if {table for table, _ in primary_keys} != TABLES:
        raise ValueError("Require one declared primary key per TPC-H table")
    return Catalog(tuple(columns), tuple(primary_keys), tuple(foreign_keys))


def _literal(value):
    if value is None:
        return "NULL"
    if type(value) is int:
        return str(value)
    if isinstance(value, Decimal) and value.is_finite():
        return str(value)
    if isinstance(value, date):
        return "DATE '" + value.isoformat() + "'"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise ValueError("Unsupported synthetic fixture value")


def render_fixture(fixture: Fixture, catalog: Catalog) -> str:
    if tuple(name for name, _ in fixture.tables) != tuple(sorted(TABLES)):
        raise ValueError("Fixture must shadow each of the eight base tables exactly once")
    parts = []
    for table, rows in fixture.tables:
        columns = catalog.table_columns(table)
        if not columns or any(len(row) != len(columns) for row in rows):
            raise ValueError("Fixture/catalog column count mismatch")
        names = ",".join(column.name for column in columns)
        if rows:
            values = ",".join("(" + ",".join(f"({_literal(v)})::{c.type_sql}" for v, c in zip(row, columns))
                              + ")" for row in rows)
            body = "VALUES " + values
        else:
            body = "SELECT " + ",".join(f"NULL::{c.type_sql}" for c in columns) + " WHERE false"
        parts.append(f"{table}({names}) AS ({body})")
    return ",".join(parts)


def schema_assertions(catalog: Catalog) -> tuple[tuple[str, str], ...]:
    assertions = []
    for table in sorted(TABLES):
        non_null = [column.name for column in catalog.table_columns(table) if column.not_null]
        if non_null:
            predicate = " OR ".join(f"{column} IS NULL" for column in non_null)
            assertions.append((f"not_null:{table}", f"NOT EXISTS (SELECT 1 FROM {table} WHERE {predicate})"))
    for table, columns in catalog.primary_keys:
        assertions.append((f"primary_key:{table}",
                           f"NOT EXISTS (SELECT 1 FROM {table} GROUP BY {','.join(columns)} HAVING count(*)>1)"))
    for key in catalog.foreign_keys:
        non_null = " AND ".join(f"c.{column} IS NOT NULL" for column in key.columns)
        equal = " AND ".join(f"c.{column}=p.{parent}" for column, parent in zip(key.columns, key.parent_columns))
        assertions.append((f"foreign_key:{key.name}", f"NOT EXISTS (SELECT 1 FROM {key.table} c WHERE {non_null} "
                           f"AND NOT EXISTS (SELECT 1 FROM {key.parent} p WHERE {equal}))"))
    return tuple(assertions)


def validate_fixture(conn, fixture: Fixture, catalog: Catalog) -> dict:
    ctes = render_fixture(fixture, catalog)
    assertions = schema_assertions(catalog)
    expressions = ",".join(f"({expression}) AS assertion_{i}" for i, (_, expression) in enumerate(assertions))
    sql = validate_read_only_sql(f"WITH {ctes} SELECT {expressions}").sql
    outcome = run_statement(conn, sql, settings=SETTINGS, statement_timeout_ms=15_000,
                            lock_timeout_ms=5_000, watchdog_grace_ms=2_000, fetch_rows_cap=1)
    values = outcome.rows[0] if outcome.status == "completed" and outcome.rows else ()
    checks = [{"name": name, "expression": expression, "passed": i < len(values) and values[i] is True}
              for i, (name, expression) in enumerate(assertions)]
    passed = outcome.status == "completed" and len(values) == len(assertions) and all(c["passed"] for c in checks)
    return {"passed": passed, "checks": checks, "execution": vars(outcome),
            "assertion_sql": sql, "assertion_sql_sha256": sha256_text(sql), "catalog_sha256": catalog.identity}


def _default(column: Column):
    if column.type_sql in {"integer", "bigint", "smallint"}:
        return 1
    if column.type_sql.startswith("numeric"):
        return Decimal("1.00")
    if column.type_sql == "date":
        return date(1994, 1, 1)
    return "F"


def _dataset(catalog: Catalog, *, nation=1, supplier_nations=None, orders=None, lines=(), customers=(1, 2, 3, 4)):
    suppliers = supplier_nations if supplier_nations is not None else {1: nation, 2: nation}
    data = {table: [] for table in TABLES}
    def add(table, **values):
        data[table].append(tuple(values.get(column.name, _default(column)) for column in catalog.table_columns(table)))
    add("region", r_regionkey=1)
    for key in sorted({1, nation, *suppliers.values()}):
        add("nation", n_nationkey=key, n_regionkey=1)
    for key in customers:
        add("customer", c_custkey=key, c_nationkey=1)
    add("part", p_partkey=1)
    for key, nation_key in suppliers.items():
        add("supplier", s_suppkey=key, s_nationkey=nation_key)
        add("partsupp", ps_partkey=1, ps_suppkey=key)
    if orders is None:
        orders = [(1, 1, date(1994, 1, 1), 10, 1)]
    for key, customer, day, price, priority in orders:
        add("orders", o_orderkey=key, o_custkey=customer, o_orderdate=day,
            o_totalprice=price, o_shippriority=priority)
    for order, number, supplier, day, quantity, price in lines:
        add("lineitem", l_orderkey=order, l_linenumber=number, l_partkey=1, l_suppkey=supplier,
            l_shipdate=day, l_commitdate=day + timedelta(days=1), l_receiptdate=day + timedelta(days=2),
            l_quantity=quantity, l_extendedprice=price)
    return tuple((table, tuple(data[table])) for table in sorted(TABLES))


def _integer_parameter(parameters, key, minimum=0, maximum=3650):
    value = parameters.get(key)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Invalid {key} fixture parameter")
    return value


def family_preconditions(query, catalog: Catalog) -> dict:
    family = GROUPS.get(query.get("semantic_group_id"))
    if family is None or query.get("ordering_required"):
        return {"passed": False, "reason": "Unsupported family or ordering contract"}
    if family in {"date_arithmetic", "materialized_cte"}:
        required = [("lineitem", "l_shipdate", "date", True)]
    elif family == "anti_subquery":
        required = [("customer", "c_custkey", "integer", True), ("orders", "o_custkey", "integer", True),
                    ("orders", "o_orderdate", "date", True)]
    elif family == "join_removal":
        required = [("orders", "o_custkey", "integer", True), ("orders", "o_orderdate", "date", True)]
    else:
        required = []
    actual = {(column.table, column.name, column.type_sql, column.not_null) for column in catalog.columns}
    passed = all(item in actual for item in required)
    exact_fk = True
    if family == "join_removal":
        exact_fk = any(key.table == "orders" and key.columns == ("o_custkey",)
                       and key.parent == "customer" and key.parent_columns == ("c_custkey",)
                       for key in catalog.foreign_keys)
        passed = passed and exact_fk and ("customer", ("c_custkey",)) in catalog.primary_keys
    return {"passed": passed, "family": family, "required_columns": required, "exact_fk_columns": exact_fk,
            "catalog_sha256": catalog.identity}


def fixtures_for(query, catalog: Catalog) -> tuple[Fixture, ...]:
    family = GROUPS.get(query.get("semantic_group_id"))
    if family is None:
        raise ValueError("No fixture registry entry for this family")
    p = query.get("parameters", {})
    empty = Fixture("empty_database", tuple((table, ()) for table in sorted(TABLES)), ("zero_matching_rows",))
    fixtures = [empty]
    def add(name, hazards, **data):
        fixtures.append(Fixture(name, _dataset(catalog, **data), tuple(hazards)))
    if family == "correlated_sum":
        nation = _integer_parameter(p, "nation_key", 0, 24)
        day = date(1994, 1, 1)
        add("missing_correlated_match", ("zero_matching_rows",), nation=nation,
            supplier_nations={1: nation, 2: nation}, lines=[(1, 1, 1, day, 7, 10)])
        add("suppliers_without_lines", ("zero_matching_rows",), nation=nation,
            supplier_nations={1: nation, 2: nation})
        add("all_matched_duplicate_quantities", ("duplicates",), nation=nation,
            supplier_nations={1: nation, 2: nation},
            lines=[(1, 1, 1, day, 5, 10), (1, 2, 1, day, 5, 10), (1, 3, 2, day, 0, 10)])
        add("nation_filter", ("predicate_boundary",), nation=nation,
            supplier_nations={1: nation, 2: (nation + 1) % 25},
            lines=[(1, 1, 1, day, 7, 10), (1, 2, 2, day, 100, 10)])
        return tuple(fixtures)

    start = date.fromisoformat(p["start_date"])
    days = _integer_parameter(p, "days")
    end = start + timedelta(days=days)
    if family == "date_arithmetic":
        lo, hi = start - timedelta(days=days), start - timedelta(days=days) + timedelta(days=6)
        add("nonempty_no_matching_date", ("zero_matching_rows",),
            lines=[(1, 1, 1, lo - timedelta(days=1), 2, 100), (1, 2, 1, hi + timedelta(days=1), 2, 100)])
        add("inclusive_date_boundaries", ("off_by_one_boundary",),
            lines=[(1, 1, 1, lo, 2, 7), (1, 2, 1, hi, 3, 10),
                   (1, 3, 1, lo - timedelta(days=1), 2, 100), (1, 4, 1, hi + timedelta(days=1), 2, 100)])
        add("duplicate_prices", ("duplicates",),
            lines=[(1, i, 1, lo + timedelta(days=1), 2, price) for i, price in enumerate((7, 7, 10), 1)])
    elif family == "anti_subquery":
        add("customers_without_orders", ("zero_matching_rows",), orders=[])
        add("nonempty_no_qualifying_orders", ("zero_matching_rows",),
            orders=[(i, i, start - timedelta(days=1), 10, 1) for i in range(1, 5)])
        add("order_date_boundaries", ("off_by_one_boundary",),
            orders=[(i, i, day, 10, 1) for i, day in enumerate(
                (start - timedelta(days=1), start, end - timedelta(days=1), end), 1)])
        add("duplicate_customer_matches", ("duplicates",),
            orders=[(1, 1, start, 10, 1), (2, 1, start, 10, 1), (3, 2, end, 10, 1)])
    elif family == "join_removal":
        add("nonempty_no_matching_orders", ("zero_matching_rows",),
            orders=[(1, 1, start - timedelta(days=1), 10, 2), (2, 1, end, 20, 9)])
        add("order_date_boundaries_nonzero_priority", ("off_by_one_boundary", "nonzero_shippriority"),
            orders=[(i, 1, day, price, priority) for i, (day, price, priority) in enumerate(
                ((start, 7, 1), (end - timedelta(days=1), 10, 9),
                 (start - timedelta(days=1), 100, 20), (end, 100, 20)), 1)])
        add("duplicate_prices_nonzero_priority", ("duplicates", "nonzero_shippriority"),
            orders=[(i, 1, start, price, priority) for i, (price, priority) in enumerate(((7, 1), (7, 3), (10, 9)), 1)])
    elif family == "materialized_cte":
        quantity = _integer_parameter(p, "quantity", 1, 1000)
        add("nonempty_no_qualifying_lines", ("zero_matching_rows",),
            lines=[(1, 1, 1, start - timedelta(days=1), quantity, 100),
                   (1, 2, 1, end, quantity, 100), (1, 3, 1, start, quantity - 1, 100)])
        add("date_and_quantity_boundaries", ("off_by_one_boundary",),
            lines=[(1, 1, 1, start, quantity, 7), (1, 2, 1, end - timedelta(days=1), quantity, 10),
                   (1, 3, 1, end, quantity, 100), (1, 4, 1, start, quantity - 1, 100)])
        add("duplicate_prices", ("duplicates",),
            lines=[(1, i, 1, start, quantity, price) for i, price in enumerate((7, 7, 10), 1)])
    return tuple(fixtures)


def evaluate_registry(conn, query, candidate_sql: str, catalog: Catalog | None = None) -> dict:
    """Recheck one candidate; no generation, timing or acceptance override."""
    catalog = catalog or capture_catalog(conn)
    result = {"registry_version": VERSION, "query_id": query.get("id"), "status": "REVIEW_REQUIRED",
              "checks": [], "original_sql_sha256": sha256_text(query["sql"]),
              "candidate_sql_sha256": sha256_text(candidate_sql)}
    result["preconditions"] = family_preconditions(query, catalog)
    if not result["preconditions"]["passed"]:
        return result
    original_sql = validate_read_only_sql(query["sql"]).sql
    candidate_sql = validate_read_only_sql(candidate_sql).sql
    invalid = False
    for fixture in fixtures_for(query, catalog):
        record = {"fixture": asdict(fixture), "fixture_sha256": sha256_json(asdict(fixture))}
        validity = validate_fixture(conn, fixture, catalog)
        record["schema_validity"] = validity
        if not validity["passed"]:
            invalid = True
            result["checks"].append(record)
            continue
        ctes = render_fixture(fixture, catalog)
        bound_original = bind_empty_database(original_sql, EmptyDatabaseFixture(ctes))
        bound_candidate = bind_empty_database(candidate_sql, EmptyDatabaseFixture(ctes))
        record["executed_sql"] = {"original": bound_original, "candidate": bound_candidate}
        record["original_self"] = compare_on_snapshot(conn, bound_original, bound_original, settings=SETTINGS)
        if record["original_self"]["status"] != "AGREED_ON_TESTED_INPUTS":
            invalid = True
        else:
            record["candidate"] = compare_on_snapshot(conn, bound_original, bound_candidate, settings=SETTINGS)
        result["checks"].append(record)
    statuses = [record.get("candidate", {}).get("status") for record in result["checks"]]
    if not invalid and statuses:
        if "MISMATCH" in statuses:
            result["status"] = "REJECTED_COUNTEREXAMPLE"
        elif all(status == "AGREED_ON_TESTED_INPUTS" for status in statuses):
            result["status"] = "AGREED_ON_TESTED_FIXTURES"
    return result
