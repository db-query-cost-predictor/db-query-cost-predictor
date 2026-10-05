"""Duplicate-run rejection and immutable, checksummed raw evidence (tmp_path only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from query_cost_predictor.errors import DuplicateRunError, EvidenceIntegrityError
from query_cost_predictor.evidence import EvidenceLedger, RawEvidenceWriter, make_record, read_raw_file, verify_record

FIXED_TIME = "2000-01-01T00:00:00+00:00"  # synthetic timestamp


def execution(modeling_key: str, repeat_no: int, runtime_ms: float, session: str = "session_a") -> dict:
    payload = {"modeling_key": modeling_key, "repeat_no": repeat_no, "run_purpose": "measured" if repeat_no else "probe",
               "status": "completed", "execution_time_ms": runtime_ms}
    return make_record("execution", payload, session_id=session, manifest_id="mf_synthetic", recorded_at=FIXED_TIME)


def test_second_terminal_run_for_the_same_repeat_is_rejected() -> None:
    ledger = EvidenceLedger()
    ledger.add_execution(execution("mk_a", 1, 10.0))
    with pytest.raises(DuplicateRunError):
        ledger.add_execution(execution("mk_a", 1, 11.0))


def test_identical_record_is_idempotent() -> None:
    ledger = EvidenceLedger()
    record = execution("mk_a", 1, 10.0)
    assert ledger.add_execution(record) is True
    assert ledger.add_execution(record) is False
    assert ledger.has_run("mk_a", 1) and not ledger.has_run("mk_a", 2)


def test_duplicates_across_sessions_are_rejected() -> None:
    with pytest.raises(DuplicateRunError):
        EvidenceLedger.from_records([execution("mk_a", 2, 10.0, "s1"), execution("mk_a", 2, 12.0, "s2")])


def test_writer_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "session_synthetic.jsonl"
    writer = RawEvidenceWriter(path)
    writer.write(execution("mk_a", 1, 10.0))
    info = writer.close()
    assert info["record_count"] == 1
    with pytest.raises(FileExistsError):
        RawEvidenceWriter(path)


def test_raw_records_round_trip_and_edits_are_detected(tmp_path: Path) -> None:
    path = tmp_path / "session_synthetic.jsonl"
    with RawEvidenceWriter(path) as writer:
        writer.write(execution("mk_a", 1, 123.456))
        writer.write(execution("mk_a", 2, 124.5))
    records = read_raw_file(path)
    assert len(records) == 2 and all(verify_record(r) for r in records)
    path.write_text(path.read_text(encoding="utf-8").replace("123.456", "654.321"), encoding="utf-8")
    with pytest.raises(EvidenceIntegrityError):
        read_raw_file(path)


def test_writer_refuses_a_record_modified_after_hashing(tmp_path: Path) -> None:
    record = execution("mk_a", 1, 10.0)
    record["payload"]["execution_time_ms"] = 5.0
    with RawEvidenceWriter(tmp_path / "session_synthetic.jsonl") as writer:
        with pytest.raises(EvidenceIntegrityError):
            writer.write(record)
