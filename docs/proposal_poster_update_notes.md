# Proposal, overview and poster: optional reference wording

This file collects **optional reference wording** for project documents
**outside this repository**: the proposal, the Overview PDF, the poster and any
slides. It would bring them in line with the RQ1 v1 study if they are ever
revised.

> **Deferred; not a merge prerequisite.** As decided on 2026-10-26, updates to
> external documents are deferred and are **not prerequisites for merging
> PR #2**.
> - The poster is already completed.
> - This patch edits none of the poster, the proposal, the Overview PDF or the
>   slides.
> - Nothing here is required to complete or merge PR #2.

Prepared for the PR #2 review follow-up, 2026-10-26. The repository's own
documentation is already consistent with the study; see the list of
repository clarifications below.

## What was inspected

| Document | Where | Status |
|---|---|---|
| Research and Implementation Plan (`.docx`) | `reference_docs/`, untracked | text read only; consistent on planner cost (details below); not edited |
| Forward Plan, Data Engineer and Data Scientist (`.docx`) | `reference_docs/`, untracked | text read only; consistent on planner cost; not edited |
| Open Issues Before Supervisor Review (`.docx`) | `reference_docs/`, untracked | text read only; no planner-cost wording found; not edited |
| Project Overview (`.pdf`) | `reference_docs/`, untracked | read; contains wording that differs from the study; not edited. Optional replacement text is below, for any future revision |
| Poster | not in this repository or in `reference_docs/` | already completed; out of scope for PR #2; not inspected or edited |

No editable, non-frozen file *in this repository* said that planner cost is
never a feature. The repository clarifications were made in
[rq1_results_v1.md](rq1_results_v1.md) (sections 3, 5, 6, 7 and 15) and in
[README.md](../README.md).

The two consistent plans say:
- **Research Plan.** "Corrected meaning of cost" lists PostgreSQL total cost as
  available before execution, in the role "Baseline feature and baseline
  predictor". Its availability contract lists "Estimated cost, estimated rows,
  estimated width" as allowed model inputs.
- **Forward Plan.** "Keep the optimizer's own estimates as features rather than
  discarding them."

One caution: the Research Plan's availability-class *letters* differ from the
repository's `config/feature_registry.yaml`.
- **Plan:** A request time; B plain plan; C catalog; D execution; E future.
- **Repository:** A SQL structure; B schema/statistics; C configuration;
  D plain `EXPLAIN`; E execution-only.

When quoting a class letter, name the source.

## 1. Planner cost is a feature, not something the models replace

### Overview PDF, section 02, "What We Are Building"

The current text reads:

> Instead of relying on the database's built-in, assumption-heavy cost
> estimate, the system learns directly from real, measured query behavior:
> actual runtimes, actual resource usage, and actual dollar cost, collected by
> running a large and varied set of queries and recording what really
> happened.

Replace it with:

> The system does not discard the database's own cost estimate. It uses
> PostgreSQL's estimated plan costs, which a plain `EXPLAIN` provides before
> the query runs, together with other pre-execution information: SQL
> structure, the estimated plan's operators and row estimates, the data scale
> and the memory setting. From measured execution times, it learns how this
> information relates to the query's actual runtime. Measured outcomes (actual
> runtimes, row counts and buffer use) serve only as training labels and for
> evaluation, never as prediction inputs. The current study predicts isolated
> server execution time; resource usage and dollar cost are not modeled.

### Overview PDF, section 02, the "Data collection" bullet

The current text reads:

> …recording their true execution time, resource usage, and internal cost
> breakdown (via EXPLAIN ANALYZE) for each one.

Replace it with:

> …capturing each query's estimated plan with a plain `EXPLAIN` before it
> runs, which is the source of the prediction features, and then measuring its
> execution time with `EXPLAIN ANALYZE` (timing per node off), which provides
> the labels only.

### Overview PDF, section 02, the "Cost prediction model" bullet

The current text reads:

> A regression model (such as XGBoost or LightGBM) trained on structural
> features pulled from each query's execution plan…

Replace it with:

> Regression models trained on features available before execution:
> SQL-structure counts and the estimated (plain `EXPLAIN`) plan, including
> PostgreSQL's own estimated costs and row counts, plus the data scale and
> memory setting. The evaluated study compares ridge regression and histogram
> gradient boosting (scikit-learn) with calibrated planner-cost baselines.

### Overview PDF, section 04, Data Scientist, first bullet

The current text reads:

> Extract structural features from query execution plans…

Replace it with:

> Extract features from the SQL text and from the estimated (plain `EXPLAIN`)
> plan, including the planner's cost and row estimates; actual-execution
> values are labels only…

### Poster and slides: a one-sentence version, if needed

> Learned models combine PostgreSQL's pre-execution cost estimates with other
> pre-execution features; we test whether this improves on calibrated
> cost-only baselines, not whether planner information can be done without.

## 2. Pilot versus expanded results

Use this wherever a pilot result appears next to the RQ1 v1 result:

> An earlier pilot (22 TPC-H templates, template-grouped 5-fold evaluation)
> found the opposite direction: the calibrated planner-cost baseline reached a
> median q-error of 1.694, against 1.794 for gradient boosting. In the expanded
> RQ1 v1 study (194 semantic groups, primary group-held-out folds), the
> calibrated cost baseline reached 1.516, ridge 1.358 and gradient boosting
> 1.126. The two are not a controlled before/after comparison. They differ in:
> - workload (pilot templates only, against TPC-H-derived, generated
>   structural, probe and rewrite queries);
> - data scale (SF 0.1, against SF 0.1 and SF 1, with two memory settings);
> - feature evidence (estimate view of `EXPLAIN ANALYZE` plans, against a plain
>   `EXPLAIN` captured before execution);
> - evaluation population (template groups, against semantic groups).

The pilot figures are as recorded in
[rq1_evaluation_protocol.md](rq1_evaluation_protocol.md). They were not
recomputed in this follow-up.

## 3. Classification scope, calibration and the metric floor

Add these as footnotes or captions wherever 1 s classification results are
shown:

> All 393 development queries at or above 1 s are at SF 1, and SF 0.1 has none.
> Slow-query detection at SF 0.1 is therefore not established. Scale factor is
> a pre-execution input feature; this limits generalization but is not, by
> itself, evidence of leakage. The 160 RQ2 holdout cases are excluded from
> RQ1.

> High average precision reflects ranking. The predicted probabilities are not
> reliable risk estimates. For gradient boosting, the top probability bin
> (315 cases, mean prediction 0.993) is 94.3 % positive, and the lowest bin
> (4,361 cases, mean 0.0009) still holds 23 positives. At the predeclared 0.5
> threshold, 27 of its 29 false alarms are queries whose median runtime is
> 800–1,250 ms.

Add this if a cost-baseline log-MAE appears:

> Log-MAE and q-error floor times at 0.001 ms, as declared. For the cost
> baseline, 16 of 4,784 predictions fall below the floor; its unfloored
> log-space MAE is 0.6075, against the reported 0.6027. Learned models are
> unaffected.

## 4. Verification wording

Earlier materials described the results as "independently reviewed in
ChatGPT". If a future revision of a slide or document still says so, this
wording can replace it:

> The results can be checked with the repository's documented verification:
> SHA-256 checks of the dataset and results packages, the pinned-input check,
> the output-manifest check of the saved run, and a reproduction comparison.
> These confirm identity and integrity; they are not, by themselves, evidence
> of model quality.

## Optional future edits (deferred)

None of these is required for PR #2. Each one is optional, if the document is
ever revised:
- **Overview PDF:** apply the four replacements in section 1, regenerating the
  PDF from its source document rather than hand-editing it.
- **Research Plan:** add a note that its availability-class letters differ
  from the repository registry.
- **Slides or a future poster revision:** reuse sections 1 to 4 where they
  apply. The current poster is complete and was not inspected for this patch.
