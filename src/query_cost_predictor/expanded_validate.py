"""Validate the expanded release, project the full-run budget, summarize it and freeze it.

* :func:`validate_expanded` (scope ``sample`` or ``full``): integrity checks
  (manifest hash, raw checksums, database = raw, byte-identical rebuild,
  label recomputation, censoring and failure rules, settings, contract). The
  ``sample`` scope also writes the budget projection. The ``full`` scope
  evaluates the release gates; an unmet gate yields status ``STOP`` with the
  reason, never a relabelled or padded dataset.
* :func:`project_budget` (pure): projected time and disk for the remaining
  keys from the measured sample, next to the worst-case bound.
* :func:`evaluate_gates` (pure): usable keys, group diversity, concentration
  and positive-class coverage at the frozen 1 s and 10 s thresholds.
* :func:`eda_summary`: descriptive tables that separate scale, work_mem, index
  availability and selectivity regime (paired ratios within query instances).
* :func:`freeze_release`: immutable release record after a full PASS.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from query_cost_predictor.contract import leakage_problems
from query_cost_predictor.errors import EvidenceIntegrityError
from query_cost_predictor.expanded_derive import (
    OUTPUT_FILES,
    THRESHOLD_COLUMNS,
    build_expanded_tables,
    canonical_label_view,
    latest_expanded_derived_dir,
)
from query_cost_predictor.expanded_manifest import KIND, MANIFEST_KIND, report_path, sample_stratum
from query_cost_predictor.hashing import sha256_file, sha256_json
from query_cost_predictor.labels import HR_POSITIVE, STATUS_CENSORED, STATUS_COMPLETE, STATUS_FAILED
from query_cost_predictor.paths import data_root, ensure_dir, expanded_reports_dir
from query_cost_predictor.smoke_derive import FEATURE_COLUMNS, load_ledger_for
from query_cost_predictor.smoke_manifest import current_manifest_id, load_manifest

SCOPES = ("sample", "full")
USABLE = (STATUS_COMPLETE, STATUS_CENSORED)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _float(value: Any) -> float | None:
    return None if value in ("", None) else float(value)


# ----------------------------------------------------------------------------------
# Pure: gates
# ----------------------------------------------------------------------------------
def evaluate_gates(labels: list[dict[str, Any]], gates: dict[str, Any], snapshot_names: list[str]) -> list[dict[str, Any]]:
    """Release gates over derived label rows. Each result is PASS or STOP with the reason."""
    results: list[dict[str, Any]] = []

    def add(gate_id: str, name: str, ok: bool, detail: Any) -> None:
        results.append({"id": gate_id, "gate": name, "status": "PASS" if ok else "STOP", "detail": detail})

    usable = [r for r in labels if r["status"] in USABLE]
    development = [r for r in usable if r["split_role"] == "development"]
    unfinished = [r for r in labels if r["observation_status"] in ("incomplete", "not_attempted")]
    add("G-01", "collection finished (no incomplete or not-attempted keys)", not unfinished,
        {"unfinished_keys": len(unfinished)} if unfinished else "all keys determined")
    add("G-02", f"usable keys (complete + right-censored) >= {gates['min_usable_keys']}",
        len(usable) >= gates["min_usable_keys"], {"usable_keys": len(usable)})
    add("G-03", f"development usable keys >= {gates['min_development_usable_keys']}",
        len(development) >= gates["min_development_usable_keys"], {"development_usable_keys": len(development)})
    snapshots_per_group: dict[str, set[str]] = defaultdict(set)
    for r in development:
        snapshots_per_group[r["semantic_group_id"]].add(r["snapshot_name"])
    complete_groups = sorted(g for g, s in snapshots_per_group.items() if s >= set(snapshot_names))
    add("G-04", f"development groups with usable keys on every snapshot >= {gates['min_groups_usable_on_all_snapshots']}",
        len(complete_groups) >= gates["min_groups_usable_on_all_snapshots"], {"groups": len(complete_groups)})
    if usable:
        template_share = max(Counter(r["template_id"] for r in usable).values()) / len(usable)
        family_share = max(Counter(r["structure_family"] for r in usable).values()) / len(usable)
    else:
        template_share = family_share = 1.0
    add("G-05", f"no template above {gates['max_template_share']:.0%} of usable keys",
        template_share <= gates["max_template_share"], {"max_template_share": round(template_share, 4)})
    add("G-06", f"no structure family above {gates['max_structure_family_share']:.0%} of usable keys",
        family_share <= gates["max_structure_family_share"], {"max_structure_family_share": round(family_share, 4)})
    positive_gate = gates["positives"]
    for gate_id, (threshold, column) in zip(("G-07", "G-08"), sorted(THRESHOLD_COLUMNS.items())):
        positives = [r for r in development if r[column] == HR_POSITIVE]
        groups = {r["semantic_group_id"] for r in positives}
        components = {r["component"] for r in positives}
        ok = (len(positives) >= positive_gate["min_keys"] and len(groups) >= positive_gate["min_groups"]
              and len(components) >= positive_gate["min_components"])
        detail = {"positive_keys": len(positives), "positive_groups": len(groups), "positive_components": sorted(components)}
        if not ok:
            detail["consequence"] = (f"the >= {threshold / 1000:g} s classifier is NOT_YET_EVALUABLE: do not train, "
                                     "report or claim it; thresholds stay frozen")
        add(gate_id, f"high-runtime positives at {threshold / 1000:g} s: >= {positive_gate['min_keys']} keys, "
                     f">= {positive_gate['min_groups']} groups, >= {positive_gate['min_components']} components", ok, detail)
    return results


# ----------------------------------------------------------------------------------
# Pure: budget projection
# ----------------------------------------------------------------------------------
def project_budget(manifest: dict[str, Any], records: list[dict[str, Any]], key_states: dict[str, str],
                   raw_bytes: int) -> dict[str, Any]:
    """Projected wall time and disk for the keys that are not yet determined.

    Per-key wall time = sum of client-observed EXPLAIN ANALYZE round trips of
    its probe, measured runs and resume warm-ups, plus a per-key overhead
    (session wall time not spent in executions). Strata follow the sample
    design; a stratum without measured keys falls back to its component mean
    and is flagged. Upper projection uses each stratum's 90th percentile.
    """
    entries = {e["modeling_key"]: e for e in manifest["entries"]}
    protocol = manifest["protocol"]
    n = int(protocol["measured_repetitions"])
    timeout_s = int(protocol["statement_timeout_ms"]) / 1000.0
    exec_ms: dict[str, float] = defaultdict(float)
    sessions: dict[str, dict[str, Any]] = defaultdict(lambda: {"start": None, "end": None, "exec_ms": 0.0, "keys": set()})
    timeouts: Counter = Counter()
    runs: Counter = Counter()
    for record in records:
        kind = record["record_type"]
        session = sessions[record["session_id"]]
        stamp = datetime.fromisoformat(record["recorded_at"])
        session["start"] = stamp if session["start"] is None or stamp < session["start"] else session["start"]
        session["end"] = stamp if session["end"] is None or stamp > session["end"] else session["end"]
        if kind in ("execution", "resume_warmup"):
            payload = record["payload"]
            elapsed = float(payload.get("client_elapsed_ms") or 0.0)
            exec_ms[payload["modeling_key"]] += elapsed
            session["exec_ms"] += elapsed
            session["keys"].add(payload["modeling_key"])
            if kind == "execution":
                runs[payload["modeling_key"]] += 1
                timeouts[payload["modeling_key"]] += payload["status"] == "timeout"
    wall_ms = sum((s["end"] - s["start"]).total_seconds() * 1000.0 for s in sessions.values() if s["start"])
    total_exec = sum(s["exec_ms"] for s in sessions.values())
    keys_touched = len({k for s in sessions.values() for k in s["keys"]})
    overhead_ms = max(0.0, wall_ms - total_exec) / keys_touched if keys_touched else 0.0

    determined = {k for k, state in key_states.items() if state not in ("not_started", "incomplete")}
    per_stratum: dict[tuple[str, str, str, str], list[float]] = defaultdict(list)
    per_component: dict[str, list[float]] = defaultdict(list)
    for key in determined:
        if key in entries:
            value = exec_ms.get(key, 0.0) + overhead_ms
            per_stratum[sample_stratum(entries[key])].append(value)
            per_component[entries[key]["component"]].append(value)
    remaining = [k for k in entries if k not in determined]
    remaining_by_stratum = Counter(sample_stratum(entries[k]) for k in remaining)

    def p90(values: list[float]) -> float:
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))]

    rows, expected_ms, upper_ms, fallback_strata = [], 0.0, 0.0, []
    for stratum, count in sorted(remaining_by_stratum.items()):
        values = per_stratum.get(stratum) or per_component.get(stratum[0]) or []
        if not per_stratum.get(stratum):
            fallback_strata.append("/".join(stratum))
        mean = statistics.fmean(values) if values else timeout_s * 1000.0 * (n + 1)
        upper = max(p90(values), mean) if values else timeout_s * 1000.0 * (n + 1)
        expected_ms += mean * count
        upper_ms += upper * count
        rows.append({"component": stratum[0], "snapshot_name": stratum[1], "configuration_name": stratum[2],
                     "regime": stratum[3], "remaining_keys": count, "measured_keys": len(per_stratum.get(stratum, [])),
                     "mean_seconds_per_key": round(mean / 1000.0, 3), "p90_seconds_per_key": round(upper / 1000.0, 3)})
    measured_keys = len(determined)
    bytes_per_key = raw_bytes / measured_keys if measured_keys else 0.0
    sample_runs = sum(runs[k] for k in determined)
    return {
        "measured_keys": measured_keys,
        "remaining_keys": len(remaining),
        "overhead_seconds_per_key": round(overhead_ms / 1000.0, 3),
        "timeout_share_of_runs_so_far": round(sum(timeouts[k] for k in determined) / sample_runs, 4) if sample_runs else None,
        "expected_hours_remaining": round(expected_ms / 3.6e6, 2),
        "upper_hours_remaining": round(upper_ms / 3.6e6, 2),
        "worst_case_hours_remaining": round(len(remaining) * (n + 1) * timeout_s / 3600.0, 1),
        "raw_bytes_per_key": round(bytes_per_key),
        "projected_raw_gb_total": round(bytes_per_key * len(entries) / 1024 ** 3, 3),
        "projected_evidence_db_gb_total_assumption": round(2 * bytes_per_key * len(entries) / 1024 ** 3, 3),
        "strata_without_measurements": fallback_strata,
        "by_stratum": rows,
        "assumptions": [
            "per-key time = probe + measured runs + resume warm-ups (client round trips) + mean session overhead",
            "strata are component x snapshot x configuration (x regime for generated groups)",
            "a stratum without measured keys uses its component mean (listed above)",
            "the evidence database is assumed to need about twice the raw JSONL volume (plans stored as jsonb)",
            "worst case: every remaining run hits the statement timeout (probe + all measured runs)",
        ],
    }


# ----------------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------------
def validate_expanded(manifest_id: str | None, *, scope: str, use_db: bool = True) -> dict[str, Any]:
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    mid = current_manifest_id(manifest_id, KIND)
    manifest, manifest_path = load_manifest(mid, KIND)
    if manifest.get("manifest_kind") != MANIFEST_KIND:
        raise EvidenceIntegrityError("not an expanded-release manifest")
    derived = latest_expanded_derived_dir(mid)
    checks: list[dict[str, Any]] = []

    def add(check_id: str, name: str, status: str, detail: Any) -> None:
        checks.append({"id": check_id, "check": name, "status": status, "detail": detail})

    add("V-01", "manifest file hashes to its manifest_id", "PASS", str(manifest_path))
    ledger, records, files = load_ledger_for(manifest["protocol"]["protocol_version"], KIND)
    add("V-02", "raw JSONL record checksums", "PASS", f"{len(records)} records in {len(files)} file(s) verified")
    derived_manifest = json.loads((derived / "derived_manifest.json").read_text(encoding="utf-8"))
    bad = [n for n, sha in derived_manifest["outputs"].items() if sha256_file(derived / n) != sha]
    add("V-03", "derived files match derived_manifest.json", "FAIL" if bad else "PASS", bad or "all match")
    current_inputs = {"manifest": {"manifest_id": mid, "sha256": sha256_file(manifest_path)},
                      "raw_files": [{"name": p.name, "sha256": sha256_file(p)} for p in files]}
    stale = current_inputs != derived_manifest["inputs"]
    add("V-04", "derived files were built from the current raw evidence", "FAIL" if stale else "PASS",
        "raw evidence changed after derive; re-run -Step Derive" if stale else "inputs identical")

    tables = build_expanded_tables(manifest, ledger)
    recomputed = {row["modeling_key"]: row for row in tables["labels"]}
    labels = _read_csv(derived / "labels.csv")
    mismatched = []
    for row in labels:
        stored = {k: row.get(k) for k in ("status", "hr_1s_class", "hr_10s_class", "failure_class", "observation_status")}
        stored["median_ms"] = _float(row["median_ms"])
        stored["censor_lower_bound_ms"] = _float(row["censor_lower_bound_ms"])
        if canonical_label_view(stored) != canonical_label_view(recomputed[row["modeling_key"]]):
            mismatched.append(row["modeling_key"])
    add("V-05", "labels and classes recompute identically from raw evidence", "FAIL" if mismatched else "PASS",
        mismatched[:5] or "identical")

    all_keys = [e["modeling_key"] for e in manifest["entries"]]
    if use_db:
        from query_cost_predictor.db import connect_evidence_writer
        from query_cost_predictor.evidence_store import registered_raw_files, verify_database_matches_ledger

        try:
            with connect_evidence_writer("qcp:expanded-validate") as ev:
                verify_database_matches_ledger(ev, ledger, all_keys)
                registered = registered_raw_files(ev, sorted({r["session_id"] for r in records}))
                unregistered = [p.name for p in files if str(p) not in registered]
                changed = [p.name for p in files if str(p) in registered and registered[str(p)] != sha256_file(p)]
            add("V-06", "evidence database matches raw JSONL (executions and estimates)", "PASS", "identical")
            add("V-07", "raw files registered with matching SHA-256", "FAIL" if changed else ("WARN" if unregistered else "PASS"),
                {"changed": changed, "unregistered": unregistered})
        except EvidenceIntegrityError as exc:
            add("V-06", "evidence database matches raw JSONL", "FAIL", str(exc))
    else:
        add("V-06", "evidence database matches raw JSONL", "NOT_CHECKED", "--no-db")

    scope_keys = set(manifest["sample"]["keys"]) if scope == "sample" else set(all_keys)
    scoped = [r for r in tables["labels"] if r["modeling_key"] in scope_keys]
    unfinished = [r["modeling_key"] for r in scoped if r["observation_status"] in ("incomplete", "not_attempted")]
    add("V-08", f"every {scope} key attempted and its label determined",
        ("FAIL" if scope == "sample" else "WARN") if unfinished else "PASS",
        {"keys_in_scope": len(scoped), "undetermined": len(unfinished)})
    missing_estimates = [r["modeling_key"] for r in scoped
                         if r["modeling_key"] not in ledger.estimates and r["observation_status"] != "not_attempted"]
    add("V-09", "attempted keys have a plain pre-execution EXPLAIN", "WARN" if missing_estimates else "PASS",
        missing_estimates[:5] or "all present")
    executions = _read_csv(derived / "executions.csv")
    bad_runtime = [r["modeling_key"] for r in executions
                   if (r["status"] == "completed" and (_float(r["execution_time_ms"]) is None or _float(r["execution_time_ms"]) < 0))
                   or (r["status"] != "completed" and r["execution_time_ms"] != "")]
    add("V-10", "completed runs carry a runtime; timeouts and errors carry NULL (never zero, never the timeout)",
        "FAIL" if bad_runtime else "PASS", bad_runtime[:5] or "consistent")
    settings_by_config: dict[str, set[str]] = defaultdict(set)
    config_of = {e["modeling_key"]: e["configuration_name"] for e in manifest["entries"]}
    for row in executions:
        if row["status"] == "completed":
            settings_by_config[config_of[row["modeling_key"]]].add(row["settings_json"])
    settings_problems = [f"{name}: {len(blocks)} different EXPLAIN SETTINGS blocks"
                         for name, blocks in settings_by_config.items() if len(blocks) != 1]
    add("V-11", "EXPLAIN SETTINGS consistent within each configuration", "FAIL" if settings_problems else "PASS",
        settings_problems or {k: len(v) for k, v in settings_by_config.items()})
    shapes: dict[str, set[str]] = defaultdict(set)
    for row in executions:
        if row["plan_shape_sha256"]:
            shapes[row["modeling_key"]].add(row["plan_shape_sha256"])
    changed_plans = [k for k, s in shapes.items() if len(s) > 1]
    add("V-12", "plan shape stable across probe and measured runs", "WARN" if changed_plans else "PASS",
        {"keys_with_plan_changes": len(changed_plans)})
    timeout_ms = float(manifest["protocol"]["statement_timeout_ms"])
    bad_censor = [r["modeling_key"] for r in scoped if r["status"] == STATUS_CENSORED and
                  (r["median_ms"] is not None or r["exec_censor_lower_bound_ms"] is None
                   or not 0 <= r["exec_censor_lower_bound_ms"] < timeout_ms or not r["censor_bound_basis"])]
    add("V-13", "right-censored keys have no median and an execution-time bound below the timeout",
        "FAIL" if bad_censor else "PASS", bad_censor[:5] or "consistent")
    bad_failures = [r["modeling_key"] for r in scoped if r["status"] == STATUS_FAILED and
                    (r["hr_1s_class"] != "unlabeled" or r["hr_10s_class"] != "unlabeled" or r["failure_class"] == "none")]
    add("V-14", "failed keys are classified and never counted as high-runtime positives",
        "FAIL" if bad_failures else "PASS", bad_failures[:5] or "consistent")
    feature_problems = leakage_problems(list(FEATURE_COLUMNS))
    add("V-15", "feature columns pass the availability contract (A-D only; no runtime, rows or buffers)",
        "FAIL" if feature_problems else "PASS", feature_problems or f"{len(FEATURE_COLUMNS)} A-D features")
    thresholds = derived_manifest.get("frozen_high_runtime_thresholds_ms")
    add("V-16", "high-runtime thresholds frozen at 1 s and 10 s", "PASS" if thresholds == [1000.0, 10000.0] else "FAIL",
        thresholds)
    sql_errors = [r["modeling_key"] for r in scoped if r["failure_class"] == "sql_error"]
    gates = manifest["release"]["gates"]
    if scope == "sample":
        add("V-17", f"SQL errors in the sample <= {gates['max_sql_error_keys_in_sample']}",
            "FAIL" if len(sql_errors) > gates["max_sql_error_keys_in_sample"] else "PASS", sql_errors[:5] or 0)
        failed_share = sum(1 for r in scoped if r["status"] == STATUS_FAILED) / len(scoped) if scoped else 1.0
        add("V-18", f"failed share of sample keys <= {gates['max_failed_share_in_sample']:.0%}",
            "FAIL" if failed_share > gates["max_failed_share_in_sample"] else "PASS", round(failed_share, 4))
    else:
        add("V-17", "SQL errors in the release", "WARN" if sql_errors else "PASS", {"keys": len(sql_errors)})

    observations = {
        "observation_status": dict(Counter(r["observation_status"] for r in scoped)),
        "failure_classes": dict(Counter(r["failure_class"] for r in scoped if r["status"] == STATUS_FAILED)),
        "right_censored_keys": sum(1 for r in scoped if r["status"] == STATUS_CENSORED),
        **{f"{col}_counts": dict(Counter(r[col] for r in scoped)) for col in THRESHOLD_COLUMNS.values()},
        "spill_keys": len({r["modeling_key"] for r in executions if r["run_purpose"] == "measured"
                           and r["spill_detected"] == "true" and r["modeling_key"] in scope_keys}),
    }
    gate_results: list[dict[str, Any]] = []
    if scope == "full":
        gate_results = evaluate_gates(tables["labels"], gates, sorted(manifest["snapshots"]))
    integrity_failed = any(c["status"] == "FAIL" for c in checks)
    status = "FAIL" if integrity_failed else ("STOP" if any(g["status"] == "STOP" for g in gate_results) else "PASS")

    report: dict[str, Any] = {
        "evidence_status": "NEW_EXPANDED_RELEASE" if scope == "full" else "NEW_EXPANDED_SAMPLE",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "scope": scope,
        "manifest_id": mid,
        "derived_dir": str(derived),
        "derived_files_sha256": {name: sha256_file(derived / name) for name in OUTPUT_FILES},
        "counts": derived_manifest["counts"],
        "checks": checks,
        "gates": gate_results,
        "observations": observations,
        "raw_files": [{"name": p.name, "sha256": sha256_file(p), "bytes": p.stat().st_size} for p in files],
        "note": ("Sample validation decides whether the full collection may be approved; it is not a release."
                 if scope == "sample" else
                 "A STOP means a stated gate is not met; the release is not frozen and nothing is relabelled."),
    }
    out = ensure_dir(expanded_reports_dir())
    name = f"validation_{scope}"
    report_path(name, mid).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report_path(name, mid, "md").write_text(_markdown(report), encoding="utf-8")

    if scope == "sample" and status == "PASS":
        key_states = {row["modeling_key"]: row["key_state"] for row in tables["keys"]}
        projection = project_budget(manifest, records, key_states, sum(p.stat().st_size for p in files))
        budget_report = {"status": "PROJECTED", "manifest_id": mid,
                         "created_at_utc": report["created_at_utc"], "projection": projection,
                         "free_gb_under_data_root": round(_free_gb(), 1)}
        report_path("budget_projection", mid).write_text(json.dumps(budget_report, indent=2), encoding="utf-8")
        report_path("budget_projection", mid, "md").write_text(_budget_markdown(budget_report), encoding="utf-8")
    if scope == "full" and status == "PASS" and use_db:
        _mark_release(mid, derived_manifest, "validated")
    return {"status": status, "summary": {
        "status": status, "scope": scope, "report": str(report_path(name, mid, "md")),
        "fails": [c["id"] for c in checks if c["status"] == "FAIL"],
        "warnings": [c["id"] for c in checks if c["status"] == "WARN"],
        "stops": [g["id"] for g in gate_results if g["status"] == "STOP"],
        "observations": observations,
        "budget_projection": str(report_path("budget_projection", mid, "md")) if scope == "sample" and status == "PASS" else None,
    }}


def _free_gb() -> float:
    import shutil

    return shutil.disk_usage(data_root()).free / 1024 ** 3


def _mark_release(mid: str, derived_manifest: dict[str, Any], status: str) -> None:
    from query_cost_predictor.db import connect_evidence_writer
    from query_cost_predictor import EXPANDED_LABEL_VERSION

    inputs_sha = sha256_json(derived_manifest["inputs"])
    release_id = f"expanded:{mid[3:19]}:{EXPANDED_LABEL_VERSION}:{inputs_sha[:16]}"
    with connect_evidence_writer("qcp:expanded-validate") as ev:
        ev.execute("UPDATE qcp.dataset_release SET status = %s, updated_at = now() WHERE release_id = %s",
                   (status, release_id))
        ev.commit()


def _markdown(report: dict[str, Any]) -> str:
    lines = [f"# Expanded release validation ({report['scope']})", "",
             f"Overall **{report['status']}** - generated {report['created_at_utc']} - manifest `{report['manifest_id']}`",
             f"Derived folder `{report['derived_dir']}`", "", report["note"], "", "## Counts", "",
             "```json", json.dumps(report["counts"], indent=2), "```", "", "## Integrity checks", "",
             "| ID | Check | Status | Detail |", "|---|---|---|---|"]
    for c in report["checks"]:
        detail = c["detail"] if isinstance(c["detail"], str) else json.dumps(c["detail"], default=str)
        lines.append(f"| {c['id']} | {c['check']} | {c['status']} | {detail[:200]} |")
    if report["gates"]:
        lines += ["", "## Release gates", "", "| ID | Gate | Status | Detail |", "|---|---|---|---|"]
        for g in report["gates"]:
            lines.append(f"| {g['id']} | {g['gate']} | {g['status']} | {json.dumps(g['detail'], default=str)[:300]} |")
    lines += ["", "## Observations (not failures)", "", "```json",
              json.dumps(report["observations"], indent=2, default=str), "```"]
    return "\n".join(lines) + "\n"


def _budget_markdown(budget: dict[str, Any]) -> str:
    p = budget["projection"]
    lines = ["# Expanded release budget projection", "",
             f"Projected from {p['measured_keys']} measured key(s); {p['remaining_keys']} key(s) remain.", "",
             "| Quantity | Value |", "|---|---:|",
             f"| expected hours remaining | {p['expected_hours_remaining']} |",
             f"| upper (p90-based) hours remaining | {p['upper_hours_remaining']} |",
             f"| worst case hours remaining (every run times out) | {p['worst_case_hours_remaining']} |",
             f"| timeout share of runs so far | {p['timeout_share_of_runs_so_far']} |",
             f"| raw bytes per key | {p['raw_bytes_per_key']} |",
             f"| projected raw evidence (GB, whole release) | {p['projected_raw_gb_total']} |",
             f"| assumed evidence-db growth (GB) | {p['projected_evidence_db_gb_total_assumption']} |",
             f"| free GB under QCP_DATA_ROOT now | {budget['free_gb_under_data_root']} |", "",
             "Strata without measurements (component mean used): " + (", ".join(p["strata_without_measurements"]) or "none"),
             "", "Assumptions:", *[f"* {a}" for a in p["assumptions"]], "",
             "| Component | Snapshot | Configuration | Regime | Remaining | Measured | Mean s/key | p90 s/key |",
             "|---|---|---|---|---:|---:|---:|---:|"]
    for row in p["by_stratum"]:
        lines.append(f"| {row['component']} | {row['snapshot_name']} | {row['configuration_name']} | {row['regime']} | "
                     f"{row['remaining_keys']} | {row['measured_keys']} | {row['mean_seconds_per_key']} | {row['p90_seconds_per_key']} |")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------------
# EDA summaries (descriptive; no model is trained)
# ----------------------------------------------------------------------------------
def _paired_ratios(labels: list[dict[str, str]], vary: str, low: str, high: str,
                   hold: tuple[str, ...]) -> dict[str, Any]:
    """Median of high/low median-runtime ratios for keys equal in every ``hold`` column (complete keys only)."""
    cells: dict[tuple[str, ...], dict[str, float]] = defaultdict(dict)
    for row in labels:
        if row["status"] == STATUS_COMPLETE and row[vary] in (low, high) and _float(row["median_ms"]):
            cells[tuple(row[h] for h in hold)][row[vary]] = float(row["median_ms"])
    ratios = [c[high] / c[low] for c in cells.values() if low in c and high in c and c[low] > 0]
    return {"pairs": len(ratios), "median_ratio": statistics.median(ratios) if ratios else None,
            "share_above_1": round(sum(r > 1 for r in ratios) / len(ratios), 4) if ratios else None,
            "definition": f"{vary}: {high} / {low}, holding {', '.join(hold)} fixed; complete keys only"}


def eda_summary(manifest_id: str | None) -> dict[str, Any]:
    from query_cost_predictor.metrics import rank_correlations

    mid = current_manifest_id(manifest_id, KIND)
    derived = latest_expanded_derived_dir(mid)
    labels = _read_csv(derived / "labels.csv")
    estimates = {r["modeling_key"]: r for r in _read_csv(derived / "estimates.csv")}
    executions = _read_csv(derived / "executions.csv")

    status_table = Counter((r["component"], r["snapshot_name"], r["configuration_name"], r["observation_status"]) for r in labels)
    runtime_table = []
    groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for r in labels:
        if r["status"] == STATUS_COMPLETE:
            groups[(r["component"], r["snapshot_name"], r["configuration_name"])].append(float(r["median_ms"]))
    for key, values in sorted(groups.items()):
        ordered = sorted(values)
        runtime_table.append({"component": key[0], "snapshot_name": key[1], "configuration_name": key[2],
                              "complete_keys": len(ordered), "median_ms_p50": statistics.median(ordered),
                              "median_ms_p90": ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))],
                              "median_ms_max": ordered[-1]})
    positives = {col: dict(Counter(r["component"] for r in labels if r[col] == HR_POSITIVE))
                 for col in THRESHOLD_COLUMNS.values()}
    config_of = {r["modeling_key"]: r["configuration_name"] for r in labels}
    spill_keys = {r["modeling_key"] for r in executions if r["run_purpose"] == "measured" and r["spill_detected"] == "true"}
    by_config_spill = Counter(config_of[k] for k in spill_keys if k in config_of)
    by_component_snapshot: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for r in labels:
        if r["status"] == STATUS_COMPLETE and r["modeling_key"] in estimates:
            by_component_snapshot[(r["component"], r["snapshot_name"])].append(r)
    rank_rows = []
    for (component, snapshot), rows in sorted(by_component_snapshot.items()):
        if len(rows) >= 5:
            rho = rank_correlations([float(estimates[r["modeling_key"]]["est_total_cost"]) for r in rows],
                                    [float(r["median_ms"]) for r in rows])["spearman"]
            rank_rows.append({"component": component, "snapshot_name": snapshot, "complete_keys": len(rows),
                              "spearman_planner_cost_vs_runtime": None if rho != rho else round(rho, 4)})
    factors = {
        "work_mem_C2_vs_C1": _paired_ratios(labels, "configuration_name", "C1_baseline", "C2_reduced_work_mem",
                                            ("query_instance_id", "snapshot_name")),
        "scale_SF1_vs_SF0_1": _paired_ratios(labels, "snapshot_name", "tpch_sf0_1", "tpch_sf1",
                                             ("query_instance_id", "configuration_name")),
        "unindexed_vs_indexed_predicate": _paired_ratios(labels, "index_variant", "indexed_predicate",
                                                         "unindexed_predicate",
                                                         ("semantic_group_id", "parameter_regime", "snapshot_name",
                                                          "configuration_name")),
    }
    regimes = defaultdict(list)
    for r in labels:
        if r["component"] == "structural" and r["status"] == STATUS_COMPLETE:
            regimes[(r["parameter_regime"], r["snapshot_name"])].append(float(r["median_ms"]))
    regime_table = [{"regime": k[0], "snapshot_name": k[1], "complete_keys": len(v), "median_ms_p50": statistics.median(v)}
                    for k, v in sorted(regimes.items())]
    summary = {
        "evidence_status": "NEW_EXPANDED_RELEASE",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manifest_id": mid,
        "derived_dir": str(derived),
        "note": "Descriptive summaries only. Ratios are within matched keys; planner costs are rank-compared only "
                "(planner units are not milliseconds). No model was trained.",
        "status_by_component_snapshot_configuration": [
            {"component": k[0], "snapshot_name": k[1], "configuration_name": k[2], "observation_status": k[3], "keys": n}
            for k, n in sorted(status_table.items())],
        "runtime_by_component_snapshot_configuration": runtime_table,
        "runtime_by_structural_regime": regime_table,
        "high_runtime_positives_by_component": positives,
        "spill_keys_by_configuration": dict(by_config_spill),
        "planner_cost_rank_agreement": rank_rows,
        "factor_separation": factors,
    }
    out_dir = ensure_dir(expanded_reports_dir())
    report_path("eda_summary", mid).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    lines = ["# Expanded release EDA summary (descriptive)", "", summary["note"], "",
             "## Factor separation (matched pairs, complete keys)", "", "| Factor | Pairs | Median ratio | Share > 1 |",
             "|---|---:|---:|---:|"]
    for name, item in factors.items():
        lines.append(f"| {name} | {item['pairs']} | {item['median_ratio']} | {item['share_above_1']} |")
    lines += ["", "## High-runtime positives by component (frozen thresholds)", "", "```json",
              json.dumps(positives, indent=2), "```", "", "## Runtime by component, snapshot and configuration", "",
              "| Component | Snapshot | Configuration | Complete keys | p50 of medians (ms) | p90 (ms) | max (ms) |",
              "|---|---|---|---:|---:|---:|---:|"]
    for r in runtime_table:
        lines.append(f"| {r['component']} | {r['snapshot_name']} | {r['configuration_name']} | {r['complete_keys']} | "
                     f"{r['median_ms_p50']:.3f} | {r['median_ms_p90']:.3f} | {r['median_ms_max']:.3f} |")
    lines += ["", "## Planner cost rank agreement (Spearman; planner units vs ms)", "",
              "| Component | Snapshot | Complete keys | Spearman |", "|---|---|---:|---:|"]
    lines += [f"| {r['component']} | {r['snapshot_name']} | {r['complete_keys']} | {r['spearman_planner_cost_vs_runtime']} |"
              for r in rank_rows]
    report_path("eda_summary", mid, "md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"status": "COMPLETED", "summary": {"report": str(report_path("eda_summary", mid, "md")),
                                               "output_dir": str(out_dir)}}


# ----------------------------------------------------------------------------------
# Freeze
# ----------------------------------------------------------------------------------
def freeze_release(manifest_id: str | None) -> dict[str, Any]:
    mid = current_manifest_id(manifest_id, KIND)
    manifest, manifest_path = load_manifest(mid, KIND)
    derived = latest_expanded_derived_dir(mid)
    validation_file = report_path("validation_full", mid)
    if not validation_file.is_file():
        return {"status": "REFUSED", "summary": {"status": "REFUSED", "reason": "no full validation report"}}
    validation = json.loads(validation_file.read_text(encoding="utf-8"))
    current = {name: sha256_file(derived / name) for name in OUTPUT_FILES}
    if validation.get("status") != "PASS":
        return {"status": "REFUSED", "summary": {"status": "REFUSED",
                                                 "reason": f"full validation status is {validation.get('status')}",
                                                 "stops": [g["id"] for g in validation.get("gates", []) if g["status"] == "STOP"]}}
    if validation.get("derived_dir") != str(derived) or validation.get("derived_files_sha256") != current:
        return {"status": "REFUSED", "summary": {"status": "REFUSED",
                                                 "reason": "derived files changed after full validation; re-run Validate"}}
    derived_manifest = json.loads((derived / "derived_manifest.json").read_text(encoding="utf-8"))
    record = {
        "release_name": manifest["release"]["release_name"],
        "manifest_id": mid,
        "manifest_sha256": sha256_file(manifest_path),
        "raw_files": derived_manifest["inputs"]["raw_files"],
        "derived_dir": str(derived),
        "derived_files_sha256": current,
        "validation_report_sha256": sha256_file(validation_file),
        "counts": derived_manifest["counts"],
        "gates": validation["gates"],
        "frozen_high_runtime_thresholds_ms": derived_manifest["frozen_high_runtime_thresholds_ms"],
        "label_version": derived_manifest["label_version"],
        "feature_version": derived_manifest["feature_version"],
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    folder = ensure_dir(data_root() / "releases" / KIND / mid[3:19] / derived.name)
    path = folder / "RELEASE_FROZEN.json"
    if path.exists():
        return {"status": "FROZEN", "summary": {"status": "FROZEN", "note": "already frozen", "record": str(path)}}
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({**record, "record_sha256": sha256_json(record)}, indent=2))
    return {"status": "FROZEN", "summary": {"status": "FROZEN", "record": str(path),
                                            "usable_keys": record["counts"]["usable_keys"]}}
