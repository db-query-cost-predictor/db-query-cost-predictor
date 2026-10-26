"""Expanded release spec: arithmetic, identities, distinct structures and safety (no database).

Written but not run by Claude Code. Planning entries use placeholder snapshot and
configuration ids; nothing here is a measurement.
"""

from __future__ import annotations

import copy
import re
from collections import Counter, defaultdict
from pathlib import Path

import pytest
import yaml

from query_cost_predictor.config import ConfigError, render_sql
from query_cost_predictor.release_spec import (
    INDEXED,
    UNINDEXED,
    default_release_spec_path,
    generate_parameter_sets,
    load_release_spec,
    make_domain,
    parse_release_spec,
    planning_entries,
    release_arithmetic,
)
from query_cost_predictor.safety import validate_read_only_sql

# The documented design (docs/expanded_release_spec.md). A change to the spec must
# update the documentation and these numbers together.
EXPECTED_TOTALS = {
    "proposed_keys": 4944,
    "development_keys": 4784,
    "heldout_rq2_keys": 160,
    "semantic_groups": 199,
    "development_groups": 194,
    "heldout_rq2_groups": 5,
    "templates": 352,
    "query_instances": 1332,
    "structure_families": 87,
}
EXPECTED_BY_COMPONENT = {"tpch_derived": 688, "structural": 3072, "probe": 384, "rewrite": 800}


@pytest.fixture(scope="module")
def spec():
    return load_release_spec()


@pytest.fixture(scope="module")
def entries(spec):
    return planning_entries(spec)


def _document() -> dict:
    return yaml.safe_load(default_release_spec_path().read_text(encoding="utf-8"))


def _parse(document: dict):
    return parse_release_spec(document, Path("expanded_release_v1.yaml"), "0" * 64)


def test_arithmetic_meets_the_target_and_matches_the_documented_design(spec, entries) -> None:
    arithmetic = release_arithmetic(spec, entries)
    totals = arithmetic["totals"]
    for name, value in EXPECTED_TOTALS.items():
        assert totals[name] == value, name
    assert arithmetic["meets_target_min_proposed_keys"] and totals["proposed_keys"] >= 4704
    assert totals["semantic_groups"] >= 150
    assert {row["component"]: row["keys"] for row in arithmetic["by_component"]} == EXPECTED_BY_COMPONENT
    assert totals["measured_runs_planned"] == 3 * totals["proposed_keys"]
    assert arithmetic["evidence_status"] == "PLANNED"


def test_worst_case_bound_reproduces_the_stated_reference(spec, entries) -> None:
    hours = release_arithmetic(spec, entries)["worst_case_hours"]
    assert hours["reference_4704_keys_measured_only"] == pytest.approx(58.8)
    assert hours["measured_runs_only_at_timeout"] == pytest.approx(4944 * 3 * 15 / 3600, abs=0.05)


def test_repetitions_never_create_keys(entries) -> None:
    cells = Counter((e["query_instance_id"], e["snapshot_name"], e["configuration_name"]) for e in entries)
    assert set(cells.values()) == {1}
    assert len({e["modeling_key"] for e in entries}) == len(entries)


def test_no_template_or_structure_family_dominates(spec, entries) -> None:
    totals = release_arithmetic(spec, entries)["totals"]
    assert totals["max_template_share"] <= spec.gates.max_template_share
    assert totals["max_structure_family_share"] <= spec.gates.max_structure_family_share


def test_no_sql_structure_is_shared_by_two_semantic_groups(entries) -> None:
    groups_per_fingerprint: dict[str, set[str]] = defaultdict(set)
    for entry in entries:
        groups_per_fingerprint[entry["literal_fingerprint"]].add(entry["semantic_group_id"])
    shared = {fp: sorted(g) for fp, g in groups_per_fingerprint.items() if len(g) > 1}
    assert shared == {}


def test_every_instance_is_a_bounded_single_read_only_select(spec) -> None:
    for rt in spec.release_templates:
        for params in rt.spec.parameter_sets:
            sql = render_sql(rt.spec.sql, rt.spec.parameter_types, params)
            validate_read_only_sql(sql)
            lowered = sql.lower()
            assert "pg_sleep" not in lowered and "generate_series" not in lowered
            assert "cross join" not in lowered


def test_generated_joins_are_all_equi_joins_with_conditions(spec) -> None:
    for rt in spec.release_templates:
        if rt.component != "structural":
            continue
        upper = " ".join(rt.spec.sql.upper().split())
        assert upper.count(" JOIN ") == len(re.findall(r" JOIN \w+ \w+ ON ", upper)), rt.spec.template_id


def test_index_variants_share_group_parameters_and_regimes(spec) -> None:
    by_group: dict[str, dict[str, object]] = defaultdict(dict)
    for rt in spec.release_templates:
        if rt.component == "structural":
            by_group[rt.spec.semantic_group_id][rt.index_variant] = rt
    assert len(by_group) == 128
    for group, variants in by_group.items():
        assert set(variants) == {INDEXED, UNINDEXED}, group
        indexed, unindexed = variants[INDEXED], variants[UNINDEXED]
        assert indexed.spec.parameter_sets == unindexed.spec.parameter_sets
        assert indexed.regimes == unindexed.regimes == ("R1_narrow", "R2_medium", "R3_wide")
        assert indexed.spec.sql.replace("l_shipdate", "l_receiptdate") == unindexed.spec.sql


def test_rewrite_forms_share_one_group_and_holdouts_are_rewrite_groups(spec) -> None:
    forms: dict[str, set[str]] = defaultdict(set)
    groups: dict[str, set[str]] = defaultdict(set)
    for rt in spec.release_templates:
        if rt.component == "rewrite":
            forms[rt.spec.rewrite_pair_id].add(rt.spec.form)
            groups[rt.spec.rewrite_pair_id].add(rt.spec.semantic_group_id)
            assert rt.spec.rewrite_source in ("NOT_A_REWRITE", "MANUAL_REFERENCE_REWRITE")
            assert rt.equivalence_argument
    assert len(forms) == 25
    assert all(f == {"original", "reference_rewrite"} for f in forms.values())
    assert all(len(g) == 1 for g in groups.values())
    reserved = spec.holdout_reasons()
    assert len(reserved) == 5
    components = {rt.spec.semantic_group_id: rt.component for rt in spec.release_templates}
    assert {components[g] for g in reserved} == {"rewrite"}


def test_parameter_generation_is_deterministic_distinct_and_respects_constraints() -> None:
    raw = {"kind": "sample", "count": 8, "seed": 7, "distinct": [["a", "b"]],
           "domains": {"a": {"choice": ["X", "Y", "Z"]}, "b": {"choice": ["X", "Y", "Z"]}}}
    types = {"a": "token", "b": "token"}
    with pytest.raises(ConfigError):  # only 6 ordered distinct pairs exist
        generate_parameter_sets(raw, types, "scope", "test")
    raw["count"] = 6
    first, labels = generate_parameter_sets(raw, types, "scope", "test")
    again, _ = generate_parameter_sets(raw, types, "scope", "test")
    assert first == again and labels == ["spec_domain_sample"] * 6
    assert all(p["a"] != p["b"] for p in first)
    assert len({(p["a"], p["b"]) for p in first}) == 6


def test_domains_render_valid_values() -> None:
    months = make_domain({"month_start": ["1997-11", "1998-02"]}, "t")
    assert [months.value(i) for i in range(months.size)] == ["1997-11-01", "1997-12-01", "1998-01-01", "1998-02-01"]
    decimals = make_domain({"decimal_range": ["0.02", "0.04", "0.01"]}, "t")
    assert [decimals.value(i) for i in range(decimals.size)] == ["0.02", "0.03", "0.04"]
    with pytest.raises(ConfigError):
        make_domain({"bogus": [1, 2]}, "t")


def test_thresholds_are_frozen_at_one_and_ten_seconds(spec) -> None:
    assert spec.labels.thresholds_ms == (1000.0, 10000.0)
    document = _document()
    document["labels"]["frozen_high_runtime_thresholds_ms"] = [500, 10000]
    document["protocol"]["high_runtime_thresholds_ms"] = [500, 10000]
    with pytest.raises(ConfigError):
        _parse(document)


def test_timeout_must_leave_room_for_the_highest_threshold() -> None:
    document = _document()
    document["protocol"]["statement_timeout_ms"] = 10500
    with pytest.raises(ConfigError):
        _parse(document)


def test_plain_explain_must_report_planning_time_and_never_analyze() -> None:
    document = _document()
    document["protocol"]["explain_estimate_options"] = "SETTINGS, FORMAT JSON"
    with pytest.raises(ConfigError):
        _parse(document)
    document["protocol"]["explain_estimate_options"] = "ANALYZE, SETTINGS, SUMMARY ON, FORMAT JSON"
    with pytest.raises(ConfigError):
        _parse(document)


def test_llm_rewrites_and_optional_snapshots_are_rejected() -> None:
    document = copy.deepcopy(_document())
    document["components"]["rewrite"]["pairs"][0]["rewrite_source"] = "LLM_REWRITE"
    with pytest.raises(ConfigError):
        _parse(document)
    document = copy.deepcopy(_document())
    document["snapshots"]["tpch_sf1"]["required"] = False
    with pytest.raises(ConfigError):
        _parse(document)


def test_token_parameters_cannot_break_out_of_literals() -> None:
    assert render_sql("SELECT '{t}'", {"t": "token"}, {"t": "Brand#45"}) == "SELECT 'Brand#45'"
    assert render_sql("SELECT '{t}'", {"t": "token"}, {"t": "MED BOX"}) == "SELECT 'MED BOX'"
    for bad in ("A'B", "a%b", "a_b", " lead", "trail ", "two  spaces", "", "x;y"):
        with pytest.raises(ConfigError):
            render_sql("SELECT '{t}'", {"t": "token"}, {"t": bad})
