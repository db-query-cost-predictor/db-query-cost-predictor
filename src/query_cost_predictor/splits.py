"""Deterministic fold assignment (no dependence on library version behaviour).

* :func:`random_kfold` — row-wise folds; used **only** as a leakage diagnostic.
* :func:`group_kfold` — every group (template or semantic group) is assigned to
  exactly one fold; this is the primary evaluation.

Fold assignments are computed once and reused for every model so that model
comparisons use identical folds.
"""

from __future__ import annotations

from typing import Iterator, Sequence

import numpy as np


class SplitError(ValueError):
    """Invalid fold configuration or a group leaking across folds."""


def random_kfold(n_rows: int, n_folds: int, seed: int) -> np.ndarray:
    if n_folds < 2 or n_rows < n_folds:
        raise SplitError("random_kfold needs n_folds >= 2 and at least n_folds rows")
    rng = np.random.default_rng(seed)
    order = rng.permutation(n_rows)
    folds = np.empty(n_rows, dtype=int)
    for position, row in enumerate(order):
        folds[row] = position % n_folds
    return folds


def group_kfold(groups: Sequence[object], n_folds: int, seed: int) -> np.ndarray:
    """Assign whole groups to folds, balancing row counts greedily.

    Groups are shuffled with ``seed``, stably sorted by size (largest first) and
    each is placed in the fold that currently holds the fewest rows (ties go to
    the lowest fold index). The result depends only on the inputs and seed.
    """
    group_list = [str(g) for g in groups]
    unique = sorted(set(group_list))
    if n_folds < 2 or len(unique) < n_folds:
        raise SplitError(f"group_kfold needs n_folds >= 2 and at least n_folds groups (got {len(unique)})")
    sizes = {g: group_list.count(g) for g in unique}
    rng = np.random.default_rng(seed)
    shuffled = [unique[i] for i in rng.permutation(len(unique))]
    ordered = sorted(shuffled, key=lambda g: -sizes[g])  # stable: keeps shuffled order among ties
    fold_rows = [0] * n_folds
    assignment: dict[str, int] = {}
    for group in ordered:
        target = min(range(n_folds), key=lambda f: (fold_rows[f], f))
        assignment[group] = target
        fold_rows[target] += sizes[group]
    return np.array([assignment[g] for g in group_list], dtype=int)


def assert_groups_disjoint(fold_ids: Sequence[int], groups: Sequence[object]) -> None:
    """Raise :class:`SplitError` if any group appears in more than one fold."""
    seen: dict[str, set[int]] = {}
    for fold, group in zip(fold_ids, groups):
        seen.setdefault(str(group), set()).add(int(fold))
    leaking = {g: sorted(f) for g, f in seen.items() if len(f) > 1}
    if leaking:
        raise SplitError(f"groups appear in multiple folds: {leaking}")


def iter_folds(fold_ids: Sequence[int]) -> Iterator[tuple[int, np.ndarray, np.ndarray]]:
    ids = np.asarray(fold_ids, dtype=int)
    for fold in sorted(set(ids.tolist())):
        test = np.flatnonzero(ids == fold)
        train = np.flatnonzero(ids != fold)
        yield fold, train, test


def fold_summary(fold_ids: Sequence[int], groups: Sequence[object]) -> list[dict[str, float]]:
    """Keys, groups and keys per group in each test fold."""
    ids = np.asarray(fold_ids, dtype=int)
    group_array = np.asarray([str(g) for g in groups])
    summary = []
    for fold in sorted(set(ids.tolist())):
        mask = ids == fold
        n_keys = int(mask.sum())
        n_groups = int(len(set(group_array[mask].tolist())))
        summary.append(
            {
                "fold": fold,
                "n_keys": n_keys,
                "n_groups": n_groups,
                "keys_per_group": (n_keys / n_groups) if n_groups else float("nan"),
            }
        )
    return summary
