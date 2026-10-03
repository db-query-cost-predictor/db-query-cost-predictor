"""Minimal, dependency-free ``.env`` loader.

Semantics (kept deliberately close to Docker Compose's ``.env`` handling):

* ``KEY=VALUE`` per line; blank lines and lines starting with ``#`` are ignored.
* An optional ``export`` prefix is accepted.
* Matching single or double quotes around a value are removed; no escape
  processing and no variable interpolation is performed.
* For unquoted values, `` #`` starts an inline comment.
* Existing process environment variables win unless ``override=True``, so a
  value can be overridden from the terminal for one command.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EnvFileError(ValueError):
    """Raised for a malformed ``.env`` line."""


def parse_env_text(text: str) -> dict[str, str]:
    """Parse ``.env`` content into a dictionary without touching ``os.environ``."""
    values: dict[str, str] = {}
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            raise EnvFileError(f".env line {line_no}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not _KEY_RE.match(key):
            raise EnvFileError(f".env line {line_no}: invalid variable name {key!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def load_env_file(path: Path, *, override: bool = False) -> dict[str, str]:
    """Load ``path`` into ``os.environ`` and return the parsed values."""
    values = parse_env_text(path.read_text(encoding="utf-8-sig"))
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return values


def load_repo_env() -> Path | None:
    """Load ``<repo>/.env`` if present. Returns the path loaded, or ``None``."""
    from query_cost_predictor.paths import repo_root

    path = repo_root() / ".env"
    if not path.is_file():
        return None
    load_env_file(path)
    return path
