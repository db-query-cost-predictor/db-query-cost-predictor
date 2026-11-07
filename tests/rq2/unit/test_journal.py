"""No database/model calls: durable ordering, crash recovery and log corruption."""

import json

import pytest

from rq2_pipeline.journal import AlreadyStartedError, AppendOnlyJournal, JournalBusyError


def test_start_is_persisted_before_execution_and_earlier_entries_are_not_rewritten(tmp_path):
    path = tmp_path / "journal.jsonl"
    with AppendOnlyJournal(path) as journal:
        def operation():
            records = [json.loads(line) for line in path.read_bytes().splitlines()]
            assert records[-1]["event"] == "STARTED"
            assert records[-1]["case_id"] == "Q06/small/default"
            return {"status": "COMPLETED"}
        journal.run_once("Q06/small/default", operation, identity={"protocol": "synthetic"})
        initial = path.read_bytes()
        journal.run_once("Q06/large/default", lambda: {"status": "INCONCLUSIVE"}, identity={})
        assert path.read_bytes().startswith(initial)


def test_recovery_marks_started_items_inconclusive_and_never_reexecutes(tmp_path):
    path = tmp_path / "journal.jsonl"
    with AppendOnlyJournal(path) as journal:
        journal.append("STARTED", "case", {"identity": {}})
    with AppendOnlyJournal(path) as journal:
        assert journal.recover_incomplete() == ("case",)
        assert journal.states["case"] == "INCONCLUSIVE"
        with pytest.raises(AlreadyStartedError):
            journal.run_once("case", lambda: pytest.fail("Do not rerun"), identity={})
        assert journal.recover_incomplete() == ()


def test_failed_start_fsync_prevents_the_operation(tmp_path, monkeypatch):
    from rq2_pipeline import journal as module
    path = tmp_path / "journal.jsonl"
    with AppendOnlyJournal(path) as journal:
        def fail(*args):
            raise OSError("Synthetic storage failure")
        monkeypatch.setattr(module.os, "fsync", fail)
        with pytest.raises(OSError):
            journal.run_once("case", lambda: pytest.fail("Operation must not start"), identity={})
        with pytest.raises(RuntimeError, match="storage failed"):
            journal.run_once("another_case", lambda: pytest.fail("No later execution"), identity={})


def test_second_writer_is_refused(tmp_path):
    path = tmp_path / "journal.jsonl"
    with AppendOnlyJournal(path):
        with pytest.raises(JournalBusyError):
            with AppendOnlyJournal(path):
                pass


@pytest.mark.parametrize("corruption", ["truncated", "changed"])
def test_corrupt_journal_is_preserved_and_not_recovered_silently(tmp_path, corruption):
    path = tmp_path / "journal.jsonl"
    with AppendOnlyJournal(path) as journal:
        journal.append("STARTED", "case", {})
    raw = path.read_bytes()
    damaged = raw[:-2] if corruption == "truncated" else raw.replace(b'"case_id":"case"', b'"case_id":"fake"')
    path.write_bytes(damaged)
    with pytest.raises(ValueError):
        with AppendOnlyJournal(path):
            pass
    assert path.read_bytes() == damaged


def test_interrupted_operation_is_terminal_and_keeps_the_start_record(tmp_path):
    path = tmp_path / "journal.jsonl"
    with AppendOnlyJournal(path) as journal:
        def interrupted():
            raise KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            journal.run_once("case", interrupted, identity={})
        assert journal.states["case"] == "INCONCLUSIVE"
    assert len(path.read_bytes().splitlines()) == 2
