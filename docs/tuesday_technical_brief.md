# Tuesday technical brief

Based only on observed results: the pilot audit, the poster smoke run and the
manual rewrite verification of September 23, 2026 (TPC-H-derived data, SF 0.1,
PostgreSQL 16.15, one laptop). No new measurements were made for this brief,
which was written on 2026-10-03, before the expanded collection.

> **Update 2026-10-21.** The expanded release has since been collected
> (2026-10-04 UTC): 4,944 determined cases (4,940 complete, 4 right-censored),
> 4,784 development and 160 reserved RQ2 cases. Full validation is **STOP at
> G-08**. Runtime regression and 1 s classification are the next scoped modeling
> tasks; 10 s classification remains `NOT_YET_EVALUABLE`. Final expanded model
> scores and LLM rewrite results are pending. The cases and answers below still
> describe only the September 23 evidence; see
> [expanded_collection_checkpoint.md](expanded_collection_checkpoint.md) for the
> expanded release.

## Observed so far

| Evidence | Result |
|---|---|
| Historical pilot (recomputed) | 220 keys, 22 templates, 660 measured runs; slowest instance 284 ms (mean of runs) |
| Poster smoke | 30 keys, 14 groups, 17 templates, 90 measured executions; slowest median 102.9 ms; **0 keys >= 1 s, 0 >= 10 s**, 0 timeouts; spill in 4 keys |
| Manual rewrites | 4 snapshot-equivalent cases (2 faster, 2 slower); negative control rejected (5 vs 30 rows) |
| Pilot, template-grouped 5-fold (recomputed) | calibrated PostgreSQL-cost baseline median q-error 1.694; nonlinear model 1.794. Random split (leakage diagnostic): nonlinear model 1.053 |

## Case 1: verified, faster rewrite (non-sargable year filter)

```sql
-- original (template tpl_rp_year_extract_original_v1; SQL SHA-256 37d242c3...d8b0)
SELECT count(*) AS n_orders, sum(o_totalprice) AS total_price
FROM orders
WHERE EXTRACT(YEAR FROM o_orderdate) = 1994
-- rewrite (tpl_rp_year_extract_rewrite_v1; SQL SHA-256 8fae3361...eda9)
SELECT count(*) AS n_orders, sum(o_totalprice) AS total_price
FROM orders
WHERE o_orderdate >= make_date(1994, 1, 1)
  AND o_orderdate < make_date(1994 + 1, 1, 1)
```

* **Schema assumptions:** `o_orderdate` is `DATE NOT NULL` (precondition
  checked: PASSED); index `idx_orders_date` on `orders(o_orderdate)`.
* **Evidence IDs:** verifications `1e687a76edf648619a7ebc9087fa0fcd` (1994) and
  `3e8334d78f7842cabe827a5a4adad85b` (1996) in
  `C:\qcp_data\raw\rewrite_verification\session_20260923T201231Z_25965ab1.jsonl`
  (SHA-256 `cde27b3d...c665`); snapshot `snap_3cab8f4f...`, configuration
  `cfg_3ca59db0...` (C1). Plans come from smoke keys `mk_1c2b3188...` (original
  1994) and `mk_e0a3a8d7...` (rewrite 1994), whose SQL hashes equal the verified texts.
* **Paired timings (ms, five order-alternated pairs), 1994:** original 13.504,
  13.162, 13.992, 13.858, 14.753; rewrite 4.338, 4.176, 4.413, 4.133, 4.438.
  Median speedup **3.171x**. For 1996: **3.300x**. Both results: 1 row,
  equal as exact multisets, `RECOMMEND`.
* **Plans (smoke measured run 1):** the original is a Seq Scan on `orders` with the
  `EXTRACT` filter: 750 rows estimated, **22,958 actual**, 2,712 shared buffers,
  planner cost 4,965.76. The rewrite is a Bitmap Index Scan on `idx_orders_date`:
  23,118 rows estimated, 22,958 actual, **2,705 shared buffers**, planner cost 3,495.63.
* **Measured:** equal results on this snapshot, faster runtime, near-identical
  buffer counts, and a much better row estimate for the rewrite.
* **Hypothesis (not measured):** the gain comes from not evaluating `EXTRACT` and
  a numeric comparison for 150,000 rows, not from reading fewer pages. Its size
  at SF 1 is unknown.

## Case 2: equivalent but slower rewrite (correlated sum)

```sql
-- original (tpl_rp_correlated_sum_original_v1; SQL SHA-256 c4c491b6...4b9a81)
SELECT c.c_custkey,
       (SELECT sum(o.o_totalprice) FROM orders o WHERE o.o_custkey = c.c_custkey) AS total_spend
FROM customer c
WHERE c.c_nationkey = 3
-- rewrite (tpl_rp_correlated_sum_rewrite_v1; SQL SHA-256 ecd6aace...4c85e)
SELECT c.c_custkey, sum(o.o_totalprice) AS total_spend
FROM customer c
LEFT JOIN orders o ON o.o_custkey = c.c_custkey
WHERE c.c_nationkey = 3
GROUP BY c.c_custkey
```

* **Schema assumptions:** `c_custkey` is the primary key (precondition PASSED);
  the LEFT JOIN keeps customers without orders, with a NULL sum like the scalar
  subquery (no `COALESCE`); index `idx_orders_customer` on `orders(o_custkey)`.
* **Evidence IDs:** verifications `da06adb84bb44858a030f8716f64e407` (nation 3) and
  `11c4b5acdb4b442d92169632ca212085` (nation 11), in the same raw file. Plans
  come from smoke keys `mk_2220c600...` (original, nation 3) and `mk_e3f26de2...`
  (rewrite, nation 3).
* **Paired timings (ms), nation 3:** original 3.401, 3.534, 3.191, 3.632, 3.203;
  rewrite 8.224, 8.228, 8.685, 8.248, 8.111. Median speedup **0.414x**. For
  nation 11: **0.484x**. Results: 621 and 633 rows, equal as exact multisets,
  `DO_NOT_RECOMMEND`.
* **Plans:** the original probes `idx_orders_customer` 621 times (about 10 rows
  each), with 7,697 shared buffers and planner cost **40,744**. The rewrite
  hash-joins a full Seq Scan of `orders` (150,000 rows), with 3,005 shared buffers
  and planner cost **5,042**.
* **Measured:** the rewrite reads fewer buffers and the planner rated it about 8x
  cheaper, yet it is about 2x slower. The decision rule correctly refused it.
* **Hypothesis:** at SF 0.1, 621 cheap index probes beat hashing all orders.
  Larger scales or other nations may change this, but that is not measured.

## Answers to difficult questions

* **Planner-cost units.** PostgreSQL costs are "arbitrary units determined by the
  planner's cost parameters" (by convention one sequential page = 1.0), not
  milliseconds. Raw cost is compared by rank only (pilot Spearman 0.674,
  recomputed). For milliseconds, the baseline is the calibrated cost: a
  log-linear fit inside each training fold. Case 2 shows the raw cost ranking a
  pair the wrong way round.
* **Leakage.** Only pre-execution features (SQL text, schema and statistics,
  configuration, plain-EXPLAIN estimates) enter models. Runtime, actual rows,
  buffers and identifiers are rejected by the contract. On the pilot, the random
  split gave median q-error 1.05, but holding out whole templates gave 1.79,
  which is worse than the calibrated cost baseline (1.69). We report the grouped
  number.
* **Independent groups.** The pilot has 22 templates and the smoke run 14 groups.
  The expanded design proposed 199 semantic groups (87 coarser families); all
  199 were collected (194 development, 5 reserved for RQ2). Every result will
  state keys, groups and keys per group.
* **Missing high-runtime positives.** None in the September 23 evidence: the pilot
  maximum is 284 ms and the smoke maximum 103 ms. The 1 s and 10 s definitions
  are frozen. In the collected expanded release, 393 development cases (106
  groups, all at SF 1) are positive at 1 s and none at 10 s, so gate G-08 stops
  the 10 s classifier (`NOT_YET_EVALUABLE`). No high-runtime classifier has been
  trained or is claimed.
* **Snapshot equivalence.** Identical result multisets on one snapshot are
  evidence, not proof. Each pair carries a written argument and checked
  preconditions, and a deliberately non-equivalent control (UNION vs UNION ALL)
  was rejected.
* **Instrumented runtime versus production latency.** EXPLAIN ANALYZE "Execution
  Time" excludes parsing, planning, network transfer and result conversion. It
  was measured warm, single-client and without concurrency on a laptop, so it is
  not production latency. A client-latency comparison is planned.
* **Runtime versus financial cost.** Local PostgreSQL has no bill. In the cloud,
  cost depends on the pricing model (compute time, bytes scanned, reserved
  capacity), so a faster query does not always cost less. We report
  milliseconds, never dollars.
