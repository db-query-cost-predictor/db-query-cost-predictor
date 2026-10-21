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

* **Rows:** development cases only (`split_role == development`, 4,784 cases in
  194 semantic groups). The 160 `heldout_rq2` cases are excluded from model
  fitting, preprocessing fitting, calibration, feature selection,
  hyperparameter or model selection, and the choice of decision thresholds.
* **Regression target:** ln(`median_ms`), the median of the three measured
  repetitions (instrumented `EXPLAIN ANALYZE` execution time, `TIMING OFF`).
  **All 4,784 development cases are `complete`**, so the development set has no
  censored labels and no censoring-aware model is needed for it. All four
  right-censored cases are reserved RQ2 cases and are outside RQ1. Censored
  labels are never imputed as completed runtimes anywhere.
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
| Raw planner cost | Spearman and Kendall rank agreement only (planner units are not milliseconds) | ranking by raw cost (PR-AUC) |
| **Cost-only (calibrated)** | ln(runtime) = a + b·ln(cost), least squares | logistic regression on ln(cost) |
| **Scale-plus-cost** | ln(runtime) = a + b·ln(cost) + c·ln(scale factor) | logistic regression on ln(cost) and ln(scale factor) |

The headline comparison is the learned model against the cost-only calibrated
baseline. The scale-plus-cost baseline shows how much the model adds beyond
knowing the snapshot size, which matters because every development positive at
1 s is at SF 1.

**Fitting rule:** every preprocessing step (scaling, encoding, feature
selection, dropping constant columns if chosen), every calibrated baseline and
every decision threshold is fitted **inside the training folds only** and then
applied to the test fold.

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
* **Leakage diagnostic only:** a random row-wise split (`splits.random_kfold`),
  always labelled as such.
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
stratum (for example PR-AUC at SF 0.1, which has no positives) is reported as
undefined, and the false-positive rate is reported there instead.

## Noise and threshold diagnostics

These are descriptive diagnostics. They never change a label, the frozen 1 s
boundary, or the release.

* **Repeated-run noise floor:** leave-one-run-out q-error of the three measured
  repetitions, reported next to model error. On development cases, the
  coefficient of variation of the repetitions has median 0.046 and 90th
  percentile 0.136 (audit, 2026-10-21).
* **Threshold sensitivity:** 31 development cases have repetitions on both sides
  of 1,000 ms (13 of them positive by median), and 245 have medians between
  800 ms and 1,250 ms. Report classification metrics with and without this
  near-threshold band, as a labelled sensitivity analysis. The headline uses all
  cases and the frozen labels.

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
| Noise floor | leave-one-run-out q-error of repeated runs | reported next to model error |

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
* Metrics: PR-AUC as the primary ranking metric; recall and false-negative rate
  as primary safety measures; precision and warnings per 100 queries; Brier
  score and calibration curve; ROC-AUC secondary; raw accuracy never a headline.
  Any operating threshold on predicted scores is chosen inside the training
  folds.

## Reporting rules

* Random-split numbers are labelled "leakage diagnostic" and never described as
  performance on unseen queries.
* Seen-template numbers are labelled "seen-template diagnostic".
* Results show grouped numbers with keys, groups and keys per group, and the
  strata above.
* Previously reported values that used a different model or definition are
  `NOT_COMPARABLE` (see `reports/poster/pilot_claim_check.md`).
