"""RQ1 Slice A: source provenance (git is queried read-only; test repositories live in tmp_path)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from query_cost_predictor.rq1_provenance import (
    SourceState,
    collect_source_state,
    dependency_versions,
    evidence_run_blockers,
    full_environment,
    is_allowed_untracked,
    parse_porcelain_z,
)

ALLOWED = ("reference_docs/", "reference_pilot/README.md")
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def clean_state(**changes) -> SourceState:
    state = SourceState(repo_root="r", source_revision="abc", tracked_dirty=False, allowed_untracked_paths=list(ALLOWED),
                        package_location="r/src/query_cost_predictor", package_from_checkout=True,
                        sklearn_version="1.9.1", tested_sklearn_version="1.9.1")
    for key, value in changes.items():
        setattr(state, key, value)
    return state


def test_porcelain_parsing_handles_untracked_modified_and_renamed_entries():
    output = b"?? reference_docs/a b.md\0 M src/x.py\0R  new.py\0old.py\0?? notes.txt\0"
    tracked, untracked = parse_porcelain_z(output)
    assert tracked == [" M src/x.py", "R  new.py"]
    assert untracked == ["reference_docs/a b.md", "notes.txt"]


def test_allowed_untracked_paths():
    assert is_allowed_untracked("reference_docs/Plan — v2.docx", ALLOWED)
    assert is_allowed_untracked("reference_pilot/README.md", ALLOWED)
    assert not is_allowed_untracked("reference_pilot/output/x.csv", ALLOWED)
    assert not is_allowed_untracked("src/query_cost_predictor/new.py", ALLOWED)


def test_clean_state_is_eligible_and_head_is_never_compared_with_the_checkpoint():
    assert evidence_run_blockers(clean_state(source_revision="f" * 40)) == []


@pytest.mark.parametrize("changes, fragment", [
    ({"tracked_dirty": True, "tracked_changes": [" M src/a.py"]}, "tracked tree"),
    ({"disallowed_untracked_paths": ["src/query_cost_predictor/x.py"]}, "untracked"),
    ({"ignored_execution_files": ["config/local.yaml"]}, "git-ignored"),
    ({"package_from_checkout": False}, "imported from"),
    ({"sklearn_version": "1.8.0"}, "scikit-learn"),
])
def test_each_condition_blocks_an_evidence_run(changes, fragment):
    blockers = evidence_run_blockers(clean_state(**changes))
    assert len(blockers) == 1 and fragment in blockers[0]


@requires_git
def test_collect_source_state_on_a_temporary_repository(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                       check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / ".gitignore").write_text("__pycache__/\n*.local\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "init")
    state = collect_source_state(tmp_path, ALLOWED, "1.9.1")
    assert state.source_revision and not state.tracked_dirty and state.untracked_paths == []

    (tmp_path / "reference_docs").mkdir()
    (tmp_path / "reference_docs" / "plan.md").write_text("ref", encoding="utf-8")
    (tmp_path / "src" / "__pycache__").mkdir()
    (tmp_path / "src" / "__pycache__" / "a.pyc").write_bytes(b"\0")
    state = collect_source_state(tmp_path, ALLOWED, "1.9.1")
    assert state.untracked_paths == ["reference_docs/plan.md"] and state.disallowed_untracked_paths == []
    assert state.ignored_execution_files == []

    (tmp_path / "src" / "new.py").write_text("y = 2\n", encoding="utf-8")
    (tmp_path / "src" / "settings.local").write_text("z", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text("x = 3\n", encoding="utf-8")
    state = collect_source_state(tmp_path, ALLOWED, "1.9.1")
    assert state.tracked_dirty and state.disallowed_untracked_paths == ["src/new.py"]
    assert state.ignored_execution_files == ["src/settings.local"]
    blockers = evidence_run_blockers(state)
    assert any("tracked tree" in b for b in blockers) and any("untracked" in b for b in blockers)
    assert any("git-ignored" in b for b in blockers)


def test_environment_records_versions():
    versions = dependency_versions()
    assert versions["scikit-learn"] and versions["python"]
    environment = full_environment()
    assert any(line.lower().startswith("scikit-learn==") for line in environment)
    names = [line.split("==")[0].lower() for line in environment]
    assert names == sorted(names) and len(names) == len(set(names))
