# RQ2 verification protocol

**RQ2 (planned).** Does an uncertainty-aware gate that decides *when* to ask an
LLM for a rewrite produce more verified runtime improvement — with fewer wasted
calls and fewer slowdowns — than never rewriting, rewriting everything, or
gating on PostgreSQL's own cost estimate or on a point prediction?

**Status:** `NOT_YET_EVALUABLE`. No LLM is integrated or called in this
milestone. The poster milestone implements and demonstrates only the
**verification and benchmarking method** on hand-written pairs labelled
`MANUAL_REFERENCE_REWRITE`.

## Full denominator (every stage is reported with counts)

```
generated → executable → verification attempted → verified equivalent → faster → recommended
```

Failures at each stage stay in the denominator: non-executable candidates,
unsafe SQL, failed preconditions, non-equivalent results, inconclusive checks,
timeouts, slowdowns and "equivalent but not faster" all count.

## Verification stages (implemented in `rewrite_verify.py` for manual pairs)

| Stage | Rule | Outcome labels |
|---|---|---|
| Parse and safety | both texts must be single read-only `SELECT`/`WITH … SELECT` statements | `PASSED_READ_ONLY_SINGLE_STATEMENT` or `FAILED: <reason>` |
| Preconditions | schema facts the written equivalence argument relies on (for example `c_custkey` is the primary key) | `PASSED` or `FAILED: <names>` → `INCONCLUSIVE` |
| Result comparison | same snapshot and configuration; results fetched through a server-side cursor up to a row cap; compared as **exact multisets** (`collections.Counter` of row tuples) — duplicates and NULLs retained; values compared with `==` | `EQUIVALENT_ON_SNAPSHOT`, `NOT_EQUIVALENT`, `INCONCLUSIVE` (row cap or timeout), `ERROR` |
| Ordering | compared only when SQL semantics require it (top-level `ORDER BY` declared in the pair); otherwise `NOT_REQUIRED` | `SATISFIED`, `VIOLATED`, `NOT_REQUIRED` |
| Paired benchmark | only after equivalence passes: one warm-up per form, then N pairs (default 5) with alternating order (A-B, B-A, …), each an `EXPLAIN (ANALYZE, BUFFERS, WAL, TIMING OFF, SUMMARY ON, SETTINGS, FORMAT JSON)` under the same timeout | `COMPLETE`, `INCOMPLETE (…)`, `SKIPPED_NOT_EQUIVALENT` |
| Speedup | per pair: original / rewrite execution time; the median over pairs | number |
| Decision | `RECOMMEND` only if the pair is a reference pair, equivalence is `EQUIVALENT_ON_SNAPSHOT`, timing is complete and median speedup ≥ threshold (default 1.10) | `RECOMMEND` / `DO_NOT_RECOMMEND` + reason |

A database `CHECK` constraint makes a `RECOMMEND` row without passing
equivalence impossible.

## What "equivalent" means here

Equal results on one or more snapshots are **evidence, not proof**. Each pair
stores a written equivalence argument and the preconditions it depends on; the
harness checks the preconditions and the results on every available snapshot
(SF 0.1, and SF 1 if loaded). The poster may say "returned identical results as
exact multisets on snapshot X"; it must not say "proven equivalent".

## Method check

A deliberately non-equivalent **negative control** (UNION vs UNION ALL) is run
with every verification session. It must come out `NOT_EQUIVALENT` and
`DO_NOT_RECOMMEND`; otherwise the harness reports `FAILED` and no rewrite result
may be used.

## Relation to measurement noise

A speedup inside the repeated-run noise band is not an improvement. The final
study sets the recommendation threshold from the measured noise floor of the
relevant runtime bin (the pilot's noise q-error is reported by the audit); the
poster milestone uses a fixed, documented 1.10 threshold.

## Planned policy comparison (RQ2 proper)

| Policy | Rule |
|---|---|
| No rewrite | always keep the original |
| Rewrite all | send every eligible query to the LLM |
| Planner gate | calibrated PostgreSQL cost above a threshold |
| Point gate | predicted median runtime above a threshold |
| Uncertainty gate | upper prediction bound above a threshold, with applicability checks |

Reported per policy: LLM calls, candidates at each denominator stage, verified
speedups, slowdowns, unchanged plans and net runtime saved after overhead.
Five anti-pattern families are held out completely (never used for training,
preprocessing, calibration or tuning) for this evaluation.

## Labels

* `MANUAL_REFERENCE_REWRITE` — hand-written reference pairs (this milestone).
* `LLM_REWRITE` — reserved in the schema; never produced in this milestone.
