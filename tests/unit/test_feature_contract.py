"""Feature-availability enforcement: E-class and identity columns never enter a model."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from query_cost_predictor.contract import (
    ContractError,
    LeakageError,
    UnregisteredFieldError,
    assert_feature_matrix_allowed,
    load_registry,
    parse_registry,
)
from query_cost_predictor.pilot_audit import MODEL_FEATURES
from query_cost_predictor.plans import estimate_features
from query_cost_predictor.smoke_derive import FEATURE_COLUMNS
from query_cost_predictor.sqltext import sql_structure_features

E_CLASS_COLUMNS = [
    "execution_time_ms", "actual_rows", "actual_loops", "shared_hit_blocks", "shared_read_blocks",
    "temp_read_blocks", "temp_written_blocks", "io_read_time_ms", "run_status", "label_status",
    "median_ms", "workers_launched", "spill_detected", "planning_time_ms", "wal_records",
]
IDENTITY_COLUMNS = [
    "template_id", "semantic_group_id", "query_instance_id", "modeling_key", "query_id", "seed",
    "family", "configuration_name", "rewrite_pair_id", "sql_fingerprint",
]
TYPE_PREFIXES = ("text", "integer", "bigint", "boolean", "jsonb", "numeric", "double", "timestamptz", "char")


def migration_columns(sql_dir: Path) -> dict[str, list[str]]:
    """Parse CREATE TABLE qcp.<table> blocks of the evidence migrations."""
    columns: dict[str, list[str]] = {}
    for path in sorted(sql_dir.glob("V*.sql")):
        current = None
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            match = re.match(r"CREATE TABLE qcp\.(\w+) \($", line)
            if match:
                current = match.group(1)
                columns[current] = []
                continue
            if current is None:
                continue
            if line.startswith(");"):
                current = None
                continue
            parts = line.split()
            if len(parts) >= 2 and re.fullmatch(r"[a-z_][a-z0-9_]*", parts[0]) and parts[1].startswith(TYPE_PREFIXES):
                columns[current].append(parts[0])
    return columns


@pytest.mark.parametrize("column", E_CLASS_COLUMNS)
def test_e_class_columns_are_rejected(column: str) -> None:
    with pytest.raises(LeakageError):
        assert_feature_matrix_allowed(["est_total_cost_log", column])


@pytest.mark.parametrize("column", IDENTITY_COLUMNS)
def test_identity_and_group_columns_are_rejected(column: str) -> None:
    with pytest.raises(LeakageError):
        assert_feature_matrix_allowed(["est_total_cost_log", column])


def test_unregistered_columns_fail_closed() -> None:
    with pytest.raises(UnregisteredFieldError):
        assert_feature_matrix_allowed(["est_total_cost_log", "a_new_unreviewed_column"])


def test_planner_cost_is_a_d_class_feature() -> None:
    registry = load_registry()
    for name in ("est_total_cost_log", "est_total_cost", "estimated_plan.est_total_cost"):
        spec = registry.get(name)
        assert spec is not None and spec.classes == ("D",) and spec.is_model_input


def test_project_feature_sets_pass_the_contract() -> None:
    assert_feature_matrix_allowed(MODEL_FEATURES)
    assert_feature_matrix_allowed(FEATURE_COLUMNS)


def test_every_produced_feature_is_registered(plan_node) -> None:
    registry = load_registry()
    document = {"Plan": plan_node("Hash Join", children=[plan_node(**{"Relation Name": "orders"}), plan_node("Hash")])}
    produced = set(estimate_features(document)) | set(sql_structure_features("SELECT 1"))
    missing = [name for name in produced if registry.get(name) is None or not registry.get(name).is_model_input]
    assert missing == []


def test_estimate_features_ignore_execution_only_fields(plan_node) -> None:
    plain = {"Plan": plan_node("Gather", children=[plan_node(**{"Workers Planned": 2})], **{"Workers Planned": 2})}
    analyzed = {
        "Plan": plan_node(
            "Gather",
            children=[plan_node(**{"Workers Planned": 2, "Actual Rows": 999, "Actual Loops": 3, "Shared Read Blocks": 77})],
            **{"Workers Planned": 2, "Workers Launched": 1, "Actual Rows": 5, "Temp Written Blocks": 12, "I/O Read Time": 4.2},
        ),
        "Execution Time": 123.0,
        "Planning Time": 1.0,
    }
    assert estimate_features(plain) == estimate_features(analyzed)


def test_every_migration_column_is_registered(repo_root: Path) -> None:
    registry = load_registry()
    columns = migration_columns(repo_root / "sql" / "evidence")
    assert columns, "no CREATE TABLE blocks parsed"
    missing = [f"{table}.{col}" for table, cols in columns.items() for col in cols if registry.get(f"{table}.{col}") is None]
    assert missing == []
    known = {f"{table}.{col}" for table, cols in columns.items() for col in cols}
    stale = [name for name in registry.fields if "." in name and not name.startswith("schema_migration.") and name not in known]
    assert stale == []


def test_registry_rejects_an_e_class_feature() -> None:
    with pytest.raises(ContractError):
        parse_registry({"version": 1, "fields": [{"name": "x", "class": "E", "role": "feature", "description": "d"}]})
