"""Append-only execution journal with durable start markers and one writer.

No operation begins before its STARTED entry is written and fsynced. A crash
leaving STARTED without FINISHED makes that item inconclusive on recovery, never
eligible for an automatic replacement execution. Hashes detect inconsistent logs;
they do not provide protection against an adversary rewriting the entire file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path


class JournalBusyError(RuntimeError):
    pass


class AlreadyStartedError(RuntimeError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def inspect_journal(path):
    """Validate a stable journal snapshot without opening a writer or lock file."""
    journal = AppendOnlyJournal(Path(path))
    journal._load()
    return {"entries": journal.entries, "states": journal.states}


class AppendOnlyJournal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = None
        self._fd = None
        self.entries = []
        self.states = {}
        self._failed = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = self.path.with_suffix(self.path.suffix + ".lock").open("a+b")
        self._lock.seek(0, os.SEEK_END)
        if self._lock.tell() == 0:
            self._lock.write(b"0")
            self._lock.flush()
        self._lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock.close()
            self._lock = None
            raise JournalBusyError("Another writer owns the study journal") from exc
        try:
            self._load()
            self._fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            self._failed = False
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        if self._lock is not None:
            self._lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
            self._lock.close()
            self._lock = None

    def _load(self):
        raw = self.path.read_bytes() if self.path.exists() else b""
        if raw and not raw.endswith(b"\n"):
            raise ValueError("Journal has a truncated tail; preserve it and investigate")
        self.entries, self.states = [], {}
        previous = None
        for line in raw.splitlines():
            entry = json.loads(line)
            digest = entry.get("sha256")
            body = {key: value for key, value in entry.items() if key != "sha256"}
            if (entry.get("sequence") != len(self.entries) or entry.get("previous_sha256") != previous
                    or digest != sha256(_canonical(body).encode("utf-8")).hexdigest()):
                raise ValueError("Journal hash chain or sequence is inconsistent")
            self._apply(entry)
            self.entries.append(entry)
            previous = digest

    def _apply(self, entry):
        case_id, event = entry["case_id"], entry["event"]
        if event == "STARTED":
            if case_id in self.states:
                raise ValueError("A journal item was started more than once")
            self.states[case_id] = "STARTED"
        elif event == "FINISHED":
            if self.states.get(case_id) != "STARTED":
                raise ValueError("A journal finish has no matching start")
            if not isinstance(entry["payload"].get("status"), str):
                raise ValueError("Finished items require an explicit status")
            self.states[case_id] = entry["payload"]["status"]
        else:
            raise ValueError("Unsupported journal event")

    def append(self, event: str, case_id: str, payload: dict):
        if self._fd is None:
            raise RuntimeError("Open the journal as a context manager")
        if self._failed:
            raise RuntimeError("Journal storage failed; close and investigate before further execution")
        if not isinstance(case_id, str) or not case_id or not isinstance(payload, dict):
            raise ValueError("A journal entry requires an identifier and object payload")
        body = {"sequence": len(self.entries), "previous_sha256": self.entries[-1]["sha256"] if self.entries else None,
                "event": event, "case_id": case_id, "at": datetime.now(timezone.utc).isoformat(), "payload": payload}
        entry = {**body, "sha256": sha256(_canonical(body).encode("utf-8")).hexdigest()}
        prior = self.states.copy()
        self._apply(entry)
        self.states = prior
        raw = (_canonical(entry) + "\n").encode("utf-8")
        view = memoryview(raw)
        try:
            while view:
                written = os.write(self._fd, view)
                if written <= 0:
                    raise OSError("Journal write did not advance")
                view = view[written:]
            os.fsync(self._fd)
        except BaseException:
            self._failed = True
            raise
        self._apply(entry)
        self.entries.append(entry)
        return entry

    def recover_incomplete(self):
        recovered = []
        for case_id, status in tuple(self.states.items()):
            if status == "STARTED":
                self.append("FINISHED", case_id, {"status": "INCONCLUSIVE", "reason": "Interrupted started item; not rerun"})
                recovered.append(case_id)
        return tuple(recovered)

    def run_once(self, case_id: str, operation, *, identity: dict):
        if case_id in self.states:
            raise AlreadyStartedError("A started item cannot be automatically executed again")
        self.append("STARTED", case_id, {"identity": identity})
        try:
            result = operation()
            if not isinstance(result, dict) or not isinstance(result.get("status"), str):
                raise ValueError("Operation must return a dictionary with an explicit status")
        except BaseException as exc:
            self.append("FINISHED", case_id, {"status": "INCONCLUSIVE", "exception_class": type(exc).__name__})
            raise
        self.append("FINISHED", case_id, {"status": result["status"]})
        return result
