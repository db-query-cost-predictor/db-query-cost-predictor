"""Expanded manifest: uniqueness, semantic-group separation, holdouts and the bounded sample.

Written but not run by Claude Code. No database: planning entries use placeholder
snapshot and configuration ids, which do not affect fold assignment.
"""

from __future__ import annotations

import copy
from collections import defaultdict

import pytest

from query_cost_predictor.expanded_manifest import (
    assign_splits,
    check_expanded_entries,
    expanded_manifest_document,
    sample_stratum,
    select_sample,
)
from query_cost_predictor.release_spec import SPLIT_DEVELOPMENT, SPLIT_HELDOUT_RQ2, load_release_spec, planning_entries
from query_cost_predictor.smoke_manifest import assign_execution_order, manifest_id_for


@pytest.fixture(scope="module")
def spec():
    return load_release_spec()


@pytest.fixture(scope="module")
def entries(spec):
    ordered = assign_execution_order(planning_entries(spec), spec.protocol.order_seed)
    return assign_splits(ordered, spec.splits.n_folds, spec.splits.seed)


@pytest.fixture(scope="module")
def sample(spec, entries):
    return select_sample(entries, spec.sample.per_stratum, spec.sample.seed)


def test_manifest_checks_pass_for_the_spec(spec, entries, sample) -> None:
    assert check_expanded_entries(spec, entries, sample) == []


def test_every_semantic_group_lies_in_exactly_one_fold(entries) -> None:
    folds = defaultdict(set)
    for entry in entries:
        folds[entry["semantic_group_id"]].add(entry["fold_group"])
    assert all(len(f) == 1 for f in folds.values())
    development_folds = {next(iter(f)) for f in folds.values()} - {None}
    assert development_folds == {0, 1, 2, 3, 4}


def test_related_variants_are_held_out_together(entries) -> None:
    """Parameterizations, snapshots, configurations, rewrite forms and index variants share one fold."""
    by_instance = defaultdict(set)
    by_pair = defaultdict(set)
    by_group_variant = defaultdict(set)
    for entry in entries:
        by_instance[entry["query_instance_id"]].add((entry["semantic_group_id"], entry["fold_group"]))
        if entry["rewrite_pair_id"]:
            by_pair[entry["rewrite_pair_id"]].add((entry["semantic_group_id"], entry["fold_group"]))
        if entry["index_variant"]:
            by_group_variant[entry["semantic_group_id"]].add(entry["fold_group"])
    assert all(len(v) == 1 for v in by_instance.values())
    assert by_pair and all(len(v) == 1 for v in by_pair.values())
    assert by_group_variant and all(len(v) == 1 for v in by_group_variant.values())


def test_rq2_reserved_keys_have_no_fold_and_are_never_sampled(spec, entries, sample) -> None:
    reserved = [e for e in entries if e["split_role"] == SPLIT_HELDOUT_RQ2]
    assert len(reserved) == 160
    assert {e["semantic_group_id"] for e in reserved} == set(spec.holdout_reasons())
    assert all(e["fold_group"] is None and e["fold_family"] is None and e["fold_seen_template"] is None for e in reserved)
    assert not set(sample) & {e["modeling_key"] for e in reserved}


def test_stricter_and_diagnostic_folds_are_consistent(entries) -> None:
    development = [e for e in entries if e["split_role"] == SPLIT_DEVELOPMENT]
    by_family = defaultdict(set)
    by_instance = defaultdict(set)
    template_folds = defaultdict(set)
    for entry in development:
        by_family[entry["structure_family"]].add(entry["fold_family"])
        by_instance[entry["query_instance_id"]].add(entry["fold_seen_template"])
        template_folds[entry["template_id"]].add(entry["fold_seen_template"])
    assert all(len(f) == 1 for f in by_family.values())
    assert all(len(f) == 1 for f in by_instance.values())
    # The seen-template diagnostic deliberately spreads one template over folds: it is
    # labelled a diagnostic and never reported as unseen-structure performance.
    assert any(len(f) > 1 for f in template_folds.values())


def test_sample_is_deterministic_bounded_and_covers_every_stratum(spec, entries, sample) -> None:
    again = select_sample(list(reversed(entries)), spec.sample.per_stratum, spec.sample.seed)
    assert again == sample
    strata = {sample_stratum(e) for e in entries if e["split_role"] == SPLIT_DEVELOPMENT}
    assert len(strata) == 22
    assert len(sample) == 22 * spec.sample.per_stratum == 110
    chosen = {e["modeling_key"]: e for e in entries if e["modeling_key"] in set(sample)}
    assert {sample_stratum(e) for e in chosen.values()} == strata
    other = select_sample(entries, spec.sample.per_stratum, spec.sample.seed + 1)
    assert other != sample


def test_check_detects_a_group_split_across_folds(spec, entries, sample) -> None:
    broken = copy.deepcopy(entries)
    victim = next(e for e in broken if e["split_role"] == SPLIT_DEVELOPMENT)
    victim["fold_group"] = (victim["fold_group"] + 1) % 5
    assert any("several folds" in p for p in check_expanded_entries(spec, broken, sample))


def test_check_detects_duplicate_keys_and_a_sampled_holdout(spec, entries, sample) -> None:
    duplicated = entries + [dict(entries[0], execution_order=len(entries) + 1)]
    assert any("duplicate modeling keys" in p for p in check_expanded_entries(spec, duplicated, sample))
    reserved_key = next(e["modeling_key"] for e in entries if e["split_role"] == SPLIT_HELDOUT_RQ2)
    assert any("RQ2-reserved" in p for p in check_expanded_entries(spec, entries, sample + [reserved_key]))


def test_manifest_document_is_content_addressed(spec, entries, sample) -> None:
    document = expanded_manifest_document(spec, entries, {}, {}, sample)
    assert manifest_id_for(document) == manifest_id_for(copy.deepcopy(document))
    assert document["counts"]["proposed_keys"] == len(entries)
    assert sorted(document["holdout_groups"]) == sorted(spec.holdout_reasons())
    assert all(p["verification_status"] == "NOT_YET_VERIFIED" for p in document["rewrite_pairs"].values())
    changed = copy.deepcopy(document)
    changed["entries"][0]["sql_text"] += " "
    assert manifest_id_for(changed) != manifest_id_for(document)
