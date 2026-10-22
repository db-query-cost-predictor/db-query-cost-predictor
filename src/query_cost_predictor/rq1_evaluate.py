"""RQ1 cross-validated evaluation on the stored folds (regression in Slice B) and the ``regression`` stage.

For each stored scheme (``fold_group``, ``fold_family``, ``fold_seen_template``)
and each fold 0-4, every declared model is built fresh, fitted on the other
four folds only and asked for the held-out fold. Folds are read from the typed
development dataset (``splits.csv``) and never generated; the 160
``heldout_rq2`` cases are not in that dataset, so they never reach fitting or
prediction. Every development case receives exactly one out-of-fold (OOF)
output per model and scheme (:func:`check_oof_coverage`).

Runtime predictions are exp(predicted ln runtime) with no smearing
correction. ``reg_raw_cost_rank`` produces only a ranking score, so its
``pred_ln``/``pred_ms`` are null and its runtime metrics carry the output-type
reason.

The stage writes ``fold_membership.csv`` (once per run), ``oof_regression.csv``
and ``metrics_regression.{csv,json}``; it adds the regression contribution to
the shared paired bootstrap and the regression section of the report to the
run context. The shared bootstrap files and ``report.md`` are assembled later,
once, by the final stage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Sequence

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
from query_cost_predictor.rq1_config import SCHEMES, Rq1Config
from query_cost_predictor.rq1_inputs import DevelopmentDataset, Rq1InputError
from query_cost_predictor.rq1_metrics import (
    METRIC_TABLE_COLUMNS,
    NULL_REASONS,
    REGRESSION_METRICS,
    RUNTIME_BIN_COLUMN,
    SCOPE_PER_FOLD,
    SCOPE_POOLED,
    TASK_REGRESSION,
    Rq1MetricError,
    check_metric_spec,
    csv_bytes,
    declared_strata,
    describe_rows,
    json_bytes,
    metric_row,
    regression_metric_values,
    runtime_metric_values,
    strata_frame,
)
from query_cost_predictor.rq1_models import RANK_SCORE, RUNTIME_PREDICTION, RegressionModel, RegressionModelSpec
from query_cost_predictor.splits import iter_folds

PRIMARY_SCHEME = "fold_group"
FOLD_MEMBERSHIP_FILE = "fold_membership.csv"
OOF_REGRESSION_FILE = "oof_regression.csv"
METRICS_REGRESSION_CSV = "metrics_regression.csv"
METRICS_REGRESSION_JSON = "metrics_regression.json"
OOF_REGRESSION_COLUMNS = ("modeling_key", "fold_scheme", "fold", "model_id", "y_true_ms", "y_true_ln", "pred_ln",
                          "pred_ms", "score")
FOLD_MEMBERSHIP_COLUMNS = ("modeling_key", "semantic_group_id", "structure_family", "query_instance_id", "component",
                           "snapshot_name", "configuration_name", *SCHEMES)
RANK_NOTE = ("Spearman and Kendall compare the actual median_ms with the model's native output (the predicted ln "
             "runtime, or the raw est_total_cost score); both are invariant to the exp back-transform.")

FitHook = Callable[[str, int, RegressionModel, np.ndarray, np.ndarray], None]


class Rq1EvaluationError(RuntimeError):
    """The cross-validated evaluation violated a coverage, isolation or ordering rule."""


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


# ----------------------------------------------------------------------------------
# Fold loop and OOF
# ----------------------------------------------------------------------------------
def evaluate_regression(dataset: DevelopmentDataset, config: Rq1Config, allowlist: Sequence[str], *,
                        on_fit: FitHook | None = None) -> pd.DataFrame:
    """OOF outputs of every declared regression model on every stored scheme.

    ``on_fit(scheme, fold, model, train_positions, test_positions)`` is called
    after each fit and prediction (used by tests to inspect fitted models).
    """
    keys = _check_aligned(dataset)
    specs = rq1_models.regression_model_specs(config)
    inputs = {spec.model_id: rq1_models.model_inputs(dataset, spec, allowlist) for spec in specs}
    y_ms, y_ln = dataset.targets["median_ms"], dataset.targets["ln_median_ms"]
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
                model = rq1_models.build_regression_model(spec)
                model.fit(frame.iloc[train], y_ms.iloc[train])
                if model.fit_keys_ != tuple(keys[train]):
                    raise Rq1EvaluationError(f"{spec.model_id} {scheme} fold {fold}: fitted rows are not the training fold")
                output = model.predict(frame.iloc[test])
                if on_fit is not None:
                    on_fit(scheme, int(fold), model, train, test)
                runtime = spec.output_type == RUNTIME_PREDICTION
                blocks.append(pd.DataFrame({
                    "modeling_key": keys[test].to_numpy(), "fold_scheme": scheme, "fold": int(fold),
                    "model_id": spec.model_id, "y_true_ms": y_ms.to_numpy()[test], "y_true_ln": y_ln.to_numpy()[test],
                    "pred_ln": output if runtime else np.nan, "pred_ms": np.exp(output) if runtime else np.nan,
                    "score": np.nan if runtime else output}))
    oof = pd.concat(blocks, ignore_index=True)
    order = {name: i for i, name in enumerate(SCHEMES)}
    models = {spec.model_id: i for i, spec in enumerate(specs)}
    oof = oof.assign(_s=oof["fold_scheme"].map(order), _m=oof["model_id"].map(models))
    oof = oof.sort_values(["_s", "_m", "modeling_key"], kind="mergesort").drop(columns=["_s", "_m"])
    oof = oof.reset_index(drop=True)[list(OOF_REGRESSION_COLUMNS)]
    check_oof_coverage(oof, keys, SCHEMES, [spec.model_id for spec in specs])
    return oof


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


def oof_export_rows(oof: pd.DataFrame, specs: Sequence[RegressionModelSpec]) -> list[dict[str, Any]]:
    """OOF rows for CSV: null ``pred_*`` for rank-score models and null ``score`` for runtime models only."""
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


# ----------------------------------------------------------------------------------
# Metric tables and bootstrap
# ----------------------------------------------------------------------------------
def _model_block(oof: pd.DataFrame, scheme: str, model_id: str, keys: pd.Index) -> pd.DataFrame:
    block = oof[(oof["fold_scheme"] == scheme) & (oof["model_id"] == model_id)].set_index("modeling_key")
    if not block.index.equals(keys):
        raise Rq1EvaluationError(f"{scheme}/{model_id}: OOF rows are not aligned with the development keys")
    return block


def _headline(config: Rq1Config) -> tuple[list[str], list[str]]:
    table = config.reporting["tables"]["headline_regression"]
    return [str(m) for m in table["models"]], [str(m) for m in table["metrics"]]


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


def regression_metric_table(oof: pd.DataFrame, dataset: DevelopmentDataset, config: Rq1Config,
                            specs: Sequence[RegressionModelSpec],
                            bootstrap: BootstrapContribution | None = None) -> list[dict[str, Any]]:
    """Pooled, per-fold and pooled-stratified rows for every scheme, model and regression metric."""
    keys = _check_aligned(dataset)
    metadata = dataset.metadata
    strata = strata_frame(metadata, dataset.targets["median_ms"], config)
    declared = declared_strata(config)
    headline_models, headline_metrics = _headline(config)
    rows: list[dict[str, Any]] = []
    for scheme in SCHEMES:
        for spec in specs:
            block = _model_block(oof, scheme, spec.model_id, keys)
            actual = block["y_true_ms"].to_numpy()
            runtime = spec.output_type == RUNTIME_PREDICTION
            pred = block["pred_ms"].to_numpy() if runtime else None
            native = block["pred_ln" if runtime else "score"].to_numpy()

            def emit(scope: str, fold: int | None, column: str | None, value: str | None, mask: np.ndarray) -> None:
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

            emit(SCOPE_POOLED, None, None, None, np.ones(len(keys), dtype=bool))
            folds = block["fold"].to_numpy()
            for fold in config.fold_values:
                emit(SCOPE_PER_FOLD, int(fold), None, None, folds == fold)
            for column, values in declared:
                labels = strata[column].to_numpy()
                total = 0
                for value in values:
                    mask = labels == value
                    total += int(mask.sum())
                    emit(SCOPE_POOLED, None, column, value, mask)
                if total != len(keys):
                    reason = "runtime-bin counts" if column == RUNTIME_BIN_COLUMN else f"{column} strata"
                    raise Rq1MetricError(f"{reason} sum to {total}, not the {len(keys)} evaluated cases")
    return rows


def metrics_document(rows: list[dict[str, Any]], config: Rq1Config) -> dict[str, Any]:
    headline_models, headline_metrics = _headline(config)
    return {
        "schema": "qcp.rq1_metrics", "schema_version": 1, "task": TASK_REGRESSION, "spec_id": config.spec_id,
        "config_sha256": config.sha256, "columns": list(METRIC_TABLE_COLUMNS),
        "target": "ln(median_ms); runtime predictions are exp(predicted ln) with no smearing correction",
        "metric_definitions": {**{k: METRIC_DEFINITIONS[k] for k in REGRESSION_METRICS}, "rank_note": RANK_NOTE},
        "null_reasons": dict(NULL_REASONS),
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
# The regression stage of rq1-run
# ----------------------------------------------------------------------------------
def stage_regression(context: Any) -> None:
    from threadpoolctl import threadpool_info, threadpool_limits

    from query_cost_predictor import rq1_report
    from query_cost_predictor.rq1_run import THREADPOOL_LIMIT

    dataset = context.artifacts.get("dataset")
    if not isinstance(dataset, DevelopmentDataset):
        raise Rq1EvaluationError("the regression stage needs the checked development dataset from input_check")
    config = context.config
    check_metric_spec(config)
    specs = rq1_models.regression_model_specs(config)
    allowlist = load_feature_allowlist(config, context.repo_root)
    with threadpool_limits(limits=THREADPOOL_LIMIT):
        effective = [{k: info.get(k) for k in ("user_api", "internal_api", "num_threads", "version")}
                     for info in threadpool_info()]
        if any(entry["num_threads"] != THREADPOOL_LIMIT for entry in effective):
            raise Rq1EvaluationError(f"thread pools are not limited to {THREADPOOL_LIMIT}: {effective}")
        oof = evaluate_regression(dataset, config, allowlist)
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
