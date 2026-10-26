"""Expanded-release readiness, full-collection approval and bounded, resumable collection.

Phases (each a separate, explicit user command):

1. ``expanded-readiness``: fresh preflight, migration V008, both TPC-H snapshots
   registered without statistics drift, free disk under ``QCP_DATA_ROOT``.
2. ``expanded-collect --phase sample``: only the manifest's bounded,
   stratified sample (plan check must have passed).
3. ``expanded-approve-full --budget-hours H``: refused unless the plan check
   and the sample validation passed and ``H`` covers the projected expected
   time of the remaining keys. Writes an immutable approval record.
4. ``expanded-collect --phase full --max-keys N [--max-minutes M]``: resumable
   batches over the whole manifest; refused without the approval record and
   without an explicit batch bound.

The collector itself is the poster-smoke collector with the expanded profile:
evidence first (raw JSONL, then the evidence database), duplicate-run
detection, timeouts kept as right-censored runs and failures kept with their
status. Nothing in this module changes the smoke release or the pilot.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from query_cost_predictor.db import connect_bench_reader, connect_evidence_writer
from query_cost_predictor.errors import EvidenceIntegrityError
from query_cost_predictor.expanded_manifest import KIND, MANIFEST_KIND, REQUIRED_MIGRATION, migration_applied, report_path
from query_cost_predictor.hashing import sha256_file, sha256_json
from query_cost_predictor.paths import data_root, ensure_dir, expanded_reports_dir
from query_cost_predictor.release_spec import load_release_spec
from query_cost_predictor.smoke_collector import EXPANDED_PROFILE, Collector
from query_cost_predictor.smoke_manifest import current_manifest_id, load_manifest, manifest_dir
from query_cost_predictor.snapshot import latest_snapshot, statistics_sha256

PHASES = ("sample", "full")
_UTC_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]00:00)?$")


class GateError(RuntimeError):
    """A required earlier phase has not passed; the command refuses to continue."""


def parse_utc(text: str) -> datetime:
    """Parse the PowerShell 'o' timestamp (7 fractional digits, 'Z') or an ISO UTC timestamp."""
    match = _UTC_PATTERN.match(str(text).strip())
    if not match:
        raise ValueError(f"unrecognised UTC timestamp {text!r}")
    return datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def approval_path(manifest_id: str) -> Path:
    return manifest_dir(KIND) / "approvals" / f"FULL_COLLECTION_{manifest_id[3:19]}.json"


# ----------------------------------------------------------------------------------
# Readiness
# ----------------------------------------------------------------------------------
def readiness(spec_path: Path | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    spec = load_release_spec(spec_path)
    checks: list[dict[str, Any]] = []

    def add(check_id: str, name: str, status: str, detail: Any) -> None:
        checks.append({"id": check_id, "check": name, "status": status, "detail": detail})

    root = data_root()
    free_gb = shutil.disk_usage(root).free / 1024 ** 3
    add("R-01", "QCP_DATA_ROOT outside OneDrive", "FAIL" if "onedrive" in str(root).lower() else "PASS", str(root))
    add("R-02", f"free space under QCP_DATA_ROOT >= {spec.budget.min_free_gb_data_root:g} GB",
        "PASS" if free_gb >= spec.budget.min_free_gb_data_root else "FAIL", round(free_gb, 1))

    preflight_file = expanded_reports_dir() / "environment" / "preflight.json"
    if not preflight_file.is_file():
        add("R-03", "fresh preflight report", "FAIL", f"missing {preflight_file}; run step 1 of EXPANDED_DATASET_RUNBOOK.md")
    else:
        preflight = _read_json(preflight_file)
        age_h = ((now or datetime.now(timezone.utc)) - parse_utc(preflight["created_at_utc"])).total_seconds() / 3600.0
        fresh = age_h <= spec.budget.max_preflight_age_hours and preflight.get("status") == "PASS"
        add("R-03", f"preflight PASS and younger than {spec.budget.max_preflight_age_hours:g} h", "PASS" if fresh else "FAIL",
            {"age_hours": round(age_h, 2), "status": preflight.get("status")})
        facts = preflight.get("facts", {})
        free_mem = (facts.get("memory_gb") or {}).get("free")
        docker_mem = (facts.get("docker") or {}).get("mem_total_gb")
        add("R-04", f"free host memory >= {spec.budget.min_free_memory_gb_warn:g} GB at preflight",
            "PASS" if free_mem is not None and free_mem >= spec.budget.min_free_memory_gb_warn else "WARN",
            {"free_gb": free_mem, "total_gb": (facts.get("memory_gb") or {}).get("total"),
             "advice": "close other applications; memory pressure on the WSL2 VM adds timing noise"})
        add("R-05", f"Docker Desktop memory >= {spec.budget.min_docker_memory_gb_warn:g} GB",
            "PASS" if docker_mem is not None and docker_mem >= spec.budget.min_docker_memory_gb_warn else "WARN",
            {"docker_mem_total_gb": docker_mem})

    with connect_evidence_writer("qcp:expanded-readiness") as ev:
        add("R-06", f"evidence migration V{REQUIRED_MIGRATION} applied",
            "PASS" if migration_applied(ev, REQUIRED_MIGRATION) else "FAIL",
            "run 03_initialize_databases.ps1 if missing")
        for name, snap_spec in spec.snapshots.items():
            snap = latest_snapshot(ev, snap_spec.database)
            if snap is None:
                add(f"R-07:{name}", f"snapshot {name} registered", "FAIL",
                    f"run 03b_load_tpch.ps1 -ScaleFactor {snap_spec.scale_factor}")
                continue
            row = ev.execute("SELECT table_row_counts FROM qcp.benchmark_snapshot WHERE snapshot_id = %s",
                             (snap["snapshot_id"],)).fetchone()
            expected = spec.expected_row_counts.get(snap_spec.scale_factor, {})
            counts_ok = bool(expected) and row is not None and dict(row[0]) == expected
            with connect_bench_reader(snap_spec.database, "qcp:expanded-readiness") as conn:
                live = statistics_sha256(conn)
            drift_ok = live == snap["statistics_sha256"]
            add(f"R-07:{name}", f"snapshot {name} registered, row counts as expected, no statistics drift",
                "PASS" if counts_ok and drift_ok else "FAIL",
                {"snapshot_id": snap["snapshot_id"], "row_counts_ok": counts_ok, "statistics_unchanged": drift_ok})
        ev.commit()

    image_file = expanded_reports_dir().parent / "poster" / "environment" / "postgres_image.json"
    add("R-08", "PostgreSQL image digest captured by 02_start_databases.ps1",
        "PASS" if image_file.is_file() and _read_json(image_file).get("repo_digests") else "WARN", str(image_file))

    status = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PASS"
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": status,
              "spec_sha256": spec.sha256, "checks": checks}
    out = ensure_dir(expanded_reports_dir()) / "readiness.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return {"status": status, "summary": {"status": status, "report": str(out),
                                          "fails": [c["id"] for c in checks if c["status"] == "FAIL"],
                                          "warnings": [c["id"] for c in checks if c["status"] == "WARN"]}}


# ----------------------------------------------------------------------------------
# Gates
# ----------------------------------------------------------------------------------
def require_report(name: str, manifest_id: str, *, statuses: tuple[str, ...] = ("PASS",)) -> dict[str, Any]:
    path = report_path(name, manifest_id)
    if not path.is_file():
        raise GateError(f"{path.name} is missing; run the earlier runbook step first")
    report = _read_json(path)
    if report.get("manifest_id") not in (None, manifest_id):
        raise GateError(f"{path.name} belongs to a different manifest")
    if report.get("status") not in statuses:
        raise GateError(f"{path.name} has status {report.get('status')}; required {statuses}")
    return report


def approve_full(manifest_id: str | None, budget_hours: float) -> dict[str, Any]:
    """Record the explicit decision to run the full collection (immutable, exclusive create)."""
    mid = current_manifest_id(manifest_id, KIND)
    load_manifest(mid, KIND)
    require_report("plan_check", mid)
    require_report("validation_sample", mid)
    budget = require_report("budget_projection", mid, statuses=("PROJECTED",))
    projection = budget["projection"]
    expected_h = float(projection["expected_hours_remaining"])
    if budget_hours <= 0 or budget_hours < expected_h:
        return {"status": "REFUSED", "summary": {
            "status": "REFUSED", "reason": f"--budget-hours {budget_hours:g} is below the projected expected "
                                           f"{expected_h:.1f} h for the remaining keys; re-read the projection first",
            "projection": projection}}
    record = {
        "manifest_id": mid,
        "approved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "budget_hours_acknowledged": budget_hours,
        "projection": projection,
        "reports_sha256": {name: sha256_file(report_path(name, mid))
                           for name in ("plan_check", "validation_sample", "budget_projection")},
        "note": "Full collection approved by the user after sample validation. Batches stay bounded by --max-keys.",
    }
    path = approval_path(mid)
    ensure_dir(path.parent)
    if path.exists():
        return {"status": "APPROVED", "summary": {"status": "APPROVED", "note": "an approval record already exists",
                                                  "approval": str(path)}}
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({**record, "record_sha256": sha256_json(record)}, indent=2))
    return {"status": "APPROVED", "summary": {"status": "APPROVED", "approval": str(path),
                                              "expected_hours_remaining": expected_h,
                                              "worst_case_hours_remaining": projection.get("worst_case_hours_remaining")}}


def verify_approval(manifest_id: str) -> dict[str, Any]:
    path = approval_path(manifest_id)
    if not path.is_file():
        raise GateError("full collection is not approved; run 08_expanded_release.ps1 -Step ApproveFull -BudgetHours <h>")
    record = _read_json(path)
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    if record.get("record_sha256") != sha256_json(body) or record.get("manifest_id") != manifest_id:
        raise EvidenceIntegrityError(f"{path.name} was edited or belongs to another manifest")
    return record


# ----------------------------------------------------------------------------------
# Collection
# ----------------------------------------------------------------------------------
def run_expanded_collection(manifest_id: str | None, *, phase: str, max_keys: int | None = None,
                            max_minutes: float | None = None) -> int:
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}")
    mid = current_manifest_id(manifest_id, KIND)
    manifest, path = load_manifest(mid, KIND)
    if manifest.get("manifest_kind") != MANIFEST_KIND:
        raise EvidenceIntegrityError("not an expanded-release manifest")
    require_report("plan_check", mid)
    budget = manifest["release"]["budget"]
    if phase == "sample":
        selected = list(manifest["sample"]["keys"])
        print(f"phase=sample: {len(selected)} bounded sample key(s); the full manifest has {len(manifest['entries'])}")
    else:
        verify_approval(mid)
        if max_keys is None or max_keys < 1:
            raise GateError("the full phase runs in explicit batches: pass --max-keys N (for example 250)")
        selected = None
        print(f"phase=full: batch of at most {max_keys} key(s)"
              + (f", at most {max_minutes:g} minute(s)" if max_minutes else ""))
    collector = Collector(manifest, path, max_keys, profile=EXPANDED_PROFILE, selected_keys=selected,
                          max_minutes=max_minutes, min_free_gb=float(budget["min_free_gb_data_root"]),
                          disk_check_every_n_keys=int(budget["disk_check_every_n_keys"]))
    return collector.run()
