"""Planned final-study arithmetic (evidence status ``PLANNED``).

Computes planned modeling-key totals from ``config/final_study_plan.yaml``.
These numbers are planning arithmetic only: nothing has been collected, and a
manifest-generation program must validate them before they are stated as facts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def component_keys(component: dict[str, Any]) -> tuple[int, int]:
    """Return (planned keys, planned held-out keys) for one component."""
    per_group_multiplier = int(component["snapshots"]) * int(component["configurations"]) * int(component["forms"])
    default_variants = int(component["variants_per_group"])
    overrides = {str(k): int(v) for k, v in (component.get("group_variant_overrides") or {}).items()}
    groups = int(component["groups"])
    variant_total = default_variants * (groups - len(overrides)) + sum(overrides.values())
    keys = variant_total * per_group_multiplier
    holdout_groups = int(component.get("holdout_groups", 0))
    if holdout_groups and overrides:
        raise ValueError("holdout groups with variant overrides are not supported in this planning helper")
    holdout_keys = holdout_groups * default_variants * per_group_multiplier
    return keys, holdout_keys


def summarize_final_plan(path: Path | None = None) -> dict[str, Any]:
    from query_cost_predictor.paths import config_dir

    target = path or (config_dir() / "final_study_plan.yaml")
    plan = yaml.safe_load(target.read_text(encoding="utf-8"))
    rows = []
    total_keys = total_holdout = total_groups = 0
    for component in plan["components"]:
        keys, holdout = component_keys(component)
        rows.append({"component": component["name"], "groups": component["groups"], "planned_keys": keys,
                     "planned_holdout_keys": holdout})
        total_keys += keys
        total_holdout += holdout
        total_groups += int(component["groups"])
    return {
        "evidence_status": "PLANNED",
        "note": "Planning arithmetic only - nothing collected. Repeated executions are stored separately and are "
                "not counted as additional modeling keys.",
        "components": rows,
        "planned_total_keys": total_keys,
        "planned_holdout_keys": total_holdout,
        "planned_development_keys": total_keys - total_holdout,
        "planned_groups": total_groups,
        "meets_minimum_groups": total_groups >= int(plan["minimum_groups"]),
        "meets_minimum_keys": (total_keys - total_holdout) >= int(plan["minimum_labeled_keys"]),
    }
