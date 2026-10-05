"""Build figure panels from ``pilot_audit.json`` (evidence status ``RECOMPUTED_PILOT``)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from query_cost_predictor.errors import MissingEvidenceError
from query_cost_predictor.reporting import figures as F

PILOT_SOURCE = "reference_pilot/output/query_runs.jsonl"


def pilot_configuration(audit: dict[str, Any]) -> str:
    settings = audit.get("pilot_settings_reported", {}) or {}
    return (
        f"pilot as reported: PostgreSQL {settings.get('server_version', '?')}, "
        f"work_mem={settings.get('work_mem', '?')}kB, "
        f"max_parallel_workers_per_gather={settings.get('max_parallel_workers_per_gather', '?')}, jit=off, warm cache"
    )


def pilot_provenance(audit: dict[str, Any], metric_definition: str) -> F.Provenance:
    instances = audit.get("instances") or []
    if not instances:
        raise MissingEvidenceError("pilot audit contains no instances; re-run step 7 (04_run_pilot_audit.ps1)")
    recomputed = audit.get("recomputed", {})
    return F.Provenance(
        source=PILOT_SOURCE,
        status="RECOMPUTED_PILOT",
        n_keys=len(instances),
        n_executions=int(recomputed.get("counts.raw_runs", 0)),
        n_groups=len({i["template_id"] for i in instances}),
        configuration=pilot_configuration(audit),
        scale_factor="0.1",
        created_at_utc=str(audit.get("created_at_utc")),
        metric_definition=metric_definition,
    )


def _labelled(audit: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in audit.get("instances", []) if i.get("median_ms") is not None]


def runtime_panel(audit: dict[str, Any]) -> F.RuntimePanel:
    return F.RuntimePanel(
        label="Pilot (TPC-H-derived, SF 0.1)",
        completed_ms=[float(i["median_ms"]) for i in _labelled(audit)],
        censored_bounds_ms=[],
        provenance=pilot_provenance(audit, "median of three measured EXPLAIN ANALYZE execution times per instance"),
    )


def scatter_panel(audit: dict[str, Any]) -> F.ScatterPanel:
    rows = _labelled(audit)
    return F.ScatterPanel(
        label="Pilot (TPC-H-derived, SF 0.1)",
        cost=[float(i["est_total_cost_run1"]) for i in rows],
        runtime_ms=[float(i["median_ms"]) for i in rows],
        censored=[False] * len(rows),
        provenance=pilot_provenance(audit, "x: root Total Cost (estimate view); y: median runtime of three runs"),
        spearman=audit.get("recomputed", {}).get("cost.spearman_total_cost_vs_median_label"),
    )


def operator_panel(audit: dict[str, Any]) -> F.OperatorPanel:
    return F.OperatorPanel(
        label="Pilot (TPC-H-derived, SF 0.1)",
        operators_per_key=[i.get("operators", []) for i in audit.get("instances", [])],
        provenance=pilot_provenance(audit, "share of instances whose estimate-view plan contains each node type"),
    )


def group_panel(audit: dict[str, Any]) -> F.GroupPanel:
    completed: dict[str, list[float]] = defaultdict(list)
    for inst in _labelled(audit):
        completed[f"Q{int(inst['template_id']):02d}"].append(float(inst["median_ms"]))
    return F.GroupPanel(
        label="Pilot by TPC-H template",
        completed=dict(completed),
        censored={},
        provenance=pilot_provenance(audit, "median of three measured runs per instance, grouped by template"),
    )


def metric_rows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    return list(audit.get("models", {}).get("rows", []))


def write_pilot_figures(audit: dict[str, Any], out_dir: Path) -> list[dict[str, Any]]:
    """Render the pilot audit figures. Each figure is skipped (with reason) if evidence is absent."""
    outputs: list[dict[str, Any]] = []
    builders = [
        ("runtime_distribution_log.png", lambda: F.runtime_distribution_figure([runtime_panel(audit)])),
        ("postgres_cost_vs_runtime.png", lambda: F.cost_vs_runtime_figure([scatter_panel(audit)])),
        ("random_vs_grouped_metrics.png", lambda: F.random_vs_grouped_figure(
            metric_rows(audit), pilot_provenance(audit, "pooled out-of-fold median q-error and log-runtime MAE"))),
        ("operator_frequency.png", lambda: F.operator_frequency_figure([operator_panel(audit)])),
        ("runtime_by_template.png", lambda: F.runtime_by_group_figure([group_panel(audit)])),
        ("repeated_run_noise_qerror.png", lambda: F.noise_ecdf_figure(
            audit.get("noise_loo_qerrors", []),
            pilot_provenance(audit, "q-error of each run against the mean of the other runs of its instance"))),
    ]
    for name, build in builders:
        try:
            fig = build()
        except MissingEvidenceError as exc:
            outputs.append({"name": name, "status": "SKIPPED_MISSING_EVIDENCE", "reason": str(exc)})
            continue
        saved = F.save_figure(fig, out_dir / name)
        outputs.append({"name": name, "status": "WRITTEN", **saved})
    return outputs
