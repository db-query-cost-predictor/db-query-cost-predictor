"""Release count reconciliation, derived determinism and release gates (no database).

Written but not run by Claude Code. Five synthetic keys with arbitrary runtimes:
complete, right-censored, failed (temp_file_limit), incomplete and not attempted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from query_cost_predictor.evidence import EvidenceLedger, make_record
from query_cost_predictor.expanded_derive import build_expanded_tables, expanded_counts, render_expanded_outputs
from query_cost_predictor.expanded_validate import evaluate_gates, project_budget
from query_cost_predictor.smoke_derive import write_outputs

TIME = "2000-01-01T00:00:00+00:00"
PLAN = {"Plan": {"Node Type": "Seq Scan", "Relation Name": "lineitem", "Startup Cost": 0.0, "Total Cost": 100.0,
                 "Plan Rows": 10.0, "Plan Width": 8}}
KEYS = ["mk_complete", "mk_censored", "mk_failed", "mk_incomplete", "mk_not_attempted"]


def entry(key: str, order: int, group: str, component: str = "structural") -> dict:
    return {
        "modeling_key": key, "execution_order": order, "template_id": f"tpl_{group}", "semantic_group_id": group,
        "family": "gen_s1_scalar_agg", "form": "standalone", "rewrite_source": "NOT_A_REWRITE", "rewrite_pair_id": None,
        "workload": "tpch", "snapshot_name": "tpch_sf1", "scale_factor": "1", "configuration_name": "C1_baseline",
        "snapshot_id": "snap_x", "config_id": "cfg_x", "query_instance_id": f"qi_{key}", "sql_sha256": "0" * 64,
        "literal_fingerprint": "1" * 64, "parameters": {"days": 3, "start_date": "1995-01-01"},
        "sql_text": "SELECT count(*) FROM lineitem l WHERE l.l_shipdate >= DATE '1995-01-01'",
        "component": component, "structure_family": "gen_p01_l", "output_shape": "s1_scalar_agg",
        "index_variant": "indexed_predicate", "parameter_regime": "R1_narrow", "split_role": "development",
        "fold_group": 0, "fold_family": 0, "fold_seen_template": 0,
    }


def synthetic_manifest() -> dict:
    return {
        "manifest_id": "mf_synthetic", "manifest_kind": "expanded_release",
        "release": {"release_name": "expanded-test", "frozen_high_runtime_thresholds_ms": [1000.0, 10000.0],
                    "censor_margin_ms": 100.0, "planning_fallback_ms": 1000.0},
        "protocol": {"measured_repetitions": 3, "statement_timeout_ms": 15000,
                     "high_runtime_thresholds_ms": [1000.0, 10000.0]},
        "configurations": {"C1_baseline": {"work_mem": {"setting": "4096", "unit": "kB"}}},
        "snapshots": {"tpch_sf1": {}},
        "sample": {"keys": ["mk_complete", "mk_censored"]},
        "entries": [entry(k, i, f"sg_{i}") for i, k in enumerate(KEYS, start=1)],
    }


def execution(key: str, repeat_no: int, status: str, runtime: float | None = None, planning: float | None = 0.5,
              sqlstate: str | None = None, error_class: str | None = None) -> dict:
    return make_record("execution", {
        "modeling_key": key, "repeat_no": repeat_no, "run_purpose": "probe" if repeat_no == 0 else "measured",
        "run_id": f"{key}-{repeat_no}", "status": status, "is_censored": status == "timeout",
        "censor_lower_bound_ms": 15000.0 if status == "timeout" else None, "timeout_ms": 15000,
        "execution_time_ms": runtime if status == "completed" else None,
        "planning_time_ms": planning if status == "completed" else None,
        "client_elapsed_ms": (runtime or 15000.0) + 1.0, "metrics": {"spill_detected": False},
        "settings_block": {"jit": "off"}, "plan_shape_sha256": "3" * 64, "sqlstate": sqlstate,
        "error_class": error_class, "error_message": "synthetic" if status == "error" else None,
    }, session_id="s1", manifest_id="mf_synthetic", recorded_at=TIME)


def estimate(key: str, planning: float) -> dict:
    return make_record("estimate", {
        "modeling_key": key, "captured_at": TIME, "explain_options": "SETTINGS, SUMMARY ON, FORMAT JSON",
        "plan_json": PLAN, "plan_sha256": "2" * 64, "plan_shape_sha256": "3" * 64, "settings_block": {"jit": "off"},
        "scalars": {"est_total_cost": 100.0, "est_startup_cost": 0.0, "est_plan_rows": 10.0, "est_plan_width": 8,
                    "est_node_count": 1, "est_workers_planned": 0},
        "planning_time_ms": planning,
    }, session_id="s1", manifest_id="mf_synthetic", recorded_at=TIME)


def synthetic_records() -> list[dict]:
    return [
        estimate("mk_complete", 0.4), execution("mk_complete", 0, "completed", 1500.0),
        execution("mk_complete", 1, "completed", 1200.0), execution("mk_complete", 2, "completed", 1300.0),
        execution("mk_complete", 3, "completed", 1250.0),
        estimate("mk_censored", 2.0), execution("mk_censored", 0, "timeout"),
        execution("mk_censored", 1, "timeout"), execution("mk_censored", 2, "timeout"),
        estimate("mk_failed", 0.3), execution("mk_failed", 0, "error", sqlstate="53400",
                                             error_class="ConfigurationLimitExceeded"),
        estimate("mk_incomplete", 0.3), execution("mk_incomplete", 0, "completed", 20.0),
        execution("mk_incomplete", 1, "completed", 19.0),
    ]


@pytest.fixture
def tables() -> dict:
    return build_expanded_tables(synthetic_manifest(), EvidenceLedger.from_records(synthetic_records()))


def test_every_proposed_key_has_exactly_one_label_row(tables) -> None:
    assert sorted(r["modeling_key"] for r in tables["labels"]) == sorted(KEYS)
    assert sorted(r["modeling_key"] for r in tables["splits"]) == sorted(KEYS)


def test_observation_statuses_reconcile_with_the_manifest(tables) -> None:
    status = {r["modeling_key"]: r["observation_status"] for r in tables["labels"]}
    assert status == {"mk_complete": "complete", "mk_censored": "right_censored", "mk_failed": "failed",
                      "mk_incomplete": "incomplete", "mk_not_attempted": "not_attempted"}
    counts = expanded_counts(tables)
    assert counts["proposed_keys"] == 5
    assert sum(counts["observation_status"].values()) == counts["proposed_keys"]
    assert counts["usable_keys"] == 2  # complete + right-censored only
    assert counts["measured_executions"] == 3 + 2 + 0 + 1  # child records, never extra keys
    assert counts["probe_executions"] == 4


def test_censored_key_uses_the_execution_bound_and_is_positive_at_both_thresholds(tables) -> None:
    row = next(r for r in tables["labels"] if r["modeling_key"] == "mk_censored")
    assert row["median_ms"] is None
    assert row["statement_censor_bound_ms"] == 15000.0
    assert row["exec_censor_lower_bound_ms"] == pytest.approx(15000 - 2.0 - 100)
    assert row["censor_lower_bound_ms"] == row["exec_censor_lower_bound_ms"]
    assert (row["hr_1s_class"], row["hr_10s_class"]) == ("positive", "positive")


def test_failures_are_classified_and_never_positive(tables) -> None:
    row = next(r for r in tables["labels"] if r["modeling_key"] == "mk_failed")
    assert row["failure_class"] == "resource_limit" and row["first_error_sqlstate"] == "53400"
    assert (row["hr_1s_class"], row["hr_10s_class"]) == ("unlabeled", "unlabeled")
    complete = next(r for r in tables["labels"] if r["modeling_key"] == "mk_complete")
    assert complete["median_ms"] == 1250.0  # the 1500 ms probe is excluded
    assert (complete["hr_1s_class"], complete["hr_10s_class"]) == ("positive", "negative")


def test_derived_outputs_rebuild_byte_identically(tmp_path: Path, tables) -> None:
    inputs = {"manifest": {"manifest_id": "mf_synthetic", "sha256": "4" * 64}, "raw_files": []}
    first = render_expanded_outputs(tables, inputs, synthetic_manifest())
    again = render_expanded_outputs(
        build_expanded_tables(synthetic_manifest(), EvidenceLedger.from_records(synthetic_records())),
        inputs, synthetic_manifest())
    assert first == again
    target = tmp_path / "derived" / "expanded" / "abc"
    assert write_outputs(first, target) == "written"
    assert write_outputs(again, target) == "identical_rebuild_verified"


GATES = {"min_usable_keys": 2, "min_development_usable_keys": 2, "min_groups_usable_on_all_snapshots": 2,
         "max_template_share": 0.6, "max_structure_family_share": 1.0,
         "positives": {"min_keys": 1, "min_groups": 1, "min_components": 1}}


def test_gates_stop_while_collection_is_unfinished(tables) -> None:
    results = {g["id"]: g["status"] for g in evaluate_gates(tables["labels"], GATES, ["tpch_sf1"])}
    assert results["G-01"] == "STOP"  # incomplete and not-attempted keys remain
    assert results["G-02"] == "PASS" and results["G-04"] == "PASS"


def test_gates_report_missing_positives_instead_of_relabelling(tables) -> None:
    strict = dict(GATES, positives={"min_keys": 10, "min_groups": 5, "min_components": 2})
    results = {g["id"]: g for g in evaluate_gates(tables["labels"], strict, ["tpch_sf1"])}
    assert results["G-07"]["status"] == "STOP" and results["G-08"]["status"] == "STOP"
    assert "NOT_YET_EVALUABLE" in results["G-08"]["detail"]["consequence"]
    short = dict(GATES, min_usable_keys=3)
    assert {g["id"]: g["status"] for g in evaluate_gates(tables["labels"], short, ["tpch_sf1"])}["G-02"] == "STOP"


def test_budget_projection_uses_measured_keys_and_states_the_worst_case() -> None:
    manifest = synthetic_manifest()
    ledger = EvidenceLedger.from_records(synthetic_records())
    tables = build_expanded_tables(manifest, ledger)
    states = {r["modeling_key"]: r["key_state"] for r in tables["keys"]}
    projection = project_budget(manifest, synthetic_records(), states, raw_bytes=30_000)
    assert projection["measured_keys"] == 3          # complete, censored, failed are determined
    assert projection["remaining_keys"] == 2
    assert projection["worst_case_hours_remaining"] == pytest.approx(2 * 4 * 15 / 3600, abs=0.05)
    assert projection["expected_hours_remaining"] <= projection["upper_hours_remaining"]
