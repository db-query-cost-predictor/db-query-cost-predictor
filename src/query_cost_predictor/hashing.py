"""Deterministic hashing and identity helpers.

All identities are SHA-256 digests of *canonical JSON* (sorted keys, compact
separators, no NaN), never of ambiguous string concatenations.

Identity definitions:

* ``query_instance_id`` = template + explicit parameter values + normalized SQL.
* ``modeling_key`` = query instance + benchmark snapshot + database
  configuration + cache protocol + protocol version. Repeated executions are
  child records of a modeling key; they never create new keys.
"""

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


def query_instance_id(template_id: str, parameters: dict[str, Any], sql_text: str) -> str:
    payload = {
        "template_id": template_id,
        "parameters": parameters,
        "sql_text": normalize_sql_text(sql_text),
    }
    return "qi_" + sha256_json(payload)


def config_id(configuration_name: str, effective_settings_sha256: str, server_version_num: int) -> str:
    payload = {
        "configuration_name": configuration_name,
        "effective_settings_sha256": effective_settings_sha256,
        "server_version_num": int(server_version_num),
    }
    return "cfg_" + sha256_json(payload)


def snapshot_id(payload: dict[str, Any]) -> str:
    return "snap_" + sha256_json(payload)


def modeling_key(
    query_instance_id_value: str,
    snapshot_id_value: str,
    config_id_value: str,
    cache_protocol: str,
    protocol_version: str,
) -> str:
    """Deterministic modeling-key identity (see module docstring)."""
    for name, value in (
        ("query_instance_id", query_instance_id_value),
        ("snapshot_id", snapshot_id_value),
        ("config_id", config_id_value),
        ("cache_protocol", cache_protocol),
        ("protocol_version", protocol_version),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"modeling_key component {name} must be a non-empty string")
    payload = {
        "query_instance_id": query_instance_id_value,
        "snapshot_id": snapshot_id_value,
        "config_id": config_id_value,
        "cache_protocol": cache_protocol,
        "protocol_version": protocol_version,
    }
    return "mk_" + sha256_json(payload)
