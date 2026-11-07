"""Exclusive, durable raw artifacts and replaceable derived reports."""

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
import json
import os
from pathlib import Path


def encode(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    if isinstance(value, Fraction):
        return {"numerator": value.numerator, "denominator": value.denominator}
    raise TypeError(f"Unsupported artifact type: {type(value).__name__}")


def digest(value):
    return sha256(json.dumps(value, default=encode, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    """Refuse overwrites; fsync raw evidence before a journal finish is allowed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, default=encode, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return sha256(path.read_bytes()).hexdigest()


def replace_text(path, text):
    """Only derived views may be replaced; raw records use write_json."""
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
