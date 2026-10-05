"""Missing evidence is reported clearly; no figure is drawn from absent or failed evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from query_cost_predictor.errors import MissingEvidenceError
from query_cost_predictor.reporting import figures as F
from query_cost_predictor.reporting import loaders
from query_cost_predictor.reporting.builder import build_poster_evidence

PROVENANCE = F.Provenance(source="synthetic", status="NEW_POSTER_SMOKE", n_keys=0, n_executions=0, n_groups=0,
                          configuration="none", scale_factor="none", created_at_utc="2000-01-01", metric_definition="none")


def test_loaders_name_the_step_that_produces_missing_evidence(tmp_path: Path) -> None:
    with pytest.raises(MissingEvidenceError, match="step 7"):
        loaders.load_pilot(tmp_path)
    with pytest.raises(MissingEvidenceError, match="steps 9-10"):
        loaders.load_smoke(tmp_path)
    with pytest.raises(MissingEvidenceError, match="step 11"):
        loaders.load_rewrite(tmp_path)


def test_failed_smoke_validation_is_not_usable(tmp_path: Path) -> None:
    (tmp_path / "smoke_validation.json").write_text(json.dumps({"status": "FAIL"}), encoding="utf-8")
    with pytest.raises(MissingEvidenceError, match="not usable"):
        loaders.load_smoke(tmp_path)


def test_load_all_records_every_problem(tmp_path: Path) -> None:
    bundle = loaders.load_all(tmp_path)
    assert bundle.pilot is None and bundle.smoke is None and bundle.rewrite is None
    assert set(bundle.problems) == {"pilot", "smoke", "rewrite"}


def test_require_stops_with_the_message() -> None:
    with pytest.raises(MissingEvidenceError, match="run step X"):
        loaders.require(None, "run step X")


def test_builder_refuses_without_evidence_and_draws_nothing(tmp_path: Path) -> None:
    result = build_poster_evidence(tmp_path)
    assert result["status"] == "FAIL"
    assert list(tmp_path.rglob("*.png")) == []


def test_figures_refuse_empty_evidence() -> None:
    with pytest.raises(MissingEvidenceError):
        F.runtime_distribution_figure([])
    with pytest.raises(MissingEvidenceError):
        F.runtime_distribution_figure([F.RuntimePanel("empty", [], [], PROVENANCE)])
    with pytest.raises(MissingEvidenceError):
        F.cost_vs_runtime_figure([F.ScatterPanel("empty", [], [], [], PROVENANCE, None)])
    with pytest.raises(MissingEvidenceError):
        F.random_vs_grouped_figure([], PROVENANCE)
    with pytest.raises(MissingEvidenceError):
        F.rewrite_speedup_figure([], PROVENANCE)
    with pytest.raises(MissingEvidenceError):
        F.noise_ecdf_figure([], PROVENANCE)
