"""Shared pytest fixtures. All fixture data is SYNTHETIC and built in memory or in tmp_path.

These tests were written but not run by Claude Code. They need no database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def plan_node() -> Callable[..., dict[str, Any]]:
    """Factory for a synthetic plan node (arbitrary test values, not measurements)."""

    def make(node_type: str = "Seq Scan", total_cost: float = 100.0, rows: float = 10.0,
             children: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
        node: dict[str, Any] = {"Node Type": node_type, "Startup Cost": 0.0, "Total Cost": total_cost,
                                "Plan Rows": rows, "Plan Width": 8}
        node.update(extra)
        if children:
            node["Plans"] = children
        return node

    return make


@pytest.fixture
def valid_db_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Synthetic, syntactically valid database environment (not real credentials)."""
    values = {
        "QCP_DB_HOST": "127.0.0.1",
        "QCP_BENCH_PORT": "55432",
        "QCP_EVIDENCE_PORT": "55433",
        "QCP_BENCH_ADMIN_USER": "qcp_admin",
        "QCP_EVIDENCE_ADMIN_USER": "qcp_admin",
        "QCP_BENCH_ADMIN_PASSWORD": "TestOnly0000000A",
        "QCP_EVIDENCE_ADMIN_PASSWORD": "TestOnly0000000B",
        "QCP_BENCH_OWNER_PASSWORD": "TestOnly0000000C",
        "QCP_BENCH_READER_PASSWORD": "TestOnly0000000D",
        "QCP_EVIDENCE_OWNER_PASSWORD": "TestOnly0000000E",
        "QCP_EVIDENCE_WRITER_PASSWORD": "TestOnly0000000F",
        "QCP_READER_TEMP_FILE_LIMIT": "5GB",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values
