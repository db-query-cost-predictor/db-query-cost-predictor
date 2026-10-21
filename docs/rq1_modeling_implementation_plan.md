# RQ1 modeling implementation plan (v1)

Status: **specification for review** (for an independent methodology review
before implementation). No model has been implemented, fitted or evaluated. The
machine-readable specification is
[`config/rq1_modeling_v1.yaml`](../config/rq1_modeling_v1.yaml). Methodology:
[rq1_evaluation_protocol.md](rq1_evaluation_protocol.md). Inputs:
[`config/rq1_modeling_input_v1.json`](../config/rq1_modeling_input_v1.json) and
[expanded_collection_checkpoint.md](expanded_collection_checkpoint.md).

Evidence-producing runs happen only after the reviewed implementation is
committed. Every number quoted below comes from the 2026-10-21 audit of the
release, not from any model.

## 1. Scope

| In v1 | Not in v1 |
|---|---|
| Runtime regression on ln(`median_ms`) | 10 s classification (`NOT_YET_EVALUABLE`, G-08; the evaluator refuses it) |
| 1 s classification on `hr_1s_class` | Hyperparameter search, adaptive feature selection, post-hoc probability calibration |
| Cross-validation on three stored fold schemes | A random row-wise split (deferred beyond v1) |
| Repetition-noise reference and near-threshold diagnostics | Any fitting, scaling, selection or scoring of the 160 `heldout_rq2` cases |
| Separate refits on all development cases for hand-off | Choosing a "best" model; LLM rewrites (RQ2) |

Rows: the 4,784 development cases in 194 semantic groups, all `complete`.
STOP/G-08 is preserved: nothing in v1 relabels data, changes a threshold or
marks the release PASS.

## 2. Data flow and validation

1. **Input record.** Read `config/rq1_modeling_input_v1.json` and check its own
   SHA-256 against `inputs.input_record.sha256` in the YAML.
2. **Evidence root.** Taken from `--evidence-dir` or `QCP_EXPANDED_RELEASE_DIR`
   (no default). `--archive` is optional.
3. **Hashes and identities.** Hash every required packaged file (seven derived
   files, the manifest, the full validation report, 12 raw files) and both
   pinned repository files. Check the manifest id, label and feature versions,
   the derived-inputs SHA and the derived release id (recomputed from
   `derived_manifest.json#inputs`), and the original validation status STOP with
   stop code G-08. Any mismatch is an error.
4. **Provenance classes.** Every identity in `input_check.json` carries exactly
   one class:

   | Class | Meaning | Examples |
   |---|---|---|
   | `verified_this_run` | recomputed from bytes read in this run | the 20 packaged files, the 2 repository files, the input record, the release identities |
   | `copied_from_input_record` | taken from the record, not recomputed | the historical checkpoint SHA (always); the original archive SHA when `--archive` is not supplied |
   | `absent` | an optional item not on this machine | the supplementary approval record |

   When `--archive` is supplied, its SHA-256 is computed and the class becomes
   `verified_this_run`.
5. **Source checkpoint.** `972c00b` is historical. The run's own HEAD is
   recorded and never compared with it.
6. **Supplementary approval** (`QCP_DATA_ROOT/manifests/expanded/approvals/…`):
   - absent: recorded as `absent`, and the run continues;
   - present with a matching hash: recorded as `verified_this_run`;
   - present with a different hash: the run stops with a provenance-mismatch error.
7. **Tables.** Read `keys`, `labels`, `features`, `estimates` and `splits` with
   explicit dtypes: identifiers as strings, fold columns as nullable integers,
   numeric columns as float64. This avoids pandas 3 string inference. Each table
   must have unique `modeling_key`, the five key sets must be identical (4,944),
   and the joins must be one-to-one. `executions.csv` is read only by the
   diagnostics and is never joined into the modeling frame.
8. **Counts.** Every value in `expected_counts` must match: cases, observation
   status, split roles, groups, fold sizes, 1 s and 10 s classes.
9. **Rows.** Rows are selected by `split_role == development` **only**. Every
   kept row must:
   - be `complete`;
   - have `hr_1s_class` in {positive, negative};
   - have folds in 0–4 for all three schemes;
   - have `est_total_cost > 0` (audit minimum 0.46) and `snap_scale_factor > 0`.

   Any exception is an input error, never a silent drop. The development
   `median_ms` range is checked against the recorded 0.011–6,906.413 ms.
   - `in_sample` (`keys.csv`) is a **collection-control annotation**: true for
     the 110 bounded-sample keys, all of them development cases. It is present
     but unused for RQ1 selection, fitting and prediction.
     `input_check.json` records this, with the observed counts (true 110,
     false 4,674 among development cases).
10. **Feature matrix (exact contract).** The learned-model matrix must equal the
    69-column allowlist **exactly, in names and order**. An extra, missing or
    reordered column is an error. It must also pass
    `contract.assert_feature_matrix_allowed`, and no column may come from
    `labels`, `splits`, `executions` or `keys`.
    - The raw `est_total_cost` is a registered class-D pre-execution field, used
      only by the calibrated baselines. It is outside the allowlist, so as an
      extra learned-model column it is rejected by the exact-contract rule: this
      preserves the declared contract and is not a leakage issue.
11. **Fold guards.**
    - For each scheme, groups are disjoint across folds
      (`splits.assert_groups_disjoint` with semantic group, structure family and
      query instance), and every fold is non-empty.
    - For `fold_family`, semantic groups are also checked to stay within one
      fold.
    - The supplementary per-fold expectations in the YAML (`expected_per_fold`:
      keys, positives and negatives for all three schemes) must match exactly.
      These are extra readable checks; `splits.csv` is already hash-pinned.
    - Every classification training fold must contain both classes. Observed:
      at least 295 positives and 3,490 negatives in every training fold.
    - `splits.group_kfold` and `splits.random_kfold` are never called.

## 3. Evaluation design

* The same development rows and the same stored folds are used for every model.
  Each scheme loops over folds 0–4: fit on the other four folds, predict the
  held-out fold, and collect out-of-fold (OOF) predictions.
* Schemes:
  - **`fold_group`** is primary (whole semantic groups);
  - **`fold_family`** is the robustness check, stricter mainly within the
    structural component;
  - **`fold_seen_template`** is a labelled diagnostic: templates are seen in
    training, so it is never unseen-structure performance.
* **Fitting rule:** every `StandardScaler`, OLS, logistic and boosting model is
  fitted on the training fold only. Calibrated baselines use
  ln(`est_total_cost`) from `estimates.csv`, never the log1p feature
  `est_total_cost_log`.
* **Baselines as specified:**
  - the regression cost baselines are **log-linear calibrations** of planner
    cost (OLS on ln cost, optionally plus ln scale factor);
  - the classification cost baselines are **logistic calibrations of log
    cost** (optionally plus log scale factor).

  Every comparative claim concerns these specified baselines, not every
  possible use of planner cost. No isotonic models are used in v1.
* **Back-transform:** predictions in ms are exp(pred_ln) with **no smearing
  correction**. This estimates a typical (median-like) runtime on the log scale
  and does not generally estimate the arithmetic-mean runtime, so ms-scale
  metrics (MAE, RMSE) stay secondary.
* **Scale coefficient:** with only two scale factors (0.1 and 1), the
  ln(scale factor) coefficient is not interpreted as an established scaling law.
* **Fixed protocol:**
  - no search, adaptive feature selection or post-hoc calibration;
  - the four constant feature columns are kept;
  - a case is classified positive when its predicted probability is ≥ 0.5.
    This threshold was fixed in this specification before any model was fitted
    or any model prediction was inspected. It was not fixed before the data were
    seen, since the dataset had already been audited. It is not tuned. The
    runtime-label boundary stays 1,000 ms;
  - recall, false-negative rate and precision at 0.5 describe **one predeclared
    operating point**, not a recommended production threshold. Ranking quality
    is characterized by average precision and the step-wise precision-recall
    curve. An optional threshold grid is deferred beyond v1;
  - planning-time features remain future-version candidates only; they are not
    in the v1 allowlist.
* **Reporting:**
  - every predeclared learned model is reported against every baseline;
  - no winner is chosen, and no cross-validation score is presented as an
    independent final test.

### Regression models (target ln(`median_ms`); predictions reported as exp)

| Id | Role | Model | Inputs |
|---|---|---|---|
| `reg_train_median` | baseline | ln(median of the training-fold `median_ms`), the same value for every case in that test fold | none |
| `reg_raw_cost_rank` | baseline | raw `est_total_cost` as a ranking score; rank metrics only | `est_total_cost` |
| `reg_cost_ols` | baseline | `LinearRegression`: ln(runtime) ~ 1 + ln(cost) | ln cost |
| `reg_scale_cost_ols` | baseline | `LinearRegression`: ln(runtime) ~ 1 + ln(cost) + ln(scale factor) | ln cost, ln scale |
| `reg_ridge_all` | learned | `StandardScaler` → `Ridge(alpha=1.0, solver="svd")` | 69 features |
| `reg_hgb_all` | learned | `HistGradientBoostingRegressor(loss="squared_error", …shared)` | 69 features |

### Classification models (`hr_1s_class`: positive → 1, negative → 0)

| Id | Role | Model | Inputs |
|---|---|---|---|
| `clf_train_prevalence` | baseline | each test fold gets its training fold's positive share as the probability | none |
| `clf_raw_cost_rank` | baseline | raw `est_total_cost` as a ranking score; AP and ROC-AUC only, no probabilities | `est_total_cost` |
| `clf_cost_logistic` | baseline | `StandardScaler` → L2 `LogisticRegression` | ln cost |
| `clf_scale_cost_logistic` | baseline | the same, with ln(scale factor) added | ln cost, ln scale |
| `clf_logistic_all` | learned | `StandardScaler` → L2 `LogisticRegression` | 69 features |
| `clf_hgb_all` | learned | `HistGradientBoostingClassifier(loss="log_loss", class_weight=None, …shared)` | 69 features |

**Logistic settings:** `LogisticRegression(C=1.0, l1_ratio=0.0, solver="lbfgs",
max_iter=5000, tol=1e-6, fit_intercept=True, class_weight=None)`.
In the tested scikit-learn 1.9.1, `l1_ratio=0.0` is the L2 penalty. `penalty`
is never passed: it was deprecated in 1.8 and emits a `FutureWarning`.

**Shared boosting settings:** `max_iter=300, learning_rate=0.05,
max_leaf_nodes=15, min_samples_leaf=5, l2_regularization=1.0,
early_stopping=False, categorical_features=None, random_state=20260923`.
`categorical_features=None` overrides the default `"from_dtype"`.

### Software environment

* The tested RQ1 environment is **scikit-learn == 1.9.1**. An evidence run
  refuses any other version. Other versions are untested; no claim is made
  about how they behave.
* Slice A adds an RQ1-specific constraints file,
  `requirements/rq1_constraints_v1.txt`, pinning the tested versions. It does
  not change the project's general minimums in `pyproject.toml`.
* Every run record and the hand-off manifest store the full installed
  environment, as well as the main versions.

## 4. Metrics

**Regression:** `metrics.regression_metrics(actual_ms, pred_ms)`, with the floor
`EPSILON_MS` = 0.001 ms.

| Group | Metrics |
|---|---|
| Primary | log-MAE; median, p90 and p95 q-error |
| Operational | MAE and RMSE in ms |
| Secondary | R² on the log and ms scales, never called accuracy |
| Rank | Spearman and Kendall via `metrics.rank_correlations` |

**Classification:**

| Metric | Definition | Models |
|---|---|---|
| **Average precision (AP)** (primary ranking) | `sklearn.metrics.average_precision_score(y, score, pos_label=1)`: the non-interpolated sum Σ (Rₙ − Rₙ₋₁) Pₙ. It is never replaced by, or called, trapezoidal PR-AUC (`auc` on a PR curve) | all |
| ROC-AUC (secondary) | `roc_auc_score` | all |
| Recall, false-negative rate (primary safety) | at probability ≥ 0.5 | probabilistic |
| Precision, false-positive rate, warning rate per 100 cases | at probability ≥ 0.5 | probabilistic |
| Brier score | `brier_score_loss(…, scale_by_half=True)` = mean (p − y)² | probabilistic |
| Calibration bins and curve (descriptive) | 10 fixed bins computed directly (see below) | probabilistic |

Accuracy is never a headline.

### Headline tables (predeclared)

All headline results are **pooled development OOF results on `fold_group`**.

| Table | Models | Metrics |
|---|---|---|
| Headline regression | runtime-predicting models: `reg_train_median`, `reg_cost_ols`, `reg_scale_cost_ols`, `reg_ridge_all`, `reg_hgb_all` | log-MAE; median, p90 and p95 q-error |
| Headline classification | all six declared classifiers, including `clf_raw_cost_rank` | average precision |
| Regression rank metrics (separate) | `reg_raw_cost_rank` and every runtime-predicting model | Spearman, Kendall |
| Operating point at 0.5 (separate) | the five probabilistic classifiers | recall, false-negative rate, precision, false-positive rate, warning rate per 100 |

Per-fold, `fold_family`, `fold_seen_template` and stratified results are
reported in further tables. No model is named a winner.

### Uncertainty: paired cluster bootstrap over semantic groups

Applies to the headline-table metrics only.
- **Groups:** take the ordered list of the 194 development `semantic_group_id`
  values. Each replicate draws 194 ids with replacement (seed 20261021,
  2,000 replicates) and keeps **every OOF row of every drawn group, once per
  occurrence**, so repeated draws repeat rows.
- **Pairing:** the same resample indices are used for every model and every
  difference.
- **Statistic:** each replicate recomputes the same case-weighted pooled metric
  definitions as the point estimates.
- **Interval:** 95 % percentile interval.
- **Differences:** learned minus baseline for each learned model against
  **both** calibrated baselines: `reg_ridge_all` and `reg_hgb_all` against
  `reg_cost_ols` and `reg_scale_cost_ols`; `clf_logistic_all` and `clf_hgb_all`
  against `clf_cost_logistic` and `clf_scale_cost_logistic`.
- **Saved outputs:** the ordered group list (`bootstrap_groups.csv`), the
  resample indices (`bootstrap_indices.npz`), the replicate statistics
  (`bootstrap_replicates.csv`) and `bootstrap_summary.json`. The summary holds
  the valid and invalid replicate counts and the reasons for invalid replicates.
  A replicate whose metric is undefined, for example one with no positives, is
  counted as invalid with its reason and never imputed.
- **Interpretation:** these are approximate uncertainty summaries, conditional
  on the fixed OOF predictions and stored folds. They do not capture retraining
  variability, and they do not establish generalization to other databases,
  workloads or machines.
- **Claims:** a comparative claim must name the metric, the baseline and the
  paired interval. No universal or multiple-comparison-adjusted statistical
  significance is claimed.

### Calibration bins (computed directly)

- **Edges:** 10 fixed bins, 0.0, 0.1, …, 1.0. Bin *i* (0–8) holds
  lower ≤ p < upper; bin 9 holds 0.9 ≤ p ≤ 1.0, so p = 0 falls in bin 0 and
  p = 1 in bin 9.
- **Valid probabilities:** finite with 0 ≤ p ≤ 1. Any other value fails the
  stage.
- **Per bin:** `n_cases`, `n_positive`, `mean_predicted_probability` and
  `observed_positive_fraction`. Empty bins are written with zero counts and
  null rates.
- **Totals:** the bin counts must sum to the evaluated row count.
- **Cross-check:** `sklearn.calibration.calibration_curve(n_bins=10,
  strategy="uniform")` may optionally cross-check the non-empty bins with
  matching boundary semantics. It is never the source of the complete table.

### Thin strata

Every row reports `n_keys`, `n_groups`, `keys_per_group` and
`n_query_instances`. Classification rows also report `n_positive`,
`n_positive_groups` and `n_contributing_positive_folds`.

Two flags apply:
- **`thin_positive_support`:** a classification stratum with fewer than 30
  positives *or* fewer than 5 positive semantic groups;
- **`single_fold_positives`:** a **pooled** stratum whose positives all come
  from one test fold. This flag is not applied automatically to per-fold rows.

Flagged results stay visible but are **descriptive only**. The report states
the concentration of positives explicitly:
- all 393 development positives are at SF 1;
- 351 of them (101 of the 106 positive groups) are structural queries;
- 34 are TPC-H-derived (4 groups), 8 are rewrite cases (1 group), and none are
  probes.

**Pooled versus per-fold values.** Pooled metrics are always computed from the
actual fold-specific OOF predictions, never derived from a per-fold identity.

**Prevalence baseline.** Its score is constant *within* each test fold: the
training prevalence of that fold. Different folds can get different scores.
- Within a single test fold that contains both classes, the constant score
  gives AP equal to that test fold's prevalence and ROC-AUC 0.5.
- These identities do **not** hold for pooled OOF predictions. Toy
  counterexample (checked with scikit-learn 1.9.1):
  - y = [1, 1, 0, 1, 0, 0], folds = [0, 0, 0, 1, 1, 1];
  - OOF scores = [1/3, 1/3, 1/3, 2/3, 2/3, 2/3];
  - overall prevalence 0.5, but pooled AP = 4/9 and pooled ROC-AUC = 1/3.
- At the fixed 0.5 threshold, the baseline predicts all negatives in a test fold
  **only if** that fold's training prevalence is below 0.5. In that fold, recall
  is 0 (defined, because positives exist) and precision is null (no predicted
  positives). With about 8 % development positives this is expected in every
  fold, but it is computed, not assumed.

### Metric-specific definedness (null only when the rule fails; a defined zero stays zero)

| Metric | Defined when | Otherwise |
|---|---|---|
| MAE, log-MAE, RMSE, median/p90/p95 q-error, Brier score, warning rate | the set of valid observations is non-empty | null, "no valid observations" |
| R² (log, ms) | at least 2 cases and non-constant actual values. Constant predictions alone do not make it undefined | null with the failing reason |
| Spearman, Kendall | at least 2 cases, and non-constant values on both sides | null, "fewer than 2 cases", "actual values are constant" or "scores are constant" |
| Average precision | at least one actual positive. An all-positive stratum gives AP = 1, flagged `trivial_single_class` (not informative discrimination) | null, "no positive cases" |
| ROC-AUC | both classes present | null, "no positive cases" or "no negative cases" |
| Recall, false-negative rate | at least one actual positive | null, "no positive cases" |
| Precision | at least one predicted positive | null, "no case predicted positive at the fixed threshold" |
| False-positive rate | at least one actual negative | null, "no negative cases" |
| Calibration bins | non-empty; single-class strata are still described | null rates only in empty bins |

Rank-score models (`reg_raw_cost_rank`, `clf_raw_cost_rank`) have no runtime or
probability metrics because of their **output type**, not because their scores
are invalid. Those cells carry the reason "not applicable to this model's output
type". There is no blanket minimum case count.

**Scopes and strata:** pooled OOF, per fold, and pooled by `snapshot_name`,
`component`, `configuration_name` and runtime bin on the actual median
(< 10, 10–100, 100–1,000 and 1,000–10,000 ms; reporting only). Every row
carries `n_keys`, `n_groups`, `keys_per_group` and `n_positive`.

## 5. Diagnostics (descriptive; labels never change)

* **Repetition-noise reference.** For each development case and each of its
  three measured runs (`executions.csv`, `run_purpose == measured`,
  `completed`):

  > q_i = max(t_i / m_i, m_i / t_i), where m_i is the mean of the other two runs

  The pilot used the same definition. Also reported: the coefficient of
  variation (sample standard deviation / mean). The median, p90 and p95 are
  given overall and by snapshot, component and runtime bin. This is a
  descriptive reference for scale, **not a proven lower bound** on prediction
  error: the label is a median of three runs and differs from any single run.
* **Near-threshold sensitivity.** Two sets are defined in advance:
  - **S**: min(run) < 1,000 ms ≤ max(run) (31 cases at the audit);
  - **B**: 800 ms ≤ `median_ms` < 1,250 ms (245 cases at the audit).

  Classification metrics are reported on all cases (the headline), on all cases
  minus S, on all cases minus B, and on B only, all from the same `fold_group`
  OOF predictions. No refit is involved, and no label or boundary changes. S and
  B are defined from **observed runtimes**, so they are retrospective
  diagnostics and never deployable pre-execution filters.
* **Runtime bins:** the counts of the four runtime bins must sum to the
  evaluated case count; no case is dropped silently. At the audit the
  development counts were 1,099, 1,777, 1,515 and 393 (total 4,784).

## 6. Run orchestration and source provenance

**One command produces results:** `rq1-run --evidence-dir <dir> [--archive
<zip>] --run-label <label>`.
- It creates one run directory, exclusively: an existing directory is never
  overwritten or reused.
- It builds a single run context and passes it through the stages `input_check`
  → `regression` → `classification_1s` → `diagnostics` → `handoff`. The context
  holds the run id, the paths, the loaded and checked frame, the fold arrays,
  the config SHA and the provenance.
- `rq1-check-inputs` runs only the input checks and writes no run directory.

**Status lifecycle** (`RUN_STATUS.json`):

| Status | When |
|---|---|
| `RUNNING` | written first, before any stage starts; updated with each completed stage |
| `FAILED` | a stage raised. It records the error, the failed stage and the last completed stage. Outputs remain as evidence of the failure only |
| `COMPLETE` | every stage finished and `output_manifest.json` (SHA-256 of every output file) was written |

Partial outputs are never presented as a complete study. `report.md` and the
`reports/rq1/` copy are written only on `COMPLETE`.

**Run safeguards:**
- A run-scoped `threadpoolctl` limit of 1 thread applies. The requested and
  effective settings are both recorded.
- A `ConvergenceWarning` during an evidence fit fails the stage and marks the
  run `FAILED`.
- Runtime-bin and calibration-bin counts must sum to the evaluated counts.

**Source provenance.** An evidence run requires all of the following; every run
records them:
- a clean tracked tree (no tracked modifications);
- no untracked, non-ignored files other than the allowed paths
  `reference_docs/` and `reference_pilot/README.md`. These are allowed only
  because they are not execution inputs, and the run never opens a file under
  them;
- no git-ignored files under `src/`, `config/` or `scripts/`, other than
  `__pycache__/` and `*.egg-info/`;
- scikit-learn 1.9.1.

The run record stores the source revision (`git rev-parse HEAD`), the
tracked-dirty flag, the observed untracked paths and the allowed untracked
paths, plus the config SHA-256, the input-record SHA-256, the input identities
with their provenance classes, the approval status, the main dependency
versions, the full environment and `threadpoolctl` info.

## 7. Outputs

Run directory: `QCP_DATA_ROOT/results/rq1/rq1-modeling-v1/<run_id>/`, with
`run_id = <UTC>_<config16>_<head12>`.

| File | Contents |
|---|---|
| `RUN_STATUS.json`, `run_record.json`, `input_check.json` | section 6 and section 2, step 4 |
| `fold_membership.csv` | development `modeling_key`, group, family, instance, component, snapshot, configuration and the three fold ids |
| `oof_regression.csv` | `modeling_key, fold_scheme, fold, model_id, y_true_ms, y_true_ln, pred_ln, pred_ms, score` (`score` only for rank-score models; `pred_*` null for them) |
| `oof_classification.csv` | `modeling_key, fold_scheme, fold, model_id, y_true, score, score_type (probability or rank_score), pred_label` (null for rank scores) |
| `metrics_regression.{json,csv}`, `metrics_classification.{json,csv}` | long format, columns below |
| `calibration_bins.csv` | `fold_scheme, model_id, scope, fold, stratum_column, stratum_value, bin_index, bin_lower, bin_upper, n_cases, n_positive, mean_predicted_probability, observed_positive_fraction`; empty bins have zero counts and null rates (section 4) |
| `bootstrap_groups.csv`, `bootstrap_indices.npz`, `bootstrap_replicates.csv`, `bootstrap_summary.json` | ordered group list, resample indices, replicate statistics, valid and invalid counts with reasons (section 4) |
| `diagnostics_noise.json`, `diagnostics_threshold.json` | section 5 |
| `figures/` | predicted vs actual ln runtime; q-error ECDF; error by runtime bin; step-wise PR curves (AP in the legend); calibration curves drawn **only** from `calibration_bins.csv` rows, each named by its fold scheme, model, scope and stratum; all on `fold_group`, using the Agg backend |
| `report.md` | written on `COMPLETE` only. Contents: the **limitations block, verbatim** (below); the scope and STOP/G-08 note; provenance; the headline tables with paired bootstrap intervals; the separate rank-metric and operating-point tables; per-fold and stratified tables with flags; diagnostics; labelled robustness and seen-template sections |
| `output_manifest.json` | SHA-256 of every output file; written last |
| `handoff/` | separate refits on all 4,784 development cases, using `joblib`, plus `handoff_manifest.json` (model ids, file hashes, fit rows, config SHA, source revision, full environment, and the all-development median runtime and positive prevalence as stored constants for the two constant baselines), labelled `REFIT_ON_ALL_DEVELOPMENT_NO_EVALUATION`. Never scored. No `heldout_rq2` predictions in v1 |

**Metric table columns:** `task, fold_scheme, model_id, scope, fold,
stratum_column, stratum_value, n_keys, n_groups, keys_per_group,
n_query_instances, n_positive, n_positive_groups, n_contributing_positive_folds,
metric, value, ci_lower, ci_upper, null_reason, flag`.
- `fold` is an integer for `per_fold` rows and null for `pooled_out_of_fold`
  rows.
- `stratum_column` is null for unstratified rows.
- `ci_lower` and `ci_upper` are filled only for bootstrapped headline rows.
- The three positive counts are null on regression rows.
- `flag` may hold several flags (`trivial_single_class`,
  `thin_positive_support`, `single_fold_positives`) separated by `;`.

**Limitations block** (stored in the YAML as `limitations_block.text`; required
verbatim in `report.md`):

> Scope and limitations. These results come from one recorded workload: TPC-H-derived
> queries (22 TPC-H-derived templates, 128 generated structural groups, 24 point/range
> probes and 20 development rewrite groups) on the TPC-H schema at two scale factors
> (SF 0.1 and SF 1), with two work_mem profiles (4 MB and 256 kB, jit off), on PostgreSQL
> 16.15 (image postgres:16, digest sha256:a3b7f434b2dc57ce85a67e171163eb8ab1a1ebcb39d27484661f26b1dfbe30d6)
> running in Docker Desktop on one Windows laptop. Each query ran in isolation: one
> client, sequentially, with one unmeasured warm-up execution followed by three measured
> executions under a warm-cache protocol and a 15-second statement timeout.
> The target is the instrumented EXPLAIN ANALYZE execution time with TIMING OFF (median of
> the three measured runs). Planning time and client or network latency are excluded. The
> 1-second label refers to this execution time only. No development case reaches 10
> seconds, so 10-second classification is not evaluated. Development runtimes range from
> 0.011 ms to 6,906.413 ms; predictions outside this observed range are unsupported.
> Free memory was low (1.13 GB) at the single preflight observation before collection; it
> was not measured during collection. Uncertainty intervals are conditional on the fixed
> out-of-fold predictions and folds and do not establish generalization to other
> databases, workloads or machines.

## 8. Implementation slices

### Slice A: input checker, loader, leakage and fold guards, run context

**Proposed files**
- `src/query_cost_predictor/rq1_config.py`: parse and validate the YAML.
- `src/query_cost_predictor/rq1_inputs.py`: checks, provenance classes, typed
  loader, row, feature and fold guards.
- `src/query_cost_predictor/rq1_provenance.py`: source cleanliness, versions,
  full environment.
- `src/query_cost_predictor/rq1_run.py`: run context, exclusive run directory,
  status lifecycle, output manifest.
- `requirements/rq1_constraints_v1.txt`.
- CLI `rq1-check-inputs` and `rq1-run` (the stages after `input_check` are
  added in slices B and C).
- `scripts/powershell/09_rq1_modeling.ps1` with the steps `CheckInputs` and
  `Run`.

**Reuse**
- `hashing.sha256_file`, `canonical_json` and `sha256_json`;
- `contract.assert_feature_matrix_allowed` and `default_registry`;
- `splits.assert_groups_disjoint`, `fold_summary` and `iter_folds` (on stored
  fold ids);
- `paths.data_root`, `config_dir` and `ensure_dir`;
- `errors`.

The version helper copies the pattern of `pilot_audit._package_versions`
without modifying `pilot_audit.py`.

**Tests** (`test_rq1_config.py`, `test_rq1_inputs.py`, `test_rq1_run.py`), using
a synthetic package in `tmp_path`:
- a changed byte in any required file fails, naming the file;
- a wrong input-record hash fails;
- a count mismatch fails;
- duplicate keys or an unmatched join fail;
- HEAD ≠ checkpoint passes and is recorded;
- approval absent → `absent`, present and matching → `verified_this_run`,
  mismatched → error;
- without `--archive` the archive SHA is `copied_from_input_record`, and with
  it `verified_this_run`;
- heldout rows never appear in the frame;
- adding an identity or execution column to the allowlist raises
  `LeakageError`;
- `group_kfold` and `random_kfold` are monkeypatched to raise and are never hit;
- a group spread over two folds raises;
- `est_total_cost <= 0` raises;
- the YAML rejects `penalty`, a search block or a threshold other than 0.5;
- an existing run directory is refused;
- a stage failure leaves `FAILED`, writes no `report.md` and no output manifest;
- an untracked file under `src/`, or a non-allowed untracked path, blocks an
  evidence run;
- an untested scikit-learn version blocks an evidence run;
- **exact feature contract:** an extra column (including `est_total_cost`
  specifically), a missing column and a reordered allowlist are each rejected,
  even when every column passes the registry check;
- a changed per-fold expectation (keys, positives or negatives) for any scheme
  fails;
- a classification training fold with one class fails;
- a semantic group split across two `fold_family` folds fails;
- `in_sample` is recorded as an unused collection-control annotation, and
  flipping its values leaves the modeling frame unchanged;
- a development `median_ms` outside the recorded range fails.

**Accept when**
- `rq1-check-inputs` passes on the real package, reporting 4,784 rows,
  194 groups and 69 features;
- a copy with one byte changed fails, naming the file;
- the unit suite passes.

### Slice B: regression evaluation and reports

**Proposed files**
- `src/query_cost_predictor/rq1_models.py`: estimator factory from the YAML.
- `src/query_cost_predictor/rq1_metrics.py`: definedness rules, null reasons,
  flags.
- `src/query_cost_predictor/rq1_evaluate.py`: fold loop and OOF.
- `src/query_cost_predictor/rq1_report.py`.
- The `regression` stage of `rq1-run`.

**Reuse**
- `metrics.regression_metrics`, `q_error`, `rank_correlations` and
  `EPSILON_MS`;
- `splits.iter_folds` and `fold_summary`.

**Tests** (`test_rq1_regression.py`, `test_rq1_metrics.py`):
- every model sees identical row and fold arrays;
- each fitted scaler's mean equals its training-fold mean;
- the OLS coefficients equal `numpy.linalg.lstsq` on the training fold;
- ln(cost) is used, not log1p;
- the median baseline equals each training-fold median;
- a fit spy proves no test-fold or heldout row reaches `fit`;
- rank-score models carry the output-type reason;
- R² is defined for constant predictions with non-constant actuals;
- R² and the rank correlations are null for constant actuals;
- a zero MAE stays 0, not null;
- `fold` is an integer on per-fold rows and null on pooled rows;
- two runs give byte-identical OOF and metric files on one machine;
- no warnings are raised (`FutureWarning` and `ConvergenceWarning` are errors);
- a forced `ConvergenceWarning` marks the run `FAILED`;
- the threadpool limit of 1 is applied, and both the requested and effective
  settings are recorded;
- **bootstrap:**
  - the same seed gives identical indices;
  - every model and difference uses the same indices;
  - a group drawn twice contributes its rows twice;
  - replicate metrics use the case-weighted pooled definition;
  - an undefined replicate is counted as invalid with its reason;
  - the ordered group list and the indices are saved;
- the runtime-bin counts sum to the evaluated cases;
- exp(pred_ln) is used with no smearing factor;
- `report.md` contains the limitations block byte-for-byte.

**Accept when**
- all six regression models produce pooled, per-fold and stratified metrics on
  all three schemes, with no NaN anywhere;
- the report labels the robustness and seen-template sections;
- the tests pass.

### Slice C: classification, diagnostics and hand-off

**Proposed files**
- classification in `rq1_evaluate.py`;
- `src/query_cost_predictor/rq1_diagnostics.py`;
- `src/query_cost_predictor/rq1_handoff.py`;
- the `classification_1s`, `diagnostics` and `handoff` stages of `rq1-run`.

**Reuse**
- `metrics.q_error` and `describe`;
- the scikit-learn metrics named in section 4.

**Tests** (`test_rq1_classification.py`, `test_rq1_diagnostics.py`,
`test_rq1_handoff.py`):
- the mapping is positive → 1, and an unknown label raises;
- a probability of exactly 0.5 is positive;
- AP equals the step-sum formula on a toy example and differs from trapezoidal
  PR-AUC;
- **prevalence baseline, per fold:** within a test fold that has both classes,
  AP equals that fold's prevalence and ROC-AUC is 0.5;
- **prevalence baseline, pooled:** the toy counterexample in section 4 gives
  pooled AP 4/9 and ROC-AUC 1/3, computed from the fold-specific scores. The
  test asserts that the pooled values are *not* taken from a per-fold identity;
- all-negative predictions at 0.5 occur only in a fold whose training
  prevalence is below 0.5. A fold with training prevalence ≥ 0.5 predicts
  positives;
- in an SF 0.1-like stratum with no positives, AP, ROC-AUC and recall are null
  with reasons, while the false-positive rate and Brier score are defined;
- an all-positive stratum gives AP = 1 with the `trivial_single_class` flag,
  and ROC-AUC null;
- precision is null only when there are no predicted positives;
- calibration bins describe a single-class stratum;
- rank-score models have no Brier or threshold metrics (output-type reason);
- `LogisticRegression` is constructed without `penalty`, with no warning;
- a 10 s task request is refused as `NOT_YET_EVALUABLE`;
- the leave-one-run-out q-error matches a hand computation;
- sets S and B match their definitions, and the labels are unchanged;
- the calibration figures read only `calibration_bins.csv`;
- hand-off refits use exactly the development rows, write no scores, and record
  the environment and the label;
- **calibration bins:**
  - p = 0 falls in bin 0, p = 0.1 in bin 1, and p = 1 in bin 9;
  - empty bins are written with zero counts and null rates;
  - the counts sum to the evaluated rows;
  - NaN, −0.01 and 1.01 are rejected;
- **thin-strata flags:**
  - 29 positives are flagged, and so are 30 positives in 4 groups;
  - 30 positives in 5 groups are not flagged;
  - a pooled stratum with positives from one fold gets `single_fold_positives`;
  - a per-fold row is not flagged automatically;
- the hand-off manifest stores the all-development median and prevalence
  constants.

**Accept when**
- a full `rq1-run` on the real package ends `COMPLETE`, with every
  classification metric, explicit nulls and flags, both diagnostics, the
  calibration bins and the hand-off artifacts;
- the report states that 10 s is `NOT_YET_EVALUABLE`;
- the tests pass.

**Final evidence run.** After you commit the reviewed implementation, run
`rq1-run` once on a clean source tree that meets section 6. Only a run whose
status is `COMPLETE` counts as the RQ1 v1 study.

## 9. Earlier protocol-consistency items and open decisions

| # | Item | Resolution |
|---|---|---|
| P1 | Random row-wise split | deferred beyond v1, in both the protocol and the YAML |
| P2 | "PR-AUC" | renamed "average precision" (`average_precision_score`) and kept distinct from trapezoidal PR-AUC |
| P3 | Headline | every predeclared learned model against every baseline; no winner |
| P4 | Decision threshold | 0.5, fixed before any model was fitted or any prediction was inspected (after the dataset audit); not tuned |
| P5 | Noise wording | "repetition-noise reference", not a lower bound |
| P6 | Software | the tested RQ1 environment is scikit-learn 1.9.1, enforced by an RQ1 constraints file; `pyproject.toml` is unchanged |

**Open decisions for review**
- **D1.** v1 produces no `heldout_rq2` predictions. They belong to the RQ2
  gating slice.
- **D2.** Cross-machine bit-identity of HGB results is not guaranteed because of
  OpenMP threading. A run-scoped limit of one thread is applied and recorded,
  and determinism is tested on one machine.

## 10. Methodology-review resolution

| Item | Topic | Resolution | Where |
|---|---|---|---|
| C1 (with O9) | Uncertainty and exact headline | **Accepted.** Headlines are pooled development OOF results on `fold_group`, using log-MAE and q-error for runtime models and AP for every classifier. A paired cluster bootstrap over the 194 groups (2,000 replicates, seed 20261021, 95 % percentile) gives learned-minus-baseline differences against both calibrated baselines. Raw-cost rank metrics and the operating point are in separate tables | YAML `reporting.tables`, `uncertainty`; plan §4 |
| C2 | Exact feature contract | **Accepted.** Exact name-and-order equality with the 69-column allowlist, plus the registry check. `est_total_cost` stays a separate baseline input, and the dictionary is clarified | YAML `features.exact_contract`; plan §2.10; `data_dictionary.md` |
| C3 (with O3) | Fold checks | **Accepted.** Per-fold keys, positives and negatives for all three schemes; both classes in every training fold; semantic-group separation for `fold_family`. The input record is unchanged | YAML `folds.expected_per_fold`, `folds.guards`; plan §2.11 |
| C4 (with O1, O2) | Baseline wording | **Accepted.** Log-linear and logistic calibration of log cost; claims limited to these baselines; no isotonic models; no smearing; ms metrics secondary; the scale coefficient is not a scaling law | YAML `baseline_definitions`, `targets.regression`; plan §3 |
| C5 | Claim scope | **Accepted.** One limitations block, required verbatim in `report.md`, with the observed development range 0.011–6,906.413 ms and low memory described as a preflight observation | YAML `limitations_block`; plan §7; checkpoint §6–7 |
| C6, C7 (with O8) | Thin strata and operating point | **Accepted.** Counts of query instances, positive groups and contributing folds; `thin_positive_support` (< 30 positives or < 5 positive groups); `single_fold_positives` on pooled strata only; positive concentration stated; 0.5 is one predeclared operating point | YAML `metrics.flags`, `reporting`; plan §3–4 |
| C8, C10 | Collection control and count reconciliation | **Accepted.** Rows are selected by `split_role` only; `in_sample` is present but unused and recorded in `input_check.json`; release-scoped counts (407 and 4) are reconciled with development-scoped gate counts (393 and 0) | YAML `rows`, `inputs.count_scopes`; plan §2.9; checkpoint §1 |
| C9 | Calibration bins | **Accepted.** Ten fixed bins computed directly, with an explicit boundary convention, empty bins, validity checks and counts that sum to the rows; `calibration_curve` only as an optional cross-check | YAML `metrics.classification.probability.calibration_bins`; plan §4 |
| O4, O5, O7, O10, O11 | Safeguards | **Accepted.** Thread limit 1, recorded; `ConvergenceWarning` gives `FAILED`; runtime-bin counts sum; S and B are retrospective; the hand-off stores the median and prevalence constants | YAML `safeguards`, `outputs.handoff`; plan §5–7 |
| — | Optional threshold grid | **Deferred** beyond v1 | YAML `fixed.threshold_grid` |
| — | Isotonic baselines | **Not in v1** | YAML `fixed.isotonic_models` |
| — | Planning-time features | **Future-version candidates only** | YAML `fixed.planning_time_features` |
