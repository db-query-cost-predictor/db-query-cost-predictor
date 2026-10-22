"""RQ1 modeling specification (``config/rq1_modeling_v1.yaml``): parse and validate.

The YAML is the reviewed, static specification of the RQ1 study. This module
reads it and fails closed on anything the approved methodology forbids (a
``penalty`` argument, a search block, a decision threshold other than 0.5, a
10 s task that is not ``NOT_YET_EVALUABLE``). It never fits or evaluates
anything; it only exposes the values that later stages read.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from query_cost_predictor.hashing import sha256_file

CONFIG_RELATIVE_PATH = "config/rq1_modeling_v1.yaml"
SCHEMES = ("fold_group", "fold_family", "fold_seen_template")
EXPECTED_STAGES = ("input_check", "regression", "classification_1s", "diagnostics", "handoff")
DECISION_THRESHOLD = 0.5
FORBIDDEN_MODEL_KEYS = frozenset({"penalty", "param_grid", "param_distributions", "search", "grid_search",
                                  "cv_search", "n_iter_search"})


class Rq1ConfigError(ValueError):
    """The RQ1 specification is missing a value or contradicts the approved methodology."""


@dataclass(frozen=True)
class Rq1Config:
    path: Path
    sha256: str
    raw: dict[str, Any]

    # ------------------------------------------------------------- inputs
    @property
    def input_record_path(self) -> str:
        return str(self.raw["inputs"]["input_record"]["path"])

    @property
    def input_record_sha256(self) -> str:
        return str(self.raw["inputs"]["input_record"]["sha256"])

    @property
    def input_record_schema(self) -> tuple[str, int]:
        record = self.raw["inputs"]["input_record"]
        return str(record["schema"]), int(record["schema_version"])

    @property
    def evidence_dir_env(self) -> str:
        return str(self.raw["inputs"]["evidence_dir_env"])

    @property
    def data_root_env(self) -> str:
        return str(self.raw["inputs"]["supplementary_approval"]["data_root_env"])

    @property
    def original_validation(self) -> dict[str, Any]:
        value = self.raw["inputs"]["checks"]["original_validation"]
        return {"status": str(value["status"]), "stop_codes": [str(c) for c in value["stop_codes"]]}

    # ------------------------------------------------------------- rows and targets
    @property
    def rows(self) -> dict[str, Any]:
        return self.raw["rows"]

    @property
    def class_mapping(self) -> dict[str, int]:
        return {str(k): int(v) for k, v in self.raw["targets"]["classification_1s"]["mapping"].items()}

    @property
    def runtime_range_ms(self) -> tuple[float, float]:
        value = self.raw["rows"]["development_runtime_range_ms"]
        return float(value["min"]), float(value["max"])

    # ------------------------------------------------------------- folds
    @property
    def fold_values(self) -> tuple[int, ...]:
        return tuple(int(v) for v in self.raw["folds"]["values"])

    @property
    def expected_per_fold(self) -> dict[str, dict[int, dict[str, int]]]:
        return {scheme: {int(f): {k: int(v) for k, v in counts.items()} for f, counts in folds.items()}
                for scheme, folds in self.raw["folds"]["expected_per_fold"].items()}

    @property
    def disjoint_group_columns(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in self.raw["folds"]["guards"]["groups_disjoint_across_folds"].items()}

    # ------------------------------------------------------------- software and provenance
    @property
    def tested_sklearn_version(self) -> str:
        pin = str(self.raw["software"]["tested_environment"]["scikit_learn"])
        return pin[2:]

    @property
    def allowed_untracked_paths(self) -> tuple[str, ...]:
        return tuple(str(p) for p in self.raw["run_record"]["source_cleanliness"]["allowed_untracked_paths"])

    @property
    def stages(self) -> tuple[str, ...]:
        return tuple(str(s) for s in self.raw["run"]["stages"])

    @property
    def spec_id(self) -> str:
        return str(self.raw["spec_id"])

    @property
    def run_dir_template(self) -> str:
        return str(self.raw["outputs"]["run_dir"])

    # ------------------------------------------------------------- models, metrics, reporting (Slice B)
    @property
    def regression_models(self) -> list[dict[str, Any]]:
        return list(_require(self.raw, "regression_models"))

    @property
    def metrics_spec(self) -> dict[str, Any]:
        return _require(self.raw, "metrics")

    @property
    def reporting(self) -> dict[str, Any]:
        return _require(self.raw, "reporting")

    @property
    def uncertainty(self) -> dict[str, Any]:
        return _require(self.raw, "uncertainty")

    @property
    def metric_table_columns(self) -> tuple[str, ...]:
        return tuple(str(c) for c in _require(self.raw, "outputs.metric_table_columns"))

    @property
    def limitations_text(self) -> str:
        return str(_require(self.raw, "limitations_block.text"))


def _require(mapping: Any, dotted: str) -> Any:
    node = mapping
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise Rq1ConfigError(f"rq1 config: missing '{dotted}'")
        node = node[part]
    return node


def _walk_keys(node: Any, trail: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{trail}.{key}" if trail else str(key), key
            yield from _walk_keys(value, f"{trail}.{key}" if trail else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_keys(value, f"{trail}[{index}]")


def parse_rq1_config(document: Any, path: Path, sha256: str) -> Rq1Config:
    """Validate a parsed RQ1 YAML document against the approved methodology."""
    if not isinstance(document, dict) or document.get("version") != 1:
        raise Rq1ConfigError("rq1 config must be a mapping with version: 1")
    for dotted in ("spec_id", "inputs.input_record.path", "inputs.input_record.sha256", "inputs.input_record.schema",
                   "inputs.input_record.schema_version", "inputs.evidence_dir_env",
                   "inputs.supplementary_approval.data_root_env", "inputs.checks.original_validation",
                   "rows.include", "rows.exclude", "rows.expected_development_cases", "rows.expected_development_groups",
                   "rows.expected_development_query_instances", "rows.required_observation_status",
                   "rows.development_runtime_range_ms", "targets.classification_1s.mapping",
                   "targets.classification_1s.label_boundary_ms", "targets.classification_10s.status",
                   "folds.values", "folds.expected_per_fold", "folds.guards.groups_disjoint_across_folds",
                   "fixed.decision_threshold.probability", "software.tested_environment.scikit_learn",
                   "run.stages", "run_record.source_cleanliness.allowed_untracked_paths", "outputs.run_dir"):
        _require(document, dotted)

    rows = document["rows"]
    if (rows["include"], rows["exclude"]) != ("development", "heldout_rq2"):
        raise Rq1ConfigError("RQ1 rows must be split_role development, excluding heldout_rq2")
    if document["targets"]["classification_1s"]["mapping"] != {"positive": 1, "negative": 0}:
        raise Rq1ConfigError("hr_1s_class mapping must be exactly {positive: 1, negative: 0}")
    if float(document["targets"]["classification_1s"]["label_boundary_ms"]) != 1000.0:
        raise Rq1ConfigError("the 1 s runtime-label boundary is frozen at 1000 ms")
    if document["targets"]["classification_10s"]["status"] != "NOT_YET_EVALUABLE":
        raise Rq1ConfigError("10 s classification must stay NOT_YET_EVALUABLE (gate G-08)")
    original = document["inputs"]["checks"]["original_validation"]
    if original.get("status") != "STOP" or list(original.get("stop_codes") or []) != ["G-08"]:
        raise Rq1ConfigError("the original validation must be recorded as STOP with stop code G-08")

    fixed = document.get("fixed") or {}
    if float(fixed["decision_threshold"]["probability"]) != DECISION_THRESHOLD:
        raise Rq1ConfigError("the decision threshold is fixed at 0.5")
    for key in ("hyperparameter_search", "adaptive_feature_selection", "post_hoc_probability_calibration"):
        if fixed.get(key) != "none":
            raise Rq1ConfigError(f"fixed.{key} must be 'none' in v1")
    for section in ("shared", "regression_models", "classification_models"):
        for trail, key in _walk_keys(document.get(section)):
            if str(key) in FORBIDDEN_MODEL_KEYS:
                raise Rq1ConfigError(f"{section}.{trail}: '{key}' is not allowed (no penalty argument, no search)")

    values = [int(v) for v in document["folds"]["values"]]
    if values != list(range(len(values))) or not values:
        raise Rq1ConfigError("folds.values must be 0..n-1")
    per_fold = document["folds"]["expected_per_fold"]
    if set(per_fold) != set(SCHEMES):
        raise Rq1ConfigError(f"folds.expected_per_fold must cover {SCHEMES}")
    for scheme, folds in per_fold.items():
        if sorted(int(f) for f in folds) != values:
            raise Rq1ConfigError(f"folds.expected_per_fold.{scheme} must list folds {values}")
        for fold, counts in folds.items():
            if set(counts) != {"keys", "positive", "negative"} or int(counts["keys"]) != int(counts["positive"]) + int(counts["negative"]):
                raise Rq1ConfigError(f"folds.expected_per_fold.{scheme}.{fold} must hold keys = positive + negative")
    if set(document["folds"]["guards"]["groups_disjoint_across_folds"]) != set(SCHEMES):
        raise Rq1ConfigError("folds.guards.groups_disjoint_across_folds must cover every scheme")

    if tuple(document["run"]["stages"]) != EXPECTED_STAGES:
        raise Rq1ConfigError(f"run.stages must be {EXPECTED_STAGES}")
    pin = str(document["software"]["tested_environment"]["scikit_learn"])
    if not pin.startswith("==") or len(pin) < 3:
        raise Rq1ConfigError("software.tested_environment.scikit_learn must be an exact '==' pin")
    rng = document["rows"]["development_runtime_range_ms"]
    if not 0 < float(rng["min"]) <= float(rng["max"]):
        raise Rq1ConfigError("rows.development_runtime_range_ms must satisfy 0 < min <= max")
    return Rq1Config(path=path, sha256=sha256, raw=document)


def default_config_path(repo: Path | None = None) -> Path:
    from query_cost_predictor.paths import repo_root

    return (repo or repo_root()) / CONFIG_RELATIVE_PATH


def load_rq1_config(path: Path | None = None) -> Rq1Config:
    target = path or default_config_path()
    with target.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return parse_rq1_config(document, target, sha256_file(target))
