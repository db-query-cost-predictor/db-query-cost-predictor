"""RQ1 estimator factory: every declared regression and 1 s classification model, built from the YAML.

Each entry of ``regression_models`` / ``classification_models`` in
``config/rq1_modeling_v1.yaml`` becomes a fold model with one interface:
``fit`` on training rows and ``predict`` on test rows, both given the frame
selected by :func:`model_inputs` (indexed by ``modeling_key``).

Regression (target ln(``median_ms``)):

* ``reg_train_median``: ln(median of the training-fold ``median_ms``), the same
  value for every test row.
* ``reg_raw_cost_rank``: the raw ``est_total_cost`` as a ranking score; nothing
  is fitted and no runtime is predicted.
* ``reg_cost_ols`` / ``reg_scale_cost_ols``: ``LinearRegression`` on the natural
  log of the raw cost (``estimates.csv``), optionally plus ln(scale factor).
  Never the log1p feature ``est_total_cost_log``.
* ``reg_ridge_all`` / ``reg_hgb_all``: the declared pipeline or estimator on
  exactly the 69 allowlisted features (names and order re-checked here).

1 s classification (target ``hr_1s``: positive 1, negative 0):

* ``clf_train_prevalence``: the training fold's positive share as the
  probability of every test row.
* ``clf_raw_cost_rank``: the raw ``est_total_cost`` as a ranking score; no
  probability is invented.
* ``clf_cost_logistic`` / ``clf_scale_cost_logistic``: ``StandardScaler`` and
  L2 ``LogisticRegression`` on ln(cost), optionally plus ln(scale factor).
* ``clf_logistic_all`` / ``clf_hgb_all``: the declared pipeline or estimator on
  exactly the 69 allowlisted features. ``penalty`` is never passed (L2 is
  ``l1_ratio=0.0``).

Parameters are passed verbatim from the YAML; nothing is searched, selected,
rebalanced or calibrated. Every output is checked: a wrong shape, a non-finite
value, an ``exp`` overflow of a predicted log runtime or a probability outside
[0, 1] raises :class:`Rq1ModelOutputError` and is never clipped or repaired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from query_cost_predictor.rq1_config import Rq1Config
from query_cost_predictor.rq1_inputs import assert_exact_feature_columns

TASK_REGRESSION, TASK_CLASSIFICATION = "regression", "classification_1s"
REGRESSION_MODEL_IDS = ("reg_train_median", "reg_raw_cost_rank", "reg_cost_ols", "reg_scale_cost_ols",
                        "reg_ridge_all", "reg_hgb_all")
CLASSIFICATION_MODEL_IDS = ("clf_train_prevalence", "clf_raw_cost_rank", "clf_cost_logistic",
                            "clf_scale_cost_logistic", "clf_logistic_all", "clf_hgb_all")
RUNTIME_PREDICTION, RANK_SCORE, PROBABILITY = "runtime_prediction", "rank_score", "probability"
ALLOWLIST_INPUT = "allowlist"
BASELINE_INPUT_COLUMNS = ("est_total_cost", "ln_cost", "ln_scale")
REGRESSION_KINDS = {
    "training_fold_median": RUNTIME_PREDICTION,
    "raw_planner_cost": RANK_SCORE,
    "ols": RUNTIME_PREDICTION,
    "pipeline": RUNTIME_PREDICTION,
    "estimator": RUNTIME_PREDICTION,
}
CLASSIFICATION_KINDS = {
    "training_fold_prevalence": PROBABILITY,
    "raw_planner_cost": RANK_SCORE,
    "pipeline": PROBABILITY,
    "estimator": PROBABILITY,
}
REGRESSION_ESTIMATORS = {
    "StandardScaler": StandardScaler,
    "Ridge": Ridge,
    "LinearRegression": LinearRegression,
    "HistGradientBoostingRegressor": HistGradientBoostingRegressor,
}
CLASSIFICATION_ESTIMATORS = {
    "StandardScaler": StandardScaler,
    "LogisticRegression": LogisticRegression,
    "HistGradientBoostingClassifier": HistGradientBoostingClassifier,
}
_TASKS = {
    TASK_REGRESSION: ("regression_models", REGRESSION_KINDS, REGRESSION_ESTIMATORS, REGRESSION_MODEL_IDS),
    TASK_CLASSIFICATION: ("classification_models", CLASSIFICATION_KINDS, CLASSIFICATION_ESTIMATORS,
                          CLASSIFICATION_MODEL_IDS),
}
NEVER_PASSED_PARAMS = frozenset({"penalty"})


class Rq1ModelSpecError(ValueError):
    """A declared model does not match what the approved specification allows."""


class Rq1ModelOutputError(ValueError):
    """A model returned an invalid output (wrong shape, non-finite or out of range); it is never repaired."""


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    kind: str
    role: str
    inputs: tuple[str, ...]
    output_type: str
    steps: tuple[tuple[str, dict[str, Any]], ...] = ()
    estimator: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    task: str = TASK_REGRESSION

    @property
    def uses_allowlist(self) -> bool:
        return self.inputs == (ALLOWLIST_INPUT,)


RegressionModelSpec = ModelSpec  # Slice B name, kept for callers and tests


def _parse_inputs(raw: Any, model_id: str) -> tuple[str, ...]:
    if raw == ALLOWLIST_INPUT:
        return (ALLOWLIST_INPUT,)
    if not isinstance(raw, list) or any(str(i) not in BASELINE_INPUT_COLUMNS for i in raw):
        raise Rq1ModelSpecError(f"{model_id}: inputs must be 'allowlist' or a list of {BASELINE_INPUT_COLUMNS}, got {raw!r}")
    return tuple(str(i) for i in raw)


def _check_params(estimator: str, params: dict[str, Any], model_id: str, estimators: dict[str, type]) -> None:
    if estimator not in estimators:
        raise Rq1ModelSpecError(f"{model_id}: estimator {estimator!r} is not a declared estimator for this task")
    forbidden = sorted(NEVER_PASSED_PARAMS & set(params))
    if forbidden:
        raise Rq1ModelSpecError(f"{model_id}: {forbidden} is never passed (L2 is expressed with l1_ratio=0.0)")
    accepted = estimators[estimator]().get_params()
    unknown = sorted(set(params) - set(accepted))
    if unknown:
        raise Rq1ModelSpecError(f"{model_id}: {estimator} does not accept {unknown}")


def _model_specs(config: Rq1Config, task: str) -> list[ModelSpec]:
    section, kinds, estimators, expected_ids = _TASKS[task]
    specs = []
    for entry in config.raw.get(section) or []:
        model_id, kind = str(entry.get("id")), str(entry.get("kind"))
        if kind not in kinds:
            raise Rq1ModelSpecError(f"{model_id}: unknown kind {kind!r} for {task}")
        output_type = str(entry.get("outputs"))
        if output_type != kinds[kind]:
            raise Rq1ModelSpecError(f"{model_id}: kind {kind} must output {kinds[kind]}, not {output_type}")
        role = str(entry.get("role"))
        inputs = _parse_inputs(entry.get("inputs"), model_id)
        if role not in ("baseline", "learned") or (role == "learned") != (inputs == (ALLOWLIST_INPUT,)):
            raise Rq1ModelSpecError(f"{model_id}: learned models use exactly the allowlist and baselines never do")
        steps: list[tuple[str, dict[str, Any]]] = []
        estimator, params = None, {}
        if kind == "pipeline":
            for step in entry.get("steps") or []:
                name, step_params = str(step["estimator"]), dict(step.get("params") or {})
                _check_params(name, step_params, model_id, estimators)
                steps.append((name, step_params))
            if not steps:
                raise Rq1ModelSpecError(f"{model_id}: a pipeline needs at least one step")
        elif kind == "estimator":
            estimator, params = str(entry.get("estimator")), dict(entry.get("params") or {})
            _check_params(estimator, params, model_id, estimators)
        elif kind in ("training_fold_median", "training_fold_prevalence") and inputs:
            raise Rq1ModelSpecError(f"{model_id}: a constant training-fold baseline takes no inputs")
        elif kind == "raw_planner_cost" and inputs != ("est_total_cost",):
            raise Rq1ModelSpecError(f"{model_id}: the raw-cost ranking uses est_total_cost only")
        elif kind == "ols" and inputs not in (("ln_cost",), ("ln_cost", "ln_scale")):
            raise Rq1ModelSpecError(f"{model_id}: an OLS cost baseline uses ln_cost, optionally plus ln_scale")
        if role == "baseline" and kind in ("pipeline", "estimator") and inputs not in (("ln_cost",), ("ln_cost", "ln_scale")):
            raise Rq1ModelSpecError(f"{model_id}: a calibrated cost baseline uses ln_cost, optionally plus ln_scale")
        specs.append(ModelSpec(model_id=model_id, kind=kind, role=role, inputs=inputs, output_type=output_type,
                               steps=tuple(steps), estimator=estimator, params=params, task=task))
    ids = tuple(spec.model_id for spec in specs)
    if ids != expected_ids:
        raise Rq1ModelSpecError(f"{section} must declare exactly {expected_ids} in this order, got {ids}")
    return specs


def regression_model_specs(config: Rq1Config) -> list[ModelSpec]:
    """The six declared regression models, in YAML order, validated against the approved design."""
    return _model_specs(config, TASK_REGRESSION)


def classification_model_specs(config: Rq1Config) -> list[ModelSpec]:
    """The six declared 1 s classifiers, in YAML order, validated against the approved design."""
    return _model_specs(config, TASK_CLASSIFICATION)


def build_estimator(spec: ModelSpec) -> Any:
    """A fresh, unfitted scikit-learn estimator for ``spec`` (``None`` for the non-fitted baselines)."""
    estimators = _TASKS[spec.task][2]
    if spec.kind == "ols":
        return LinearRegression(fit_intercept=True)
    if spec.kind == "pipeline":
        return Pipeline([(name.lower(), estimators[name](**params)) for name, params in spec.steps])
    if spec.kind == "estimator":
        return estimators[spec.estimator](**spec.params)  # type: ignore[index]
    return None


def model_inputs(dataset: Any, spec: ModelSpec, allowlist: Sequence[str]) -> pd.DataFrame:
    """The input frame of ``spec`` over every development row (indexed by ``modeling_key``).

    Learned models get exactly the allowlisted feature columns (re-checked
    against the pinned allowlist); baselines get only their declared baseline
    columns. The two never mix.
    """
    if spec.uses_allowlist:
        assert_exact_feature_columns(list(dataset.features.columns), allowlist)
        return dataset.features
    return dataset.baseline_inputs.loc[:, list(spec.inputs)]


def validate_output(model_id: str, values: Any, expected_rows: int, output_type: str) -> np.ndarray:
    """Return ``values`` as a float64 vector or raise :class:`Rq1ModelOutputError`; nothing is repaired."""
    array = np.asarray(values)
    if array.shape != (expected_rows,):
        raise Rq1ModelOutputError(f"{model_id}: expected {expected_rows} outputs, got shape {array.shape}")
    if not np.issubdtype(array.dtype, np.number) or np.issubdtype(array.dtype, np.complexfloating):
        raise Rq1ModelOutputError(f"{model_id}: outputs must be real numbers, got dtype {array.dtype}")
    array = array.astype("float64", copy=False)
    bad = int((~np.isfinite(array)).sum())
    if bad:
        raise Rq1ModelOutputError(f"{model_id}: {bad} non-finite output(s); invalid outputs are never repaired")
    if output_type == RUNTIME_PREDICTION:
        with np.errstate(over="ignore", under="ignore"):
            back = np.exp(array)
        if not (np.isfinite(back).all() and (back > 0).all()):
            raise Rq1ModelOutputError(f"{model_id}: exp(predicted ln runtime) is not a finite positive number")
    elif output_type == PROBABILITY:
        outside = int(((array < 0.0) | (array > 1.0)).sum())
        if outside:
            raise Rq1ModelOutputError(f"{model_id}: {outside} probability(ies) outside [0, 1]; probabilities are "
                                      "never clipped")
    return array


class FoldModel:
    """Shared bookkeeping: one instance is fitted once, on one training fold, and never predicts its own rows."""

    def __init__(self, spec: ModelSpec):
        self.spec = spec
        self.estimator = build_estimator(spec)
        self.fit_keys_: tuple[str, ...] | None = None

    @property
    def model_id(self) -> str:
        return self.spec.model_id

    @property
    def output_type(self) -> str:
        return self.spec.output_type

    @staticmethod
    def _matrix(inputs: pd.DataFrame) -> np.ndarray:
        return np.ascontiguousarray(inputs.to_numpy(dtype="float64"))

    def _begin_fit(self, inputs: pd.DataFrame, target: pd.Series) -> None:
        if self.fit_keys_ is not None:
            raise RuntimeError(f"{self.model_id}: a model instance is fitted once (one per training fold)")
        if not inputs.index.equals(target.index):
            raise ValueError(f"{self.model_id}: inputs and target rows differ")
        self.fit_keys_ = tuple(str(k) for k in inputs.index)

    def _begin_predict(self, inputs: pd.DataFrame) -> int:
        if self.fit_keys_ is None:
            raise RuntimeError(f"{self.model_id}: predict before fit")
        overlap = set(self.fit_keys_) & {str(k) for k in inputs.index}
        if overlap:
            raise RuntimeError(f"{self.model_id}: {len(overlap)} prediction row(s) were also training rows")
        return len(inputs)


class RegressionModel(FoldModel):
    """One declared regression model, fitted on one training fold."""

    def __init__(self, spec: ModelSpec):
        super().__init__(spec)
        self.constant_ln_: float | None = None

    def fit(self, inputs: pd.DataFrame, y_ms: pd.Series) -> RegressionModel:
        """Fit on training rows only; ``y_ms`` is the training ``median_ms`` (target ln(median_ms))."""
        target = y_ms.to_numpy(dtype="float64")
        if target.size == 0 or not (np.isfinite(target).all() and (target > 0).all()):
            raise ValueError(f"{self.model_id}: training median_ms must be non-empty, finite and positive")
        self._begin_fit(inputs, y_ms)
        if self.spec.kind == "training_fold_median":
            self.constant_ln_ = float(np.log(np.median(target)))
        elif self.estimator is not None:
            self.estimator.fit(self._matrix(inputs), np.log(target))
        return self

    def predict(self, inputs: pd.DataFrame) -> np.ndarray:
        """Predicted ln runtime (runtime models) or the raw ranking score (``reg_raw_cost_rank``)."""
        n = self._begin_predict(inputs)
        if self.spec.kind == "training_fold_median":
            raw = np.full(n, self.constant_ln_, dtype="float64")
        elif self.spec.kind == "raw_planner_cost":
            raw = inputs["est_total_cost"].to_numpy(dtype="float64").copy()
        else:
            raw = self.estimator.predict(self._matrix(inputs))
        return validate_output(self.model_id, raw, n, self.output_type)


class ClassificationModel(FoldModel):
    """One declared 1 s classifier, fitted on one training fold."""

    def __init__(self, spec: ModelSpec):
        super().__init__(spec)
        self.constant_probability_: float | None = None

    def fit(self, inputs: pd.DataFrame, y: pd.Series) -> ClassificationModel:
        """Fit on training rows only; ``y`` is the training ``hr_1s`` (positive 1, negative 0)."""
        target = y.to_numpy()
        if target.size == 0 or not np.isin(target, (0, 1)).all():
            raise ValueError(f"{self.model_id}: the training target must be non-empty and only 0 or 1")
        if self.estimator is not None and len(set(target.tolist())) != 2:
            raise ValueError(f"{self.model_id}: a fitted classifier needs both classes in its training fold")
        self._begin_fit(inputs, y)
        if self.spec.kind == "training_fold_prevalence":
            self.constant_probability_ = float(np.mean(target == 1))
        elif self.estimator is not None:
            self.estimator.fit(self._matrix(inputs), target.astype("int64"))
        return self

    def _positive_column(self, probabilities: Any, n: int) -> np.ndarray:
        classes = [int(c) for c in getattr(self.estimator, "classes_", [])]
        if classes != [0, 1]:
            raise Rq1ModelOutputError(f"{self.model_id}: fitted classes are {classes}, expected [0, 1]")
        array = np.asarray(probabilities)
        if array.shape != (n, 2):
            raise Rq1ModelOutputError(f"{self.model_id}: predict_proba returned shape {array.shape}, expected {(n, 2)}")
        return array[:, classes.index(1)]

    def predict(self, inputs: pd.DataFrame) -> np.ndarray:
        """Positive-class probability, or the raw ranking score (``clf_raw_cost_rank``)."""
        n = self._begin_predict(inputs)
        if self.spec.kind == "training_fold_prevalence":
            raw = np.full(n, self.constant_probability_, dtype="float64")
        elif self.spec.kind == "raw_planner_cost":
            raw = inputs["est_total_cost"].to_numpy(dtype="float64").copy()
        else:
            raw = self._positive_column(self.estimator.predict_proba(self._matrix(inputs)), n)
        return validate_output(self.model_id, raw, n, self.output_type)


def build_regression_model(spec: ModelSpec) -> RegressionModel:
    """A fresh, unfitted model for one training fold (the evaluator calls this through the module)."""
    return RegressionModel(spec)


def build_classification_model(spec: ModelSpec) -> ClassificationModel:
    """A fresh, unfitted classifier for one training fold (the evaluator calls this through the module)."""
    return ClassificationModel(spec)
