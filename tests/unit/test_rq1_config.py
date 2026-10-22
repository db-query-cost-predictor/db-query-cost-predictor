"""RQ1 Slice A: the reviewed specification parses and fails closed on forbidden changes."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from query_cost_predictor.rq1_config import EXPECTED_STAGES, Rq1ConfigError, load_rq1_config, parse_rq1_config
from query_cost_predictor.rq1_run import EXCLUDED_FROM_MANIFEST, STATUS_FILE
from rq1_fixtures import REAL_CONFIG, REPO_ROOT


@pytest.fixture
def document() -> dict:
    return yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))


def parse(document: dict):
    return parse_rq1_config(document, Path("rq1_modeling_v1.yaml"), "test")


def test_committed_specification_loads():
    config = load_rq1_config()
    assert config.stages == EXPECTED_STAGES
    assert config.tested_sklearn_version == "1.9.1"
    assert config.allowed_untracked_paths == ("reference_docs/", "reference_pilot/README.md")
    assert config.original_validation == {"status": "STOP", "stop_codes": ["G-08"]}
    assert config.class_mapping == {"positive": 1, "negative": 0}
    assert config.expected_per_fold["fold_group"][4] == {"keys": 944, "positive": 70, "negative": 874}


def test_constraints_file_pins_the_tested_scikit_learn():
    pins = (REPO_ROOT / "requirements" / "rq1_constraints_v1.txt").read_text(encoding="utf-8").splitlines()
    assert f"scikit-learn=={load_rq1_config().tested_sklearn_version}" in pins


def test_output_manifest_exclusions_match_the_specification(document):
    manifest = document["run"]["output_manifest"]
    assert tuple(manifest["excluded"]) == EXCLUDED_FROM_MANIFEST
    assert STATUS_FILE in manifest["excluded"]
    assert "observed_positive_fraction" in document["outputs"]["calibration_bins_columns"]
    assert "observed_positive_rate" not in REAL_CONFIG.read_text(encoding="utf-8")


def _mutated(document: dict, change) -> dict:
    copy_ = copy.deepcopy(document)
    change(copy_)
    return copy_


@pytest.mark.parametrize("change, message", [
    (lambda d: d["shared"]["logistic"].__setitem__("penalty", "l2"), "penalty"),
    (lambda d: d["regression_models"][4]["steps"][1]["params"].__setitem__("param_grid", {"alpha": [1, 2]}), "param_grid"),
    (lambda d: d["fixed"].__setitem__("hyperparameter_search", "grid"), "hyperparameter_search"),
    (lambda d: d["fixed"]["decision_threshold"].__setitem__("probability", 0.4), "0.5"),
    (lambda d: d["targets"]["classification_10s"].__setitem__("status", "EVALUABLE"), "NOT_YET_EVALUABLE"),
    (lambda d: d["inputs"]["checks"]["original_validation"].__setitem__("status", "PASS"), "STOP"),
    (lambda d: d["targets"]["classification_1s"].__setitem__("label_boundary_ms", 900.0), "1000"),
    (lambda d: d["rows"].__setitem__("include", "all"), "development"),
    (lambda d: d["folds"]["expected_per_fold"]["fold_group"][0].__setitem__("positive", 1), "keys = positive"),
    (lambda d: d["run"].__setitem__("stages", ["input_check", "regression"]), "run.stages"),
    (lambda d: d["software"]["tested_environment"].__setitem__("scikit_learn", ">=1.8"), "exact"),
    (lambda d: d["inputs"].pop("input_record"), "input_record"),
])
def test_specification_fails_closed(document, change, message):
    with pytest.raises(Rq1ConfigError, match=message):
        parse(_mutated(document, change))
