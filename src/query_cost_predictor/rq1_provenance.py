"""Source provenance and software environment for RQ1 runs (read-only).

Git is only queried, never changed. Every command runs with
``GIT_OPTIONAL_LOCKS=0`` so that ``git status`` does not refresh the index.

An evidence-producing run requires (``evidence_run_blockers``):

* a clean tracked tree (no staged or unstaged modification);
* no untracked, non-ignored path other than the allowed reference paths,
  which are allowed only because no RQ1 code reads them;
* no git-ignored file under ``src/``, ``config/`` or ``scripts/`` other than
  ``__pycache__/`` and ``*.egg-info/``;
* the package imported from this checkout's ``src/``;
* the tested scikit-learn version.

``rq1-check-inputs`` reports the same state without requiring it.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

MAIN_DISTRIBUTIONS = ("numpy", "scipy", "scikit-learn", "pandas", "matplotlib", "PyYAML", "joblib", "threadpoolctl")
EXECUTION_PATHS = ("src", "config", "scripts")
IGNORED_ALLOWED_PARTS = ("__pycache__",)
IGNORED_ALLOWED_SUFFIXES = (".egg-info",)


class SourceProvenanceError(RuntimeError):
    """Git could not be queried for the source state."""


@dataclass
class SourceState:
    repo_root: str
    source_revision: str | None
    tracked_dirty: bool
    tracked_changes: list[str] = field(default_factory=list)
    untracked_paths: list[str] = field(default_factory=list)
    disallowed_untracked_paths: list[str] = field(default_factory=list)
    ignored_execution_files: list[str] = field(default_factory=list)
    allowed_untracked_paths: list[str] = field(default_factory=list)
    package_location: str = ""
    package_from_checkout: bool = False
    sklearn_version: str | None = None
    tested_sklearn_version: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _git(repo: Path, *args: str) -> bytes:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env, check=False)
    except FileNotFoundError as exc:
        raise SourceProvenanceError("git is not available") from exc
    if result.returncode != 0:
        raise SourceProvenanceError(f"git {' '.join(args)} failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def _split_z(output: bytes) -> list[str]:
    return [part.decode("utf-8", "surrogateescape") for part in output.split(b"\0") if part]


def parse_porcelain_z(output: bytes) -> tuple[list[str], list[str]]:
    """Return ``(tracked_changes, untracked_paths)`` from ``git status --porcelain=v1 -z``."""
    tracked, untracked = [], []
    entries = _split_z(output)
    index = 0
    while index < len(entries):
        entry = entries[index]
        code, path = entry[:2], entry[3:]
        if code == "??":
            untracked.append(path)
        elif code != "!!":
            tracked.append(f"{code} {path}")
            if "R" in code or "C" in code:
                index += 1  # the rename/copy source follows as its own entry
        index += 1
    return tracked, untracked


def is_allowed_untracked(path: str, allowed: tuple[str, ...]) -> bool:
    for entry in allowed:
        if entry.endswith("/"):
            if path.startswith(entry):
                return True
        elif path == entry:
            return True
    return False


def _ignored_is_allowed(path: str) -> bool:
    parts = path.split("/")
    return any(p in IGNORED_ALLOWED_PARTS for p in parts) or any(p.endswith(IGNORED_ALLOWED_SUFFIXES) for p in parts)


def installed_sklearn_version() -> str | None:
    try:
        return metadata.version("scikit-learn")
    except metadata.PackageNotFoundError:
        return None


def collect_source_state(repo: Path, allowed: tuple[str, ...], tested_sklearn: str | None) -> SourceState:
    import query_cost_predictor

    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    tracked, untracked = parse_porcelain_z(_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all"))
    ignored = _split_z(_git(repo, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--", *EXECUTION_PATHS))
    package_dir = Path(query_cost_predictor.__file__).resolve().parent
    return SourceState(
        repo_root=str(repo),
        source_revision=head,
        tracked_dirty=bool(tracked),
        tracked_changes=tracked,
        untracked_paths=untracked,
        disallowed_untracked_paths=[p for p in untracked if not is_allowed_untracked(p, allowed)],
        ignored_execution_files=[p for p in ignored if not _ignored_is_allowed(p)],
        allowed_untracked_paths=list(allowed),
        package_location=str(package_dir),
        package_from_checkout=package_dir == (repo / "src" / "query_cost_predictor").resolve(),
        sklearn_version=installed_sklearn_version(),
        tested_sklearn_version=tested_sklearn,
    )


def evidence_run_blockers(state: SourceState) -> list[str]:
    """Reasons an evidence-producing run must be refused (empty list: eligible)."""
    blockers = []
    if state.tracked_dirty:
        blockers.append(f"tracked tree is not clean ({len(state.tracked_changes)} change(s))")
    if state.disallowed_untracked_paths:
        blockers.append(f"untracked paths outside the allowed reference paths: {state.disallowed_untracked_paths[:5]}")
    if state.ignored_execution_files:
        blockers.append(f"git-ignored files in execution paths: {state.ignored_execution_files[:5]}")
    if not state.package_from_checkout:
        blockers.append(f"query_cost_predictor is imported from {state.package_location}, not this checkout's src/")
    if state.tested_sklearn_version and state.sklearn_version != state.tested_sklearn_version:
        blockers.append(f"scikit-learn {state.sklearn_version} is not the tested {state.tested_sklearn_version}")
    return blockers


def dependency_versions() -> dict[str, str]:
    from query_cost_predictor import __version__

    versions = {"python": sys.version.split()[0], "platform": platform.platform(), "query_cost_predictor": __version__}
    for dist in MAIN_DISTRIBUTIONS:
        try:
            versions[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            versions[dist] = "not installed"
    return versions


def full_environment() -> list[str]:
    """Every installed distribution as ``name==version`` (sorted, case-insensitive)."""
    seen: dict[str, str] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            seen.setdefault(name.lower(), f"{name}=={dist.version}")
    return [seen[key] for key in sorted(seen)]
