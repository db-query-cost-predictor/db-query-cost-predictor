# RQ1 evaluation protocol

**RQ1.** How accurately can a model predict isolated PostgreSQL execution time,
and flag high-runtime queries, for **unseen query structures**, using only
information available before execution (SQL, schema/statistics, configuration,
plain `EXPLAIN`)? Does it improve on a **calibrated PostgreSQL plan-cost
baseline** evaluated on the same grouped folds?

Status: the data exist; the models do not yet. The expanded release was
collected on 2026-10-04 (UTC): 4,944 determined cases (4,940 complete, 4
right-censored), 4,784 development cases and 160 reserved RQ2 cases
([expanded_collection_checkpoint.md](expanded_collection_checkpoint.md)). Its
full validation is **STOP at G-08**, so:

| Task | Status |
|---|---|
| Runtime regression | next scoped modeling task |
| High-runtime classification at 1 s | next scoped modeling task (gate G-07 passed) |
| High-runtime classification at 10 s | `NOT_YET_EVALUABLE` (G-08): not trained, reported or claimed |
| Final expanded model scores | pending |

The only model results so far are the pilot diagnostic below (`RECOMPUTED_PILOT`).

## Inputs are pinned

Every RQ1 run reads the files pinned by
[`config/rq1_modeling_input_v1.json`](../config/rq1_modeling_input_v1.json):

* the seven derived files, the original manifest and the full validation report,
  by path relative to the evidence package and by SHA-256;
* the workload specification and the feature registry;
* the expected case and split-role counts;
* the original validation status (STOP, G-08) and the permitted scope.

The record is static metadata. It does not call the release PASS and does not
use the freeze logic. The modeling slice will add a runtime input checker with
these rules:

* **The checkpoint is historical.** `source_checkpoint.commit_sha` (`972c00b`)
  identifies the audited source that reproduces the derivation. Later modeling
  commits will have a different HEAD; the checker must **not** fail because
  HEAD differs from `972c00b`.
* **Each run records its own source revision.** Every modeling run stores its
  own commit SHA (and whether the working tree had uncommitted changes) in its
  outputs, separately from the checkpoint SHA.
* **Required inputs must match.** The checker refuses to fit if any required
  packaged input differs from its pinned SHA-256: the seven derived files, the
  original manifest, the full validation report and the raw files. The same
  applies to the pinned repository files (`config/expanded_release_v1.yaml`,
  `config/feature_registry.yaml`) and to the expected counts.
* **Supplementary provenance does not block.** The approval record under
  `supplementary_provenance_outside_package` lives in `QCP_DATA_ROOT`, not in the
  archive. If it is present, its hash is checked. If it is absent, for example on
  a teammate's machine, the run records it as absent and continues: offline RQ1
  evaluation needs only the packaged evidence and the repository. If the
  approval record is present but its hash differs from the pinned value, the
  input checker stops the modeling run with a provenance-mismatch error.

## Rows and targets

* **Rows:** development cases only, selected by `split_role == development`
  alone (4,784 cases in 194 semantic groups). `in_sample` is a
  collection-control annotation: it is present, but it is not used for RQ1
  selection, fitting or prediction. The 160 `heldout_rq2` cases are excluded from model
  fitting, preprocessing fitting, calibration, feature selection,
  hyperparameter or model selection, and the choice of decision thresholds.
* **Regression target:** ln(`median_ms`), the median of the three measured
  repetitions (instrumented `EXPLAIN ANALYZE` execution time, `TIMING OFF`).
  **All 4,784 development cases are `complete`**, so the development set has no
  censored labels and no censoring-aware model is needed for it. All four
  right-censored cases are reserved RQ2 cases and are outside RQ1. Censored
  labels are never imputed as completed runtimes anywhere. Predictions in ms
  are exp(predicted ln) with **no smearing correction**. They do not generally
  estimate the arithmetic-mean runtime, so ms-scale metrics are secondary.
* **1 s classification target:** `hr_1s_class`, positive or negative, frozen at
  1,000 ms. Development cases are 393 positive (106 semantic groups) and 4,391
  negative.
* Instrumented server time is not end-to-end client latency.

## Features and baselines

**Features:** only registered `feature` fields of class A-D
([`config/feature_registry.yaml`](../config/feature_registry.yaml)). The release
has 69: 23 SQL-structure (A), 1 statistics (B: `snap_scale_factor`), 1
configuration (C: `cfg_work_mem_kb`) and 44 plain-`EXPLAIN` estimate features
(D). Template, semantic-group and query identities are used for grouping only.
Class E fields (execution time, actual rows and loops, buffers, temporary blocks,
I/O timing, WAL, launched workers, runtime status) are rejected mechanically.
Four feature columns are constant in this release (`est_n_function_scan`,
`est_n_other`, `est_n_setop`, `est_n_values_scan`).

**Two log transforms of the planner cost must not be confused:**

| Quantity | Definition | Use |
|---|---|---|
| ln(`est_total_cost`) | natural log of the root total cost in `derived/estimates.csv` | calibrated baselines |
| `est_total_cost_log` | log1p(`est_total_cost`), a model feature | learned models only |

**Baselines** (same folds, rows and metrics as the learned models):

| Baseline | Regression | 1 s classification |
|---|---|---|
| Global median / prevalence | median of the training-fold labels | training-fold positive rate |
| Raw planner cost | Spearman and Kendall rank agreement only (planner units are not milliseconds) | ranking by raw cost: average precision and ROC-AUC only |
| **Cost-only (calibrated)** | ln(runtime) = a + b·ln(cost), least squares | logistic regression on ln(cost) |
| **Scale-plus-cost** | ln(runtime) = a + b·ln(cost) + c·ln(scale factor) | logistic regression on ln(cost) and ln(scale factor) |

The headline reports **every predeclared learned model** (Ridge and
histogram gradient boosting for regression; all-feature logistic regression and
histogram gradient boosting for classification) against all baselines,
including the cost-only calibrated baseline. No model is selected as a winner,
and no cross-validation score is presented as an independent final test. The
scale-plus-cost baseline shows how much a model adds beyond knowing the snapshot
size, which matters because every development positive at 1 s is at SF 1.

* **Baseline wording:**
  - the regression cost baselines are **log-linear calibrations** of planner
    cost;
  - the classification cost baselines are **logistic calibrations of log
    cost**.

  Claims concern these specified baselines only. No isotonic models are used in
  v1. With only two scale factors, the scale coefficient is not interpreted as
  an established scaling law.
* **Exact headline:**
  - all headline results are pooled development out-of-fold results on
    `fold_group`;
  - for regression, log-MAE and median, p90 and p95 q-error for the
    runtime-predicting models;
  - for classification, average precision for every declared classifier;
  - raw-cost regression ranking goes in a separate rank-metric table, and
    threshold-dependent metrics go in a separate operating-point table.
* **Uncertainty:**
  - method: a paired cluster bootstrap over the 194 development semantic
    groups (2,000 replicates, seed 20261021, 95 % percentile intervals);
  - each replicate keeps every row of each drawn group, once per occurrence;
    every model uses the same indices, and the case-weighted pooled metric
    definitions are kept;
  - differences are learned minus baseline, against both the cost-only and the
    scale-plus-cost baselines.

  These intervals are approximate and conditional on the fixed out-of-fold
  predictions and folds. They do not capture retraining variability or
  establish generalization to other databases or machines. A comparative claim
  must name the metric, the baseline and the paired interval. No universal or
  multiple-comparison-adjusted significance is claimed. Details are in
  [rq1_modeling_implementation_plan.md](rq1_modeling_implementation_plan.md).
* **Feature contract:** the learned models use exactly the 69 allowlisted
  columns, with the same names in the same order. The raw `est_total_cost` is a
  separate pre-execution baseline input outside that allowlist.

**Fitting rule:** every preprocessing step (scaling, encoding, feature
selection, dropping constant columns if chosen) and every calibrated baseline is
fitted **inside the training folds only** and then applied to the test fold.
The classification decision threshold is not fitted: v1 fixes it at a
predicted probability of 0.5 (see High-runtime classification).

## Splits

* **Folds are read, never generated.** Use the frozen assignments in
  `derived/splits.csv` (columns `split_role`, `fold_group`, `fold_family`,
  `fold_seen_template`). Never call `splits.group_kfold` or any other fold
  generator for RQ1. The stored folds come from the manifest, and the NumPy
  generator behind `group_kfold` is not guaranteed stable across library
  versions.
* **Primary:** `fold_group`, 5 folds, whole semantic groups held out. Every
  parameterization, snapshot, configuration, index variant and rewrite form of a
  group stays together. Development keys per fold: 960, 960, 960, 960, 944.
* **Stricter check:** `fold_family`, whole structure families held out. For
  TPC-H-derived, probe and rewrite groups the structure family **is** the
  semantic group, so the stricter separation applies **primarily within the
  structural component**: all eight output shapes over one join path are held
  out together. It does not merge a TPC-H query or a rewrite pair with a
  structural group that touches the same tables. Report it as a robustness
  check, not as complete schema-level separation.
* **Labelled diagnostic:** `fold_seen_template` holds out whole query
  instances, so templates appear in training and test. It is never presented as
  unseen-structure performance.
* **Random row-wise split: deferred beyond v1.** A random split would have to
  generate new folds, which v1 forbids. If a later version adds one as a
  leakage diagnostic, it must be a separately recorded, seeded assignment that
  is always labelled "leakage diagnostic" and never replaces `splits.csv`.
* **Tuning:** the first scope uses fixed hyperparameters. If tuning is
  introduced, it uses **nested grouped partitions**: inside each outer training
  fold, the inner folds are formed from whole semantic groups of that training
  fold only, with a recorded seed. The outer test fold and the RQ2 cases are
  never seen during selection.
* Fold assignments are **reused for every model**, and every result reports
  keys, groups and keys per group.
* Historical plan ([final_dataset_plan.md](final_dataset_plan.md)): fingerprint,
  workload, scale and configuration holdouts were planned for a final study.
  They are not part of this release's frozen splits; adding one would be a new,
  separately recorded split, never a change to `splits.csv`.

## Reporting strata

The development data are concentrated, so pooled numbers alone would mislead:

* all 393 development 1 s positives are at **SF 1** (183 under C1, 210 under C2);
  SF 0.1 has none;
* 351 of the positives (101 of the 106 positive groups) are **structural**,
  34 are TPC-H-derived (4 groups), 8 are rewrite cases (1 group) and none are probes;
* in `fold_group` fold 4, every positive comes from the structural component.

Every regression and classification result is therefore reported pooled
(out-of-fold) **and** by snapshot, by component, by configuration and by runtime
bin, with per-fold values for variability. A metric that is undefined in a
stratum is reported as null with a reason, following the metric-specific rules
in [rq1_modeling_implementation_plan.md](rq1_modeling_implementation_plan.md).
For example, average precision, ROC-AUC and recall are null at SF 0.1, which
has no positives, while the false-positive rate and Brier score are still
reported there.

Some strata are thin, and they are flagged rather than hidden:
- a classification stratum with fewer than 30 positives, or fewer than 5
  positive semantic groups;
- a pooled stratum whose positives all come from a single fold.

Flagged results stay visible but are descriptive only. Rows also report query
instances, positive groups and the folds contributing positives.

**Limitations.** Every report carries the limitations block from
`config/rq1_modeling_v1.yaml` verbatim. It covers:
- the workload, the PostgreSQL version and image, the single machine, the two
  scale factors, the two `work_mem` profiles, isolated warm-cache execution and
  the 15 s timeout;
- the execution-time-only target (planning and client latency excluded);
- the absence of 10 s development cases;
- the observed development range of 0.011–6,906.413 ms, outside which
  predictions are unsupported.

## Noise and threshold diagnostics

These are descriptive diagnostics. They never change a label, the frozen 1 s
boundary, or the release.

* **Repetition-noise reference:** leave-one-run-out q-error of the three
  measured repetitions, reported next to model error as a descriptive reference.
  It is not a proven lower bound on prediction error. On development cases, the
  coefficient of variation of the repetitions has median 0.046 and 90th
  percentile 0.136 (audit, 2026-10-21).
* **Threshold sensitivity:** 31 development cases have repetitions on both sides
  of 1,000 ms (13 of them positive by median), and 245 have medians between
  800 ms and 1,250 ms. Report classification metrics with and without this
  near-threshold band, as a labelled sensitivity analysis. The headline uses all
  cases and the frozen labels. Both sets are defined from observed runtimes, so
  they are retrospective diagnostics and never pre-execution filters.

## Pilot diagnostic (implemented in `pilot_audit.py`; historical)

| Item | Setting |
|---|---|
| Keys / groups | pilot instances with a complete label / TPC-H templates |
| Folds | 5, seed `20260923`, identical for all models |
| Global median | median of the training-fold labels |
| Raw PostgreSQL cost | Spearman and Kendall rank correlation only (planner units cannot be scored in ms) |
| Calibrated PostgreSQL cost | ln(runtime) = a + b·ln(cost), least squares on each training fold |
| Nonlinear model | scikit-learn `HistGradientBoostingRegressor` on ln(runtime); fixed hyperparameters (`max_iter=300`, `learning_rate=0.05`, `max_leaf_nodes=15`, `min_samples_leaf=5`, `l2_regularization=1.0`, no early stopping), `random_state` = seed; **no tuning**, so no nested selection is needed |
| Features | 23 A-class SQL-structure counts + 44 D-class estimate-view plan features |
| Caveat | pilot features come from the estimate view of `EXPLAIN ANALYZE` plans; no plain plan was captured |

On the pilot (recomputed, 22 template groups), the calibrated baseline reached
median q-error 1.694 and the nonlinear model 1.794 under template-grouped folds.
The learned model did not beat the baseline on unseen templates there.

## Metrics

| Metric | Definition | Role |
|---|---|---|
| log-runtime MAE | mean \|ln pred − ln actual\| (floor 0.001 ms) | primary |
| median, p90, p95 q-error | max(pred/actual, actual/pred) | primary |
| MAE, RMSE (ms) | raw-scale errors | operational context |
| R² (log and ms) | variance explained | **secondary; never called accuracy** |
| Spearman / Kendall | rank agreement | raw-cost baseline |
| Repetition-noise reference | leave-one-run-out q-error of repeated runs | descriptive reference next to model error; not a lower bound |

Out-of-fold predictions are pooled for headline numbers; per-fold values are
reported for variability. Tail error is reported by runtime bin.

## High-runtime classification

* A threshold is evaluable only with at least 10 positive development cases from
  at least 5 semantic groups and 2 components (gates G-07 and G-08). Otherwise
  its status is `NOT_YET_EVALUABLE` and no classifier is trained or reported.
* **1 s:** evaluable (G-07 passed: 393 positives, 106 groups, 3 components).
* **10 s:** `NOT_YET_EVALUABLE` (G-08: no development positives). The four
  right-censored cases at or above 10 s are reserved RQ2 cases. Moving them, or
  any other RQ2 case, into development to reach the gate is not allowed.
* Metrics:
  - **average precision** (`sklearn.metrics.average_precision_score`, the
    non-interpolated step sum) is the primary ranking metric. It is reported
    under that name and is never replaced by, or called, the trapezoidal area
    under the precision-recall curve;
  - recall and false-negative rate are the primary safety measures;
  - precision and warnings per 100 queries;
  - Brier score and calibration curve;
  - ROC-AUC is secondary;
  - raw accuracy is never a headline.
* **Decision threshold:** v1 fixes the probability threshold at 0.5 (positive
  when the predicted probability is at least 0.5). It was fixed in this
  specification before any model was fitted and before any model prediction was
  inspected. It was not chosen before any data were seen: the dataset had already
  been audited. It is not tuned. The runtime-label boundary stays 1,000 ms.
  Recall, false-negative rate and precision at 0.5 describe one predeclared
  operating point, not a recommended production threshold. Ranking quality is
  characterized by average precision and the step-wise precision-recall curve.
  An optional threshold grid is deferred.

## Reporting rules

* No random-split numbers are produced in v1. If a later version adds them,
  they are labelled "leakage diagnostic" and never described as performance on
  unseen queries.
* Seen-template numbers are labelled "seen-template diagnostic".
* Results show grouped numbers with keys, groups and keys per group, and the
  strata above.
* Previously reported values that used a different model or definition are
  `NOT_COMPARABLE` (see `reports/poster/pilot_claim_check.md`).
