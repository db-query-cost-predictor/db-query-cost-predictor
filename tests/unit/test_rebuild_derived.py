"""Derived labels/features are deterministic, versioned and rebuildable from raw evidence.

All inputs are SYNTHETIC (one fake modeling key with arbitrary runtimes).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from query_cost_predictor.errors import EvidenceIntegrityError
from query_cost_predictor.evidence import EvidenceLedger, RawEvidenceWriter, make_record, read_raw_file
from query_cost_predictor.hashing import sha256_file
from query_cost_predictor.smoke_derive import build_tables, render_outputs, write_outputs

TIME = "2000-01-01T00:00:00+00:00"
KEY = "mk_synthetic"
PLAN = {"Plan": {"Node Type": "Seq Scan", "Relation Name": "orders", "Startup Cost": 0.0, "Total Cost": 100.0,
                 "Plan Rows": 10.0, "Plan Width": 8}}


def synthetic_manifest() -> dict:
    return {
        "protocol": {"measured_repetitions": 3, "statement_timeout_ms": 15000},
        "configurations": {"C1_baseline": {"work_mem": {"setting": "4096", "unit": "kB"}}},
        "entries": [{
            "modeling_key": KEY, "execution_order": 1, "template_id": "tpl_x", "semantic_group_id": "sg_x",
            "family": "fam_x", "form": "standalone", "rewrite_source": "NOT_A_REWRITE", "rewrite_pair_id": None,
            "workload": "tpch", "snapshot_name": "tpch_sf0_1", "scale_factor": "0.1", "configuration_name": "C1_baseline",
            "snapshot_id": "snap_x", "config_id": "cfg_x", "query_instance_id": "qi_x", "sql_sha256": "0" * 64,
            "literal_fingerprint": "1" * 64, "parameters": {"k": 1}, "sql_text": "SELECT o_orderkey FROM orders WHERE o_orderkey = 1",
        }],
    }


def synthetic_records() -> list[dict]:
    records = [make_record("estimate", {
        "modeling_key": KEY, "captured_at": TIME, "explain_options": "SETTINGS, FORMAT JSON", "plan_json": PLAN,
        "plan_sha256": "2" * 64, "plan_shape_sha256": "3" * 64, "settings_block": {"jit": "off"},
        "scalars": {"est_total_cost": 100.0, "est_startup_cost": 0.0, "est_plan_rows": 10.0, "est_plan_width": 8,
                    "est_node_count": 1, "est_workers_planned": 0},
    }, session_id="s1", manifest_id="mf_x", recorded_at=TIME)]
    for repeat_no, runtime in ((0, 9.0), (1, 3.0), (2, 1.0), (3, 2.0)):
        records.append(make_record("execution", {
            "modeling_key": KEY, "repeat_no": repeat_no, "run_purpose": "probe" if repeat_no == 0 else "measured",
            "run_id": f"run{repeat_no}", "status": "completed", "is_censored": False, "censor_lower_bound_ms": None,
            "timeout_ms": 15000, "execution_time_ms": runtime, "planning_time_ms": 0.1, "client_elapsed_ms": runtime + 1,
            "metrics": {"spill_detected": False, "workers_planned": 0, "workers_launched": 0},
            "settings_block": {"jit": "off"}, "plan_shape_sha256": "3" * 64,
        }, session_id="s1", manifest_id="mf_x", recorded_at=TIME))
    return records


def test_labels_use_measured_runs_only() -> None:
    tables = build_tables(synthetic_manifest(), EvidenceLedger.from_records(synthetic_records()))
    (label,) = tables["labels"]
    assert label["status"] == "complete"
    assert label["median_ms"] == 2.0  # the 9.0 ms probe is excluded
    assert label["n_successful"] == 3


def test_rebuild_is_byte_identical_and_versioned(tmp_path: Path) -> None:
    inputs = {"manifest": {"manifest_id": "mf_x", "sha256": "4" * 64}, "raw_files": []}
    first = render_outputs(build_tables(synthetic_manifest(), EvidenceLedger.from_records(synthetic_records())),
                           inputs, "label-v", "feature-v")
    second = render_outputs(build_tables(synthetic_manifest(), EvidenceLedger.from_records(synthetic_records())),
                            inputs, "label-v", "feature-v")
    assert first == second
    target = tmp_path / "derived" / "label-v" / "abc"
    assert write_outputs(first, target) == "written"
    assert write_outputs(second, target) == "identical_rebuild_verified"


def test_a_different_rebuild_is_refused(tmp_path: Path) -> None:
    inputs = {"manifest": {"manifest_id": "mf_x", "sha256": "4" * 64}, "raw_files": []}
    outputs = render_outputs(build_tables(synthetic_manifest(), EvidenceLedger.from_records(synthetic_records())),
                             inputs, "label-v", "feature-v")
    target = tmp_path / "derived" / "label-v" / "abc"
    write_outputs(outputs, target)
    (target / "labels.csv").write_bytes(b"edited\n")
    with pytest.raises(EvidenceIntegrityError):
        write_outputs(outputs, target)


def test_deriving_does_not_modify_raw_evidence(tmp_path: Path) -> None:
    raw = tmp_path / "session_synthetic.jsonl"
    with RawEvidenceWriter(raw) as writer:
        for record in synthetic_records():
            writer.write(record)
    before = sha256_file(raw)
    build_tables(synthetic_manifest(), EvidenceLedger.from_records(read_raw_file(raw)))
    assert sha256_file(raw) == before
