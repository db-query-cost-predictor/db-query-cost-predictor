"""Expanded-release manifest: grouped splits, bounded sample, registration and plan check.

Pure functions (unit-tested, no database): :func:`assign_splits`,
:func:`select_sample`, :func:`check_expanded_entries`,
:func:`expanded_manifest_document`.

Database work: :func:`build_and_register_expanded_manifest` resolves the two
registered TPC-H snapshots (statistics-drift check), captures the effective
settings of both configurations, registers groups, templates, instances,
modeling keys and the immutable manifest. :func:`plan_check` runs a **plain**
``EXPLAIN`` (no ``ANALYZE``: nothing is executed) for every query instance on
every snapshot so SQL errors are found before any collection.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from query_cost_predictor.db import connect_bench_reader, connect_evidence_writer
from query_cost_predictor.executor import TransientDatabaseError, explain_statement, run_statement
from query_cost_predictor.hashing import sha256_file
from query_cost_predictor.paths import ensure_dir, expanded_reports_dir
from query_cost_predictor.plans import estimate_scalars, operator_list
from query_cost_predictor.release_spec import (
    SPLIT_DEVELOPMENT,
    SPLIT_HELDOUT_RQ2,
    ReleaseSpec,
    annotate_entries,
    load_release_spec,
    release_arithmetic,
)
from query_cost_predictor.safety import validate_read_only_sql
from query_cost_predictor.smoke_manifest import (
    ManifestError,
    _register,
    assign_execution_order,
    capture_configurations,
    current_manifest_id,
    expand_entries,
    load_manifest,
    manifest_dir,
    manifest_id_for,
    resolve_snapshots,
    write_manifest_file,
)
from query_cost_predictor.splits import fold_summary, group_kfold

KIND = "expanded"
MANIFEST_KIND = "expanded_release"
REQUIRED_MIGRATION = "008"
PLAN_CHECK_OPTIONS = "SETTINGS, FORMAT JSON"


def report_path(name: str, manifest_id: str, suffix: str = "json") -> Path:
    """reports/expanded/<name>_<manifest16>.<suffix> (git-ignored)."""
    return expanded_reports_dir() / f"{name}_{manifest_id[3:19]}.{suffix}"


# ----------------------------------------------------------------------------------
# Pure functions
# ----------------------------------------------------------------------------------
def assign_splits(entries: list[dict[str, Any]], n_folds: int, seed: int) -> list[dict[str, Any]]:
    """Assign grouped folds to development keys; RQ2-reserved keys get no fold at all.

    * ``fold_group`` (primary, unseen-structure): whole semantic groups per fold,
      so every parameterization, snapshot, configuration and rewrite form of a
      group is held out together.
    * ``fold_family`` (stricter diagnostic): whole structure families per fold
      (for generated groups: every output shape over one join path).
    * ``fold_seen_template`` (labelled diagnostic only): whole query instances
      per fold, so templates *are* seen in training; never reported as
      unseen-structure performance.
    """
    development = [e for e in entries if e["split_role"] == SPLIT_DEVELOPMENT]
    if not development:
        raise ManifestError("no development keys to split")
    by_group = group_kfold([e["semantic_group_id"] for e in development], n_folds, seed)
    by_family = group_kfold([e["structure_family"] for e in development], n_folds, seed + 1)
    by_instance = group_kfold([e["query_instance_id"] for e in development], n_folds, seed + 2)
    folds = {e["modeling_key"]: (int(a), int(b), int(c))
             for e, a, b, c in zip(development, by_group, by_family, by_instance)}
    out = []
    for entry in entries:
        assigned = folds.get(entry["modeling_key"])
        out.append({**entry,
                    "fold_group": assigned[0] if assigned else None,
                    "fold_family": assigned[1] if assigned else None,
                    "fold_seen_template": assigned[2] if assigned else None})
    return out


def sample_stratum(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    regime = entry["parameter_regime"] if entry["component"] == "structural" else "all"
    return entry["component"], entry["snapshot_name"], entry["configuration_name"], regime


def select_sample(entries: list[dict[str, Any]], per_stratum: int, seed: int) -> list[str]:
    """Deterministic, bounded, stratified sample of development keys.

    Strata: component x snapshot x configuration (x selectivity regime for
    generated groups). Within a stratum keys are ranked by SHA-256 of
    ``seed|modeling_key`` and the first ``per_stratum`` are taken. RQ2-reserved
    keys are never sampled, so sample-based decisions cannot peek at them.
    """
    if per_stratum < 1:
        raise ManifestError("sample.per_stratum must be >= 1")
    strata: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        if entry["split_role"] == SPLIT_DEVELOPMENT:
            strata[sample_stratum(entry)].append(entry)
    chosen: list[str] = []
    for stratum in sorted(strata):
        ranked = sorted(strata[stratum],
                        key=lambda e: hashlib.sha256(f"{seed}|{e['modeling_key']}".encode("utf-8")).hexdigest())
        chosen += [e["modeling_key"] for e in ranked[:per_stratum]]
    return sorted(chosen)


def sample_strata_summary(entries: list[dict[str, Any]], sample_keys: list[str]) -> list[dict[str, Any]]:
    chosen = set(sample_keys)
    totals = Counter(sample_stratum(e) for e in entries)
    picked = Counter(sample_stratum(e) for e in entries if e["modeling_key"] in chosen)
    return [{"component": s[0], "snapshot_name": s[1], "configuration_name": s[2], "regime": s[3],
             "keys_in_manifest": totals[s], "keys_in_sample": picked[s]} for s in sorted(totals)]


def check_expanded_entries(spec: ReleaseSpec, entries: list[dict[str, Any]], sample_keys: list[str]) -> list[str]:
    """Return problems; an empty list means the manifest may be registered."""
    problems: list[str] = []
    keys = Counter(e["modeling_key"] for e in entries)
    duplicates = [k for k, n in keys.items() if n > 1]
    if duplicates:
        problems.append(f"duplicate modeling keys: {duplicates[:3]}")
    if any(n > 1 for n in Counter(e["execution_order"] for e in entries).values()):
        problems.append("duplicate execution_order values")
    if len(entries) < spec.target_min_proposed_keys:
        problems.append(f"{len(entries)} proposed keys < target {spec.target_min_proposed_keys}")
    if len(entries) > spec.protocol.max_keys:
        problems.append(f"{len(entries)} proposed keys > protocol.max_keys {spec.protocol.max_keys}")
    expected_cells = {(s, c) for t in spec.templates for s in t.snapshots for c in t.configurations}
    present_cells = {(e["snapshot_name"], e["configuration_name"]) for e in entries}
    if expected_cells - present_cells:
        problems.append(f"snapshot x configuration cells without keys: {sorted(expected_cells - present_cells)}")

    group_folds: dict[str, set[Any]] = defaultdict(set)
    family_folds: dict[str, set[Any]] = defaultdict(set)
    instance_folds: dict[str, set[Any]] = defaultdict(set)
    for e in entries:
        group_folds[e["semantic_group_id"]].add(e["fold_group"])
        if e["split_role"] == SPLIT_DEVELOPMENT:
            family_folds[e["structure_family"]].add(e["fold_family"])
            instance_folds[e["query_instance_id"]].add(e["fold_seen_template"])
        elif any(e[f] is not None for f in ("fold_group", "fold_family", "fold_seen_template")):
            problems.append(f"RQ2-reserved key {e['modeling_key']} has a development fold")
    leaking = [g for g, folds in group_folds.items() if len(folds) != 1]
    if leaking:
        problems.append(f"semantic groups spread over several folds or roles: {leaking[:3]}")
    if any(len(f) != 1 for f in family_folds.values()):
        problems.append("a structure family is spread over several family folds")
    if any(len(f) != 1 for f in instance_folds.values()):
        problems.append("a query instance is spread over several seen-template folds")
    reserved_roles = {e["semantic_group_id"]: e["split_role"] for e in entries}
    for group, reason in spec.holdout_reasons().items():
        if reserved_roles.get(group) != SPLIT_HELDOUT_RQ2:
            problems.append(f"reserved group {group} ({reason}) is not marked {SPLIT_HELDOUT_RQ2}")

    pair_forms: dict[str, set[str]] = defaultdict(set)
    pair_groups: dict[str, set[str]] = defaultdict(set)
    for e in entries:
        if e["rewrite_pair_id"]:
            pair_forms[e["rewrite_pair_id"]].add(e["form"])
            pair_groups[e["rewrite_pair_id"]].add(e["semantic_group_id"])
    if any(forms != {"original", "reference_rewrite"} for forms in pair_forms.values()):
        problems.append("a rewrite pair lacks its original or its reference-rewrite form")
    if any(len(groups) != 1 for groups in pair_groups.values()):
        problems.append("a rewrite pair spans several semantic groups")

    per_template = Counter(e["template_id"] for e in entries)
    if max(per_template.values()) / len(entries) > spec.gates.max_template_share:
        problems.append("one template exceeds the maximum template share of proposed keys")
    per_family = Counter(e["structure_family"] for e in entries)
    if max(per_family.values()) / len(entries) > spec.gates.max_structure_family_share:
        problems.append("one structure family exceeds the maximum share of proposed keys")
    if len({e["semantic_group_id"] for e in entries}) < spec.gates.min_groups_usable_on_all_snapshots:
        problems.append("fewer proposed semantic groups than the group-diversity gate requires")

    known = set(keys)
    if not sample_keys:
        problems.append("the bounded sample is empty")
    if set(sample_keys) - known:
        problems.append("sample keys outside the manifest")
    reserved_keys = {e["modeling_key"] for e in entries if e["split_role"] == SPLIT_HELDOUT_RQ2}
    if set(sample_keys) & reserved_keys:
        problems.append("the sample contains RQ2-reserved keys")
    return problems


def expanded_manifest_document(spec: ReleaseSpec, entries: list[dict[str, Any]], snapshots_info: dict[str, Any],
                               configurations_info: dict[str, Any], sample_keys: list[str]) -> dict[str, Any]:
    protocol = spec.protocol
    arithmetic = release_arithmetic(spec, entries)
    pairs: dict[str, Any] = {}
    for rt in spec.release_templates:
        if rt.component == "rewrite" and rt.spec.rewrite_pair_id not in pairs:
            pairs[rt.spec.rewrite_pair_id] = {
                "semantic_group_id": rt.spec.semantic_group_id, "family": rt.spec.family,
                "rewrite_source": "MANUAL_REFERENCE_REWRITE", "pair_role": rt.pair_role,
                "equivalence_argument": rt.equivalence_argument,
                "preconditions": [{"name": n, "sql": s} for n, s in rt.preconditions],
                "holdout_reason": rt.holdout_reason, "verification_status": "NOT_YET_VERIFIED",
            }
    return {
        "manifest_name": spec.release_name,
        "manifest_kind": MANIFEST_KIND,
        "note": "Expanded-release manifest. Each entry is a PROPOSED modeling key, not a measured label. "
                "Expected mechanisms are hypotheses; only collected evidence decides runtimes.",
        "release": {
            "release_name": spec.release_name,
            "frozen_high_runtime_thresholds_ms": list(spec.labels.thresholds_ms),
            "censor_margin_ms": spec.labels.censor_margin_ms,
            "planning_fallback_ms": spec.labels.planning_fallback_ms,
            "split_policy": {"n_folds": spec.splits.n_folds, "seed": spec.splits.seed,
                             "primary": "semantic_group", "stricter_diagnostic": "structure_family",
                             "labelled_diagnostic": "query_instance (seen-template)"},
            "sample_policy": {"per_stratum": spec.sample.per_stratum, "seed": spec.sample.seed},
            "gates": {
                "min_usable_keys": spec.gates.min_usable_keys,
                "min_development_usable_keys": spec.gates.min_development_usable_keys,
                "min_groups_usable_on_all_snapshots": spec.gates.min_groups_usable_on_all_snapshots,
                "max_template_share": spec.gates.max_template_share,
                "max_structure_family_share": spec.gates.max_structure_family_share,
                "positives": {"min_keys": spec.gates.positives.min_keys, "min_groups": spec.gates.positives.min_groups,
                              "min_components": spec.gates.positives.min_components},
                "max_sql_error_keys_in_sample": spec.gates.max_sql_error_keys_in_sample,
                "max_failed_share_in_sample": spec.gates.max_failed_share_in_sample,
            },
            "budget": {"min_free_gb_data_root": spec.budget.min_free_gb_data_root,
                       "disk_check_every_n_keys": spec.budget.disk_check_every_n_keys},
        },
        "protocol": {
            "protocol_version": protocol.protocol_version,
            "cache_protocol": protocol.cache_protocol,
            "statement_timeout_ms": protocol.statement_timeout_ms,
            "planned_final_timeout_ms": protocol.planned_final_timeout_ms,
            "lock_timeout_ms": protocol.lock_timeout_ms,
            "warmup_probes": protocol.warmup_probes,
            "measured_repetitions": protocol.measured_repetitions,
            "order_seed": protocol.order_seed,
            "watchdog_grace_ms": protocol.watchdog_grace_ms,
            "max_transient_retries": protocol.max_transient_retries,
            "snapshot_check_every_n_keys": protocol.snapshot_check_every_n_keys,
            "explain_estimate_options": protocol.explain_estimate_options,
            "explain_measured_options": protocol.explain_measured_options,
            "high_runtime_thresholds_ms": list(protocol.high_runtime_thresholds_ms),
            "min_keys": protocol.min_keys,
            "max_keys": protocol.max_keys,
        },
        "config_file": {"path": f"config/{spec.path.name}", "sha256": spec.sha256},
        "snapshots": snapshots_info,
        "configurations": configurations_info,
        "counts": arithmetic["totals"],
        "holdout_groups": spec.holdout_reasons(),
        "rewrite_pairs": pairs,
        "sample": {"keys": sorted(sample_keys), "strata": sample_strata_summary(entries, sample_keys)},
        "entries": sorted(entries, key=lambda e: e["execution_order"]),
    }


# ----------------------------------------------------------------------------------
# Database work
# ----------------------------------------------------------------------------------
def migration_applied(ev: Any, version: str) -> bool:
    row = ev.execute("SELECT 1 FROM qcp.schema_migration WHERE version = %s", (version,)).fetchone()
    return row is not None


def build_and_register_expanded_manifest(spec_path: Path | None = None) -> dict[str, Any]:
    spec = load_release_spec(spec_path)
    app = "qcp:expanded-manifest"
    with connect_evidence_writer(app) as ev:
        if not migration_applied(ev, REQUIRED_MIGRATION):
            raise ManifestError("evidence migration V008 is not applied; run 03_initialize_databases.ps1 (db-init)")
        snapshot_ids, snapshots_info, unavailable = resolve_snapshots(ev, spec, app)
        if unavailable:
            raise ManifestError(f"snapshots not registered: {sorted(unavailable)}")
        config_ids, configurations_info, image_status = capture_configurations(ev, spec, app,
                                                                               include_server_provenance=True)
        entries, skipped = expand_entries(spec, snapshot_ids, config_ids)
        if skipped:
            raise ManifestError("expansion skipped entries; every expanded snapshot is required")
        entries = annotate_entries(spec, entries)
        entries = assign_execution_order(entries, spec.protocol.order_seed)
        entries = assign_splits(entries, spec.splits.n_folds, spec.splits.seed)
        sample_keys = select_sample(entries, spec.sample.per_stratum, spec.sample.seed)
        problems = check_expanded_entries(spec, entries, sample_keys)
        if problems:
            raise ManifestError("; ".join(problems))
        document = expanded_manifest_document(spec, entries, snapshots_info, configurations_info, sample_keys)
        mid = manifest_id_for(document)
        folder = ensure_dir(manifest_dir(KIND))
        path, note = write_manifest_file(folder, mid, document)
        _register(ev, spec, entries, document, mid, sha256_file(path), manifest_name=spec.release_name,
                  source_config=f"config/{spec.path.name}", holdout_reasons=spec.holdout_reasons())
        ev.commit()
    (folder / "CURRENT_MANIFEST.txt").write_text(mid + "\n", encoding="utf-8")

    development = [e for e in entries if e["split_role"] == SPLIT_DEVELOPMENT]
    summary = {
        "manifest_id": mid,
        "manifest_file": str(path),
        "note": note,
        "counts": document["counts"],
        "sample_keys": len(sample_keys),
        "sample_strata": len(document["sample"]["strata"]),
        "folds_by_semantic_group": fold_summary([e["fold_group"] for e in development],
                                                [e["semantic_group_id"] for e in development]),
        "image_capture_status": image_status,
        "next_step": "Inspect the manifest and the arithmetic report, then run 08_expanded_release.ps1 -Step PlanCheck",
    }
    out = ensure_dir(expanded_reports_dir())
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": "PASS",
              "summary": summary, "arithmetic": release_arithmetic(spec, entries)}
    report_path("manifest_summary", mid).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return {"status": "PASS", "manifest_id": mid, "summary": summary,
            "report": str(report_path("manifest_summary", mid))}


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"n": 0, "min": None, "p50": None, "p90": None, "max": None}
    ordered = sorted(values)

    def q(p: float) -> float:
        return ordered[min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))]

    return {"n": len(ordered), "min": ordered[0], "p50": q(0.5), "p90": q(0.9), "max": ordered[-1]}


def plan_check(manifest_id: str | None = None) -> dict[str, Any]:
    """Plain EXPLAIN (estimates only; no query is executed) for every instance on every snapshot."""
    mid = current_manifest_id(manifest_id, KIND)
    manifest, _ = load_manifest(mid, KIND)
    if manifest.get("manifest_kind") != MANIFEST_KIND:
        raise ManifestError("not an expanded-release manifest")
    protocol = manifest["protocol"]
    targets: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in sorted(manifest["entries"], key=lambda e: (e["configuration_name"], e["execution_order"])):
        targets.setdefault((entry["query_instance_id"], entry["snapshot_name"]), entry)
    connections: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    try:
        for (instance, snapshot), entry in sorted(targets.items()):
            row = {"query_instance_id": instance, "snapshot_name": snapshot, "template_id": entry["template_id"],
                   "semantic_group_id": entry["semantic_group_id"], "component": entry["component"],
                   "configuration_name": entry["configuration_name"]}
            try:
                validate_read_only_sql(entry["sql_text"])
            except Exception as exc:  # noqa: BLE001 - reported per instance
                results.append({**row, "status": "FAIL", "reason": f"safety: {exc}"})
                continue
            outcome = None
            for _attempt in range(3):
                conn = connections.get(entry["database"])
                if conn is None or conn.closed or conn.broken:
                    conn = connect_bench_reader(entry["database"], "qcp:expanded-plan-check")
                    connections[entry["database"]] = conn
                try:
                    outcome = run_statement(conn, explain_statement(entry["sql_text"], PLAN_CHECK_OPTIONS),
                                            settings=entry["session_settings"],
                                            statement_timeout_ms=int(protocol["statement_timeout_ms"]),
                                            lock_timeout_ms=int(protocol["lock_timeout_ms"]),
                                            watchdog_grace_ms=int(protocol["watchdog_grace_ms"]))
                    break
                except TransientDatabaseError:
                    connections.pop(entry["database"], None)
            if outcome is None:
                results.append({**row, "status": "FAIL", "reason": "connection failures (3 attempts)"})
            elif outcome.status == "completed" and outcome.document is not None:
                scalars = estimate_scalars(outcome.document)
                results.append({**row, "status": "PASS", "est_total_cost": scalars["est_total_cost"],
                                "est_plan_rows": scalars["est_plan_rows"], "operators": operator_list(outcome.document)})
            else:
                results.append({**row, "status": "FAIL", "reason": f"{outcome.error_class}: {outcome.error_message}",
                                "sqlstate": outcome.sqlstate})
    finally:
        for conn in connections.values():
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    failures = [r for r in results if r["status"] != "PASS"]
    cost_summary = []
    for component, snapshot in sorted({(r["component"], r["snapshot_name"]) for r in results}):
        costs = [r["est_total_cost"] for r in results
                 if r["status"] == "PASS" and r["component"] == component and r["snapshot_name"] == snapshot]
        cost_summary.append({"component": component, "snapshot_name": snapshot,
                             "est_total_cost_planner_units": _quantiles(costs)})
    operators = Counter(op for r in results if r["status"] == "PASS" for op in set(r["operators"]))
    status = "PASS" if not failures else "FAIL"
    report = {
        "evidence_status": "ESTIMATE_ONLY_NOT_A_MEASUREMENT",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "manifest_id": mid,
        "explain_options": PLAN_CHECK_OPTIONS,
        "note": "Plain EXPLAIN only (no ANALYZE): nothing was executed. Planner costs are in planner units, "
                "not milliseconds, and say nothing about measured runtime.",
        "checked": len(results),
        "failures": failures,
        "estimated_cost_by_component_snapshot": cost_summary,
        "plans_containing_operator": dict(sorted(operators.items())),
    }
    ensure_dir(expanded_reports_dir())
    report_path("plan_check", mid).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    lines = ["# Expanded release plan check (plain EXPLAIN; estimates only)", "",
             f"Status **{status}** - {len(results)} instance x snapshot plans, {len(failures)} failure(s).",
             f"Manifest `{mid}`. Nothing was executed; planner costs are not runtimes.", "",
             "| Component | Snapshot | n | min cost | median cost | p90 cost | max cost |", "|---|---|---:|---:|---:|---:|---:|"]
    for item in cost_summary:
        q = item["est_total_cost_planner_units"]
        lines.append(f"| {item['component']} | {item['snapshot_name']} | {q['n']} | {q['min']} | {q['p50']} | {q['p90']} | {q['max']} |")
    if failures:
        lines += ["", "## Failures (fix the template before any collection)", ""]
        lines += [f"* `{f['template_id']}` on {f['snapshot_name']}: {f.get('reason')}" for f in failures[:50]]
    report_path("plan_check", mid, "md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"status": status, "summary": {"status": status, "checked": len(results), "failures": len(failures),
                                          "report": str(report_path("plan_check", mid, "md"))}}
