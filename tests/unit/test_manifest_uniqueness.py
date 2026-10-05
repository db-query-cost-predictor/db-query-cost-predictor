"""Manifest expansion: unique keys, seeded order, honest skips (no database; synthetic ids)."""

from __future__ import annotations

import copy
from collections import defaultdict

import pytest

from query_cost_predictor.config import load_poster_smoke_config
from query_cost_predictor.smoke_manifest import (
    SKIPPED_SNAPSHOT,
    assign_execution_order,
    check_entries,
    expand_entries,
    manifest_document,
    manifest_id_for,
)

SNAPSHOTS = {"tpch_sf0_1": "snap_test_sf0_1", "tpch_sf1": "snap_test_sf1"}
CONFIGS = {"C1_baseline": "cfg_test_c1", "C2_reduced_work_mem": "cfg_test_c2"}


@pytest.fixture(scope="module")
def cfg():
    return load_poster_smoke_config()


def test_all_keys_unique_and_within_range(cfg) -> None:
    entries, skipped = expand_entries(cfg, SNAPSHOTS, CONFIGS)
    keys = [e["modeling_key"] for e in entries]
    assert len(keys) == len(set(keys))
    assert cfg.protocol.min_keys <= len(entries) <= cfg.protocol.max_keys
    assert skipped == []
    assert check_entries(cfg, assign_execution_order(entries, cfg.protocol.order_seed)) == []


def test_missing_optional_snapshot_is_reported_not_silently_dropped(cfg) -> None:
    entries, skipped = expand_entries(cfg, {"tpch_sf0_1": "snap_test_sf0_1"}, CONFIGS)
    assert skipped, "SF 1 entries must be listed as skipped"
    assert all(s["reason"] == SKIPPED_SNAPSHOT and s["snapshot"] == "tpch_sf1" for s in skipped)
    assert check_entries(cfg, assign_execution_order(entries, cfg.protocol.order_seed)) == []


def test_execution_order_is_deterministic_and_seeded(cfg) -> None:
    entries, _ = expand_entries(cfg, SNAPSHOTS, CONFIGS)
    first = assign_execution_order(entries, 1)
    again = assign_execution_order(list(reversed(entries)), 1)
    other = assign_execution_order(entries, 2)
    assert [e["modeling_key"] for e in first] == [e["modeling_key"] for e in again]
    assert [e["modeling_key"] for e in first] != [e["modeling_key"] for e in other]
    assert sorted(e["execution_order"] for e in first) == list(range(1, len(first) + 1))


def test_duplicate_modeling_key_is_detected(cfg) -> None:
    entries, _ = expand_entries(cfg, SNAPSHOTS, CONFIGS)
    ordered = assign_execution_order(entries, 1)
    duplicated = ordered + [dict(ordered[0], execution_order=len(ordered) + 1)]
    assert any("duplicate modeling keys" in problem for problem in check_entries(cfg, duplicated))


def test_rewrite_forms_share_one_semantic_group(cfg) -> None:
    entries, _ = expand_entries(cfg, SNAPSHOTS, CONFIGS)
    groups = defaultdict(set)
    forms = defaultdict(set)
    for entry in entries:
        if entry["rewrite_pair_id"]:
            groups[entry["rewrite_pair_id"]].add(entry["semantic_group_id"])
            forms[entry["rewrite_pair_id"]].add(entry["form"])
    assert groups and all(len(g) == 1 for g in groups.values())
    assert sum(1 for f in forms.values() if {"original", "reference_rewrite"} <= f) >= 2


def test_manifest_id_is_a_content_hash(cfg) -> None:
    entries, skipped = expand_entries(cfg, SNAPSHOTS, CONFIGS)
    ordered = assign_execution_order(entries, cfg.protocol.order_seed)
    document = manifest_document(cfg, ordered, skipped, {}, {})
    assert manifest_id_for(document) == manifest_id_for(copy.deepcopy(document))
    changed = copy.deepcopy(document)
    changed["entries"][0]["sql_text"] += " "
    assert manifest_id_for(changed) != manifest_id_for(document)
