# Team handoff

Status as of 2026-10-26, updated after the PR #2 review. This page records
three things:
- what is complete;
- what remains;
- who owns each remaining piece, with acceptance criteria and dependencies.

Setup instructions: [teammate_setup.md](teammate_setup.md) (Windows) and
[teammate_setup_posix.md](teammate_setup_posix.md) (macOS and Linux). Results:
[rq1_results_v1.md](rq1_results_v1.md). Assets and hashes:
[handoff_assets_v1.json](handoff_assets_v1.json). Review response:
[pr2_review_response.md](pr2_review_response.md).

## 1. Where things stand

### Complete

| Item | Done by | Evidence |
|---|---|---|
| Expanded dataset `expanded-v1`: 4,944 determined cases, collected 2026-10-04 (UTC) and audited 2026-10-21. Full validation is STOP at G-08, preserved as recorded | Shreeyash Baranwal (Data Scientist), who also did the dataset work needed to unblock the team | [expanded_collection_checkpoint.md](expanded_collection_checkpoint.md); dataset asset |
| RQ1 methodology and pinned inputs | Shreeyash Baranwal | [rq1_evaluation_protocol.md](rq1_evaluation_protocol.md), [`config/rq1_modeling_v1.yaml`](../config/rq1_modeling_v1.yaml), [`config/rq1_modeling_input_v1.json`](../config/rq1_modeling_input_v1.json) |
| **RQ1 modeling and evaluation v1.** Implemented in commits `501c937`, `dd58292`, `9c3c647` and `ad66662`; the single official run ended `COMPLETE` | Shreeyash Baranwal | [rq1_results_v1.md](rq1_results_v1.md); results asset |
| Eight hand-off refits, labelled `REFIT_ON_ALL_DEVELOPMENT_NO_EVALUATION` (never scored) | Shreeyash Baranwal | `handoff/` in the results asset |
| Reproducible verification paths: the input check, manifest verification, hand-off identity checks and reproduction comparisons, with the saved predictions, metric tables and calibration bins | Shreeyash Baranwal | [rq1_results_v1.md, section 15](rq1_results_v1.md#15-verification-and-available-evidence) |
| Handoff documentation: this page, the Windows setup guide, the results summary and the asset catalogue | Shreeyash Baranwal | committed in `c32a247b4bbd80bbf2bbd0908e7b27dcab9bb1e9`; branch pushed; PR #2 open |
| Review corrections for PR #2: the macOS/Linux guide, the cross-platform extraction helper with its tests, and the documentation clarifications | Shreeyash Baranwal | [pr2_review_response.md](pr2_review_response.md); this follow-up is **not yet committed** |

### Remaining

| Item | Status | Owner |
|---|---|---|
| Commit and push the review follow-up; merge PR #2 | the branch is pushed and PR #2 is open; the follow-up and the merge are **pending** | Shreeyash Baranwal |
| Distribute the dataset and results assets, unchanged, with their hashes | pending | Data Engineer |
| Fresh-machine acceptance of the setup guide | **not yet done on any other machine** | Data Engineer, then each teammate |
| Database readiness for further experiments | not started | Data Engineer |
| RQ2: LLM rewrite generation, semantic verification and evaluation | `NOT_YET_EVALUABLE`; not started. The 25 manual expanded-release pairs remain `NOT_YET_VERIFIED` | AI Engineer |
| Model integration, presentation of predictions, application and reporting deliverables | not started | Backend/reporting owner |
| Housekeeping: decide whether to migrate the tracked pilot data files under `reference_pilot/output/` (about 7.2 MB) out of normal Git tracking | not started; a separate change, not part of PR #2. See [pr2_review_response.md](pr2_review_response.md#h1-pilot-data-file) for the dependency checks needed first | Data Engineer, with the Data Scientist |

Outside v1, these items have no owner until someone re-scopes them as a new,
separately specified version:
- 10 s classification (`NOT_YET_EVALUABLE`, G-08);
- a random row-wise split (deferred);
- predictions for the 160 reserved RQ2 cases (not in v1);
- hyperparameter tuning;
- a model that needs only SQL text.

## 2. Publication sequence

As of 2026-10-26, steps 1 and 2 are partly done:
- the handoff documentation is committed;
- the branch is pushed;
- PR #2 is open.

The PR #2 review follow-up, the merge, distribution and acceptance remain.

1. **Commit. Done for the handoff documentation**, in `c32a247`. The review
   follow-up, described in [pr2_review_response.md](pr2_review_response.md),
   is not yet committed. Shreeyash reviews and commits it.
2. **Push and merge.** The branch `feat/expanded-query-dataset` is pushed, and
   PR #2 into `main` is open. **Not merged yet.** Shreeyash pushes the
   follow-up from PowerShell, then merges the pull request on GitHub.
   - Merge with a **merge commit**, not squash or rebase. That keeps the
     study revision `ad66662` in `main`'s history, so
     `git switch --detach ad66662…` works from any clone.
   - Optional: tag the study revision and push the tag:
     `git tag rq1-v1-study ad666623822a4c5baa911cc29e410ed0abc9c14e`, then
     `git push origin rq1-v1-study`.
3. **Distribute. Pending.** Having the code on GitHub does not mean the ZIP
   files have been uploaded. Share `expanded_release.zip` and
   `rq1_results_review_20261022_100326_043.zip` unchanged, with the sizes and
   SHA-256 values in [handoff_assets_v1.json](handoff_assets_v1.json).
   - Generated datasets, results and models stay **out of normal Git
     tracking**: no commit and no `.gitignore` change.
   - Record the chosen location in the catalogue's `publication` block in a
     follow-up documentation commit.
4. **Accept. Pending.** A teammate follows [teammate_setup.md](teammate_setup.md)
   or [teammate_setup_posix.md](teammate_setup_posix.md) on a fresh machine
   and records the
   [acceptance checklist](#fresh-machine-acceptance-checklist), for example in
   the pull request or an issue.

After these steps, both the source and the assets can be found:
- the README links every document;
- the catalogue lists every asset with its hashes and, once distributed, its
  location.

## 3. Roles, acceptance criteria and dependencies

### Data Scientist: Shreeyash Baranwal

* **Done.**
  - The dataset work that unblocked the team: collection, audit and packaging.
  - RQ1 modeling and evaluation v1, complete.
* **Remaining.**
  - Commit and push the PR #2 review follow-up, then merge PR #2 (steps 1
    and 2 of the publication sequence).
  - **Deferred, and not a merge prerequisite:** wording updates to external
    documents (the proposal, the Overview PDF and the slides). The poster is
    already completed.
    [proposal_poster_update_notes.md](proposal_poster_update_notes.md) keeps
    optional reference wording for any future revision.
  - Answer questions on interpreting the results.
  - Review any downstream use of RQ1 numbers. A comparative claim names the
    metric, the baseline and the paired interval, and no model is called a
    universal winner.
* **Not started; optional.** A future RQ1 version could add predictions for
  the RQ2 holdout (needed by the RQ2 gating experiment) or a SQL-text-only
  model. Either would be a new specification version; v1 artifacts are never
  edited.
* **Accept when:**
  - the documentation is merged;
  - the assets are published;
  - at least one fresh-machine acceptance is recorded.

### Data Engineer

Owns dataset distribution, environment and reproduction support, and database
readiness for further experiments.

| Task | Accept when | Depends on |
|---|---|---|
| **Distribution** | The hosted copies match the catalogue's size and SHA-256 exactly, checked by downloading them back. The location is recorded in `handoff_assets_v1.json`. Every teammate has access | the two files from Shreeyash |
| **Environment and reproduction support** | Part A of [teammate_setup.md](teammate_setup.md), or [teammate_setup_posix.md](teammate_setup_posix.md) on macOS or Linux, passes on a machine other than the original: **A4** shows both ZIPs `OK`; **A7** shows `PASS` with the expected counts and `ELIGIBLE`; **A8** shows `36 files verified` and the expected identities. The OS build, Python version and scikit-learn version are recorded. Optional: an A9 reproduction ends `COMPLETE` with `IDENTITY CHECK: PASS` and `COMPARISON: PASS`, or any `FAIL` output is reported, never used to replace official results | distribution; the pushed branch or merged PR |
| **Database readiness** | Part B prerequisites are met. [POSTER_RUNBOOK.md](../POSTER_RUNBOOK.md) steps 4–6c pass (`PREFLIGHT: PASS`, `START-DATABASES: PASS`, `DB-INIT: PASS` with V001–V008 applied, and SF 0.1 and SF 1 loaded and registered with the expected row counts). `.\scripts\powershell\08_expanded_release.ps1 -Step Readiness` reports `EXPANDED-READINESS: PASS`, with any R-04 or R-05 memory warning written down. The PostgreSQL server version (16.x) and the image digest are recorded. No collection is started without an explicit, recorded approval | a Docker-capable machine; independent of the other two tasks |

Two distribution cautions:
- **Personal details.** The results package's root transcript contains the
  original machine name and user name. The raw session records contain the
  host name, and four packaged reports contain absolute local paths. Raw
  records cannot be edited without breaking their checksums.
- **Licensing.** The TPC terms for redistributing TPC-H-derived query text
  have not been confirmed.

Prefer team-only access until both points are settled.

### AI Engineer

Owns the remaining RQ2 rewrite generation, semantic verification and
evaluation, under the existing
[RQ2 verification protocol](rq2_verification_protocol.md). That protocol is
unchanged by this handoff.

**Current state. RQ2 is not complete.**
* RQ2 is `NOT_YET_EVALUABLE`, and no LLM is integrated. `LLM_REWRITE` is a
  reserved label that has never been produced.
* The verification harness (`rewrite_verify.py`, command `verify-rewrites`)
  runs the stages in the protocol:
  - safety checks;
  - preconditions;
  - an exact multiset result comparison;
  - ordering checks;
  - a paired, order-alternated benchmark and the recommendation rule.

  It currently reads **only** the hand-written pairs in
  [`config/poster_smoke.yaml`](../config/poster_smoke.yaml). Its only results
  so far are the 2026-09-23 manual reference run: 4 snapshot-equivalent cases
  and a rejected negative control.
* **The 25 manual rewrite pairs of the expanded release are dataset keys, not
  verified rewrites.** Their status is `NOT_YET_VERIFIED`. Five are reserved
  for the RQ2 holdout, which holds 160 cases in 5 rewrite groups and is never
  used for training, preprocessing, calibration or tuning.

| Task | Accept when | Depends on |
|---|---|---|
| **Candidate generation** (`LLM_REWRITE`) | Every LLM call is recorded: model, version, prompt, parameters and output. Every candidate enters the denominator. No prompt is tuned on the reserved holdout groups | can start now, offline |
| **Verification** | The harness accepts candidate pairs beyond `poster_smoke.yaml`. This is new, reviewed code with unit tests on synthetic fixtures. Each pair goes through every protocol stage on every available snapshot. Every session's negative control comes out `NOT_EQUIVALENT` / `DO_NOT_RECOMMEND`. Equivalence is worded as "identical results as exact multisets on snapshot X", never "proven equivalent" | database readiness, for execution. Parsing and safety checks can start offline |
| **Evaluation** | The full denominator is reported with a count at every stage: generated → executable → verification attempted → verified equivalent → faster → recommended. Failures stay in the denominator. Results are reported separately per `rewrite_source`; manual results are never pooled with LLM results. The speedup threshold follows the protocol and is recorded before timing | verification; database readiness |
| **Manual pairs** (optional) | Any change from `NOT_YET_VERIFIED` comes only from a recorded harness run, labelled `MANUAL_REFERENCE_REWRITE` | database readiness; the harness loader |
| **Secondary gating experiment** (later) | It runs only after the primary RQ2 denominator exists. Its policies are compared on the reserved holdout only | primary RQ2 results, plus RQ1 predictions for the holdout. v1 does not provide those predictions; they need a new RQ1 version from the Data Scientist |

### Backend/reporting owner

Owns model integration, the presentation of predictions, and the application
and reporting deliverables. The API, dashboard and CI were deferred in earlier
milestones; their design is open.

| Task | Accept when | Depends on |
|---|---|---|
| **Integration** | The service loads the hand-off models only after the SHA-256 check against `handoff_manifest.json`. It builds inputs exactly as in the [contract](#model-inputoutput-contract) and returns the documented outputs and units. A **feature-parity test** passes: for at least 50 development keys covering every component, both scale factors and both configurations, features computed from the manifest SQL text and the stored plain-`EXPLAIN` plan equal `derived/features.csv`. `ln(est_total_cost)` must also match `estimates.csv` | the results asset and dataset asset (both offline; available once distributed) |
| **Live prediction** (optional) | Plain `EXPLAIN` (never `ANALYZE`) runs on a database with the same schema and statistics, and with the case's settings (`work_mem`, jit off) | database readiness |
| **Presentation** | Runtime is shown as a typical runtime with its error context. The probability is shown with the 0.5 operating point, labelled "predeclared, untuned, not a recommended production threshold". Out-of-scope inputs are flagged (see the contract). The 10 s flag is never offered | integration |
| **Reporting deliverables** | Every performance figure cites the out-of-fold results in [rq1_results_v1.md](rq1_results_v1.md) or the saved metric files. No performance number is computed from the hand-off models on development rows, because they were fitted on those rows | the results asset |

## 4. Work that can proceed in parallel

| Can start now | Starts after database readiness | Starts later |
|---|---|---|
| **Data Engineer:** distribution; fresh-machine acceptance; database readiness | **AI Engineer:** execution-based verification and paired timing; optional verification of the 25 manual pairs | **AI Engineer:** the secondary gating experiment, after primary RQ2 and new RQ1 holdout predictions |
| **Backend/reporting:** offline integration from the saved models; feature-parity test; reports from saved metrics | **Backend/reporting:** the live plain-`EXPLAIN` prediction path | **Data Scientist:** any RQ1 v2 that is specified |
| **AI Engineer:** generation design, call logging, offline parsing and safety checks, the harness loader for new pairs (with unit tests) | | |
| **Data Scientist:** publication; interpretation questions | | |

## 5. Structural SQL features versus full features

| Feature set | Columns | Computed from | Function in this repository |
|---|---|---|---|
| **Structural SQL** (class A) | the 23 `sql_*` columns | the SQL text only; no database | `query_cost_predictor.sqltext.sql_structure_features(sql)` |
| **Full** (classes A–D) | the 69 columns: the 23 `sql_*` columns, then the 44 `est_*` columns, `snap_scale_factor` and `cfg_work_mem_kb` | the SQL text, **plus a plain `EXPLAIN`** on the target database, the snapshot scale factor and the session's `work_mem` | `est_*`: `query_cost_predictor.plans.estimate_features(plan_json)`; `est_total_cost` for the baselines: `plans.estimate_scalars(plan_json)` |

The expanded release captured each plan with plain
`EXPLAIN (SETTINGS, SUMMARY ON, FORMAT JSON)`, which plans the query without
executing it. **Every saved v1 model needs plain-`EXPLAIN` output.**
- The cost baselines need `est_total_cost`, the root `Total Cost`.
- The learned models need all 69 columns.

No v1 model works from SQL text alone. Such a model would be a new, separately
evaluated version, and none of the v1 results would apply to it.

## Model input/output contract

**Source of truth:** `handoff/handoff_manifest.json` (SHA-256
`a18200fbdd672f4d16e4c4c0697ef503ce9c5d2b94ba1d17b6a85ea8fc88859d`). It is
listed in `output_manifest.json` and found in two places:
- the official run directory;
- inside the results ZIP, under
  `20261022T144433Z_5db1608ab5d0bb69_ad666623822a\handoff\`.

The models are fitted on all 4,784 development cases and labelled
`REFIT_ON_ALL_DEVELOPMENT_NO_EVALUATION`. They were never scored.

| Model | File | Object | Inputs, in order | Call | Output |
|---|---|---|---|---|---|
| `reg_cost_ols` | `handoff/reg_cost_ols.joblib` | `LinearRegression` | `ln_cost` | `predict` | ln(ms) |
| `reg_scale_cost_ols` | `handoff/reg_scale_cost_ols.joblib` | `LinearRegression` | `ln_cost`, `ln_scale` | `predict` | ln(ms) |
| `reg_ridge_all` | `handoff/reg_ridge_all.joblib` | `Pipeline` (`StandardScaler`, `Ridge`) | the 69 features | `predict` | ln(ms) |
| `reg_hgb_all` | `handoff/reg_hgb_all.joblib` | `HistGradientBoostingRegressor` | the 69 features | `predict` | ln(ms) |
| `clf_cost_logistic` | `handoff/clf_cost_logistic.joblib` | `Pipeline` (`StandardScaler`, `LogisticRegression`) | `ln_cost` | `predict_proba` | P(≥ 1 s) |
| `clf_scale_cost_logistic` | `handoff/clf_scale_cost_logistic.joblib` | `Pipeline` (`StandardScaler`, `LogisticRegression`) | `ln_cost`, `ln_scale` | `predict_proba` | P(≥ 1 s) |
| `clf_logistic_all` | `handoff/clf_logistic_all.joblib` | `Pipeline` (`StandardScaler`, `LogisticRegression`) | the 69 features | `predict_proba` | P(≥ 1 s) |
| `clf_hgb_all` | `handoff/clf_hgb_all.joblib` | `HistGradientBoostingClassifier` | the 69 features | `predict_proba` | P(≥ 1 s) |

**Inputs and ordering**
* **Array format.** Pass a 2-D `float64` NumPy array of shape (*n*, *k*), with
  columns in exactly the order of that model's `inputs.ordered_columns` in the
  manifest.
* **Column order is the only safeguard.** The models were fitted on NumPy
  arrays and store no feature names, so a wrong column order fails silently,
  with wrong predictions. Check names and order in your own code before
  converting. If you start from a pandas DataFrame, reorder it, then call
  `.to_numpy(dtype="float64")`.
* **The 69-feature order** is the order of the feature columns in
  `derived/features.csv`, and the manifest lists it in full:
  - the 23 `sql_*` columns, from `sql_length_chars` to
    `sql_aggregate_call_count`;
  - the 44 `est_*` columns, from `est_total_cost_log` to `est_n_window_agg`;
  - `snap_scale_factor`;
  - `cfg_work_mem_kb`.

**Transformations and units**
* **`ln_cost`** = ln(`est_total_cost`), the natural log of the raw root
  planner total cost. It requires `est_total_cost` > 0. It is **not**
  `log1p`, and **not** the `est_total_cost_log` feature.
* **`ln_scale`** = ln(`snap_scale_factor`), which requires a value > 0.
* **Feature units.**
  - `snap_scale_factor` is the TPC-H scale factor: 0.1 or 1.0 in the training
    data.
  - `cfg_work_mem_kb` is in kB: 4096 for `C1_baseline` and 256 for
    `C2_reduced_work_mem`.
  - The `est_*_log` features are ln(1 + estimate).
  - Counts are floats.
* **69-feature models.** Apply no scaling outside the estimator. The two
  pipelines contain their own `StandardScaler`.
* **A note on the manifest wording.** For every model, the manifest's
  `transformation` text says "a declared StandardScaler is inside the
  serialized pipeline". That is true only for the four `Pipeline` models,
  whose `declared_steps` list the scaler. The two `LinearRegression`
  baselines and the two gradient-boosting models have no scaler and need none.
  The saved models are correct; only that sentence is generic.

**Outputs**
* **Regression.** `predict` returns ln(ms). The runtime estimate is
  `exp(prediction)` in ms, with **no smearing correction**, so it is a
  typical, median-like runtime, not a mean.
  - Cross-validated error context (`fold_group`), as median / p95 q-error:
    `reg_hgb_all` 1.13 / 2.18, `reg_ridge_all` 1.36 / 3.29, `reg_cost_ols`
    1.52 / 5.17.
* **Classification.** The probability that the median execution time is at
  least 1,000 ms is `predict_proba(X)[:, list(model.classes_).index(1)]`.
  - The label mapping is positive = 1 and negative = 0.
  - The predeclared operating point is "positive when probability ≥ 0.5". It
    is untuned and not a recommended production threshold.
  - Probabilities have approximate calibration on this workload only; see
    [rq1_results_v1.md, section 11](rq1_results_v1.md#11-calibration).
* **Constant baselines (not serialized).**
  - `reg_train_median` predicts 45.6585 ms (ln 3.8212).
  - `clf_train_prevalence` predicts 0.0821 (393 / 4,784).
* **Not available in v1:**
  - a 10 s classifier;
  - serialized raw-cost rankers (they are simply `est_total_cost`);
  - predictions for the reserved RQ2 cases.

**Loading.** Load a model with `joblib.load` only after its SHA-256 matches the
manifest. Use the recorded environment: Python 3.11, scikit-learn 1.9.1 and
joblib 1.6.0, installed from `requirements/rq1_constraints_v1.txt`. The
`.joblib` files are pickles; never load one from an unverified source.

**Scope to enforce or flag**
* The training conditions were:
  - PostgreSQL 16.15 on the TPC-H schema, with the TPC-H-derived workload;
  - scale factors 0.1 and 1;
  - `work_mem` of 4 MB or 256 kB, with jit off;
  - warm cache and isolated execution;
  - one Windows laptop.

  Anything else is extrapolation.
* Predicted runtimes outside the observed 0.011–6,906.413 ms are unsupported.
* Reject non-finite features and `est_total_cost` ≤ 0.

**Sketch.** This mirrors how the derivation builds a feature row
(`smoke_derive.py`, lines 126–131). It has not been exercised as a standalone
script: prove it with the feature-parity test before relying on it.

```python
import hashlib
import json
import math
from pathlib import Path

import joblib
import numpy as np

from query_cost_predictor.plans import estimate_features
from query_cost_predictor.sqltext import sql_structure_features

run_dir = Path(r"<run directory>")
manifest = json.loads((run_dir / "handoff" / "handoff_manifest.json").read_text(encoding="utf-8"))
spec = next(m for m in manifest["models"] if m["model_id"] == "reg_hgb_all")
path = run_dir / spec["file"]
if hashlib.sha256(path.read_bytes()).hexdigest() != spec["sha256"]:
    raise SystemExit("model file does not match the manifest; do not load it")
model = joblib.load(path)


def full_feature_row(sql_text, plain_explain_json, scale_factor, work_mem_kb):
    row = {**sql_structure_features(sql_text), **estimate_features(plain_explain_json),
           "snap_scale_factor": float(scale_factor), "cfg_work_mem_kb": float(work_mem_kb)}
    return np.array([[row[c] for c in spec["inputs"]["ordered_columns"]]], dtype="float64")


x = full_feature_row(sql_text, plain_explain_json, 1.0, 4096)
typical_runtime_ms = math.exp(model.predict(x)[0])
```

## Fresh-machine acceptance checklist

Neither setup guide **has been exercised on any machine other than the
original**. The first teammate to complete one should record the outcome
below, for example in the pull request or an issue.
- Step labels refer to [teammate_setup.md](teammate_setup.md) (A2 to A9).
- On macOS or Linux, use the matching steps 2 to 10 of
  [teammate_setup_posix.md](teammate_setup_posix.md).

- [ ] Machine recorded: OS and build, processor, shell (PowerShell, bash or
      zsh), Python version. Record no user names.
- [ ] A2: the checkout contains `ad66662` (`study revision present: True`),
      and `git status --short` is empty.
- [ ] A3: Python 3.11.x, scikit-learn 1.9.1 and joblib 1.6.0 are installed;
      `pip check` is clean; `ENV-CHECK: PASS`.
- [ ] A4: both ZIPs show `OK` for size and SHA-256.
- [ ] A5: the expected file counts (34 and 38) and `derived\features.csv`
      present.
- [ ] A6: `.env` contains only `QCP_DATA_ROOT`.
- [ ] A7: `RQ1-CHECK-INPUTS: PASS` with exit code 0. The counts match:
      4,784 cases, 194 groups, 1,292 instances, 393 / 4,391, and 21 files
      verified. The output shows STOP (G-08) and `ELIGIBLE`.
- [ ] A8: `36 files verified`, the expected run identity, and
      `output_manifest.json` SHA-256
      `e7c2ba2bb2f964d3995ba0d154fc3a581f0bf62741566c4ccce2bc9f34a9b4a3`.
- [ ] A8, step 3 (optional): all eight models pass the hash check and load.
- [ ] A9 (optional): the reproduction ends `COMPLETE`. Record the identity
      check result (`PASS`, and whether the approval record was `absent` or
      verified) and the comparison result (`PASS` with the largest difference,
      or the full `FAIL` list).
- [ ] Any deviation is written down, with the exact error text.
