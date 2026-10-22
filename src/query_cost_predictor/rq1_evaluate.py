"""RQ1 cross-validated evaluation on the stored folds, and the ``regression`` and ``classification_1s`` stages.

For each stored scheme (``fold_group``, ``fold_family``, ``fold_seen_template``)
and each fold 0-4, every declared model is built fresh, fitted on the other
four folds only and asked for the held-out fold. Folds are read from the typed
development dataset (``splits.csv``) and never generated; the 160
``heldout_rq2`` cases are not in that dataset, so they never reach fitting or
prediction. Every development case receives exactly one out-of-fold (OOF)
output per model and scheme (:func:`check_oof_coverage`).

Regression: runtime predictions are exp(predicted ln runtime) with no smearing
correction. ``reg_raw_cost_rank`` produces only a ranking score, so its
``pred_ln``/``pred_ms`` are null and its runtime metrics carry the output-type
reason.

1 s classification: the target is ``hr_1s`` (positive 1, negative 0). The
probabilistic models give the positive-class probability and ``pred_label`` is
1 when it is at least 0.5 (one predeclared operating point).
``clf_raw_cost_rank`` gives only a ranking score: no probability, no label, no
calibration bins. A request for the 10 s task is refused as
``NOT_YET_EVALUABLE`` (gate G-08).

The regression stage writes ``fold_membership.csv`` (once per run, shared),
``oof_regression.csv`` and ``metrics_regression.{csv,json}``. The
classification stage writes ``oof_classification.csv``,
``metrics_classification.{csv,json}`` and ``calibration_bins.csv``. Each stage
adds its contribution to the shared paired bootstrap and its section to the
report in the run context. The shared bootstrap files and ``report.md`` are
assembled later, once, by the final stage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

import numpy as np
import pandas as pd

from query_cost_predictor import rq1_models
from query_cost_predictor.hashing import sha256_file
from query_cost_predictor.metrics import METRIC_DEFINITIONS
from query_cost_predictor.rq1_bootstrap import (
    BootstrapContribution,
    BootstrapPlan,
    add_contribution,
    get_or_create_plan,
    paired_bootstrap,
)
from query_cost_predictor.rq1_config import DECISION_THRESHOLD, SCHEMES, Rq1Config
from query_cost_predictor.rq1_inputs import DevelopmentDataset, Rq1InputError
from query_cost_predictor.rq1_metrics import (
    CALIBRATION_COLUMNS,
    CLASSIFICATION_METRICS,
    METRIC_TABLE_COLUMNS,
    NULL_REASONS,
    REGRESSION_METRICS,
    RUNTIME_BIN_COLUMN,
    SCOPE_PER_FOLD,
    SCOPE_POOLED,
    TASK_CLASSIFICATION,
    TASK_REGRESSION,
    Rq1MetricError,
    average_precision_value,
    calibration_bins,
    check_metric_spec,
    check_probabilities,
    classification_flags,
    classification_metric_values,
    csv_bytes,
    declared_strata,
    describe_rows,
    json_bytes,
    metric_row,
    positive_support,
    regression_metric_values,
    runtime_metric_values,
    strata_frame,
)
from query_cost_predictor.rq1_models import PROBABILITY, RANK_SCORE, RUNTIME_PREDICTION, FoldModel, ModelSpec
from query_cost_predictor.splits import iter_folds

PRIMARY_SCHEME = "fold_group"
TASK_CLASSIFICATION_10S = "classification_10s"
FOLD_MEMBERSHIP_FILE = "fold_membership.csv"
OOF_REGRESSION_FILE = "oof_regression.csv"
METRICS_REGRESSION_CSV = "metrics_regression.csv"
METRICS_REGRESSION_JSON = "metrics_regression.json"
OOF_CLASSIFICATION_FILE = "oof_classification.csv"
METRICS_CLASSIFICATION_CSV = "metrics_classification.csv"
METRICS_CLASSIFICATION_JSON = "metrics_classification.json"
CALIBRATION_BINS_FILE = "calibration_bins.csv"
OOF_REGRESSION_COLUMNS = ("modeling_key", "fold_scheme", "fold", "model_id", "y_true_ms", "y_true_ln", "pred_ln",
                          "pred_ms", "score")
OOF_CLASSIFICATION_COLUMNS = ("modeling_key", "fold_scheme", "fold", "model_id", "y_true", "score", "score_type",
                              "pred_label")
FOLD_MEMBERSHIP_COLUMNS = ("modeling_key", "semantic_group_id", "structure_family", "query_instance_id", "component",
                           "snapshot_name", "configuration_name", *SCHEMES)
RANK_NOTE = ("Spearman and Kendall compare the actual median_ms with the model's native output (the predicted ln "
             "runtime, or the raw est_total_cost score); both are invariant to the exp back-transform.")
CLASSIFICATION_DEFINITIONS = {
    "average_precision": "average precision (AP): sklearn.metrics.average_precision_score(y, score, pos_label=1), "
                         "the non-interpolated step sum over thresholds; never trapezoidal PR-AUC",
    "roc_auc": "secondary: sklearn.metrics.roc_auc_score",
    "recall": "true positives / actual positives at probability >= 0.5",
    "false_negative_rate": "false negatives / actual positives at probability >= 0.5",
    "precision": "true positives / predicted positives at probability >= 0.5",
    "false_positive_rate": "false positives / actual negatives at probability >= 0.5",
    "warning_rate_per_100": "100 * predicted positives / cases at probability >= 0.5",
    "brier": "sklearn.metrics.brier_score_loss(scale_by_half=True) = mean((p - y)^2)",
}

FitHook = Callable[[str, int, FoldModel, np.ndarray, np.ndarray], None]


class Rq1EvaluationError(RuntimeError):
    """The cross-validated evaluation violated a coverage, isolation or ordering rule."""


class Rq1NotEvaluableError(Rq1EvaluationError):
    """The requested task is NOT_YET_EVALUABLE under the release gates; nothing is fitted or reported."""


def load_feature_allowlist(config: Rq1Config, repo_root: Path) -> list[str]:
    """The 69-column allowlist from the input record, after re-checking the record's pinned SHA-256."""
    path = Path(repo_root) / config.input_record_path
    observed = sha256_file(path)
    if observed != config.input_record_sha256:
        raise Rq1InputError(f"{config.input_record_path}: SHA-256 {observed} differs from the pinned "
                            f"{config.input_record_sha256}")
    return list(json.loads(path.read_text(encoding="utf-8"))["features"]["feature_columns"])


def _check_aligned(dataset: DevelopmentDataset) -> pd.Index:
    keys = dataset.features.index
    for name in ("baseline_inputs", "targets", "metadata", "folds"):
        if not getattr(dataset, name).index.equals(keys):
            raise Rq1EvaluationError(f"dataset.{name} is not aligned with the feature rows")
    if not keys.is_unique or not keys.is_monotonic_increasing:
        raise Rq1EvaluationError("development keys must be unique and sorted")
    return keys


def refuse_unevaluable(config: Rq1Config, task: str) -> None:
    """Only the 1 s classification task is evaluable; the 10 s task is refused as NOT_YET_EVALUABLE."""
    if task == TASK_CLASSIFICATION_10S:
        target = config.raw["targets"]["classification_10s"]
        raise Rq1NotEvaluableError(f"{task} is {target['status']} (gate G-08: {target['reason']}); no classifier "
                                   "is trained, evaluated or reported for it")
    if task != TASK_CLASSIFICATION:
        raise Rq1EvaluationError(f"unknown classification task {task!r}")


# ----------------------------------------------------------------------------------
# Fold loop and OOF
# ----------------------------------------------------------------------------------
def _fold_loop(dataset: DevelopmentDataset, config: Rq1Config, specs: Sequence[ModelSpec],
               inputs: dict[str, pd.DataFrame], build: Callable[[ModelSpec], FoldModel], target: pd.Series,
               make_block: Callable[[ModelSpec, str, int, np.ndarray, np.ndarray], pd.DataFrame],
               on_fit: FitHook | None) -> list[pd.DataFrame]:
    """Fit every model on each stored training fold and predict its test fold (identical rows for every model)."""
    keys = _check_aligned(dataset)
    blocks = []
    for scheme in SCHEMES:
        fold_ids = dataset.folds[scheme].to_numpy(dtype="int64")
        if sorted(set(fold_ids.tolist())) != list(config.fold_values):
            raise Rq1EvaluationError(f"{scheme}: stored folds {sorted(set(fold_ids.tolist()))} differ from "
                                     f"{list(config.fold_values)}")
        for fold, train, test in iter_folds(fold_ids):
            if np.intersect1d(train, test).size or train.size + test.size != len(keys):
                raise Rq1EvaluationError(f"{scheme} fold {fold}: training and test rows overlap or miss rows")
            for spec in specs:
                frame = inputs[spec.model_id]
                model = build(spec)
                model.fit(frame.iloc[train], target.iloc[train])
                if model.fit_keys_ != tuple(keys[train]):
                    raise Rq1EvaluationError(f"{spec.model_id} {scheme} fold {fold}: fitted rows are not the training fold")
                output = model.predict(frame.iloc[test])
                if on_fit is not None:
                    on_fit(scheme, int(fold), model, train, test)
                block = make_block(spec, scheme, int(fold), test, output)
                block.insert(0, "modeling_key", keys[test].to_numpy())
                blocks.append(block)
    return blocks


def _sorted_oof(blocks: list[pd.DataFrame], specs: Sequence[ModelSpec], columns: Sequence[str],
                keys: pd.Index) -> pd.DataFrame:
    oof = pd.concat(blocks, ignore_index=True)
    order = {name: i for i, name in enumerate(SCHEMES)}
    models = {spec.model_id: i for i, spec in enumerate(specs)}
    oof = oof.assign(_s=oof["fold_scheme"].map(order), _m=oof["model_id"].map(models))
    oof = oof.sort_values(["_s", "_m", "modeling_key"], kind="mergesort").drop(columns=["_s", "_m"])
    oof = oof.reset_index(drop=True)[list(columns)]
    check_oof_coverage(oof, keys, SCHEMES, [spec.model_id for spec in specs])
    return oof


def evaluate_regression(dataset: DevelopmentDataset, config: Rq1Config, allowlist: Sequence[str], *,
                        on_fit: FitHook | None = None) -> pd.DataFrame:
    """OOF outputs of every declared regression model on every stored scheme.

    ``on_fit(scheme, fold, model, train_positions, test_positions)`` is called
    after each fit and prediction (used by tests to inspect fitted models).
    """
    keys = _check_aligned(dataset)
    specs = rq1_models.regression_model_specs(config)
    inputs = {spec.model_id: rq1_models.model_inputs(dataset, spec, allowlist) for spec in specs}
    y_ms, y_ln = dataset.targets["median_ms"].to_numpy(), dataset.targets["ln_median_ms"].to_numpy()

    def block(spec: ModelSpec, scheme: str, fold: int, test: np.ndarray, output: np.ndarray) -> pd.DataFrame:
        runtime = spec.output_type == RUNTIME_PREDICTION
        return pd.DataFrame({
            "fold_scheme": scheme, "fold": fold, "model_id": spec.model_id, "y_true_ms": y_ms[test],
            "y_true_ln": y_ln[test], "pred_ln": output if runtime else np.nan,
            "pred_ms": np.exp(output) if runtime else np.nan, "score": np.nan if runtime else output})
    blocks = _fold_loop(dataset, config, specs, inputs, lambda spec: rq1_models.build_regression_model(spec),
                        dataset.targets["median_ms"], block, on_fit)
    return _sorted_oof(blocks, specs, OOF_REGRESSION_COLUMNS, keys)


def evaluate_classification(dataset: DevelopmentDataset, config: Rq1Config, allowlist: Sequence[str], *,
                            task: str = TASK_CLASSIFICATION, on_fit: FitHook | None = None) -> pd.DataFrame:
    """OOF outputs of every declared 1 s classifier on every stored scheme (the 10 s task is refused)."""
    refuse_unevaluable(config, task)
    keys = _check_aligned(dataset)
    specs = rq1_models.classification_model_specs(config)
    inputs = {spec.model_id: rq1_models.model_inputs(dataset, spec, allowlist) for spec in specs}
    target = dataset.targets["hr_1s"].astype("int64")
    if not target.isin((0, 1)).all():
        raise Rq1EvaluationError("hr_1s must be 0 (negative) or 1 (positive) on every development row")
    y = target.to_numpy()

    def block(spec: ModelSpec, scheme: str, fold: int, test: np.ndarray, output: np.ndarray) -> pd.DataFrame:
        probability = spec.output_type == PROBABILITY
        labels = (output >= DECISION_THRESHOLD).astype("int64").tolist() if probability else [None] * test.size
        return pd.DataFrame({"fold_scheme": scheme, "fold": fold, "model_id": spec.model_id, "y_true": y[test],
                             "score": output, "score_type": spec.output_type,
                             "pred_label": pd.Series(labels, dtype=object)})
    blocks = _fold_loop(dataset, config, specs, inputs, lambda spec: rq1_models.build_classification_model(spec),
                        target, block, on_fit)
    return _sorted_oof(blocks, specs, OOF_CLASSIFICATION_COLUMNS, keys)


def check_oof_coverage(oof: pd.DataFrame, keys: Sequence[str], schemes: Sequence[str], model_ids: Sequence[str]) -> None:
    """Every development case has exactly one OOF output per model and scheme, and nothing else."""
    expected = sorted(str(k) for k in keys)
    pairs = set(zip(oof["fold_scheme"], oof["model_id"]))
    if pairs != {(s, m) for s in schemes for m in model_ids}:
        raise Rq1EvaluationError(f"OOF holds scheme/model pairs {sorted(pairs)}")
    for (scheme, model_id), block in oof.groupby(["fold_scheme", "model_id"], sort=False):
        if sorted(block["modeling_key"].tolist()) != expected:
            raise Rq1EvaluationError(f"{scheme}/{model_id}: OOF rows are not exactly one per development case")
    if len(oof) != len(expected) * len(schemes) * len(model_ids):
        raise Rq1EvaluationError("OOF row count differs from cases x schemes x models")


def oof_export_rows(oof: pd.DataFrame, specs: Sequence[ModelSpec]) -> list[dict[str, Any]]:
    """OOF regression rows for CSV: null ``pred_*`` for rank-score models and null ``score`` for runtime models only."""
    output_types = {spec.model_id: spec.output_type for spec in specs}
    rows = []
    for record in oof.to_dict(orient="records"):
        rank = output_types[record["model_id"]] == RANK_SCORE
        structural_null = ("pred_ln", "pred_ms") if rank else ("score",)
        row = {}
        for column in OOF_REGRESSION_COLUMNS:
            value = record[column]
            if column in structural_null:
                if not (isinstance(value, float) and np.isnan(value)):
                    raise Rq1EvaluationError(f"{record['model_id']}: {column} must be null for its output type")
                value = None
            elif isinstance(value, float) and not np.isfinite(value):
                raise Rq1EvaluationError(f"{record['model_id']} {record['modeling_key']}: {column} is not finite")
            row[column] = value
        rows.append(row)
    return rows


def oof_classification_rows(oof: pd.DataFrame, specs: Sequence[ModelSpec]) -> list[dict[str, Any]]:
    """OOF classification rows for CSV; ``pred_label`` is null exactly for rank scores."""
    output_types = {spec.model_id: spec.output_type for spec in specs}
    rows = []
    for record in oof.to_dict(orient="records"):
        output_type = output_types[record["model_id"]]
        score, label = float(record["score"]), record["pred_label"]
        if record["score_type"] != output_type or not np.isfinite(score):
            raise Rq1EvaluationError(f"{record['model_id']} {record['modeling_key']}: invalid score or score type")
        if output_type == PROBABILITY:
            check_probabilities([score])
            if label != int(score >= DECISION_THRESHOLD):
                raise Rq1EvaluationError(f"{record['model_id']}: pred_label disagrees with the 0.5 threshold")
            label = int(label)
        elif label is not None:
            raise Rq1EvaluationError(f"{record['model_id']}: a rank score never carries a predicted label")
        rows.append({"modeling_key": record["modeling_key"], "fold_scheme": record["fold_scheme"],
                     "fold": int(record["fold"]), "model_id": record["model_id"], "y_true": int(record["y_true"]),
                     "score": score, "score_type": output_type, "pred_label": label})
    return rows


# ----------------------------------------------------------------------------------
# Reporting slices, metric tables and bootstrap
# ----------------------------------------------------------------------------------
def _model_block(oof: pd.DataFrame, scheme: str, model_id: str, keys: pd.Index) -> pd.DataFrame:
    block = oof[(oof["fold_scheme"] == scheme) & (oof["model_id"] == model_id)].set_index("modeling_key")
    if not block.index.equals(keys):
        raise Rq1EvaluationError(f"{scheme}/{model_id}: OOF rows are not aligned with the development keys")
    return block


def iter_slices(folds: np.ndarray, strata: pd.DataFrame, config: Rq1Config) -> Iterator[
        tuple[str, int | None, str | None, str | None, np.ndarray]]:
    """Pooled, per-fold and pooled-stratified row masks; every stratum column must cover every row once."""
    n = folds.size
    yield SCOPE_POOLED, None, None, None, np.ones(n, dtype=bool)
    for fold in config.fold_values:
        yield SCOPE_PER_FOLD, int(fold), None, None, folds == fold
    for column, values in declared_strata(config):
        labels = strata[column].to_numpy()
        total = 0
        for value in values:
            mask = labels == value
            total += int(mask.sum())
            yield SCOPE_POOLED, None, column, value, mask
        if total != n:
            reason = "runtime-bin counts" if column == RUNTIME_BIN_COLUMN else f"{column} strata"
            raise Rq1MetricError(f"{reason} sum to {total}, not the {n} evaluated cases")


def _headline(config: Rq1Config, table: str = "headline_regression") -> tuple[list[str], list[str]]:
    spec = config.reporting["tables"][table]
    return [str(m) for m in spec["models"]], [str(m) for m in spec["metrics"]]


def regression_bootstrap(plan: BootstrapPlan, oof: pd.DataFrame, dataset: DevelopmentDataset,
                         config: Rq1Config) -> BootstrapContribution:
    """Paired cluster bootstrap of the headline regression metrics on pooled ``fold_group`` OOF rows."""
    keys = _check_aligned(dataset)
    models, metrics = _headline(config)
    statistics = {}
    for model_id in models:
        block = _model_block(oof, PRIMARY_SCHEME, model_id, keys)
        actual, pred = block["y_true_ms"].to_numpy(), block["pred_ms"].to_numpy()
        statistics[model_id] = (lambda rows, a=actual, p=pred: runtime_metric_values(a[rows], p[rows]))
    spec = config.uncertainty["differences"]["regression"]
    differences = [(str(l), str(b)) for l in spec["learned"] for b in spec["against"]]
    return paired_bootstrap(TASK_REGRESSION, plan, dataset.metadata[plan.cluster_column].tolist(), statistics, metrics,
                            differences, str(spec["direction"]))


def classification_bootstrap(plan: BootstrapPlan, oof: pd.DataFrame, dataset: DevelopmentDataset,
                             config: Rq1Config) -> BootstrapContribution:
    """Paired cluster bootstrap of average precision for every classifier on pooled ``fold_group`` OOF rows.

    Uses the run's single plan, so the group draws are identical to the
    regression contribution. A replicate without positives is invalid with
    its reason; an all-positive replicate is valid (AP = 1).
    """
    keys = _check_aligned(dataset)
    models, metrics = _headline(config, "headline_classification")
    statistics = {}
    for model_id in models:
        block = _model_block(oof, PRIMARY_SCHEME, model_id, keys)
        y, score = block["y_true"].to_numpy(dtype="int64"), block["score"].to_numpy(dtype="float64")
        statistics[model_id] = (lambda rows, a=y, s=score: {"average_precision": average_precision_value(a[rows], s[rows])})
    spec = config.uncertainty["differences"]["classification"]
    differences = [(str(l), str(b)) for l in spec["learned"] for b in spec["against"]]
    return paired_bootstrap(TASK_CLASSIFICATION, plan, dataset.metadata[plan.cluster_column].tolist(), statistics,
                            metrics, differences, str(spec["direction"]))


def regression_metric_table(oof: pd.DataFrame, dataset: DevelopmentDataset, config: Rq1Config,
                            specs: Sequence[ModelSpec],
                            bootstrap: BootstrapContribution | None = None) -> list[dict[str, Any]]:
    """Pooled, per-fold and pooled-stratified rows for every scheme, model and regression metric."""
    keys = _check_aligned(dataset)
    metadata = dataset.metadata
    strata = strata_frame(metadata, dataset.targets["median_ms"], config)
    headline_models, headline_metrics = _headline(config)
    rows: list[dict[str, Any]] = []
    for scheme in SCHEMES:
        for spec in specs:
            block = _model_block(oof, scheme, spec.model_id, keys)
            actual = block["y_true_ms"].to_numpy()
            runtime = spec.output_type == RUNTIME_PREDICTION
            pred = block["pred_ms"].to_numpy() if runtime else None
            native = block["pred_ln" if runtime else "score"].to_numpy()
            for scope, fold, column, value, mask in iter_slices(block["fold"].to_numpy(), strata, config):
                values = regression_metric_values(spec.output_type, actual[mask], None if pred is None else pred[mask],
                                                  native[mask])
                descriptor = describe_rows(metadata[mask])
                for metric in REGRESSION_METRICS:
                    ci = (None, None)
                    if (bootstrap is not None and scheme == PRIMARY_SCHEME and scope == SCOPE_POOLED and column is None
                            and spec.model_id in headline_models and metric in headline_metrics):
                        ci = bootstrap.interval(spec.model_id, metric)
                    rows.append(metric_row(task=TASK_REGRESSION, fold_scheme=scheme, model_id=spec.model_id,
                                           scope=scope, fold=fold, stratum_column=column, stratum_value=value,
                                           descriptor=descriptor, metric=metric, result=values[metric], ci=ci))
    return rows


def classification_metric_table(oof: pd.DataFrame, dataset: DevelopmentDataset, config: Rq1Config,
                                specs: Sequence[ModelSpec],
                                bootstrap: BootstrapContribution | None = None) -> list[dict[str, Any]]:
    """Pooled, per-fold and pooled-stratified rows for every scheme, classifier and metric, with counts and flags.

    Pooled values always come from the fold-specific OOF scores of the rows in
    the slice; no per-fold identity is assumed.
    """
    keys = _check_aligned(dataset)
    metadata = dataset.metadata
    groups = metadata["semantic_group_id"].to_numpy(dtype=object)
    strata = strata_frame(metadata, dataset.targets["median_ms"], config)
    headline_models, headline_metrics = _headline(config, "headline_classification")
    rows: list[dict[str, Any]] = []
    for scheme in SCHEMES:
        for spec in specs:
            block = _model_block(oof, scheme, spec.model_id, keys)
            y, score = block["y_true"].to_numpy(dtype="int64"), block["score"].to_numpy(dtype="float64")
            folds = block["fold"].to_numpy()
            for scope, fold, column, value, mask in iter_slices(folds, strata, config):
                values = classification_metric_values(spec.output_type, y[mask], score[mask])
                descriptor = {**describe_rows(metadata[mask]), **positive_support(y[mask], groups[mask], folds[mask])}
                for metric in CLASSIFICATION_METRICS:
                    result = values[metric]
                    ci = (None, None)
                    if (bootstrap is not None and scheme == PRIMARY_SCHEME and scope == SCOPE_POOLED and column is None
                            and spec.model_id in headline_models and metric in headline_metrics):
                        ci = bootstrap.interval(spec.model_id, metric)
                    rows.append(metric_row(task=TASK_CLASSIFICATION, fold_scheme=scheme, model_id=spec.model_id,
                                           scope=scope, fold=fold, stratum_column=column, stratum_value=value,
                                           descriptor=descriptor, metric=metric, result=result, ci=ci,
                                           flag=classification_flags(scope, descriptor, result.value is not None)))
    return rows


def calibration_table(oof: pd.DataFrame, dataset: DevelopmentDataset, config: Rq1Config,
                      specs: Sequence[ModelSpec]) -> list[dict[str, Any]]:
    """The fixed ten-bin calibration table for every probabilistic classifier, scheme and reporting slice.

    Rank-score models get no bins. Every slice lists all ten bins (empty ones
    with zero counts and null rates), and its counts sum to its rows.
    """
    keys = _check_aligned(dataset)
    strata = strata_frame(dataset.metadata, dataset.targets["median_ms"], config)
    edges = config.metrics_spec["classification"]["probability"]["calibration_bins"]["edges"]
    rows: list[dict[str, Any]] = []
    for scheme in SCHEMES:
        for spec in specs:
            if spec.output_type != PROBABILITY:
                continue
            block = _model_block(oof, scheme, spec.model_id, keys)
            y, p = block["y_true"].to_numpy(dtype="int64"), block["score"].to_numpy(dtype="float64")
            for scope, fold, column, value, mask in iter_slices(block["fold"].to_numpy(), strata, config):
                for entry in calibration_bins(y[mask], p[mask], [float(e) for e in edges]):
                    rows.append({"fold_scheme": scheme, "model_id": spec.model_id, "scope": scope, "fold": fold,
                                 "stratum_column": column, "stratum_value": value, **entry})
    return rows


def metrics_document(rows: list[dict[str, Any]], config: Rq1Config, task: str = TASK_REGRESSION) -> dict[str, Any]:
    table = "headline_regression" if task == TASK_REGRESSION else "headline_classification"
    headline_models, headline_metrics = _headline(config, table)
    if task == TASK_REGRESSION:
        target = "ln(median_ms); runtime predictions are exp(predicted ln) with no smearing correction"
        definitions = {**{k: METRIC_DEFINITIONS[k] for k in REGRESSION_METRICS}, "rank_note": RANK_NOTE}
    else:
        target = ("hr_1s (positive 1, negative 0; frozen 1,000 ms label boundary); predicted positive when the "
                  "probability is >= 0.5, one predeclared operating point")
        definitions = {**CLASSIFICATION_DEFINITIONS, "flags": dict(config.metrics_spec["flags"]),
                       "operating_point": config.raw["fixed"]["operating_point"]}
    return {
        "schema": "qcp.rq1_metrics", "schema_version": 1, "task": task, "spec_id": config.spec_id,
        "config_sha256": config.sha256, "columns": list(METRIC_TABLE_COLUMNS), "target": target,
        "metric_definitions": definitions, "null_reasons": dict(NULL_REASONS),
        "headline": {"fold_scheme": PRIMARY_SCHEME, "scope": SCOPE_POOLED, "models": headline_models,
                     "metrics": headline_metrics, "intervals": "paired cluster bootstrap (bootstrap_summary.json)"},
        "rows": rows,
    }


def fold_membership_rows(dataset: DevelopmentDataset) -> list[dict[str, Any]]:
    keys = _check_aligned(dataset)
    frame = dataset.metadata.join(dataset.folds)[list(FOLD_MEMBERSHIP_COLUMNS[1:])]
    return [{"modeling_key": str(key), **{c: (int(v) if c in SCHEMES else str(v)) for c, v in row.items()}}
            for key, row in zip(keys, frame.to_dict(orient="records"))]


def write_fold_membership(context: Any, dataset: DevelopmentDataset) -> None:
    """``fold_membership.csv`` is shared by every task and written once per run."""
    if FOLD_MEMBERSHIP_FILE not in context.payloads:
        context.write_payload(FOLD_MEMBERSHIP_FILE, csv_bytes(fold_membership_rows(dataset), FOLD_MEMBERSHIP_COLUMNS))


# ----------------------------------------------------------------------------------
# Stages of rq1-run
# ----------------------------------------------------------------------------------
def _stage_inputs(context: Any, stage: str) -> tuple[DevelopmentDataset, Rq1Config, list[str]]:
    dataset = context.artifacts.get("dataset")
    if not isinstance(dataset, DevelopmentDataset):
        raise Rq1EvaluationError(f"the {stage} stage needs the checked development dataset from input_check")
    check_metric_spec(context.config)
    return dataset, context.config, load_feature_allowlist(context.config, context.repo_root)


def _under_thread_limit(function: Callable[[], pd.DataFrame]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Run the fits under a nested one-thread limit and record the effective pools (all must be 1)."""
    from threadpoolctl import threadpool_info, threadpool_limits

    from query_cost_predictor.rq1_run import THREADPOOL_LIMIT

    with threadpool_limits(limits=THREADPOOL_LIMIT):
        effective = [{k: info.get(k) for k in ("user_api", "internal_api", "num_threads", "version")}
                     for info in threadpool_info()]
        if any(entry["num_threads"] != THREADPOOL_LIMIT for entry in effective):
            raise Rq1EvaluationError(f"thread pools are not limited to {THREADPOOL_LIMIT}: {effective}")
        return function(), effective


def stage_regression(context: Any) -> None:
    from query_cost_predictor import rq1_report
    from query_cost_predictor.rq1_run import THREADPOOL_LIMIT

    dataset, config, allowlist = _stage_inputs(context, "regression")
    specs = rq1_models.regression_model_specs(config)
    oof, effective = _under_thread_limit(lambda: evaluate_regression(dataset, config, allowlist))
    plan = get_or_create_plan(context, dataset.metadata[config.uncertainty["cluster_column"]].tolist())
    contribution = regression_bootstrap(plan, oof, dataset, config)
    add_contribution(context, contribution)
    rows = regression_metric_table(oof, dataset, config, specs, contribution)

    write_fold_membership(context, dataset)
    context.write_payload(OOF_REGRESSION_FILE, csv_bytes(oof_export_rows(oof, specs), OOF_REGRESSION_COLUMNS))
    context.write_payload(METRICS_REGRESSION_CSV, csv_bytes(rows, METRIC_TABLE_COLUMNS))
    context.write_payload(METRICS_REGRESSION_JSON, json_bytes(metrics_document(rows, config)))
    context.artifacts.setdefault("report_sections", {})["regression"] = rq1_report.regression_section(
        rows, contribution.summary, config)
    context.provenance["regression"] = {
        "models": [spec.model_id for spec in specs],
        "fold_schemes": list(SCHEMES),
        "fits_per_model": len(SCHEMES) * len(config.fold_values),
        "development_rows": int(len(dataset.features)),
        "oof_rows": int(len(oof)),
        "metric_rows": len(rows),
        "bootstrap_replicates": plan.replicates,
        "threadpool_during_fit": {"requested_limit": THREADPOOL_LIMIT, "effective": effective},
    }


def stage_classification_1s(context: Any) -> None:
    from query_cost_predictor import rq1_report
    from query_cost_predictor.rq1_run import THREADPOOL_LIMIT

    dataset, config, allowlist = _stage_inputs(context, TASK_CLASSIFICATION)
    specs = rq1_models.classification_model_specs(config)
    oof, effective = _under_thread_limit(lambda: evaluate_classification(dataset, config, allowlist))
    plan = get_or_create_plan(context, dataset.metadata[config.uncertainty["cluster_column"]].tolist())
    contribution = classification_bootstrap(plan, oof, dataset, config)
    add_contribution(context, contribution)
    rows = classification_metric_table(oof, dataset, config, specs, contribution)
    bins = calibration_table(oof, dataset, config, specs)

    write_fold_membership(context, dataset)
    context.write_payload(OOF_CLASSIFICATION_FILE, csv_bytes(oof_classification_rows(oof, specs),
                                                             OOF_CLASSIFICATION_COLUMNS))
    context.write_payload(METRICS_CLASSIFICATION_CSV, csv_bytes(rows, METRIC_TABLE_COLUMNS))
    context.write_payload(METRICS_CLASSIFICATION_JSON, json_bytes(metrics_document(rows, config, TASK_CLASSIFICATION)))
    context.write_payload(CALIBRATION_BINS_FILE, csv_bytes(bins, CALIBRATION_COLUMNS))
    context.artifacts.setdefault("report_sections", {})[TASK_CLASSIFICATION] = rq1_report.classification_section(
        rows, contribution.summary, config)
    context.provenance[TASK_CLASSIFICATION] = {
        "models": [spec.model_id for spec in specs],
        "fold_schemes": list(SCHEMES),
        "fits_per_model": len(SCHEMES) * len(config.fold_values),
        "development_rows": int(len(dataset.features)),
        "oof_rows": int(len(oof)),
        "metric_rows": len(rows),
        "calibration_rows": len(bins),
        "decision_threshold": DECISION_THRESHOLD,
        "bootstrap_replicates": plan.replicates,
        "threadpool_during_fit": {"requested_limit": THREADPOOL_LIMIT, "effective": effective},
    }
