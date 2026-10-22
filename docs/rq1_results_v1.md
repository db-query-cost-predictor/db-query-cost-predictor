# RQ1 results (v1)

**Status: complete.** The single official RQ1 v1 run ended `COMPLETE` on
2026-10-22 (UTC), from committed source `ad66662` with a clean tracked tree.
Every number on this page is copied from that run's saved outputs. Nothing was
re-estimated for this summary. Confusion counts at the 0.5 operating point were
tabulated from the saved out-of-fold predictions and reconcile exactly with the
stored rates.

Related: [evaluation protocol](rq1_evaluation_protocol.md) (methodology, fixed
before the run) · [implementation plan](rq1_modeling_implementation_plan.md) ·
[run guide](rq1_run_guide.md) · [teammate setup](teammate_setup.md) ·
[team handoff](team_handoff.md) · [asset catalogue](handoff_assets_v1.json)

## Summary

* **Runtime regression.** Under the primary grouped evaluation, where whole
  semantic groups are held out, both learned models (`reg_ridge_all`,
  `reg_hgb_all`) had lower log-MAE and lower median, p90 and p95 q-error than
  both calibrated plan-cost baselines. All 16 paired 95 % intervals exclude
  zero.
* **1 s high-runtime flag.** Both learned classifiers (`clf_logistic_all`,
  `clf_hgb_all`) had higher average precision (AP) than both cost-logistic
  baselines. The differences were +0.059 to +0.073 AP, and all four paired
  intervals exclude zero.
* **In practice, at the predeclared 0.5 threshold:**
  - the learned classifiers caught 333 and 338 of the 393 development cases
    that ran for at least 1 s, compared with 243 for the cost-logistic baseline;
  - each learned classifier raised 29 false alarms among the 4,391 faster
    cases, compared with 21 for the baseline.
* **The gains are not uniform.**
  - Most remaining classification errors lie near the 1 s boundary.
  - In some strata (TPC-H-derived queries, probes, some runtime bins), a learned
    model is no better than a cost baseline on some metrics, or worse.
* **No winner is named.** The two learned models were never compared with each
  other by a paired interval, so neither is called the better model, and no
  model is a universal winner.
* **Scope.**
  - The results come from one TPC-H-derived workload, on one schema and one
    laptop.
  - All 393 positives are at SF 1.
  - The original release status, STOP at G-08, is unchanged.
  - 10 s classification remains `NOT_YET_EVALUABLE`.

## 1. Research question

> How accurately can a model predict isolated PostgreSQL execution time, and
> flag high-runtime queries, for **unseen query structures**, using only
> information available before execution (SQL, schema/statistics,
> configuration, plain `EXPLAIN`)? Does it improve on a **calibrated PostgreSQL
> plan-cost baseline** evaluated on the same grouped folds?

| Task | Target | v1 status |
|---|---|---|
| Runtime regression | ln(`median_ms`) | evaluated |
| High-runtime classification at 1 s | `hr_1s_class` (positive = 1) | evaluated |
| High-runtime classification at 10 s | `hr_10s_class` | `NOT_YET_EVALUABLE`: no development case reaches 10 s; nothing is trained or reported |

## 2. Dataset

* **Release.** The release is `expanded-v1`, with manifest
  `mf_a75c6408fb59ab8f…` and derived release id
  `expanded:a75c6408fb59ab8f:expanded-label-v1:003c5455636b8843`. It was
  collected on 2026-10-04 (UTC) and audited on 2026-10-21; see
  [expanded_collection_checkpoint.md](expanded_collection_checkpoint.md). Its
  4,944 determined cases split as follows.
  - **Development rows (used by RQ1).** 4,784 cases with
    `split_role == development`, all `complete`, in 194 semantic groups and
    1,292 query instances.
  - **Reserved RQ2 rows (not used).** 160 `heldout_rq2` cases in 5 rewrite
    groups. They were never fitted, scaled, calibrated, selected or scored.
* **Workload.** It contains:
  - 22 TPC-H-derived templates;
  - 128 generated structural groups;
  - 24 point/range probes;
  - 20 development rewrite groups.

  These ran on the TPC-H schema at SF 0.1 and SF 1, under two configurations:
  `C1_baseline` (`work_mem` 4 MB) and `C2_reduced_work_mem` (256 kB), both with
  jit off. The server was PostgreSQL 16.15 in Docker Desktop on one Windows
  laptop.
* **Measurement.** Each case had:
  - one plain `EXPLAIN`;
  - one unmeasured warm-up;
  - three measured `EXPLAIN ANALYZE` runs with `TIMING OFF`, from one client,
    sequentially, under a warm cache and a 15 s statement timeout.

  The target is the median of the three execution times. Planning time and
  client or network latency are excluded.
* **Label distribution.**
  - Development runtimes range from 0.011 ms to 6,906.413 ms.
  - 393 cases (8.2 %) reach 1 s, in 106 semantic groups, and none reaches 10 s.
  - All 393 positives are at SF 1: 183 under C1 and 210 under C2.
  - By component, 351 positives (101 groups) are structural, 34 (4 groups) are
    TPC-H-derived, 8 (1 group) are rewrite cases and none are probes.

## 3. Pre-execution inputs and models

Every learned model sees the same 69 registered features, in a fixed order. The
input check confirms them against
[`config/feature_registry.yaml`](../config/feature_registry.yaml) before any
fit.

| Availability class | Columns | Count | What computing them needs |
|---|---|---:|---|
| A, SQL structure | `sql_*` | 23 | the SQL text only |
| B, schema/statistics | `snap_scale_factor` | 1 | the snapshot's scale factor |
| C, configuration | `cfg_work_mem_kb` | 1 | the session's `work_mem` |
| D, plain `EXPLAIN` | `est_*` | 44 | a plain `EXPLAIN` on the target database; the query is planned, not executed |

No runtime, actual row count, buffer count, query identifier or group
identifier enters any model.

| Model | Task | Role | Definition |
|---|---|---|---|
| `reg_train_median` | regression | baseline | the training-fold median runtime for every case |
| `reg_raw_cost_rank` | regression | baseline | raw planner total cost as a ranking score; rank metrics only |
| `reg_cost_ols` | regression | baseline | OLS of ln runtime on ln(planner cost) |
| `reg_scale_cost_ols` | regression | baseline | OLS on ln(planner cost) and ln(scale factor) |
| `reg_ridge_all` | regression | learned | standardize, then ridge (alpha 1.0), on the 69 features |
| `reg_hgb_all` | regression | learned | histogram gradient boosting on the 69 features (300 iterations, learning rate 0.05, 15 leaves, min 5 per leaf, L2 1.0) |
| `clf_train_prevalence` | 1 s | baseline | the training-fold positive share for every case |
| `clf_raw_cost_rank` | 1 s | baseline | raw planner cost as a ranking score; AP and ROC-AUC only |
| `clf_cost_logistic` | 1 s | baseline | standardize, then logistic regression on ln(planner cost) |
| `clf_scale_cost_logistic` | 1 s | baseline | the same, on ln(planner cost) and ln(scale factor) |
| `clf_logistic_all` | 1 s | learned | standardize, then logistic regression (C 1.0), on the 69 features |
| `clf_hgb_all` | 1 s | learned | histogram gradient boosting classifier on the 69 features (same settings) |

What was fixed in advance:
- all hyperparameters (no tuning);
- the feature set (no feature selection);
- the probabilities (no post-hoc calibration);
- the classification threshold of 0.5, set before any model was fitted.

## 4. Grouped evaluation

The folds are read from the stored `derived/splits.csv` and never regenerated.
Each scheme has 5 folds, and each model is fitted once per training fold, so
there are 15 fits per model.

| Scheme | What is held out together | Role in this report |
|---|---|---|
| `fold_group` | whole semantic groups (960, 960, 960, 960 and 944 keys) | **primary**: every headline number and every interval |
| `fold_family` | whole structure families | robustness check. It is stricter mainly within the structural component; for the other components the family is the semantic group. It is not schema-level separation |
| `fold_seen_template` | whole query instances, so templates appear in both training and test | labelled diagnostic; never presented as unseen-structure performance |

A random row-wise split is **deferred beyond v1**, so no random-split numbers
exist.

**Uncertainty.** Intervals come from a paired cluster bootstrap over the 194
semantic groups:
- 2,000 replicates with seed 20261021 and 95 % percentile intervals;
- the same resampled groups for every model and every difference;
- computed for the primary `fold_group` pooled results only.

The intervals are conditional on the fixed out-of-fold predictions and folds.
They do not capture retraining variability or generalization to other
databases, workloads or machines. No multiple-comparison adjustment is made.
All 46 intervals had 2,000 valid replicates and none invalid. They break down
as follows:
- 20 regression model metrics;
- 16 regression differences;
- 6 AP values;
- 4 AP differences.

## 5. How to read the regression metrics

* **q-error.** For a case with actual time *t* and prediction *p*, q-error is
  max(*p*/*t*, *t*/*p*). It is never below 1 and treats over- and
  under-prediction alike.
  - A q-error of 1 is exact.
  - A q-error of 2 means off by a factor of two in either direction: for
    example, 50 ms or 200 ms predicted for a 100 ms query.

  The median q-error describes a typical case; p90 and p95 describe the worst
  10 % and 5 %.
* **log-MAE.** The mean of |ln *p* − ln *t*|, which is the mean of ln(q-error).
  Its exponential is the geometric-mean q-error:

  | Model | Geometric-mean q-error |
  |---|---:|
  | `reg_hgb_all` | 1.25 |
  | `reg_ridge_all` | 1.50 |
  | `reg_cost_ols` | 1.83 |
  | `reg_train_median` (constant) | 8.74 |
* **Runtime predictions.** A runtime prediction is exp(predicted ln runtime),
  with no smearing correction. It estimates a typical (median-like) runtime,
  not a mean.
  - Millisecond MAE and RMSE are secondary.
  - R² is secondary and is never called accuracy.

## 6. Runtime regression (primary: `fold_group`, pooled out-of-fold)

**Table 1. Error metrics with 95 % paired-bootstrap intervals (4,784 cases, 194 groups).**

| Model | Role | log-MAE | median q-error | p90 q-error | p95 q-error |
|---|---|---|---|---|---|
| `reg_train_median` | baseline | 2.1681 [1.9891, 2.3810] | 5.5987 [5.3547, 5.9651] | 86.1265 [48.3746, 736.8790] | 1625.3704 [327.4478, 2389.4542] |
| `reg_cost_ols` | baseline | 0.6027 [0.5594, 0.6499] | 1.5161 [1.4516, 1.5990] | 3.9458 [3.5700, 4.4104] | 5.1731 [4.6170, 6.6220] |
| `reg_scale_cost_ols` | baseline | 0.6023 [0.5590, 0.6496] | 1.5127 [1.4449, 1.5961] | 3.9124 [3.5908, 4.3601] | 5.2131 [4.6434, 6.6326] |
| `reg_ridge_all` | learned | 0.4032 [0.3655, 0.4436] | 1.3576 [1.3219, 1.3944] | 2.3861 [2.1369, 2.7819] | 3.2931 [2.7220, 3.9683] |
| `reg_hgb_all` | learned | 0.2196 [0.1908, 0.2490] | 1.1258 [1.1127, 1.1407] | 1.7524 [1.6060, 1.9614] | 2.1812 [1.9559, 2.5396] |

**Table 2. Paired differences, learned minus baseline (negative = lower error), with 95 % intervals.**

| Learned model | Metric | vs `reg_cost_ols` | vs `reg_scale_cost_ols` |
|---|---|---|---|
| `reg_ridge_all` | log-MAE | −0.1995 [−0.2464, −0.1547] | −0.1991 [−0.2468, −0.1543] |
| `reg_ridge_all` | median q-error | −0.1584 [−0.2403, −0.0944] | −0.1551 [−0.2381, −0.0857] |
| `reg_ridge_all` | p90 q-error | −1.5597 [−1.9800, −1.1751] | −1.5263 [−1.9365, −1.1418] |
| `reg_ridge_all` | p95 q-error | −1.8799 [−3.2899, −0.9902] | −1.9200 [−3.3343, −1.0426] |
| `reg_hgb_all` | log-MAE | −0.3831 [−0.4271, −0.3417] | −0.3827 [−0.4270, −0.3403] |
| `reg_hgb_all` | median q-error | −0.3903 [−0.4703, −0.3273] | −0.3869 [−0.4666, −0.3208] |
| `reg_hgb_all` | p90 q-error | −2.1934 [−2.6271, −1.8238] | −2.1600 [−2.5831, −1.8285] |
| `reg_hgb_all` | p95 q-error | −2.9919 [−4.3316, −2.3743] | −3.0320 [−4.3600, −2.4155] |

Each comparison names one metric, one baseline and one paired interval. That is
the only form of comparative claim this study supports.

**Rank agreement and secondary metrics (no intervals).**

| Model | Spearman | Kendall | MAE (ms) | RMSE (ms) | R² (log scale) |
|---|---|---|---|---|---|
| `reg_raw_cost_rank` | 0.9452 | 0.8191 | n/a (ranking score) | n/a | n/a |
| `reg_train_median` | −0.0412 | −0.0297 | 295.5023 | 761.1299 | −0.0076 |
| `reg_cost_ols` | 0.9446 | 0.8163 | 142.9299 | 471.9121 | 0.9207 |
| `reg_scale_cost_ols` | 0.9434 | 0.8146 | 141.6308 | 468.2210 | 0.9206 |
| `reg_ridge_all` | 0.9732 | 0.8654 | 95.7758 | 325.4537 | 0.9648 |
| `reg_hgb_all` | 0.9895 | 0.9214 | 46.3229 | 176.5262 | 0.9850 |

The plan-cost baselines already rank queries well (Spearman about 0.94). The
learned models' largest gains are in the tail: p95 q-error falls from about 5.2
for the cost baselines to 3.3 for ridge and 2.2 for HGB.

## 7. High-runtime classification at 1 s (primary: `fold_group`)

The headline metric is **average precision (AP)**, computed with scikit-learn's
`average_precision_score`. This is the non-interpolated step sum: it is not
accuracy and not trapezoidal PR-AUC. The 4,784 cases include 393 positives in
106 groups, spread over all 5 folds. A model that ranks at random scores about
the positive share, 0.082.

**Table 3. AP with 95 % paired-bootstrap intervals.**

| Model | Role | average precision (AP) | ROC-AUC (secondary) | Brier score |
|---|---|---|---|---|
| `clf_train_prevalence` | baseline | 0.0780 [0.0660, 0.0929] | 0.4737 | 0.0754 |
| `clf_raw_cost_rank` | baseline | 0.9004 [0.8487, 0.9431] | 0.9853 | n/a (ranking score) |
| `clf_cost_logistic` | baseline | 0.8946 [0.8422, 0.9388] | 0.9847 | 0.0261 |
| `clf_scale_cost_logistic` | baseline | 0.8946 [0.8421, 0.9388] | 0.9847 | 0.0261 |
| `clf_logistic_all` | learned | 0.9673 [0.9470, 0.9824] | 0.9968 | 0.0131 |
| `clf_hgb_all` | learned | 0.9534 [0.9161, 0.9809] | 0.9897 | 0.0139 |

**Table 4. Paired AP differences, learned minus calibrated baseline (positive = higher AP).**

| Learned model | vs `clf_cost_logistic` | vs `clf_scale_cost_logistic` |
|---|---|---|
| `clf_logistic_all` | +0.0727 [+0.0319, +0.1205] | +0.0727 [+0.0319, +0.1206] |
| `clf_hgb_all` | +0.0588 [+0.0265, +0.0964] | +0.0589 [+0.0266, +0.0964] |

The raw planner-cost ranking (`clf_raw_cost_rank`) was not part of the
predeclared paired comparisons. No interval-backed claim is made against it.

## 8. Practical detection at the predeclared 0.5 threshold

A case is flagged when its predicted probability is at least 0.5. The threshold
was fixed before any model was fitted, it is **not tuned**, and it is **not a
recommended production setting**. The counts below are pooled `fold_group`
out-of-fold results over all 4,784 development cases: 393 positives (≥ 1 s) and
4,391 negatives.

**Table 5. Detection counts and rates at 0.5.**

| Model | Flagged | True alarms | Missed slow cases | False alarms | Recall | Precision | False-positive rate | Warnings per 100 cases |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `clf_train_prevalence` | 0 | 0 | 393 | 0 | 0.0000 | undefined (nothing flagged) | 0.0000 | 0.00 |
| `clf_cost_logistic` | 264 | 243 | 150 | 21 | 0.6183 | 0.9205 | 0.0048 | 5.52 |
| `clf_scale_cost_logistic` | 264 | 243 | 150 | 21 | 0.6183 | 0.9205 | 0.0048 | 5.52 |
| `clf_logistic_all` | 362 | 333 | 60 | 29 | 0.8473 | 0.9199 | 0.0066 | 7.57 |
| `clf_hgb_all` | 367 | 338 | 55 | 29 | 0.8601 | 0.9210 | 0.0066 | 7.67 |

In plain terms:
- The cost-logistic baseline flags about 5.5 of every 100 queries and misses
  150 of the 393 slow ones (38 %).
- Each learned classifier flags about 7.6 of every 100 queries and misses 55 to
  60 (14–15 %).
- Precision stays about 0.92 for all three, so roughly one flag in twelve is a
  false alarm.

## 9. Robustness: families held out versus seen templates

These are pooled out-of-fold results. They have no bootstrap intervals, so the
differences between schemes are descriptive.

**Table 6. Regression across fold schemes (log-MAE / median q-error / p95 q-error).**

| Model | `fold_group` (primary) | `fold_family` (robustness) | `fold_seen_template` (diagnostic) |
|---|---|---|---|
| `reg_cost_ols` | 0.6027 / 1.5161 / 5.1731 | 0.6030 / 1.5144 / 5.1353 | 0.6029 / 1.5181 / 5.1742 |
| `reg_scale_cost_ols` | 0.6023 / 1.5127 / 5.2131 | 0.6029 / 1.5206 / 5.1452 | 0.6029 / 1.5179 / 5.1797 |
| `reg_ridge_all` | 0.4032 / 1.3576 / 3.2931 | 0.3919 / 1.3370 / 3.2863 | 0.3237 / 1.2918 / 2.3215 |
| `reg_hgb_all` | 0.2196 / 1.1258 / 2.1812 | 0.2150 / 1.1260 / 2.2112 | 0.1108 / 1.0771 / 1.3943 |

**Table 7. 1 s classification across fold schemes (AP / recall at 0.5 / precision at 0.5).**

| Model | `fold_group` (primary) | `fold_family` (robustness) | `fold_seen_template` (diagnostic) |
|---|---|---|---|
| `clf_cost_logistic` | 0.8946 / 0.6183 / 0.9205 | 0.8962 / 0.6183 / 0.9205 | 0.8994 / 0.6132 / 0.9198 |
| `clf_logistic_all` | 0.9673 / 0.8473 / 0.9199 | 0.9782 / 0.9160 / 0.9254 | 0.9833 / 0.9237 / 0.9478 |
| `clf_hgb_all` | 0.9534 / 0.8601 / 0.9210 | 0.9500 / 0.8550 / 0.8889 | 0.9886 / 0.9593 / 0.9642 |

**Family-held-out (robustness).** Holding out whole structure families gave
results close to the primary scheme:
- HGB regression log-MAE was 0.2150, against 0.2196 under `fold_group`.
- Logistic AP was 0.9782, against 0.9673.
- HGB AP was 0.9500, against 0.9534; at the 0.5 threshold its false alarms rose
  from 29 to 42.

`fold_family` differs from `fold_group` only within the structural component.
This is therefore a check against leakage between related join-path shapes,
not evidence for unseen schemas.

**Seen-template diagnostic.** Here the same templates are in training and test,
and the learned models look markedly better:
- HGB log-MAE was 0.1108 and p95 q-error 1.39, against 0.2196 and 2.18 under
  `fold_group`.
- HGB AP was 0.9886.

The one- and two-parameter cost baselines barely change. The gap shows how much
more optimistic the learned models look when their templates were seen in
training. The seen-template numbers are never used as unseen-structure
performance.

## 10. Where prediction is harder

### Near the 1 s boundary

Two retrospective sets are defined from observed runtimes. They describe where
errors occur. They are not deployable filters, and no model, label or
threshold changes.

| Set | Definition | Cases | Positives |
|---|---|---:|---:|
| S (straddle) | the three measured runs fall on both sides of 1,000 ms | 31 | 13 |
| B (band) | 800 ms ≤ `median_ms` < 1,250 ms | 245 | 132 |

| Model | AP, all cases | AP, excluding B | AP, B only | Recall at 0.5, B only | False-positive rate at 0.5, B only |
|---|---:|---:|---:|---:|---:|
| `clf_raw_cost_rank` | 0.9004 | 0.9580 | 0.7587 | n/a | n/a |
| `clf_cost_logistic` | 0.8946 | 0.9509 | 0.7538 | 0.1515 | 0.0265 |
| `clf_logistic_all` | 0.9673 | 0.9969 | 0.7780 | 0.6136 | 0.2389 |
| `clf_hgb_all` | 0.9534 | 0.9949 | 0.8178 | 0.7424 | 0.2389 |

Most residual classification errors sit in this band:
- For each learned classifier, **27 of its 29 false alarms** are band cases.
- The band also holds **51 of 60** misses for `clf_logistic_all`, **34 of 55**
  for `clf_hgb_all` and **112 of 150** for `clf_cost_logistic`.
- Excluding the 31 straddle cases changes AP only slightly: 0.9727 for
  logistic and 0.9595 for HGB.

For the 31 straddle cases, the three measured runs fell on both sides of
1,000 ms. This shows measurement variability near the threshold. It does
**not** prove that the classification errors there are irreducible:
- the labels are fixed functions of the median of the three runs, so a model
  could still classify these cases correctly;
- the near-threshold sets describe where errors concentrate in v1, not an
  error floor.

### Repetition-noise reference

For scale, the three measured runs of the same case differ:
- **leave-one-run-out q-error:** median 1.046, p90 1.183, p95 1.256;
- **coefficient of variation:** median 0.046.

The noise is larger for probes (median q-error 1.090, p95 1.490) and for cases
under 10 ms (median 1.070, p95 1.370). This is a descriptive reference only. It
is not a proven lower bound on prediction error.

### Across query components and other strata

These are pooled `fold_group` strata without intervals. Thin strata stay
visible.

| Component (regression) | Cases / groups | `reg_cost_ols` log-MAE / p95 q | `reg_ridge_all` log-MAE / p95 q | `reg_hgb_all` log-MAE / p95 q |
|---|---|---|---|---|
| structural | 3,072 / 128 | 0.5432 / 4.5455 | 0.2957 / 2.1169 | 0.1114 / 1.3645 |
| TPC-H-derived | 688 / 22 | 0.5838 / 4.2978 | 0.7294 / 5.6512 | 0.4448 / 3.1109 |
| probe | 384 / 24 | 1.0400 / 15.9479 | 0.6319 / 4.4411 | 0.4284 / 2.7697 |
| rewrite | 640 / 20 | 0.6465 / 8.6890 | 0.4317 / 2.8613 | 0.3719 / 2.9398 |

**Regression.**
* Structural queries are the easiest stratum: HGB median q-error is 1.08.
* On the 22 TPC-H-derived groups, `reg_ridge_all` is **worse** than both cost
  baselines on log-MAE, median q-error (1.868 against 1.515) and p95 q-error.
  HGB is better on all three.
* On probes, where the cost baselines are weakest, both learned models cut the
  error. On Spearman, though, HGB (0.713) is only on a par with the cost
  baseline (0.708), and ridge reaches 0.799.
* On rewrite cases, ridge's median q-error (1.442) is above the cost baseline's
  (1.408).
* By actual-runtime bin:
  - in 100–1,000 ms, ridge's median q-error (1.304) is above the cost
    baseline's (1.257);
  - in 1,000–10,000 ms, ridge's p95 q-error (5.31) is above the cost
    baseline's (4.32).
* HGB has the lowest point estimate in every component and runtime bin on
  log-MAE. Across scale factors and configurations, all three models keep a
  similar order.

**1 s classification.**

| Component | Positives (groups) | `clf_cost_logistic` AP | `clf_logistic_all` AP | `clf_hgb_all` AP | Flag |
|---|---|---:|---:|---:|---|
| structural | 351 (101) | 0.9383 | 0.9694 | 0.9751 | none |
| TPC-H-derived | 34 (4) | 0.7649 | 0.9712 | 0.7284 | thin positive support |
| rewrite | 8 (1, one fold) | 0.6575 | 1.0000 | 1.0000 | thin positive support; positives from a single fold |
| probe | 0 | undefined | undefined | undefined | single class |

* On the TPC-H-derived positives, HGB's AP (0.728) is below the cost baseline's
  (0.765), and its recall at 0.5 is 0.235.
* For the rewrite stratum, `clf_logistic_all` has AP 1.000 but recall 0 at 0.5:
  it ranks those 8 positives correctly but scores them all below 0.5. With 8
  positives from one group, these values say very little.
* By configuration, AP is:
  - `C1_baseline` (183 positives): logistic 0.9683, HGB 0.9711, cost logistic
    0.9091;
  - `C2_reduced_work_mem` (210 positives): logistic 0.9678, HGB 0.9399, cost
    logistic 0.8834.

## 11. Calibration

Every probabilistic model, scheme and slice has ten fixed probability bins in
`calibration_bins.csv`, with empty bins kept (2,700 rows). Probabilities were
not post-hoc calibrated.

For the primary pooled out-of-fold results:

* **Almost every case sits at the extremes.**
  - `clf_logistic_all` puts 4,182 of 4,784 cases (87 %) below 0.1 and 200 cases
    at 0.9 or above; all 200 are positive.
  - `clf_hgb_all` puts 4,361 cases (91 %) below 0.1 and 315 at 0.9 or above.
  - The eight middle bins (0.1–0.9) hold only 402 cases for logistic (27 to
    112 per bin) and 108 for HGB (10 to 24 per bin). Their observed fractions
    are therefore noisy.
* **HGB is over-confident at both ends.**
  - Its top bin has mean predicted probability 0.993, but only 94.3 % of its
    cases are positive.
  - Its lowest bin holds 23 positives, at a mean predicted probability of
    0.0009.
* **The cost-logistic baseline under-predicts in the 0.2–0.4 range.** In the
  0.2–0.3 bin it predicts 0.24 where 37 % are positive; in the 0.3–0.4 bin it
  predicts 0.34 where 74 % are positive.
* **Brier scores** are 0.0131 for logistic, 0.0139 for HGB and 0.0261 for the
  cost logistic. They are dominated by the many easy negatives, so they say
  little about the uncertain middle range.

The probabilities should be read as **scores with approximate calibration on
this workload**, not as reliable risk estimates. This holds especially:
- for SF 0.1, where no positive exists to check them against;
- for any other workload, schema or machine.

## 12. Thin and single-class subsets

Flagged results stay visible, but they support no conclusion:

| Subset | Positives | Flags |
|---|---|---|
| SF 0.1 (2,392 cases) | 0 | single class; thin positive support |
| probe component (384) | 0 | single class; thin positive support |
| TPC-H-derived component (688) | 34 in 4 groups | thin positive support (< 5 positive groups) |
| rewrite component (640) | 8 in 1 group, one fold | thin positive support; positives from a single fold |
| runtime bins < 10, 10–100 and 100–1,000 ms | 0 | single class |
| runtime bin 1,000–10,000 ms | 393 of 393 | single class (AP 1.0 there means nothing) |

A stratum is flagged `thin_positive_support` when it has fewer than 30
positives or fewer than 5 positive groups.

## 13. Scope and limitations

* **One workload, schema and machine.** The study covers a TPC-H-derived
  workload on the TPC-H schema, at two scale factors and two `work_mem`
  profiles, on PostgreSQL 16.15 in Docker Desktop on one Windows laptop. Free
  memory was low (1.13 GB) at the single preflight observation before
  collection.
  - Generalization to other databases, schemas, workloads, PostgreSQL versions
    or hardware is **not** established.
* **Positive concentration.** All 1 s positives are at SF 1, and 351 of the 393
  are structural. Classification results say nothing about high-runtime
  queries at SF 0.1, or about query families with few positives.
* **Observed range.** Development runtimes run from 0.011 ms to 6,906.413 ms.
  Predictions outside that range are unsupported.
* **Measured quantity.** The target is instrumented server execution time
  (`EXPLAIN ANALYZE`, `TIMING OFF`). It is not end-to-end latency, and it
  excludes planning time.
* **What the evaluation is.** All results are cross-validated. There is no
  separate final test set, and the 160 reserved RQ2 cases were not scored
  (`heldout_rq2` predictions are not part of v1).
* **Interval scope.** Intervals cover only the primary scheme. They are
  conditional on the fixed folds and predictions, and they are not adjusted for
  multiple comparisons.
* **Release status unchanged.**
  - The original release validation remains **STOP (G-08)**. Nothing in v1
    relabels data, moves a reserved case into development, changes a threshold
    or marks the release PASS.
  - 10 s classification remains **`NOT_YET_EVALUABLE`**.
* **Determinism.** A one-thread limit was applied and recorded. Bit-identical
  gradient-boosting results on other hardware are not guaranteed.

## 14. Hand-off refits (separate, unscored)

The run also saved eight models, each refitted once on all 4,784 development
cases:
- `reg_cost_ols`, `reg_scale_cost_ols`, `reg_ridge_all`, `reg_hgb_all`;
- `clf_cost_logistic`, `clf_scale_cost_logistic`, `clf_logistic_all`,
  `clf_hgb_all`.

They are labelled `REFIT_ON_ALL_DEVELOPMENT_NO_EVALUATION`. They were never
scored and produced no predictions, and **no number on this page comes from
them**. They exist for integration only. Their input and output contracts are
in [team_handoff.md](team_handoff.md#model-inputoutput-contract) and in
`handoff/handoff_manifest.json`.

## 15. External review

The uploaded dataset and results packages were independently reviewed in
ChatGPT. That external review reported the following matches:

* the archive and all 21 pinned input hashes;
* all 29,708 raw record signatures;
* the original labels, development selection and stored folds, which
  reconciled;
* all 5,832 metric rows (3,240 regression, 2,592 classification) and all 2,700
  calibration bins;
* all 2,000 bootstrap draws, 92,000 replicate statistics and 46 intervals,
  against independent calculations;
* the noise diagnostics, and the exact S and B set memberships, against the
  raw measurements.

This is an attribution of that external review. No local audit log of it is
part of the repository or the assets.

## 16. Run identity and artifacts

| Item | Value |
|---|---|
| Specification | `rq1-modeling-v1` ([`config/rq1_modeling_v1.yaml`](../config/rq1_modeling_v1.yaml)) |
| Run id | `20261022T144433Z_5db1608ab5d0bb69_ad666623822a` |
| Run label / status | `rq1-v1-official` / `COMPLETE` (stages `input_check`, `regression`, `classification_1s`, `diagnostics`, `handoff`) |
| Started / completed (UTC) | 2026-10-22 14:44:33 / 14:45:19 |
| Source revision | `ad666623822a4c5baa911cc29e410ed0abc9c14e` (tracked tree clean; production inventory checked) |
| Config SHA-256 | `5db1608ab5d0bb6949d3d225d84853ea65c07e8964c8b157754e48333bf45ea0` |
| Input record SHA-256 | `7a5e47a5ff7533b147b47e449e5263575df6a10a64d507dba8e99c3d32320453` ([`config/rq1_modeling_input_v1.json`](../config/rq1_modeling_input_v1.json)) |
| `output_manifest.json` SHA-256 | `e7c2ba2bb2f964d3995ba0d154fc3a581f0bf62741566c4ccce2bc9f34a9b4a3` (covers the other 36 files; also recorded in `RUN_STATUS.json`) |
| `handoff/handoff_manifest.json` SHA-256 | `a18200fbdd672f4d16e4c4c0697ef503ce9c5d2b94ba1d17b6a85ea8fc88859d` |
| Dataset archive | `expanded_release.zip`, SHA-256 `f4bc90bc74e02579ece9cbbdafb4b9b792f655d90742de143d5a5f1d60e0291a` (verified in this run) |
| Supplementary approval record | verified in this run (SHA-256 `9112e834…`; not part of the archive) |
| Environment | Python 3.11.9, scikit-learn 1.9.1, numpy 2.4.6, pandas 3.0.6, joblib 1.6.0; thread limit 1 |
| Results package | `rq1_results_review_20261022_100326_043.zip` ([asset catalogue](handoff_assets_v1.json)) |

The files below sit in the run directory. The same files are in the results
package, under its run-id folder.

| File | Contents |
|---|---|
| `report.md` | the generated report with every table, flag and null reason; figures linked from `figures/` |
| `metrics_regression.{csv,json}`, `metrics_classification.{csv,json}` | every scheme, model, fold and stratum in long format, with null reasons and flags |
| `oof_regression.csv`, `oof_classification.csv` | out-of-fold predictions (86,112 rows each) |
| `calibration_bins.csv` | 10 fixed bins per probabilistic model, scheme and slice |
| `bootstrap_summary.json`, `bootstrap_replicates.csv`, `bootstrap_indices.npz`, `bootstrap_groups.csv` | the bootstrap method, the replicate statistics, the resample indices and the ordered group list |
| `diagnostics_noise.json`, `diagnostics_threshold.json` | the repetition-noise reference; the S and B sets with their member keys and metrics |
| `fold_membership.csv` | the stored fold of every development case in each scheme |
| `input_check.json`, `run_record.json` | input verification, provenance, environment, thread limits |
| `figures/` | nine PNG figures and `figure_index.json` |
| `handoff/` | eight `.joblib` refits and `handoff_manifest.json` |
| `RUN_STATUS.json`, `output_manifest.json` | the lifecycle status and the SHA-256 of every other file |
