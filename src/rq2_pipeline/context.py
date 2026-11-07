"""Allowlisted catalog/plain-plan projection for the RQ2 generation prompt.

This is a typed projection from trusted catalog queries, not a general secret
detector. It rejects unknown fields instead of forwarding arbitrary nested JSON.
No execution outcomes, reference SQL or runtime labels belong in this contract.
"""

from __future__ import annotations

from decimal import Decimal
import math
import re
from typing import Any, Mapping


TABLES = frozenset({"region", "nation", "part", "supplier", "partsupp", "customer", "orders", "lineitem"})
IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*\Z")
TYPES = frozenset({"integer", "bigint", "smallint", "numeric", "date", "character", "character varying"})
PLAN_TEXT_FIELDS = frozenset({
    "Node Type", "Parent Relationship", "Subplan Name", "Scan Direction", "Index Name",
    "Relation Name", "Schema", "Alias", "CTE Name", "Strategy", "Partial Mode", "Operation",
    "Join Type", "Index Cond", "Recheck Cond", "Filter", "Join Filter", "Hash Cond", "Merge Cond",
    "Function Name", "Function Call", "Cache Key", "Cache Mode",
})
PLAN_BOOLEAN_FIELDS = frozenset({"Parallel Aware", "Async Capable", "Inner Unique", "Single Copy"})
PLAN_NUMBER_FIELDS = frozenset({"Startup Cost", "Total Cost", "Plan Rows", "Plan Width", "Workers Planned"})
PLAN_TEXT_LIST_FIELDS = frozenset({"Output", "Sort Key", "Group Key", "Hash Key", "Presorted Key"})
PLAN_FIELDS = PLAN_TEXT_FIELDS | PLAN_BOOLEAN_FIELDS | PLAN_NUMBER_FIELDS | PLAN_TEXT_LIST_FIELDS | {"Plans"}


def _mapping(value: Any, fields: set | frozenset, name: str, *, required=()) -> Mapping:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    if set(value) - fields or set(required) - set(value):
        raise ValueError(f"Unexpected or missing field in {name}")
    return value


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {name} identifier")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _records(value: Any, width: int, name: str) -> list:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must contain catalog rows")
    if any(not isinstance(row, (list, tuple)) or len(row) != width for row in value):
        raise ValueError(f"Unexpected catalog row shape in {name}")
    return [list(row) for row in value]


def _settings(raw: Any) -> dict:
    settings = _mapping(raw, {"jit", "work_mem"}, "settings")
    if "jit" in settings and settings["jit"] != "off":
        raise ValueError("This study declares JIT off")
    if "work_mem" in settings and settings["work_mem"] not in {"4MB", "256kB"}:
        raise ValueError("Unexpected work_mem for this study")
    return dict(settings)


def _schema(raw: Any) -> dict:
    fields = {"columns", "constraints", "indexes", "row_counts"}
    schema = _mapping(raw, fields, "schema", required=fields)
    columns = _records(schema["columns"], 4, "columns")
    seen = set()
    for table, column, kind, nullable in columns:
        if not isinstance(table, str) or table not in TABLES:
            raise ValueError("Unexpected catalog table")
        _identifier(column, "column")
        if kind not in TYPES or nullable not in {"YES", "NO"}:
            raise ValueError("Unexpected catalog type or nullability")
        if (table, column) in seen:
            raise ValueError("Duplicate catalog column")
        seen.add((table, column))
    constraints = _records(schema["constraints"], 2, "constraints")
    for table, definition in constraints:
        if table not in TABLES or not _text(definition, "constraint").startswith(("PRIMARY KEY ", "FOREIGN KEY ")):
            raise ValueError("Supply only declared primary/foreign keys for this prompt")
    indexes = _records(schema["indexes"], 3, "indexes")
    for table, name, definition in indexes:
        if table not in TABLES:
            raise ValueError("Unexpected index table")
        _identifier(name, "index")
        if not _text(definition, "index definition").startswith(("CREATE INDEX ", "CREATE UNIQUE INDEX ")):
            raise ValueError("Unexpected catalog index definition")
    counts = _mapping(schema["row_counts"], TABLES, "row_counts")
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise ValueError("Row counts must be nonnegative integers")
    return {"columns": columns, "constraints": constraints, "indexes": indexes, "row_counts": dict(counts)}


def _plan_node(raw: Any) -> dict:
    node = _mapping(raw, PLAN_FIELDS, "plain plan node", required=("Node Type",))
    projected = {}
    for key, value in node.items():
        if key in PLAN_TEXT_FIELDS:
            projected[key] = _text(value, key)
        elif key in PLAN_BOOLEAN_FIELDS:
            if type(value) is not bool:
                raise ValueError(f"{key} must be boolean")
            projected[key] = value
        elif key in PLAN_NUMBER_FIELDS:
            if type(value) not in (int, float, Decimal) or not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid estimated numeric field: {key}")
            projected[key] = value
        elif key in PLAN_TEXT_LIST_FIELDS:
            if not isinstance(value, (list, tuple)) or any(not isinstance(x, str) for x in value):
                raise ValueError(f"{key} must be a string list")
            projected[key] = list(value)
        elif key == "Plans":
            if not isinstance(value, (list, tuple)):
                raise ValueError("Plans must be a list")
            projected[key] = [_plan_node(child) for child in value]
    return projected


def project_context(raw: Mapping[str, Any]) -> dict:
    """Return only the declared, recursively validated context fields."""
    fields = {"database", "settings", "schema", "plain_explain"}
    context = _mapping(raw, fields, "context", required=fields)
    plan = _mapping(context["plain_explain"], {"Plan", "Settings", "Planning Time"},
                    "plain_explain", required=("Plan",))
    # Planning duration is discarded; only plain estimates enter generation.
    return {
        "database": _identifier(context["database"], "database"),
        "settings": _settings(context["settings"]),
        "schema": _schema(context["schema"]),
        "plain_explain": {"Plan": _plan_node(plan["Plan"]), "Settings": _settings(plan.get("Settings", {}))},
    }
