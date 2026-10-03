"""Refusal to generate unsupported or prohibited poster claims."""

from __future__ import annotations

from pathlib import Path

import pytest

from query_cost_predictor.claims import PLACEHOLDER, ClaimSpec, lint_text, load_claim_specs, render_claim, render_claims


def test_r2_described_as_accuracy_is_prohibited() -> None:
    assert "X-01" in lint_text("The model achieves an R² accuracy of 0.95")


def test_random_split_presented_as_unseen_performance_is_prohibited() -> None:
    assert "X-02" in lint_text("Random split results show the model generalizes to unseen queries")
    assert "X-02" not in lint_text("0.3 under random 5-fold (leakage diagnostic only) versus 1.4 under grouped folds")


def test_financial_savings_without_pricing_model_are_prohibited() -> None:
    assert "X-03" in lint_text("The rewrite saves $1,200 per month")
    assert "X-03" in lint_text("Estimated dollar savings of the gate")


def test_manual_rewrites_presented_as_llm_results_are_prohibited() -> None:
    assert "X-04" in lint_text("The LLM rewrite achieved a 3x speedup")
    assert "X-04" not in lint_text("Manual reference rewrites (MANUAL_REFERENCE_REWRITE, not LLM output) were verified")


def test_planned_counts_presented_as_collected_are_prohibited() -> None:
    assert "X-05" in lint_text("We collected 4,704 modeling keys")
    assert "X-05" not in lint_text("Planned final study (not collected): approximately 4,704 modeling keys")


def test_ten_second_classifier_without_positives_is_prohibited() -> None:
    assert "X-06" in lint_text("Our 10 s classifier reaches PR-AUC 0.9", positives_10s=0)
    assert "X-06" not in lint_text("A 10 s high-runtime classifier is not yet evaluable", positives_10s=0)


def test_other_prohibited_wordings() -> None:
    assert "X-07" in lint_text("Results on our OLTP benchmark")
    assert "X-08" in lint_text("The rewrite is proven equivalent")
    assert "X-09" in lint_text("These are TPC-H benchmark results")
    assert "X-12" in lint_text("This query is guaranteed to spill")


def test_every_configured_claim_template_is_lint_clean() -> None:
    for spec in load_claim_specs():
        for template in filter(None, (spec.template, spec.template_if_zero)):
            text = PLACEHOLDER.sub("1", template)
            assert lint_text(text, positives_10s=0) == [], (spec.id, text)


def test_missing_evidence_renders_as_unusable() -> None:
    rendered = render_claim(ClaimSpec("T-1", "RECOMPUTED_PILOT", "{pilot.instances} instances"), {})
    assert rendered.render_status == "EVIDENCE_MISSING" and rendered.missing == ("pilot.instances",)


def test_zero_observation_uses_the_honest_variant() -> None:
    spec = ClaimSpec("T-2", "NEW_POSTER_SMOKE", "{smoke.x} keys spilled", zero_key="smoke.x",
                     template_if_zero="No smoke key showed spill evidence.")
    assert render_claim(spec, {"smoke.x": 0}).text == "No smoke key showed spill evidence."
    assert render_claim(spec, {"smoke.x": 3}).text == "3 keys spilled"


def test_pilot_claims_cannot_use_smoke_evidence() -> None:
    with pytest.raises(ValueError):
        render_claims([ClaimSpec("T-3", "RECOMPUTED_PILOT", "{smoke.keys_complete} keys")], {})


def test_prohibited_status_cannot_be_configured(tmp_path: Path) -> None:
    path = tmp_path / "claims.yaml"
    path.write_text("version: 1\nclaims:\n  - {id: T-4, status: PROHIBITED, template: \"x\"}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_claim_specs(path)
