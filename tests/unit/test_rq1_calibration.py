"""RQ1 Slice C1: the ten fixed calibration bins and calibration_bins.csv (SYNTHETIC values only)."""

from __future__ import annotations

import numpy as np
import pytest
from sklearn.calibration import calibration_curve

from query_cost_predictor.rq1_config import SCHEMES, load_rq1_config
from query_cost_predictor.rq1_evaluate import (
    calibration_table,
    classification_metric_table,
    evaluate_classification,
    load_feature_allowlist,
)
from query_cost_predictor.rq1_inputs import check_inputs
from query_cost_predictor.rq1_metrics import (
    CALIBRATION_COLUMNS,
    CALIBRATION_EDGES,
    Rq1MetricError,
    calibration_bin_index,
    calibration_bins,
)
from query_cost_predictor.rq1_models import classification_model_specs
from rq1_fixtures import build_release

pytestmark = [pytest.mark.filterwarnings("error::FutureWarning"), pytest.mark.filterwarnings("error::DeprecationWarning"),
              pytest.mark.filterwarnings("error::sklearn.exceptions.ConvergenceWarning")]


def test_edges_and_columns_are_the_declared_ones():
    config = load_rq1_config()
    bins = config.metrics_spec["classification"]["probability"]["calibration_bins"]
    assert tuple(float(e) for e in bins["edges"]) == CALIBRATION_EDGES and bins["n_bins"] == 10
    assert tuple(config.raw["outputs"]["calibration_bins_columns"]) == CALIBRATION_COLUMNS
    assert "observed_positive_fraction" in CALIBRATION_COLUMNS


def test_boundary_convention_at_zero_one_and_the_first_edge():
    assert calibration_bin_index([0.0, 0.1, 1.0]).tolist() == [0, 1, 9]


@pytest.mark.parametrize("position", range(1, 10))
def test_every_internal_edge_and_its_neighbours(position):
    edge = CALIBRATION_EDGES[position]
    below, above = np.nextafter(edge, 0.0), np.nextafter(edge, 1.0)
    assert calibration_bin_index([below, edge, above]).tolist() == [position - 1, position, position]


def test_values_next_to_the_outer_edges():
    assert calibration_bin_index([np.nextafter(0.0, 1.0), np.nextafter(1.0, 0.0)]).tolist() == [0, 9]


def test_empty_bins_have_zero_counts_and_null_rates_and_counts_sum():
    rows = calibration_bins([1, 0, 1], [0.05, 0.05, 0.95])
    assert [r["bin_index"] for r in rows] == list(range(10))
    assert [r["n_cases"] for r in rows] == [2, 0, 0, 0, 0, 0, 0, 0, 0, 1]
    assert rows[0] == {"bin_index": 0, "bin_lower": 0.0, "bin_upper": 0.1, "n_cases": 2, "n_positive": 1,
                       "mean_predicted_probability": 0.05, "observed_positive_fraction": 0.5}
    for row in rows[1:9]:
        assert (row["n_cases"], row["n_positive"]) == (0, 0)
        assert row["mean_predicted_probability"] is None and row["observed_positive_fraction"] is None
    assert rows[9]["bin_upper"] == 1.0 and rows[9]["observed_positive_fraction"] == 1.0
    assert sum(r["n_cases"] for r in rows) == 3


def test_single_class_and_empty_sets_are_still_described():
    negatives = calibration_bins([0, 0, 0, 0], [0.0, 0.12, 0.5, 1.0])
    assert [r["n_cases"] for r in negatives if r["n_cases"]] == [1, 1, 1, 1]
    assert all(r["observed_positive_fraction"] == 0.0 for r in negatives if r["n_cases"])
    positives = calibration_bins([1, 1], [0.3, 0.31])
    assert positives[3]["n_cases"] == 2 and positives[3]["observed_positive_fraction"] == 1.0
    empty = calibration_bins([], [])
    assert len(empty) == 10 and all(r["n_cases"] == 0 and r["mean_predicted_probability"] is None for r in empty)


@pytest.mark.parametrize("bad", [np.nan, -0.01, 1.01, np.inf])
def test_invalid_probabilities_fail(bad):
    with pytest.raises(Rq1MetricError, match="never clipped"):
        calibration_bins([1, 0], [0.5, bad])
    with pytest.raises(Rq1MetricError):
        calibration_bin_index([bad])


def test_non_empty_bins_agree_with_calibration_curve_away_from_edges():
    rng = np.random.default_rng(3)
    p = rng.uniform(0, 1, 400)
    p = p[np.abs(p * 10 - np.round(p * 10)) > 1e-9]  # calibration_curve puts an exact edge in the lower bin
    y = (rng.uniform(0, 1, p.size) < p).astype(int)
    rows = [r for r in calibration_bins(y, p) if r["n_cases"]]
    prob_true, prob_pred = calibration_curve(y, p, n_bins=10, strategy="uniform")
    assert np.allclose([r["observed_positive_fraction"] for r in rows], prob_true)
    assert np.allclose([r["mean_predicted_probability"] for r in rows], prob_pred)


# ----------------------------------------------------------------------------- calibration_bins table on the fixture
@pytest.fixture(scope="module")
def tables(tmp_path_factory):
    release = build_release(tmp_path_factory.mktemp("release"))
    config = release.config
    dataset = check_inputs(config, release.evidence_dir, repo_root=release.repo).dataset
    oof = evaluate_classification(dataset, config, load_feature_allowlist(config, release.repo))
    specs = classification_model_specs(config)
    return calibration_table(oof, dataset, config, specs), classification_metric_table(oof, dataset, config, specs)


def test_table_covers_every_probabilistic_model_scheme_and_slice_with_ten_bins(tables):
    bins, _ = tables
    assert all(tuple(row) == CALIBRATION_COLUMNS for row in bins)
    assert len(bins) == len(SCHEMES) * 5 * 18 * 10
    assert "clf_raw_cost_rank" not in {row["model_id"] for row in bins}
    slices = {}
    for row in bins:
        slices.setdefault((row["fold_scheme"], row["model_id"], row["scope"], row["fold"], row["stratum_column"],
                           row["stratum_value"]), []).append(row)
    assert all([r["bin_index"] for r in rows] == list(range(10)) for rows in slices.values())
    assert all((row["fold"] is None) == (row["scope"] == "pooled_out_of_fold") for row in bins)


def test_bin_counts_sum_to_the_rows_of_each_slice(tables):
    bins, metrics = tables
    totals, positives = {}, {}
    for row in bins:
        key = (row["fold_scheme"], row["model_id"], row["scope"], row["fold"], row["stratum_column"], row["stratum_value"])
        totals[key] = totals.get(key, 0) + row["n_cases"]
        positives[key] = positives.get(key, 0) + row["n_positive"]
    for row in metrics:
        key = (row["fold_scheme"], row["model_id"], row["scope"], row["fold"], row["stratum_column"], row["stratum_value"])
        if row["metric"] == "brier" and key in totals:
            assert totals[key] == row["n_keys"] and positives[key] == row["n_positive"]
    assert totals[("fold_group", "clf_hgb_all", "pooled_out_of_fold", None, None, None)] == 40
    empty = totals[("fold_group", "clf_hgb_all", "pooled_out_of_fold", None, "runtime_bin_ms", "100-1000")]
    assert empty == 0  # empty stratum: ten empty bins with null rates


def test_single_class_strata_receive_calibration_tables(tables):
    bins, _ = tables
    high = [r for r in bins if (r["fold_scheme"], r["model_id"], r["stratum_value"])
            == ("fold_group", "clf_logistic_all", "1000-10000")]
    assert len(high) == 10 and sum(r["n_cases"] for r in high) == 4
    assert all(r["observed_positive_fraction"] == 1.0 for r in high if r["n_cases"])
