"""Append-only raw evidence (JSONL) and the duplicate-run ledger.

Rules:

* Each collection session writes a **new** JSONL file opened in exclusive
  create mode (``"x"``); an existing file is never reopened or overwritten.
* Every record carries ``record_sha256`` = SHA-256 of its canonical JSON body;
  readers verify it, so any edit to a raw file is detected.
* Records are flushed and fsynced before the evidence database is written
  ("evidence first"), so a crash can lose at most the record being written.
* The :class:`EvidenceLedger` accepts one terminal execution per
  ``modeling_key + repeat_no``; an identical re-offer is idempotent, a
  different one raises :class:`DuplicateRunError`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from query_cost_predictor.errors import DuplicateRunError, EvidenceIntegrityError
from query_cost_predictor.hashing import canonical_json, sha256_file, sha256_json
from query_cost_predictor.paths import ensure_dir

RAW_SCHEMA_VERSION = 1
RECORD_TYPES = frozenset(
    {
        "session_start", "estimate", "execution", "resume_warmup", "error", "heartbeat",
        "interruption", "session_end", "rewrite_verification",
    }
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def make_record(record_type: str, payload: dict[str, Any], *, session_id: str, manifest_id: str | None,
                recorded_at: str | None = None) -> dict[str, Any]:
    if record_type not in RECORD_TYPES:
        raise ValueError(f"unknown raw record type {record_type!r}")
    body = {
        "schema_version": RAW_SCHEMA_VERSION,
        "record_type": record_type,
        "session_id": session_id,
        "manifest_id": manifest_id,
        "recorded_at": recorded_at or utc_now(),
        "payload": payload,
    }
    body["record_sha256"] = sha256_json(body)
    return body


def verify_record(record: dict[str, Any]) -> bool:
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    return record.get("record_sha256") == sha256_json(body)


class RawEvidenceWriter:
    """Exclusive-create JSONL writer for one session."""

    def __init__(self, path: Path) -> None:
        ensure_dir(path.parent)
        self.path = path
        self._handle = path.open("x", encoding="utf-8", newline="\n")  # FileExistsError if present
        self.record_count = 0
        self._closed = False

    def write(self, record: dict[str, Any]) -> str:
        if self._closed:
            raise EvidenceIntegrityError("raw evidence writer is closed")
        if not verify_record(record):
            raise EvidenceIntegrityError("refusing to write a record whose checksum does not match its body")
        self._handle.write(canonical_json(record) + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self.record_count += 1
        return str(record["record_sha256"])

    def close(self) -> dict[str, Any]:
        if not self._closed:
            self._handle.close()
            self._closed = True
        return {"path": str(self.path), "sha256": sha256_file(self.path),
                "bytes": self.path.stat().st_size, "record_count": self.record_count}

    def __enter__(self) -> "RawEvidenceWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_raw_file(path: Path) -> list[dict[str, Any]]:
    """Read and checksum-verify every record of a raw JSONL file."""
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvidenceIntegrityError(f"{path.name} line {line_no}: invalid JSON ({exc})") from exc
            if not verify_record(record):
                raise EvidenceIntegrityError(f"{path.name} line {line_no}: record checksum mismatch (edited raw evidence?)")
            records.append(record)
    return records


def raw_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("session_*.jsonl")) if directory.is_dir() else []


class EvidenceLedger:
    """Terminal executions per (modeling_key, repeat_no) and estimates per key."""

    def __init__(self) -> None:
        self.executions: dict[tuple[str, int], dict[str, Any]] = {}
        self.estimates: dict[str, dict[str, Any]] = {}

    def add_execution(self, record: dict[str, Any]) -> bool:
        """Register a terminal execution record. Returns True if it was new."""
        payload = record["payload"]
        key = (str(payload["modeling_key"]), int(payload["repeat_no"]))
        existing = self.executions.get(key)
        if existing is None:
            self.executions[key] = record
            return True
        if existing["record_sha256"] == record["record_sha256"]:
            return False
        raise DuplicateRunError(f"second terminal execution for modeling_key={key[0]} repeat_no={key[1]}")

    def add_estimate(self, record: dict[str, Any]) -> bool:
        key = str(record["payload"]["modeling_key"])
        existing = self.estimates.get(key)
        if existing is None:
            self.estimates[key] = record
            return True
        if existing["record_sha256"] == record["record_sha256"]:
            return False
        raise DuplicateRunError(f"second estimate for modeling_key={key}")

    def has_run(self, modeling_key: str, repeat_no: int) -> bool:
        return (modeling_key, repeat_no) in self.executions

    def runs_for(self, modeling_key: str) -> list[dict[str, Any]]:
        runs = [r["payload"] for (mk, _), r in self.executions.items() if mk == modeling_key]
        return sorted(runs, key=lambda p: int(p["repeat_no"]))

    def measured_runs(self, modeling_key: str) -> list[dict[str, Any]]:
        return [p for p in self.runs_for(modeling_key) if int(p["repeat_no"]) >= 1]

    def probe(self, modeling_key: str) -> dict[str, Any] | None:
        record = self.executions.get((modeling_key, 0))
        return None if record is None else record["payload"]

    @classmethod
    def from_records(cls, records: Iterable[dict[str, Any]]) -> "EvidenceLedger":
        ledger = cls()
        for record in records:
            if record.get("record_type") == "execution":
                ledger.add_execution(record)
            elif record.get("record_type") == "estimate":
                ledger.add_estimate(record)
        return ledger
