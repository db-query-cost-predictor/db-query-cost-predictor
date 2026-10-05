"""Repository and data-root path resolution.

Rules enforced here:

* The package must be installed in editable mode from this repository, so the
  repository root can be located next to ``src/``.
* ``QCP_DATA_ROOT`` must be an absolute, existing directory **outside** the
  repository. Raw evidence, manifests and derived datasets live there.
* Poster reports are written to ``reports/poster`` inside the repository.
"""

from __future__ import annotations

import os
from pathlib import Path


class PathConfigError(RuntimeError):
    """Raised when a required path is missing or violates a location rule."""


def repo_root() -> Path:
    """Return the repository root (the folder containing ``pyproject.toml``)."""
    override = os.environ.get("QCP_REPO_ROOT", "").strip()
    root = Path(override).resolve() if override else Path(__file__).resolve().parents[2]
    if not (root / "pyproject.toml").is_file() or not (root / "src" / "query_cost_predictor").is_dir():
        raise PathConfigError(
            f"Cannot locate the repository root (looked at {root}). Install the package "
            "in editable mode from the repository: python -m pip install -e \".[notebook,dev]\""
        )
    return root


def config_dir() -> Path:
    return repo_root() / "config"


def reports_dir() -> Path:
    """Directory for generated poster evidence (git-ignored)."""
    return repo_root() / "reports" / "poster"


def environment_reports_dir() -> Path:
    return reports_dir() / "environment"


def pilot_output_dir() -> Path:
    return repo_root() / "reference_pilot" / "output"


def is_within(child: Path, parent: Path) -> bool:
    """True if ``child`` is ``parent`` or lies inside it (after resolving)."""
    child_r = child.resolve()
    parent_r = parent.resolve()
    return child_r == parent_r or child_r.is_relative_to(parent_r)


def data_root(*, must_exist: bool = True) -> Path:
    """Return ``QCP_DATA_ROOT`` after validating its location."""
    raw = os.environ.get("QCP_DATA_ROOT", "").strip()
    if not raw:
        raise PathConfigError("QCP_DATA_ROOT is not set. Set it in .env (runbook step 3).")
    path = Path(raw)
    if not path.is_absolute():
        raise PathConfigError(f"QCP_DATA_ROOT must be an absolute path, got {raw!r}.")
    path = path.resolve()
    if is_within(path, repo_root()):
        raise PathConfigError(f"QCP_DATA_ROOT ({path}) must be outside the repository.")
    if must_exist and not path.is_dir():
        raise PathConfigError(f"QCP_DATA_ROOT ({path}) does not exist. Create it yourself (runbook step 3).")
    return path


def ensure_dir(path: Path) -> Path:
    """Create ``path`` (and parents) if needed and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def refuse_reference_write(target: Path) -> None:
    """Raise if ``target`` would be written inside ``reference_docs`` or ``reference_pilot``."""
    root = repo_root()
    for protected in (root / "reference_docs", root / "reference_pilot"):
        if is_within(target, protected):
            raise PathConfigError(f"Refusing to write inside protected reference folder: {target}")
