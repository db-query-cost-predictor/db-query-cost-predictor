"""Canonical hashes and exact SQL text normalization."""
from __future__ import annotations
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"Cannot canonicalize value of type {type(value).__name__}")


def canonical_json(obj: Any) -> str:
    """Serialize ``obj`` deterministically (sorted keys, no whitespace, no NaN)."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_json_default,
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_json(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Stream a file through SHA-256 (read-only)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_sql_text(sql: str) -> str:
    """Normalize line endings and outer whitespace; drop one trailing semicolon.

    The SQL body is otherwise unchanged (no case folding, no comment removal),
    so the stored text is exactly what is executed.
    """
    text = sql.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    if text.endswith(";"):
        text = text[:-1].rstrip()
    return text


def snapshot_id(payload: dict[str, Any]) -> str:
    return "snap_" + sha256_json(payload)
