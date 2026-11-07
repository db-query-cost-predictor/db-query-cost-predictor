"""Explicit private environment-file support; no credentials are written to runs."""
import os
from pathlib import Path
import re

ALLOWED = {"GROQ_API_KEY", "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGPASSFILE", "PGSSLMODE",
           "QCP_DB_HOST", "QCP_BENCH_PORT", "QCP_BENCH_READER_PASSWORD"}


def load_private_env(path):
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"): continue
        if line.startswith("export "): line = line[7:].lstrip()
        if "=" not in line: raise ValueError("Malformed environment line " + str(number))
        name, value = line.split("=", 1); name = name.strip(); value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name): raise ValueError("Invalid environment variable name")
        if name not in ALLOWED: continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}: value = value[1:-1]
        else: value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(name, value)


def reader_environment():
    os.environ.setdefault("PGHOST", os.environ.get("QCP_DB_HOST", "127.0.0.1"))
    os.environ.setdefault("PGPORT", os.environ.get("QCP_BENCH_PORT", "55432"))
    os.environ.setdefault("PGUSER", "qcp_bench_reader")
    if "PGPASSWORD" not in os.environ and "QCP_BENCH_READER_PASSWORD" in os.environ:
        os.environ["PGPASSWORD"] = os.environ["QCP_BENCH_READER_PASSWORD"]
