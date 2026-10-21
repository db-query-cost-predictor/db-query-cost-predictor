# RQ1 evaluation protocol

**RQ1.** How accurately can a model predict isolated PostgreSQL execution time,
and flag high-runtime queries, for **unseen query structures**, using only
information available before execution (SQL, schema/statistics, configuration,
plain `EXPLAIN`)? Does it improve on a **calibrated PostgreSQL plan-cost
baseline** evaluated on the same grouped folds?

Status: the poster milestone contains only the pilot diagnostic below
(`RECOMPUTED_PILOT`). The full RQ1 study is `PLANNED`. Its data is the expanded
release ([expanded_release_spec.md](expanded_release_spec.md)), which is not
yet collected.

## Primary comparison

The headline result is the learned model versus the calibrated PostgreSQL
plan-cost baseline, ln(runtime) = a + b ln(cost), fitted on each training fold
only. Both use identical primary folds (whole semantic groups held out) and the
same metrics. The raw planner cost is reported only as a rank baseline, because
planner units are not milliseconds. The global median is a sanity floor. On the
pilot (recomputed, 22 template groups), the calibrated baseline reached median
q-error 1.694 and the nonlinear model 1.794 under template-grouped folds. The
learned model has therefore not yet beaten the baseline on unseen templates.

## Targets

| Target | Definition |
|---|---|
| Regression label | median of the measured repetitions (instrumented `EXPLAIN ANALYZE` execution time, `TIMING OFF`), modelled as ln(ms) |
| Censoring | a timed-out run is right-censored; censored labels are never imputed as completed runtimes. The expanded release's lower bound is the **execution-time** bound (timeout minus observed planning time minus 100 ms), because `statement_timeout` also counts parsing and planning |
| High-runtime flag | **frozen** at 1 s and 10 s: positive if the median is at or above the threshold, or if a censored key's bound is; censored below the threshold is *indeterminate*; failed keys are unlabeled. Never changed to manufacture positives |

Instrumented server time is not end-to-end latency; the final study adds a
client-latency comparison by runtime bin during calibration.

## Inputs

Only fields with role `feature` and class A–D in
[`config/feature_registry.yaml`](../config/feature_registry.yaml). Template,
semantic-group and query identities are used for grouping, never as predictors.
Class E fields (execution time, actual rows/loops, buffers, temporary blocks,
I/O timing, WAL, launched workers, runtime status) are rejected mechanically.

## Splits

* **Primary:** grouped K-fold where every template / semantic group lies in exactly
  one fold (`splits.group_kfold`, seeded, deterministic).
* **Leakage diagnostic only:** random row-wise K-fold (`splits.random_kfold`).
* Fold assignments are computed once and **reused for every model**.
* Every result reports keys, groups and keys per group.
* Final study (planned) adds fingerprint, workload, scale, configuration and
  reserved anti-pattern-family holdouts; hyperparameters are selected with
  nested grouped validation, never on a held-out split.
* Expanded release: the folds are stored in the manifest. `fold_group` (whole
  semantic groups) is primary. `fold_family` (whole structure families) is a
  stricter check. `fold_seen_template` is a labelled diagnostic, never presented
  as unseen-structure performance. The 160 RQ2-reserved keys are excluded from
  every RQ1 fold.

## Pilot diagnostic (implemented in `pilot_audit.py`, unexecuted)

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

* Evaluable only with at least 10 positive examples at a threshold. For the
  expanded release, these must come from at least 5 semantic groups and 2
  components (gates G-07 and G-08). Otherwise the status is `NOT_YET_EVALUABLE`
  and no classifier is trained or reported.
  The pilot's maximum runtime is far below 1 s, so both thresholds are expected
  to be `NOT_YET_EVALUABLE` — the audit computes and reports this.
* Final study (planned): PR-AUC as the primary ranking metric; recall and
  false-negative rate as primary safety measures; precision and warnings per
  100 queries; Brier score and calibration curve; ROC-AUC secondary; raw
  accuracy never a headline.

## Reporting rules

* Random-split numbers are labelled "leakage diagnostic" and never described as
  performance on unseen queries.
* The poster shows grouped results with keys, groups and keys per group.
* Previously reported values that used a different model or definition are
  `NOT_COMPARABLE` (see `reports/poster/pilot_claim_check.md`).
