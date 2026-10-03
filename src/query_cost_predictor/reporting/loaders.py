"""Load generated evidence for the builder and the notebook.

Every loader raises :class:`MissingEvidenceError` naming the runbook step that
produces the missing evidence. No loader ever returns demonstration data.
Smoke evidence is only usable when its validation report says ``PASS`` and the
derived files still match the hashes recorded in that report.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from query_cost_predictor.errors import EvidenceIntegrityError, MissingEvidenceError
from query_cost_predictor.hashing import sha256_file
from query_cost_predictor.paths import reports_dir

STEP_PILOT = "run runbook step 7: .\\scripts\\powershell\\04_run_pilot_audit.ps1"
STEP_SMOKE = "run runbook steps 9-10: .\\scripts\\powershell\\05_run_poster_smoke.ps1 -Step Manifest|Collect|Derive|Validate"
STEP_REWRITE = "run runbook step 11: .\\scripts\\powershell\\05b_run_rewrite_verification.ps1"


@dataclass
class PilotEvidence:
    audit: dict[str, Any]
    path: Path
    sha256: str


@dataclass
class SmokeEvidence:
    validation: dict[str, Any]
    validation_path: Path
    derived_dir: Path
    keys: list[dict[str, str]]
    labels: list[dict[str, str]]
    executions: list[dict[str, str]]
    estimates: list[dict[str, str]]
    features: list[dict[str, str]]


@dataclass
class RewriteEvidence:
    summary: dict[str, Any]
    path: Path
    sha256: str


@dataclass
class EvidenceBundle:
    pilot: PilotEvidence | None
    smoke: SmokeEvidence | None
    rewrite: RewriteEvidence | None
    environment: dict[str, Any]
    problems: dict[str, str] = field(default_factory=dict)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def require(item: Any, message: str) -> Any:
    """Stop with a clear message instead of continuing without evidence."""
    if item is None:
        raise MissingEvidenceError(message)
    return item


def load_pilot(reports: Path | None = None) -> PilotEvidence:
    path = (reports or reports_dir()) / "pilot_audit.json"
    if not path.is_file():
        raise MissingEvidenceError(f"pilot audit not found at {path}; {STEP_PILOT}")
    audit = _read_json(path)
    if audit.get("evidence_status") != "RECOMPUTED_PILOT":
        raise EvidenceIntegrityError(f"{path} does not carry evidence_status RECOMPUTED_PILOT")
    return PilotEvidence(audit=audit, path=path, sha256=sha256_file(path))


def load_smoke(reports: Path | None = None) -> SmokeEvidence:
    path = (reports or reports_dir()) / "smoke_validation.json"
    if not path.is_file():
        raise MissingEvidenceError(f"smoke validation not found at {path}; {STEP_SMOKE}")
    validation = _read_json(path)
    if validation.get("status") != "PASS":
        raise MissingEvidenceError(f"smoke validation status is {validation.get('status')}; smoke evidence is not usable until "
                                   "validation passes (inspect reports/poster/smoke_validation.md)")
    derived = Path(validation["derived_dir"])
    if not derived.is_dir():
        raise MissingEvidenceError(f"derived smoke folder {derived} is missing; re-run -Step Derive and -Step Validate")
    for name, expected in validation["derived_files_sha256"].items():
        if sha256_file(derived / name) != expected:
            raise EvidenceIntegrityError(f"{derived / name} changed after validation; re-run -Step Validate")
    return SmokeEvidence(
        validation=validation, validation_path=path, derived_dir=derived,
        keys=read_csv_rows(derived / "keys.csv"), labels=read_csv_rows(derived / "labels.csv"),
        executions=read_csv_rows(derived / "executions.csv"), estimates=read_csv_rows(derived / "estimates.csv"),
        features=read_csv_rows(derived / "features.csv"),
    )


def load_rewrite(reports: Path | None = None) -> RewriteEvidence:
    path = (reports or reports_dir()) / "rewrite_verification_summary.json"
    if not path.is_file():
        raise MissingEvidenceError(f"rewrite verification summary not found at {path}; {STEP_REWRITE}")
    summary = _read_json(path)
    if summary.get("rewrite_source") != "MANUAL_REFERENCE_REWRITE":
        raise EvidenceIntegrityError("rewrite summary is not labelled MANUAL_REFERENCE_REWRITE")
    return RewriteEvidence(summary=summary, path=path, sha256=sha256_file(path))


def load_environment(reports: Path | None = None) -> dict[str, Any]:
    folder = (reports or reports_dir()) / "environment"
    found: dict[str, Any] = {}
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            try:
                found[path.name] = {"path": str(path), "sha256": sha256_file(path), "content": _read_json(path)}
            except (OSError, json.JSONDecodeError) as exc:
                found[path.name] = {"path": str(path), "error": str(exc)}
    return found


def load_all(reports: Path | None = None) -> EvidenceBundle:
    """Load whatever evidence exists; record why anything is missing."""
    problems: dict[str, str] = {}
    pilot = smoke = rewrite = None
    try:
        pilot = load_pilot(reports)
    except (MissingEvidenceError, EvidenceIntegrityError) as exc:
        problems["pilot"] = str(exc)
    try:
        smoke = load_smoke(reports)
    except (MissingEvidenceError, EvidenceIntegrityError) as exc:
        problems["smoke"] = str(exc)
    try:
        rewrite = load_rewrite(reports)
    except (MissingEvidenceError, EvidenceIntegrityError) as exc:
        problems["rewrite"] = str(exc)
    return EvidenceBundle(pilot=pilot, smoke=smoke, rewrite=rewrite, environment=load_environment(reports),
                          problems=problems)
