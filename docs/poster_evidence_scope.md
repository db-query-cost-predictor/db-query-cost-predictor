# Poster evidence scope

This milestone produces **evidence for a poster**, not the final study. Nothing in
it has been executed by its author; every result exists only after you run
[POSTER_RUNBOOK.md](../POSTER_RUNBOOK.md).

## Four kinds of content — never mixed

| Kind | Label | What it is | Where it comes from |
|---|---|---|---|
| Existing pilot evidence | `RECOMPUTED_PILOT` | 220 TPC-H-derived instances, 660 runs, SF 0.1, collected earlier by the team | Recomputed by `pilot-audit` from `reference_pilot/output/` (read-only) |
| Fresh poster-smoke evidence | `NEW_POSTER_SMOKE` | A controlled 20–50-key collection plus a manual rewrite verification | `smoke-*` commands and `verify-rewrites` |
| Planned final-study work | `PLANNED` | ~4,000–5,000 modeling keys, ≥150 groups, full model comparison | [final_dataset_plan.md](final_dataset_plan.md), [architecture.md](architecture.md) |
| Unimplemented RQ2 work | `NOT_YET_EVALUABLE` | LLM rewrite generation and the gated-rewrite policy study | [rq2_verification_protocol.md](rq2_verification_protocol.md) |

A pilot panel and a smoke panel are never pooled into one statistic.

## In scope

* Independent audit of the pilot: counts, repetitions, duplicates, runtime
  statistics, variance decomposition, resources, diversity, noise floor,
  PostgreSQL-cost baselines and one nonlinear model under identical random
  (leakage diagnostic) and template-grouped (primary) folds; a claim check
  against every previously reported pilot value.
* PostgreSQL 16 environment: `bench-db` (measured) and `evidence-db` (evidence
  store), both on `127.0.0.1`; a restricted read-only benchmark role.
* A poster-smoke manifest of 20–50 modeling keys covering: indexed lookup,
  selective ranges, large scan/aggregation, join, grouped aggregate, sort, a
  spill candidate under reduced `work_mem`, a timeout-protected risk query and
  two manual reference-rewrite pairs (plus one negative control).
* Collection protocol: plain `EXPLAIN (SETTINGS, FORMAT JSON)`, one unmeasured
  probe, three measured `EXPLAIN (ANALYZE, BUFFERS, WAL, TIMING OFF, SUMMARY ON,
  SETTINGS, FORMAT JSON)` executions, warm cache, 15 s statement timeout
  (final-study timeout recorded separately as 60 s), timeouts kept as
  right-censored observations.
* Resumable, idempotent, append-only raw evidence; versioned, rebuildable
  labels and features; validation reports; an evidence builder and an
  unexecuted notebook.

## Out of scope for the poster milestone (deferred)

STATS-CEB, JOB/IMDb, the 4,000–5,000-key collection, calibration studies
(client latency, noise audit, bin freezing), model tuning, uncertainty
intervals, any LLM integration, API, dashboard and CI. Bash parity for the
scripts is deferred; PowerShell is primary.

## Configuration used by the smoke run

| Setting | Value |
|---|---|
| PostgreSQL | 16 (image tag from `.env`; digest captured by the start script, never typed by hand) |
| `jit` | `off` (server and session) |
| `track_io_timing` | `on` (server) |
| Parallel query | PostgreSQL defaults retained; planned and launched workers recorded |
| C1 `C1_baseline` | defaults above |
| C2 `C2_reduced_work_mem` | C1 plus `work_mem = 256kB` (poster value; the final value comes from calibration) |
| Cache protocol | warm (one unmeasured probe before measured runs) |
| Statement timeout | 15 s (poster smoke); 60 s planned for the final study |
| Scale factors | SF 0.1 for general smoke queries; SF 1 only for selected risk probes, if loaded |

## Honesty rules for the poster

* Expected mechanisms (spill, slow, timeout) are hypotheses. The validation
  report states `OBSERVED` or `OBSERVED_ABSENT`; if no key reaches 10 s, the
  poster says so.
* No 10-second classifier is reported without positive examples.
* No dollar figures: local PostgreSQL produces no bill.
* Manual reference rewrites are labelled `MANUAL_REFERENCE_REWRITE`, never LLM.
* See [poster_claim_register.md](poster_claim_register.md) for the complete list.
