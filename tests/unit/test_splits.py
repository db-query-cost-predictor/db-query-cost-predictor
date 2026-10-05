"""Grouped-split disjointness and deterministic fold assignment."""

from __future__ import annotations

import numpy as np
import pytest

from query_cost_predictor.splits import (
    SplitError,
    assert_groups_disjoint,
    fold_summary,
    group_kfold,
    iter_folds,
    random_kfold,
)


def test_group_kfold_keeps_every_group_in_one_fold() -> None:
    groups = [f"template_{i % 7}" for i in range(70)]
    folds = group_kfold(groups, 5, seed=3)
    assert_groups_disjoint(folds, groups)
    assert set(folds.tolist()) == set(range(5))


def test_group_kfold_is_deterministic_for_a_seed() -> None:
    groups = [f"g{i % 11}" for i in range(55)]
    assert np.array_equal(group_kfold(groups, 5, seed=7), group_kfold(groups, 5, seed=7))


def test_group_kfold_needs_enough_groups() -> None:
    with pytest.raises(SplitError):
        group_kfold(["a", "a", "b"], 5, seed=0)


def test_leaking_group_is_detected() -> None:
    with pytest.raises(SplitError):
        assert_groups_disjoint([0, 1], ["same_template", "same_template"])


def test_random_kfold_is_a_balanced_partition() -> None:
    folds = random_kfold(23, 5, seed=1)
    counts = np.bincount(folds)
    assert len(folds) == 23 and counts.max() - counts.min() <= 1
    assert np.array_equal(folds, random_kfold(23, 5, seed=1))


def test_iter_folds_cover_all_rows_without_overlap() -> None:
    groups = [f"g{i % 6}" for i in range(30)]
    folds = group_kfold(groups, 3, seed=0)
    seen = []
    for _, train, test in iter_folds(folds):
        assert set(train).isdisjoint(set(test))
        assert {groups[i] for i in train}.isdisjoint({groups[i] for i in test})
        seen.extend(test.tolist())
    assert sorted(seen) == list(range(30))


def test_fold_summary_reports_keys_and_groups() -> None:
    groups = ["a", "a", "b", "b", "c", "c"]
    folds = group_kfold(groups, 3, seed=0)
    summary = fold_summary(folds, groups)
    assert sum(row["n_keys"] for row in summary) == 6
    assert all(row["n_groups"] == 1 and row["keys_per_group"] == 2 for row in summary)
