"""Resumability, idempotency and batch bounds of the shared collector (no database).

Written but not run by Claude Code. Records are synthetic and live in memory or tmp_path.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from query_cost_predictor.errors import DuplicateRunError
from query_cost_predictor.evidence import EvidenceLedger, RawEvidenceWriter, make_record, raw_files, read_raw_file
from query_cost_predictor.smoke_collector import (
    EXPANDED_PROFILE,
    Collector,
    CollectorError,
    key_state,
    pending_entries,
)

TIME = "2000-01-01T00:00:00+00:00"


def run(key: str, repeat_no: int, status: str, runtime: float | None = None, session: str = "s1") -> dict:
    payload = {"modeling_key": key, "repeat_no": repeat_no, "run_purpose": "probe" if repeat_no == 0 else "measured",
               "status": status, "execution_time_ms": runtime if status == "completed" else None,
               "client_elapsed_ms": (runtime or 1.0) + 0.5}
    return make_record("execution", payload, session_id=session, manifest_id="mf_synthetic", recorded_at=TIME)


def ledger_with_states() -> EvidenceLedger:
    records = [
        run("mk_1", 0, "completed", 5.0), run("mk_1", 1, "completed", 4.0), run("mk_1", 2, "completed", 4.2),
        run("mk_1", 3, "completed", 4.1),                                      # complete
        run("mk_2", 0, "timeout"), run("mk_2", 1, "timeout"), run("mk_2", 2, "timeout"),  # right-censored
        run("mk_3", 0, "error"),                                               # failed at the probe
        run("mk_4", 0, "completed", 9.0), run("mk_4", 1, "completed", 8.0),    # incomplete (interrupted)
    ]
    return EvidenceLedger.from_records(records)


def entries(n: int = 6) -> list[dict]:
    return [{"modeling_key": f"mk_{i}", "execution_order": i, "database": "tpch_sf0_1"} for i in range(1, n + 1)]


def synthetic_manifest() -> dict:
    return {"manifest_id": "mf_synthetic", "protocol": {"measured_repetitions": 3, "statement_timeout_ms": 15000,
                                                        "lock_timeout_ms": 5000, "watchdog_grace_ms": 10000},
            "entries": entries()}


def test_key_states_from_raw_evidence() -> None:
    ledger = ledger_with_states()
    assert [key_state(ledger, f"mk_{i}", 3) for i in range(1, 7)] == [
        "complete", "right_censored", "failed", "incomplete", "not_started", "not_started"]


def test_resume_processes_only_undetermined_keys_in_manifest_order() -> None:
    ledger = ledger_with_states()
    assert [e["modeling_key"] for e in pending_entries(entries(), ledger, 3)] == ["mk_4", "mk_5", "mk_6"]
    assert [e["modeling_key"] for e in pending_entries(list(reversed(entries())), ledger, 3)] == ["mk_4", "mk_5", "mk_6"]


def test_batch_bound_limits_a_session() -> None:
    ledger = ledger_with_states()
    assert [e["modeling_key"] for e in pending_entries(entries(), ledger, 3, max_keys=2)] == ["mk_4", "mk_5"]
    assert pending_entries(entries(), ledger, 3, max_keys=0) == []
    with pytest.raises(ValueError):
        pending_entries(entries(), ledger, 3, max_keys=-1)


def test_a_conflicting_rerun_of_a_completed_repeat_is_rejected() -> None:
    ledger = ledger_with_states()
    with pytest.raises(DuplicateRunError):
        ledger.add_execution(run("mk_1", 2, "completed", 99.0, session="s2"))
    assert ledger.add_execution(ledger.executions[("mk_1", 2)]) is False  # identical re-offer is idempotent


def test_per_key_index_matches_a_full_scan() -> None:
    ledger = ledger_with_states()
    for key in ("mk_1", "mk_2", "mk_3", "mk_4", "mk_5"):
        scanned = sorted((r["payload"] for (k, _), r in ledger.executions.items() if k == key),
                         key=lambda p: int(p["repeat_no"]))
        assert ledger.runs_for(key) == scanned


def test_reconciling_session_files_reproduces_the_same_states(tmp_path: Path) -> None:
    first = [run("mk_1", 0, "completed", 5.0), run("mk_1", 1, "completed", 4.0)]
    second = [run("mk_1", 2, "completed", 4.2, session="s2"), run("mk_1", 3, "completed", 4.1, session="s2")]
    for name, records in (("session_a.jsonl", first), ("session_b.jsonl", second)):
        with RawEvidenceWriter(tmp_path / name) as writer:
            for record in records:
                writer.write(record)
    loaded = [r for p in raw_files(tmp_path) for r in read_raw_file(p)]
    ledger = EvidenceLedger.from_records(loaded)
    assert key_state(ledger, "mk_1", 3) == "complete"
    again = EvidenceLedger.from_records(loaded + loaded)  # re-reading the same files adds nothing
    assert again.executions.keys() == ledger.executions.keys()


def test_collector_restricts_itself_to_the_selected_sample() -> None:
    manifest = synthetic_manifest()
    collector = Collector(manifest, Path("manifest.json"), None, profile=EXPANDED_PROFILE, selected_keys=["mk_2", "mk_5"])
    assert [e["modeling_key"] for e in collector.entries] == ["mk_2", "mk_5"]
    assert len(collector.all_entries) == 6
    with pytest.raises(CollectorError):
        Collector(manifest, Path("manifest.json"), None, selected_keys=["mk_unknown"])


def test_stop_reasons_fire_between_keys() -> None:
    manifest = synthetic_manifest()
    batch = Collector(manifest, Path("manifest.json"), 2, profile=EXPANDED_PROFILE)
    assert batch._stop_reason(time.monotonic(), 1) is None
    assert "batch limit" in batch._stop_reason(time.monotonic(), 2)
    timed = Collector(manifest, Path("manifest.json"), None, profile=EXPANDED_PROFILE, max_minutes=0.0)
    assert "wall-clock" in timed._stop_reason(time.monotonic(), 0)
