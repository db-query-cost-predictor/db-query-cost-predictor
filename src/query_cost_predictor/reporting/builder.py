"""Build evidence-backed poster tables and figures (``build-poster-evidence``).

Uses only: ``pilot_audit.json`` (RECOMPUTED_PILOT), the validated smoke evidence
(NEW_POSTER_SMOKE) and the manual rewrite summary (NEW_POSTER_SMOKE). Every
figure panel is drawn from one source and stamped with its provenance. A
figure whose evidence is absent is skipped with the reason recorded; nothing is
ever drawn from placeholder values. Rendered claims are linted; any prohibited
wording fails the build.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from query_cost_predictor.claims import lint_text, load_claim_specs, render_claims, rule_descriptions
from query_cost_predictor.errors import MissingEvidenceError
from query_cost_predictor.hashing import sha256_file
from query_cost_predictor.paths import ensure_dir, refuse_reference_write, reports_dir
from query_cost_predictor.reporting import figures as F
from query_cost_predictor.reporting import pilot_figures as PF
from query_cost_predictor.reporting.loaders import EvidenceBundle, SmokeEvidence, load_all


def _f(value: str | None) -> float | None:
    return None if value in (None, "") else float(value)


# ------------------------------------------------------------------------------ smoke panels
def smoke_provenance(smoke: SmokeEvidence, metric: str) -> F.Provenance:
    labels = smoke.labels
    configs = smoke.validation["configurations"]
    config_text = "; ".join(
        f"{name}: {info['session_settings']}" for name, info in sorted(configs.items())
    )
    scale_factors = sorted({row["scale_factor"] for row in labels})
    return F.Provenance(
        source=str(smoke.derived_dir),
        status="NEW_POSTER_SMOKE",
        n_keys=len(labels),
        n_executions=sum(1 for r in smoke.executions if r["run_purpose"] == "measured"),
        n_groups=len({row["semantic_group_id"] for row in labels}),
        configuration=f"PostgreSQL 16, jit=off, track_io_timing=on, warm cache, timeout "
                      f"{smoke.validation['protocol']['statement_timeout_ms']} ms; {config_text}",
        scale_factor=", ".join(scale_factors),
        created_at_utc=str(smoke.validation["created_at_utc"]),
        metric_definition=metric,
    )


def smoke_runtime_panel(smoke: SmokeEvidence) -> F.RuntimePanel:
    complete = [float(r["median_ms"]) for r in smoke.labels if r["status"] == "complete"]
    censored = [float(r["censor_lower_bound_ms"]) for r in smoke.labels if r["status"] == "right_censored"]
    return F.RuntimePanel("Poster smoke (NEW)", complete, censored,
                          smoke_provenance(smoke, "median of 3 measured runs; censored keys drawn at the timeout"))


def smoke_scatter_panel(smoke: SmokeEvidence) -> F.ScatterPanel:
    cost = {r["modeling_key"]: float(r["est_total_cost"]) for r in smoke.estimates}
    xs, ys, cens = [], [], []
    for row in smoke.labels:
        if row["modeling_key"] not in cost:
            continue
        if row["status"] == "complete":
            xs.append(cost[row["modeling_key"]]); ys.append(float(row["median_ms"])); cens.append(False)
        elif row["status"] == "right_censored":
            xs.append(cost[row["modeling_key"]]); ys.append(float(row["censor_lower_bound_ms"])); cens.append(True)
    complete_pairs = [(x, y) for x, y, c in zip(xs, ys, cens) if not c]
    spearman = None
    if len(complete_pairs) >= 3:
        from query_cost_predictor.metrics import rank_correlations
        spearman = rank_correlations([p[0] for p in complete_pairs], [p[1] for p in complete_pairs])["spearman"]
    return F.ScatterPanel("Poster smoke (NEW)", xs, ys, cens,
                          smoke_provenance(smoke, "x: plain-EXPLAIN root total cost; y: median runtime or censoring bound"),
                          spearman)


def smoke_operator_panel(smoke: SmokeEvidence) -> F.OperatorPanel:
    return F.OperatorPanel("Poster smoke (NEW)", [json.loads(r["operators_json"]) for r in smoke.estimates],
                           smoke_provenance(smoke, "share of keys whose plain plan contains each node type"))


def smoke_group_panel(smoke: SmokeEvidence) -> F.GroupPanel:
    completed: dict[str, list[float]] = defaultdict(list)
    censored: dict[str, list[float]] = defaultdict(list)
    for row in smoke.labels:
        label = f"{row['family']} [{row['configuration_name'].split('_')[0]}, SF {row['scale_factor']}]"
        if row["status"] == "complete":
            completed[label].append(float(row["median_ms"]))
        elif row["status"] == "right_censored":
            censored[label].append(float(row["censor_lower_bound_ms"]))
    return F.GroupPanel("Poster smoke by family", dict(completed), dict(censored),
                        smoke_provenance(smoke, "median of 3 measured runs; triangles = censored at the timeout"))


# ------------------------------------------------------------------------------ values for claims
def evidence_values(bundle: EvidenceBundle) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if bundle.pilot is not None:
        audit = bundle.pilot.audit
        r = audit["recomputed"]
        claims = Counter(c["status"] for c in audit["claim_check"])
        hr = audit["high_runtime_classification"]
        values.update({
            "pilot.instances": r.get("counts.instances_raw"),
            "pilot.templates": r.get("counts.templates"),
            "pilot.raw_runs": r.get("counts.raw_runs"),
            "pilot.runtime_min_ms": r.get("runtime.median_label.min"),
            "pilot.runtime_max_ms": r.get("runtime.median_label.max"),
            "pilot.n_at_least_1s": hr["threshold_1000ms"]["n_positive"],
            "pilot.n_at_least_10s": hr["threshold_10000ms"]["n_positive"],
            "pilot.between_template_share_ms_pct": (100.0 * r["variance.between_template_share_mean_ms"]
                                                    if r.get("variance.between_template_share_mean_ms") is not None else None),
            "pilot.noise_q50": r.get("noise.loo_qerror.p50"),
            "pilot.noise_q90": r.get("noise.loo_qerror.p90"),
            "pilot.noise_q95": r.get("noise.loo_qerror.p95"),
            "pilot.hgb_random_median_q": r.get("models.hgb.random.pooled.median_qerror"),
            "pilot.hgb_grouped_median_q": r.get("models.hgb.grouped.pooled.median_qerror"),
            "pilot.cost_grouped_median_q": r.get("models.calibrated_postgres_cost.grouped.pooled.median_qerror"),
            "pilot.cost_spearman": r.get("cost.spearman_total_cost_vs_median_label"),
            "pilot.grouped_keys": audit["models"].get("n_keys"),
            "pilot.grouped_groups": audit["models"].get("n_groups"),
            "pilot.instances_with_disk_reads": r.get("resources.instances_any_shared_read"),
            "pilot.instances_with_spill": r.get("resources.instances_any_spill"),
            "pilot.instances_with_parallel_workers": r.get("resources.instances_any_workers_launched"),
            "pilot.distinct_sql": r.get("counts.distinct_sql"),
            "pilot.claims_match": claims.get("MATCH", 0),
            "pilot.claims_mismatch": claims.get("MISMATCH", 0),
        })
    if bundle.smoke is not None:
        smoke = bundle.smoke
        statuses = Counter(r["status"] for r in smoke.labels)
        medians = [float(r["median_ms"]) for r in smoke.labels if r["status"] == "complete"]
        measured = [r for r in smoke.executions if r["run_purpose"] == "measured"]
        spill_keys = {r["modeling_key"] for r in measured if r["spill_detected"] == "true"}
        config_of = {r["modeling_key"]: r["configuration_name"] for r in smoke.labels}
        n10 = sum(1 for v in medians if v >= 10000.0)
        values.update({
            "smoke.keys_labeled": statuses["complete"] + statuses["right_censored"],
            "smoke.keys_complete": statuses["complete"],
            "smoke.keys_censored": statuses["right_censored"],
            "smoke.keys_failed": statuses["failed"],
            "smoke.measured_executions": len(measured),
            "smoke.groups": len({r["semantic_group_id"] for r in smoke.labels}),
            "smoke.runtime_min_ms": min(medians) if medians else None,
            "smoke.runtime_max_ms": max(medians) if medians else None,
            "smoke.n_at_least_10s": n10,
            "smoke.n_slow_or_censored": n10 + statuses["right_censored"],
            "smoke.timeout_s": smoke.validation["protocol"]["statement_timeout_ms"] / 1000.0,
            "smoke.keys_with_spill": len(spill_keys),
            "smoke.keys_with_spill_c2": sum(1 for k in spill_keys if config_of.get(k) == "C2_reduced_work_mem"),
            "smoke.runs_workers_mismatch": sum(1 for r in measured if r["workers_planned"] and r["workers_launched"]
                                               and int(r["workers_launched"]) < int(r["workers_planned"])),
        })
    if bundle.rewrite is not None:
        checks = bundle.rewrite.summary["checks"]
        reference = [c for c in checks if c["pair_role"] == "reference_pair"]
        negative = [c for c in checks if c["pair_role"] == "negative_control"]
        values.update({
            "rewrite.reference_checked": len(reference),
            "rewrite.reference_equivalent": sum(1 for c in reference if c["equivalence_status"] == "EQUIVALENT_ON_SNAPSHOT"),
            "rewrite.recommended": sum(1 for c in reference if c["recommendation"] == "RECOMMEND"),
            "rewrite.min_speedup": bundle.rewrite.summary["config"]["min_speedup_to_recommend"],
            "rewrite.negative_checked": len(negative),
            "rewrite.negative_rejected": sum(1 for c in negative if c["equivalence_status"] == "NOT_EQUIVALENT"),
        })
    return values


# ------------------------------------------------------------------------------ tables
def slow_query_rows(bundle: EvidenceBundle, limit: int = 10) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if bundle.smoke is not None:
        smoke = bundle.smoke
        estimates = {r["modeling_key"]: r for r in smoke.estimates}
        measured = defaultdict(list)
        for r in smoke.executions:
            if r["run_purpose"] == "measured":
                measured[r["modeling_key"]].append(r)
        ordered = sorted(smoke.labels, key=lambda r: -(_f(r["median_ms"]) or _f(r["censor_lower_bound_ms"]) or 0.0))
        for row in ordered[:limit]:
            runs = measured[row["modeling_key"]]
            est = estimates.get(row["modeling_key"], {})
            ops = json.loads(est["operators_json"]) if est.get("operators_json") else []
            value = _f(row["median_ms"]) or _f(row["censor_lower_bound_ms"])
            rows.append({
                "evidence_status": "NEW_POSTER_SMOKE", "id": row["modeling_key"], "family_or_template": row["family"],
                "configuration": row["configuration_name"], "scale_factor": row["scale_factor"],
                "label_status": row["status"], "median_ms": row["median_ms"], "censor_lower_bound_ms": row["censor_lower_bound_ms"],
                "at_least_1s": value is not None and value >= 1000.0, "at_least_10s_or_censored": row["status"] == "right_censored"
                or (value is not None and value >= 10000.0),
                "spill_in_any_measured_run": any(r["spill_detected"] == "true" for r in runs),
                "max_workers_launched": max((int(r["workers_launched"]) for r in runs if r["workers_launched"]), default=0),
                "root_operator": ops[0] if ops else "", "operators": " > ".join(ops[:8]),
                "est_total_cost": est.get("est_total_cost", ""),
            })
    if bundle.pilot is not None:
        instances = [i for i in bundle.pilot.audit["instances"] if i.get("median_ms") is not None]
        for inst in sorted(instances, key=lambda i: -i["median_ms"])[:5]:
            rows.append({
                "evidence_status": "RECOMPUTED_PILOT", "id": inst["query_id"], "family_or_template": f"Q{int(inst['template_id']):02d}",
                "configuration": "pilot (reported defaults, jit=off)", "scale_factor": "0.1", "label_status": "complete",
                "median_ms": inst["median_ms"], "censor_lower_bound_ms": "", "at_least_1s": inst["median_ms"] >= 1000.0,
                "at_least_10s_or_censored": inst["median_ms"] >= 10000.0, "spill_in_any_measured_run": inst["any_run_spill"],
                "max_workers_launched": "", "root_operator": inst["operators"][0] if inst["operators"] else "",
                "operators": " > ".join(inst["operators"][:8]), "est_total_cost": inst["est_total_cost_run1"],
            })
    return rows


def rewrite_speedup_inputs(bundle: EvidenceBundle) -> tuple[list[F.RewriteTiming], F.Provenance]:
    """Only reference pairs that passed the equivalence check with complete paired timings are eligible."""
    if bundle.rewrite is None:
        raise MissingEvidenceError(bundle.problems.get("rewrite", "rewrite verification summary missing"))
    eligible = [c for c in bundle.rewrite.summary["checks"] if c["pair_role"] == "reference_pair"
                and c["equivalence_status"] == "EQUIVALENT_ON_SNAPSHOT" and c["timing_status"] == "COMPLETE"]
    if not eligible:
        raise MissingEvidenceError("no reference pair passed the equivalence check with complete paired timings; "
                                   "a speedup chart would be misleading")
    timings = [F.RewriteTiming(f"{c['pair_id']} {json.dumps(c['parameters'], sort_keys=True)} SF {c['scale_factor']}",
                               [p["original_ms"] for p in c["timing_pairs"]], [p["rewrite_ms"] for p in c["timing_pairs"]],
                               float(c["median_speedup"])) for c in eligible]
    provenance = F.Provenance(
        source=str(bundle.rewrite.path), status="NEW_POSTER_SMOKE", n_keys=len(eligible),
        n_executions=sum(2 * len(c["timing_pairs"]) for c in eligible), n_groups=len({c["pair_id"] for c in eligible}),
        configuration=str(bundle.rewrite.summary["config"]["configuration"]),
        scale_factor=", ".join(sorted({str(c["scale_factor"]) for c in eligible})),
        created_at_utc=str(bundle.rewrite.summary["created_at_utc"]),
        metric_definition="speedup = original / rewrite instrumented execution time per pair; median shown",
    )
    return timings, provenance


def rewrite_rows(bundle: EvidenceBundle) -> list[dict[str, Any]]:
    if bundle.rewrite is None:
        return []
    out = []
    for c in bundle.rewrite.summary["checks"]:
        out.append({
            "rewrite_source": "MANUAL_REFERENCE_REWRITE", "pair_id": c["pair_id"], "pair_role": c["pair_role"],
            "parameters": json.dumps(c["parameters"], sort_keys=True), "snapshot": c["snapshot_name"],
            "scale_factor": c["scale_factor"], "safety_status": c["safety_status"],
            "precondition_status": c["precondition_status"], "original_rows": c["original_row_count"],
            "rewrite_rows": c["rewrite_row_count"], "multiset_equal": c["multiset_equal"],
            "ordering_status": c["ordering_status"], "equivalence_status": c["equivalence_status"],
            "timing_status": c["timing_status"], "median_speedup": c["median_speedup"],
            "min_speedup_required": c["min_speedup_required"], "recommendation": c["recommendation"], "reason": c["reason"],
        })
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> dict[str, str]:
    ensure_dir(path.parent)
    columns = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return {"path": str(path), "sha256": sha256_file(path)}


# ------------------------------------------------------------------------------ build
def build_poster_evidence(out_dir: Path | None = None) -> dict[str, Any]:
    reports = out_dir or reports_dir()
    refuse_reference_write(reports)
    bundle = load_all(reports)
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if bundle.pilot is None and bundle.smoke is None and bundle.rewrite is None:
        return {"status": "FAIL", "summary": {"status": "FAIL", "reason": "no evidence found", "problems": bundle.problems}}

    outputs: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    def figure(name: str, build: Callable[[], Any]) -> None:
        try:
            fig = build()
        except MissingEvidenceError as exc:
            skipped.append({"output": name, "reason": str(exc)})
            return
        outputs.append({"output": name, **F.save_figure(fig, reports / "figures" / name)})

    def panels(*makers: Callable[[], Any]) -> list[Any]:
        made = []
        for make in makers:
            try:
                made.append(make())
            except (MissingEvidenceError, KeyError, ValueError) as exc:
                skipped.append({"output": "panel", "reason": f"{getattr(make, '__name__', 'panel')}: {exc}"})
        return made

    pilot, smoke = bundle.pilot, bundle.smoke
    pilot_on = pilot is not None
    smoke_on = smoke is not None
    figure("runtime_distribution_log.png", lambda: F.runtime_distribution_figure(panels(
        *([lambda: PF.runtime_panel(pilot.audit)] if pilot_on else []),
        *([lambda: smoke_runtime_panel(smoke)] if smoke_on else []))))
    figure("postgres_cost_vs_runtime.png", lambda: F.cost_vs_runtime_figure(panels(
        *([lambda: PF.scatter_panel(pilot.audit)] if pilot_on else []),
        *([lambda: smoke_scatter_panel(smoke)] if smoke_on else []))))
    if pilot_on:
        figure("random_vs_grouped_metrics.png", lambda: F.random_vs_grouped_figure(
            PF.metric_rows(pilot.audit), PF.pilot_provenance(pilot.audit, "pooled out-of-fold median q-error and log-runtime MAE")))
    else:
        skipped.append({"output": "random_vs_grouped_metrics.png", "reason": bundle.problems.get("pilot", "no pilot audit")})
    skipped.append({"output": "random_vs_grouped_metrics.png (smoke panel)",
                    "reason": "the smoke run is too small for cross-validation; no smoke model metrics are reported"})
    figure("operator_frequency.png", lambda: F.operator_frequency_figure(panels(
        *([lambda: PF.operator_panel(pilot.audit)] if pilot_on else []),
        *([lambda: smoke_operator_panel(smoke)] if smoke_on else []))))
    figure("runtime_by_query_family.png", lambda: F.runtime_by_group_figure(panels(
        *([lambda: PF.group_panel(pilot.audit)] if pilot_on else []),
        *([lambda: smoke_group_panel(smoke)] if smoke_on else []))))

    figure("manual_reference_rewrite_speedup.png", lambda: F.rewrite_speedup_figure(*rewrite_speedup_inputs(bundle)))

    slow_rows = slow_query_rows(bundle)
    if slow_rows:
        outputs.append({"output": "slow_query_examples.csv", **_write_csv(reports / "tables" / "slow_query_examples.csv", slow_rows)})
    else:
        skipped.append({"output": "slow_query_examples.csv", "reason": "no labelled keys available"})
    rw_rows = rewrite_rows(bundle)
    if rw_rows:
        outputs.append({"output": "manual_reference_rewrite_results.csv",
                        **_write_csv(reports / "tables" / "manual_reference_rewrite_results.csv", rw_rows)})
    else:
        skipped.append({"output": "manual_reference_rewrite_results.csv", "reason": bundle.problems.get("rewrite", "missing")})

    values = evidence_values(bundle)
    positives_10s = None
    if "pilot.n_at_least_10s" in values or "smoke.n_at_least_10s" in values:
        positives_10s = int(values.get("pilot.n_at_least_10s") or 0) + int(values.get("smoke.n_at_least_10s") or 0)
    claims = render_claims(load_claim_specs(), values, positives_10s=positives_10s)
    violations = [(c.id, v) for c in claims for v in c.violations]
    status = "FAIL" if violations else "PASS"

    inputs = []
    if pilot is not None:
        inputs.append({"path": str(pilot.path), "sha256": pilot.sha256, "evidence_status": "RECOMPUTED_PILOT"})
    if smoke is not None:
        inputs.append({"path": str(smoke.validation_path), "sha256": sha256_file(smoke.validation_path), "evidence_status": "NEW_POSTER_SMOKE"})
        inputs += [{"path": str(smoke.derived_dir / n), "sha256": s, "evidence_status": "NEW_POSTER_SMOKE"}
                   for n, s in smoke.validation["derived_files_sha256"].items()]
    if bundle.rewrite is not None:
        inputs.append({"path": str(bundle.rewrite.path), "sha256": bundle.rewrite.sha256, "evidence_status": "NEW_POSTER_SMOKE"})
    manifest = {
        "created_at_utc": created, "status": status, "inputs": inputs, "outputs": outputs, "skipped": skipped,
        "missing_evidence": bundle.problems,
        "environment": {k: {"path": v.get("path"), "sha256": v.get("sha256")} for k, v in bundle.environment.items()},
        "claims": [c.as_dict() for c in claims], "claim_violations": violations,
        "note": "Nothing here was produced by Claude Code; all values come from files generated by the user's runs.",
    }
    ensure_dir(reports)
    (reports / "evidence_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    (reports / "poster_evidence_summary.md").write_text(_summary_markdown(manifest, bundle), encoding="utf-8")
    return {"status": status, "summary": {
        "status": status, "outputs": [o["output"] for o in outputs], "skipped": [s["output"] for s in skipped],
        "claims_ready": sum(1 for c in claims if c.render_status == "READY"),
        "claims_missing_evidence": [c.id for c in claims if c.render_status == "EVIDENCE_MISSING"],
        "claim_violations": violations, "missing_evidence": list(bundle.problems)}}


def _summary_markdown(manifest: dict[str, Any], bundle: EvidenceBundle) -> str:
    rules = rule_descriptions()
    lines = ["# Poster evidence summary", "",
             f"Generated {manifest['created_at_utc']} · build status **{manifest['status']}**", "",
             "Every number below comes from evidence files generated by your own runs. Labels: "
             "`RECOMPUTED_PILOT` (pilot audit), `NEW_POSTER_SMOKE` (this milestone), `PLANNED`, `NOT_YET_EVALUABLE`.", "",
             "## Evidence sources", "", "| Source | Status |", "|---|---|",
             f"| Pilot audit | {'present' if bundle.pilot else 'MISSING - ' + bundle.problems.get('pilot', '')} |",
             f"| Poster smoke (validated) | {'present' if bundle.smoke else 'MISSING - ' + bundle.problems.get('smoke', '')} |",
             f"| Manual rewrite verification | {'present' if bundle.rewrite else 'MISSING - ' + bundle.problems.get('rewrite', '')} |",
             "", "## Poster-safe claim table", "", "| ID | Status | Render | Text |", "|---|---|---|---|"]
    for claim in manifest["claims"]:
        text = claim["text"] if claim["render_status"] == "READY" else f"(not usable: missing {', '.join(claim['missing'])})"
        if claim["violations"]:
            text = f"PROHIBITED WORDING {claim['violations']}: {claim['text']}"
        lines.append(f"| {claim['id']} | {claim['status']} | {claim['render_status']} | {text} |")
    if manifest["claim_violations"]:
        lines += ["", "## Claim violations (build FAILED)", ""]
        lines += [f"* {cid}: {rid} - {rules.get(rid, rid)}" for cid, rid in manifest["claim_violations"]]
    lines += ["", "## Outputs", ""] + [f"* `{o['output']}` - sha256 {o['sha256']}" for o in manifest["outputs"]]
    lines += ["", "## Skipped (evidence absent or not applicable)", ""] + [f"* `{s['output']}`: {s['reason']}" for s in manifest["skipped"]]
    lines += ["", "## Prohibited on the poster", ""] + [f"* {rid}: {text}" for rid, text in sorted(rules.items())]
    lines += ["", "## Limitations", "",
              "* The pilot is SF 0.1, one machine, one session; its features come from the estimate view of EXPLAIN ANALYZE plans.",
              "* The smoke run is a small controlled sample (20-50 keys); it demonstrates the protocol, not generalization.",
              "* Instrumented EXPLAIN ANALYZE server time is not end-user latency.",
              "* Manual reference rewrites demonstrate the verification method; RQ2 (LLM rewrites) is not yet evaluable."]
    return "\n".join(lines) + "\n"


def lint_free_text(text: str) -> list[str]:
    """Convenience for the notebook: lint any caption you intend to put on the poster."""
    return lint_text(text)
