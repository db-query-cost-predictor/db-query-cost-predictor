"""PostgreSQL JSON plan parsing with a strict estimate/execution split.

* :func:`estimate_view` removes every execution-only key from a plan tree.
  All D-class features are computed from that view, so an ``EXPLAIN ANALYZE``
  document (as in the pilot) yields exactly the fields a plain ``EXPLAIN``
  would contain.
* :func:`execution_metrics` returns E-class outcomes (runtime, actual rows,
  buffers, temporary blocks, I/O timing, WAL, launched workers, spill flags).
  These are labels and diagnostics only.
"""

from __future__ import annotations

import math
from typing import Any, Iterator

from query_cost_predictor.hashing import sha256_json

_EXECUTION_ONLY_PREFIXES = ("Actual ", "Rows Removed", "Shared ", "Local ", "Temp ", "WAL ")
_EXECUTION_ONLY_KEYS = frozenset(
    {
        "Heap Fetches", "Exact Heap Blocks", "Lossy Heap Blocks", "Sort Method", "Sort Space Used",
        "Sort Space Type", "Presorted Groups", "Full-sort Groups", "Peak Memory Usage", "Disk Usage",
        "HashAgg Batches", "Hash Buckets", "Original Hash Buckets", "Hash Batches",
        "Original Hash Batches", "Cache Hits", "Cache Misses", "Cache Evictions", "Cache Overflows",
        "Workers Launched", "Workers",
    }
)
_TOP_LEVEL_EXECUTION_KEYS = frozenset({"Execution Time", "Planning Time", "Planning", "Triggers", "JIT"})

NODE_CATEGORIES: dict[str, str] = {
    "Seq Scan": "seq_scan",
    "Index Scan": "index_scan",
    "Index Only Scan": "index_only_scan",
    "Bitmap Heap Scan": "bitmap_heap_scan",
    "Bitmap Index Scan": "bitmap_index_scan",
    "BitmapAnd": "bitmap_combine",
    "BitmapOr": "bitmap_combine",
    "Nested Loop": "nested_loop",
    "Hash Join": "hash_join",
    "Merge Join": "merge_join",
    "Hash": "hash",
    "Sort": "sort",
    "Incremental Sort": "incremental_sort",
    "Aggregate": "aggregate",
    "Group": "group",
    "Gather": "gather",
    "Gather Merge": "gather_merge",
    "Materialize": "materialize",
    "Memoize": "memoize",
    "Limit": "limit",
    "Subquery Scan": "subquery_scan",
    "CTE Scan": "cte_scan",
    "WindowAgg": "window_agg",
    "Unique": "unique",
    "Append": "append",
    "Merge Append": "append",
    "Result": "result",
    "SetOp": "setop",
    "Function Scan": "function_scan",
    "Values Scan": "values_scan",
}
NODE_CATEGORY_NAMES: tuple[str, ...] = tuple(sorted(set(NODE_CATEGORIES.values()) | {"other"}))

#: D-class feature names produced by :func:`estimate_features`.
ESTIMATE_FEATURE_NAMES: tuple[str, ...] = (
    "est_total_cost_log",
    "est_startup_cost_log",
    "est_plan_rows_log",
    "est_plan_width",
    "est_node_count",
    "est_max_depth",
    "est_sum_node_rows_log",
    "est_max_node_rows_log",
    "est_join_count",
    "est_scan_count",
    "est_subplan_count",
    "est_relation_count",
    "est_workers_planned",
    "est_parallel_aware_count",
    "est_hashed_aggregate_count",
) + tuple(f"est_n_{name}" for name in NODE_CATEGORY_NAMES)


def plan_document(explain_result: Any) -> dict[str, Any]:
    """Return the top-level plan document from an EXPLAIN JSON result."""
    doc = explain_result[0] if isinstance(explain_result, list) else explain_result
    if not isinstance(doc, dict) or not isinstance(doc.get("Plan"), dict):
        raise ValueError("EXPLAIN result does not contain a 'Plan' object")
    return doc


def _is_execution_key(key: str) -> bool:
    return key.startswith(_EXECUTION_ONLY_PREFIXES) or "I/O" in key or key in _EXECUTION_ONLY_KEYS


def estimate_view(node: dict[str, Any]) -> dict[str, Any]:
    """Recursive copy of a plan node without execution-only keys."""
    view: dict[str, Any] = {}
    for key, value in node.items():
        if _is_execution_key(key):
            continue
        if key == "Plans":
            view["Plans"] = [estimate_view(child) for child in value]
        else:
            view[key] = value
    return view


def estimate_document(explain_result: Any) -> dict[str, Any]:
    """Plan document reduced to estimate-time content (top-level summary keys removed)."""
    doc = plan_document(explain_result)
    reduced = {k: v for k, v in doc.items() if k not in _TOP_LEVEL_EXECUTION_KEYS and k != "Plan"}
    reduced["Plan"] = estimate_view(doc["Plan"])
    return reduced


def iter_nodes(node: dict[str, Any], depth: int = 1) -> Iterator[tuple[dict[str, Any], int]]:
    """Depth-first traversal yielding ``(node, depth)``, including SubPlans/InitPlans."""
    yield node, depth
    for child in node.get("Plans", []) or []:
        yield from iter_nodes(child, depth + 1)


def _shape(node: dict[str, Any], physical: bool) -> list[Any]:
    item: list[Any] = [
        node.get("Node Type"),
        node.get("Parent Relationship"),
        node.get("Join Type"),
        node.get("Strategy"),
        node.get("Partial Mode"),
    ]
    if physical:
        item += [node.get("Relation Name"), node.get("Index Name"), node.get("Scan Direction"),
                 node.get("CTE Name"), node.get("Subplan Name")]
    item.append([_shape(child, physical) for child in node.get("Plans", []) or []])
    return item


def plan_shape_hash(explain_result: Any) -> str:
    """Hash of operator structure (node types and relationships), independent of costs."""
    root = estimate_view(plan_document(explain_result)["Plan"])
    return sha256_json(_shape(root, physical=False))


def physical_plan_hash(explain_result: Any) -> str:
    """Hash of operator structure plus relations, indexes and scan directions."""
    root = estimate_view(plan_document(explain_result)["Plan"])
    return sha256_json(_shape(root, physical=True))


def plan_json_hash(explain_result: Any) -> str:
    """Hash of the complete plan document as returned by PostgreSQL."""
    return sha256_json(plan_document(explain_result))


def node_category(node_type: str | None) -> str:
    return NODE_CATEGORIES.get(node_type or "", "other")


def operator_list(explain_result: Any) -> list[str]:
    """Node types of the estimate view in depth-first order."""
    root = estimate_view(plan_document(explain_result)["Plan"])
    return [str(node.get("Node Type")) for node, _ in iter_nodes(root)]


def _log1p(value: Any) -> float:
    number = float(value or 0.0)
    return math.log1p(max(number, 0.0))


def estimate_features(explain_result: Any) -> dict[str, float]:
    """Availability-class D features from estimate-only plan content."""
    root = estimate_view(plan_document(explain_result)["Plan"])
    nodes = list(iter_nodes(root))
    counts = {name: 0 for name in NODE_CATEGORY_NAMES}
    relations: set[str] = set()
    sum_rows = 0.0
    max_rows = 0.0
    max_depth = 0
    subplans = 0
    workers_planned = 0
    parallel_aware = 0
    hashed_aggregates = 0
    scans = 0
    for node, depth in nodes:
        node_type = node.get("Node Type")
        counts[node_category(node_type)] += 1
        if node_type and "Scan" in node_type:
            scans += 1
        if node.get("Relation Name"):
            relations.add(str(node["Relation Name"]))
        rows = float(node.get("Plan Rows") or 0.0)
        sum_rows += rows
        max_rows = max(max_rows, rows)
        max_depth = max(max_depth, depth)
        if node.get("Parent Relationship") in ("SubPlan", "InitPlan"):
            subplans += 1
        if node_type in ("Gather", "Gather Merge"):
            workers_planned += int(node.get("Workers Planned") or 0)
        if node.get("Parallel Aware"):
            parallel_aware += 1
        if node_type == "Aggregate" and node.get("Strategy") in ("Hashed", "Mixed"):
            hashed_aggregates += 1

    features: dict[str, float] = {
        "est_total_cost_log": _log1p(root.get("Total Cost")),
        "est_startup_cost_log": _log1p(root.get("Startup Cost")),
        "est_plan_rows_log": _log1p(root.get("Plan Rows")),
        "est_plan_width": float(root.get("Plan Width") or 0.0),
        "est_node_count": float(len(nodes)),
        "est_max_depth": float(max_depth),
        "est_sum_node_rows_log": math.log1p(sum_rows),
        "est_max_node_rows_log": math.log1p(max_rows),
        "est_join_count": float(counts["nested_loop"] + counts["hash_join"] + counts["merge_join"]),
        "est_scan_count": float(scans),
        "est_subplan_count": float(subplans),
        "est_relation_count": float(len(relations)),
        "est_workers_planned": float(workers_planned),
        "est_parallel_aware_count": float(parallel_aware),
        "est_hashed_aggregate_count": float(hashed_aggregates),
    }
    for name in NODE_CATEGORY_NAMES:
        features[f"est_n_{name}"] = float(counts[name])
    return features


def estimate_scalars(explain_result: Any) -> dict[str, Any]:
    """Raw root estimates (D) stored in ``estimated_plan``."""
    root = estimate_view(plan_document(explain_result)["Plan"])
    nodes = list(iter_nodes(root))
    workers = sum(int(n.get("Workers Planned") or 0) for n, _ in nodes if n.get("Node Type") in ("Gather", "Gather Merge"))
    return {
        "est_total_cost": float(root.get("Total Cost") or 0.0),
        "est_startup_cost": float(root.get("Startup Cost") or 0.0),
        "est_plan_rows": float(root.get("Plan Rows") or 0.0),
        "est_plan_width": int(root.get("Plan Width") or 0),
        "est_node_count": len(nodes),
        "est_workers_planned": workers,
    }


def _int_or_none(value: Any) -> int | None:
    return None if value is None else int(value)


def _float_or_none(value: Any) -> float | None:
    return None if value is None else float(value)


def execution_metrics(explain_result: Any) -> dict[str, Any]:
    """Availability-class E outcomes from an ``EXPLAIN ANALYZE`` JSON document."""
    doc = plan_document(explain_result)
    root = doc["Plan"]
    nodes = [n for n, _ in iter_nodes(root)]
    gathers = [n for n in nodes if n.get("Node Type") in ("Gather", "Gather Merge")]
    spill = False
    sort_space_types: set[str] = set()
    for node in nodes:
        if int(node.get("Temp Written Blocks") or 0) > 0 or int(node.get("Temp Read Blocks") or 0) > 0:
            spill = True
        if node.get("Sort Space Type"):
            sort_space_types.add(str(node["Sort Space Type"]))
            if node["Sort Space Type"] == "Disk":
                spill = True
        if float(node.get("Disk Usage") or 0) > 0 or int(node.get("HashAgg Batches") or 0) > 1:
            spill = True
        if int(node.get("Hash Batches") or 0) > 1:
            spill = True
    io_read = root.get("I/O Read Time", root.get("Shared I/O Read Time"))
    io_write = root.get("I/O Write Time", root.get("Shared I/O Write Time"))
    return {
        "execution_time_ms": _float_or_none(doc.get("Execution Time")),
        "planning_time_ms": _float_or_none(doc.get("Planning Time")),
        "actual_rows": _float_or_none(root.get("Actual Rows")),
        "actual_loops": _float_or_none(root.get("Actual Loops")),
        "shared_hit_blocks": _int_or_none(root.get("Shared Hit Blocks")),
        "shared_read_blocks": _int_or_none(root.get("Shared Read Blocks")),
        "shared_dirtied_blocks": _int_or_none(root.get("Shared Dirtied Blocks")),
        "shared_written_blocks": _int_or_none(root.get("Shared Written Blocks")),
        "temp_read_blocks": _int_or_none(root.get("Temp Read Blocks")),
        "temp_written_blocks": _int_or_none(root.get("Temp Written Blocks")),
        "io_read_time_ms": _float_or_none(io_read),
        "io_write_time_ms": _float_or_none(io_write),
        "wal_records": _int_or_none(root.get("WAL Records")),
        "wal_fpi": _int_or_none(root.get("WAL FPI")),
        "wal_bytes": _int_or_none(root.get("WAL Bytes")),
        "workers_planned": sum(int(n.get("Workers Planned") or 0) for n in gathers),
        "workers_launched": sum(int(n.get("Workers Launched") or 0) for n in gathers),
        "spill_detected": spill,
        "sort_space_types": sorted(sort_space_types),
    }


def settings_block(explain_result: Any) -> dict[str, Any]:
    """The ``Settings`` object emitted by ``EXPLAIN (SETTINGS)`` (non-default planner settings)."""
    return dict(plan_document(explain_result).get("Settings") or {})
