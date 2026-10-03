"""Validate the poster-smoke evidence and write reports/poster/smoke_validation.{json,md}.

Integrity problems fail validation. Mechanisms that were hoped for but not
observed (no spill, no key >= 10 s, no timeout) are reported as
``OBSERVED_ABSENT`` — honest observations, not failures.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from query_cost_predictor import POSTER_LABEL_VERSION
from query_cost_predictor.contract import leakage_problems
from query_cost_predictor.errors import EvidenceIntegrityError
from query_cost_predictor.hashing import sha256_file, sha256_json
from query_cost_predictor.paths import ensure_dir, reports_dir
from query_cost_predictor.smoke_derive import (
    FEATURE_COLUMNS,
    OUTPUT_FILES,
    build_tables,
    latest_derived_dir,
    load_ledger_for,
)
from query_cost_predictor.smoke_manifest import current_manifest_id, load_manifest

EVIDENCE_STATUS = "NEW_POSTER_SMOKE"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _float(value: str) -> float | None:
    return None if value in ("", None) else float(value)


def validate(manifest_id: str | None, *, use_db: bool = True) -> dict[str, Any]:
    mid = current_manifest_id(manifest_id)
    manifest, manifest_path = load_manifest(mid)
    protocol = manifest["protocol"]
    derived = latest_derived_dir(mid)
    checks: list[dict[str, Any]] = []

    def add(check_id: str, name: str, status: str, detail: Any) -> None:
        checks.append({"id": check_id, "check": name, "status": status, "detail": detail})

    add("V-01", "manifest file hashes to its manifest_id", "PASS", str(manifest_path))
    ledger, records, files = load_ledger_for(protocol["protocol_version"])
    add("V-02", "raw JSONL record checksums", "PASS", f"{len(records)} records in {len(files)} file(s) verified")

    derived_manifest = json.loads((derived / "derived_manifest.json").read_text(encoding="utf-8"))
    bad_outputs = [n for n, sha in derived_manifest["outputs"].items() if sha256_file(derived / n) != sha]
    add("V-03", "derived files match derived_manifest.json", "FAIL" if bad_outputs else "PASS", bad_outputs or "all match")
    current_inputs = {"manifest": {"manifest_id": mid, "sha256": sha256_file(manifest_path)},
                      "raw_files": [{"name": p.name, "sha256": sha256_file(p)} for p in files]}
    stale = current_inputs != derived_manifest["inputs"]
    add("V-04", "derived files were built from the current raw evidence", "FAIL" if stale else "PASS",
        "raw evidence changed after derive; re-run -Step Derive" if stale else "inputs identical")

    tables = build_tables(manifest, ledger)
    recomputed_labels = {row["modeling_key"]: row for row in tables["labels"]}
    labels = _read_csv(derived / "labels.csv")
    label_mismatch = [row["modeling_key"] for row in labels
                      if row["status"] != recomputed_labels[row["modeling_key"]]["status"]
                      or _float(row["median_ms"]) != recomputed_labels[row["modeling_key"]]["median_ms"]]
    add("V-05", "labels recompute identically from raw evidence (measured runs only)",
        "FAIL" if label_mismatch else "PASS", label_mismatch[:5] or "identical")

    keys = [e["modeling_key"] for e in manifest["entries"]]
    if use_db:
        from query_cost_predictor.db import connect_evidence_writer
        from query_cost_predictor.evidence_store import registered_raw_files, verify_database_matches_ledger

        try:
            with connect_evidence_writer("qcp:smoke-validate") as ev:
                verify_database_matches_ledger(ev, ledger, keys)
                session_ids = sorted({r["session_id"] for r in records})
                registered = registered_raw_files(ev, session_ids)
                unregistered = [p.name for p in files if str(p) not in registered]
                changed = [p.name for p in files if str(p) in registered and registered[str(p)] != sha256_file(p)]
                add("V-06", "evidence database matches raw JSONL (executions and estimates)", "PASS", "identical")
                add("V-07", "raw files registered with matching SHA-256", "FAIL" if changed else ("WARN" if unregistered else "PASS"),
                    {"changed": changed, "unregistered": unregistered})
        except EvidenceIntegrityError as exc:
            add("V-06", "evidence database matches raw JSONL (executions and estimates)", "FAIL", str(exc))
    else:
        add("V-06", "evidence database matches raw JSONL", "NOT_CHECKED", "--no-db")

    states = Counter(row["status"] for row in labels)
    key_states = Counter(row["key_state"] for row in tables["keys"])
    unfinished = key_states["not_started"] + key_states["incomplete"]
    add("V-08", "every planned key attempted and its label determined", "FAIL" if unfinished else "PASS",
        {"planned": len(keys), **dict(key_states)})
    missing_estimates = [k for k in keys if k not in ledger.estimates and ledger.probe(k) is not None]
    add("V-09", "attempted keys have a plain pre-execution estimate", "WARN" if missing_estimates else "PASS",
        missing_estimates[:5] or "all present")

    executions = _read_csv(derived / "executions.csv")
    bad_runtime = [r["modeling_key"] for r in executions
                   if (r["status"] == "completed" and (_float(r["execution_time_ms"]) is None or _float(r["execution_time_ms"]) < 0))
                   or (r["status"] != "completed" and r["execution_time_ms"] != "")]
    add("V-10", "completed runs have a runtime; timeouts/errors have NULL (never zero)", "FAIL" if bad_runtime else "PASS",
        bad_runtime[:5] or "consistent")

    settings_by_config: dict[str, set[str]] = defaultdict(set)
    config_of = {e["modeling_key"]: e["configuration_name"] for e in manifest["entries"]}
    for row in executions:
        if row["status"] == "completed":
            settings_by_config[config_of[row["modeling_key"]]].add(row["settings_json"])
    settings_problems = []
    for name, blocks in settings_by_config.items():
        if len(blocks) != 1:
            settings_problems.append(f"{name}: {len(blocks)} different EXPLAIN SETTINGS blocks")
            continue
        block = json.loads(next(iter(blocks)))
        if block.get("jit") != "off":
            settings_problems.append(f"{name}: jit is not off")
        expected_work_mem = manifest["configurations"][name]["session_settings"].get("work_mem")
        if expected_work_mem and block.get("work_mem") != expected_work_mem:
            settings_problems.append(f"{name}: work_mem {block.get('work_mem')!r} != {expected_work_mem!r}")
    add("V-11", "EXPLAIN SETTINGS consistent within each configuration", "FAIL" if settings_problems else "PASS",
        settings_problems or {k: len(v) for k, v in settings_by_config.items()})

    shapes: dict[str, set[str]] = defaultdict(set)
    for row in executions:
        if row["plan_shape_sha256"]:
            shapes[row["modeling_key"]].add(row["plan_shape_sha256"])
    changed_plans = [k for k, s in shapes.items() if len(s) > 1]
    add("V-12", "plan shape stable across probe and measured runs", "WARN" if changed_plans else "PASS",
        changed_plans[:5] or "stable")

    mismatched_workers = [r for r in executions if r["workers_planned"] and r["workers_launched"]
                          and int(r["workers_launched"]) < int(r["workers_planned"])]
    add("V-13", "parallel workers launched as planned", "WARN" if mismatched_workers else "PASS",
        f"{len(mismatched_workers)} run(s) launched fewer workers than planned")

    labelled = {row["modeling_key"] for row in labels if row["status"] in ("complete", "right_censored")}
    coverage_missing = []
    for tag in manifest["required_coverage"]:
        if not any(tag in e["coverage"] and e["modeling_key"] in labelled for e in manifest["entries"]):
            coverage_missing.append(tag)
    add("V-14", "every required coverage tag has a labelled key", "FAIL" if coverage_missing else "PASS",
        coverage_missing or "all covered")
    pairs_ok = defaultdict(set)
    for e in manifest["entries"]:
        if e["rewrite_pair_id"] and e["modeling_key"] in labelled:
            pairs_ok[e["rewrite_pair_id"]].add(e["form"])
    complete_pairs = [p for p, forms in pairs_ok.items() if {"original", "reference_rewrite"} <= forms]
    add("V-15", "at least two reference rewrite pairs labelled in both forms", "PASS" if len(complete_pairs) >= 2 else "FAIL",
        complete_pairs)

    feature_problems = leakage_problems(list(FEATURE_COLUMNS))
    add("V-16", "feature columns pass the availability contract (no E-class, no identifiers)",
        "FAIL" if feature_problems else "PASS", feature_problems or f"{len(FEATURE_COLUMNS)} A-D features")
    n = len(keys)
    low, high = int(protocol["min_keys"]), int(protocol["max_keys"])
    add("V-17", f"key count within the poster-smoke range {low}-{high}", "PASS" if low <= n <= high else "FAIL", n)

    measured = [r for r in executions if r["run_purpose"] == "measured"]
    spill_keys = sorted({r["modeling_key"] for r in measured if r["spill_detected"] == "true"})
    medians = {row["modeling_key"]: _float(row["median_ms"]) for row in labels}
    censored = sorted(k for k, row in ((r["modeling_key"], r) for r in labels) if row["status"] == "right_censored")
    at_least = {thr: sorted(k for k, v in medians.items() if v is not None and v >= thr)
                for thr in (1000.0, 10000.0)}
    add("O-01", "spill observed in a measured run", "OBSERVED" if spill_keys else "OBSERVED_ABSENT", len(spill_keys))
    add("O-02", "keys with median >= 1 s", "OBSERVED" if at_least[1000.0] else "OBSERVED_ABSENT", len(at_least[1000.0]))
    add("O-03", "keys with median >= 10 s", "OBSERVED" if at_least[10000.0] else "OBSERVED_ABSENT", len(at_least[10000.0]))
    add("O-04", "right-censored keys retained (timeout)", "OBSERVED" if censored else "OBSERVED_ABSENT", len(censored))

    status = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PASS"
    derived_files = {name: sha256_file(derived / name) for name in OUTPUT_FILES}
    report = {
        "evidence_status": EVIDENCE_STATUS,
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "manifest_id": mid,
        "manifest_file": str(manifest_path),
        "derived_dir": str(derived),
        "derived_files_sha256": derived_files,
        "label_version": POSTER_LABEL_VERSION,
        "protocol": protocol,
        "configurations": manifest["configurations"],
        "snapshots": manifest["snapshots"],
        "counts": {**derived_manifest["counts"], "label_status": dict(states)},
        "observations": {"spill_keys": spill_keys, "censored_keys": censored,
                         "keys_at_least_1s": at_least[1000.0], "keys_at_least_10s": at_least[10000.0]},
        "checks": checks,
        "raw_files": [{"name": p.name, "sha256": sha256_file(p)} for p in files],
        "evidence_fingerprint": sha256_json({"derived": derived_files, "manifest": mid}),
    }
    out_dir = ensure_dir(reports_dir())
    (out_dir / "smoke_validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "smoke_validation.md").write_text(_markdown(report), encoding="utf-8")
    if use_db:
        _update_release(mid, derived_manifest, status)
    report["summary"] = {"status": status, "report": str(out_dir / "smoke_validation.md"),
                         "fails": [c["id"] for c in checks if c["status"] == "FAIL"],
                         "warnings": [c["id"] for c in checks if c["status"] == "WARN"],
                         "observations": {c["id"]: c["status"] for c in checks if c["id"].startswith("O-")},
                         **report["counts"]}
    return report


def _update_release(mid: str, derived_manifest: dict[str, Any], status: str) -> None:
    from query_cost_predictor.db import connect_evidence_writer

    inputs_sha = sha256_json(derived_manifest["inputs"])
    release_id = f"poster_smoke:{mid[3:19]}:{derived_manifest['label_version']}:{inputs_sha[:16]}"
    with connect_evidence_writer("qcp:smoke-validate") as ev:
        ev.execute("UPDATE qcp.dataset_release SET status = %s, updated_at = now() WHERE release_id = %s",
                   ("validated" if status == "PASS" else "rejected", release_id))
        ev.commit()


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Poster smoke validation",
        "",
        f"Evidence status: `{report['evidence_status']}` · overall **{report['status']}** · generated {report['created_at_utc']}",
        f"Manifest `{report['manifest_id']}` · derived folder `{report['derived_dir']}`",
        "",
        "This is a small smoke collection, not the final dataset.",
        "",
        "## Counts",
        "",
        "| Quantity | Value |", "|---|---:|",
    ]
    lines += [f"| {k} | {v} |" for k, v in report["counts"].items() if not isinstance(v, dict)]
    lines += ["", "Label status: " + ", ".join(f"{k}={v}" for k, v in report["counts"]["label_status"].items()), "",
              "## Checks", "", "| ID | Check | Status | Detail |", "|---|---|---|---|"]
    for check in report["checks"]:
        detail = json.dumps(check["detail"]) if not isinstance(check["detail"], str) else check["detail"]
        lines.append(f"| {check['id']} | {check['check']} | {check['status']} | {detail[:200]} |")
    obs = report["observations"]
    lines += ["", "## Observations (not failures)", "",
              f"* Keys with spill evidence: {len(obs['spill_keys'])}",
              f"* Keys with median >= 1 s: {len(obs['keys_at_least_1s'])}",
              f"* Keys with median >= 10 s: {len(obs['keys_at_least_10s'])}",
              f"* Right-censored keys (timeout {report['protocol']['statement_timeout_ms']} ms): {len(obs['censored_keys'])}",
              "", "If a count is zero, the poster must say so; nothing is relabelled."]
    return "\n".join(lines) + "\n"
