# Poster Claim Register

Every statement, number, table or figure placed on the poster must appear in this
register with exactly one status. The machine-readable version used by the
evidence builder is [`config/poster_claims.yaml`](../config/poster_claims.yaml);
the rendering and linting logic is `query_cost_predictor.claims`.

**Nothing in this register is a result.** Claim texts contain placeholders such as
`{pilot.instances}`. The builder fills them only from evidence files you generate
by following [POSTER_RUNBOOK.md](../POSTER_RUNBOOK.md). A claim whose evidence is
missing is rendered as `EVIDENCE_MISSING` and must not appear on the poster.

## Status vocabulary

| Status | Meaning | Evidence required |
|---|---|---|
| `RECOMPUTED_PILOT` | A pilot value recomputed from `reference_pilot/output/` raw files by `pilot-audit` | `reports/poster/pilot_audit.json` |
| `NEW_POSTER_SMOKE` | A value from the fresh smoke collection or the manual rewrite harness | `reports/poster/smoke_validation.json` with status `PASS`; `reports/poster/rewrite_verification_summary.json` |
| `PLANNED` | Final-study design; nothing has been collected | None — must be worded as a plan |
| `NOT_YET_EVALUABLE` | Cannot be evaluated with current evidence | None — may only be stated as not yet evaluable |
| `PROHIBITED` | Must never appear | — |

Every figure additionally carries its own evidence label, key count, raw-execution
count, group count, configuration, scale factor, creation time and metric
definition (enforced by `query_cost_predictor.reporting.figures`).

## Intended claims

### `RECOMPUTED_PILOT`

| ID | Intended claim (template) | Condition for use |
|---|---|---|
| P-01 | The pilot contains `{pilot.instances}` query instances from `{pilot.templates}` TPC-H-derived templates at SF 0.1, with `{pilot.raw_runs}` measured executions. | Audit integrity checks have no `FAIL` |
| P-02 | Per-instance median runtimes span `{pilot.runtime_min_ms}`–`{pilot.runtime_max_ms}` ms; `{pilot.n_at_least_1s}` instances reach 1 s. | — |
| P-03 | Template identity accounts for `{pilot.between_template_share_ms}`% of per-instance runtime variance (raw ms). | State that this motivates grouped evaluation |
| P-04 | Repeated-run noise floor: leave-one-run-out q-error p50 `{pilot.noise_q50}`, p90 `{pilot.noise_q90}`, p95 `{pilot.noise_q95}`. | Label as measurement noise, not model error |
| P-05 | Nonlinear model median q-error: `{pilot.hgb_random_median_q}` under random 5-fold versus `{pilot.hgb_grouped_median_q}` under template-grouped 5-fold (identical folds for all models). | Random split must be called a leakage diagnostic |
| P-06 | PostgreSQL estimated total cost has Spearman ρ = `{pilot.cost_spearman}` with measured runtime. | Rank correlation only; cost is in planner units |
| P-07 | Under template-grouped folds, calibrated PostgreSQL cost reaches median q-error `{pilot.cost_grouped_median_q}` and the nonlinear model `{pilot.hgb_grouped_median_q}`. | Report keys, groups and keys per group with it |
| P-08 | `{pilot.instances_with_disk_reads}` instances read from disk, `{pilot.instances_with_spill}` spilled to temporary files and `{pilot.instances_with_parallel_workers}` launched parallel workers. | — |
| P-09 | `{pilot.distinct_sql}` distinct SQL texts across `{pilot.instances}` instances. | Name the templates with duplicates |
| P-10 | The audit reproduced `{pilot.claims_match}` previously reported pilot values; `{pilot.claims_mismatch}` did not match. | Show mismatches; never hide them |

### `NEW_POSTER_SMOKE`

| ID | Intended claim (template) | Condition for use |
|---|---|---|
| S-01 | The smoke run labelled `{smoke.keys_labeled}` modeling keys (`{smoke.keys_complete}` complete, `{smoke.keys_censored}` right-censored, `{smoke.keys_failed}` failed) from `{smoke.measured_executions}` measured executions across `{smoke.groups}` semantic groups. | Smoke validation `PASS` |
| S-02 | Observed smoke runtimes span `{smoke.runtime_min_ms}`–`{smoke.runtime_max_ms}` ms; `{smoke.n_at_least_10s}` keys reached 10 s and `{smoke.keys_censored}` hit the 15-second timeout. | If both counts are 0, say so explicitly |
| S-03 | `{smoke.keys_with_spill}` keys wrote temporary files (spill); `{smoke.keys_with_spill_c2}` of them ran under reduced `work_mem`. | Only as observed; never "guaranteed" |
| S-04 | Parallel workers: `{smoke.runs_workers_mismatch}` measured runs launched fewer workers than planned. | — |
| S-05 | Manual reference rewrite `{rewrite.pair_id}`: results equal as exact multisets on snapshot `{rewrite.snapshot}`; median paired speedup `{rewrite.median_speedup}`×; recommendation `{rewrite.recommendation}`. | Label `MANUAL_REFERENCE_REWRITE` |
| S-06 | The negative-control pair (UNION vs UNION ALL) was rejected as not equivalent. | Only if observed |

### `PLANNED`

| ID | Intended claim | Wording rule |
|---|---|---|
| F-01 | The final study plans approximately 4,704 modeling keys (about 4,544 development and about 160 fully held-out rewrite/evaluation keys) across at least 150 structural/semantic groups. | "planned", "approximately"; never "collected" |
| F-02 | Planned models: global median, raw and calibrated PostgreSQL cost, Elastic Net, XGBoost or CatBoost regression and high-runtime classification, timeout-aware XGBoost AFT; QueryFormer-style plan-tree models only as a data-gated challenger. | Plan only |
| F-03 | Primary evaluation holds out unseen structural/semantic groups; random splits are only a leakage diagnostic. | Plan only |
| F-04 | High-runtime classification will be judged by PR-AUC, recall and false-negative rate first; precision, warnings per 100 queries, Brier score and calibration second; ROC-AUC secondary; never raw accuracy. | Plan only |
| F-05 | RQ2 will report the full denominator: generated → executable → verification attempted → verified equivalent → faster → recommended. | Plan only |
| F-06 | The final study uses a 60-second statement timeout (the smoke run uses 15 seconds). | — |

### `NOT_YET_EVALUABLE`

| ID | Statement allowed on the poster |
|---|---|
| N-01 | A ≥10 s high-runtime classifier is not yet evaluable: the pilot has no positive examples and the smoke run is too small. |
| N-02 | A ≥1 s classifier is not evaluable on the pilot (no instance reaches 1 s). |
| N-03 | Generalization to unseen structures at the planned scale is not yet evaluable. |
| N-04 | Prediction-interval coverage and calibration are not yet evaluable. |
| N-05 | RQ2 (whether gated LLM rewrites help) is not yet evaluable; no LLM has been run. |
| N-06 | Cross-workload (STATS-CEB, JOB), cross-scale and cross-configuration generalization are not yet evaluable. |
| N-07 | Monetary impact is not evaluable: no pricing model is documented. |

## `PROHIBITED` claims

The builder's linter rejects generated text matching these rules, and they must
not be written by hand either.

| ID | Prohibited | Why |
|---|---|---|
| X-01 | Describing R² as "accuracy" | R² is a variance-explained statistic, secondary here |
| X-02 | Presenting random-split results as performance on unseen queries or structures | Random splits leak template identity (the pilot's 99%+ between-template variance) |
| X-03 | Claiming financial or dollar savings, or "cost" in currency, without a documented pricing model | Local PostgreSQL produces no bill |
| X-04 | Presenting deterministic or manual rewrites as LLM results | The harness uses `MANUAL_REFERENCE_REWRITE` only |
| X-05 | Presenting planned dataset counts (4,704 / 4,544 / 160 / 150 groups) as collected observations | Nothing beyond the smoke run is collected |
| X-06 | Training or reporting a 10-second classifier when the dataset has no 10-second-positive examples | The metric is undefined or degenerate |
| X-07 | Calling the point/range probes an "OLTP benchmark" | They are single-query probes, not a transactional workload |
| X-08 | Claiming proven semantic equivalence from equal results on one snapshot | Sampling cannot prove equivalence; state the snapshot and the written argument |
| X-09 | Calling any number a "TPC-H benchmark result" | Results are derived from TPC-H data and queries, not audited |
| X-10 | Presenting the Open Issues XGBoost numbers (R² 0.956 / 0.390) as recomputed | The audit uses a different model; those values are `NOT_COMPARABLE` |
| X-11 | Presenting instrumented `EXPLAIN ANALYZE` server time as production or end-to-end latency | Warm cache, single host, instrumentation overhead |
| X-12 | Saying a query is "guaranteed slow" or "always spills" | Only the collected evidence decides |

## Wording rules

* Say "median q-error under template-grouped 5-fold cross-validation (N keys,
  G groups)", never an unqualified "accuracy".
* Put the evidence label next to every number: `RECOMPUTED_PILOT` or
  `NEW_POSTER_SMOKE`.
* Report keys, groups and keys per group next to every evaluation result.
* If an expected mechanism was not observed (no spill, no key ≥ 10 s), say so.
