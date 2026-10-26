# Query workload evidence register

Purpose: record why each expensive-query family in the expanded release exists,
and how strong the evidence behind it is. Reviewed 2026-10-03. Sources were read
on the web or from local project files; nothing was executed for this register.

## Evidence levels

| Level | Meaning |
|---|---|
| **A** | Published, measured **production** case with sufficiently described SQL and timing or resource evidence |
| **B** | Production-motivated pattern without reusable SQL or timings (production telemetry without SQL text, or anti-patterns found in real code but timed on synthetic data) |
| **C** | Constructed benchmark case measured on our PostgreSQL environment |

Rules applied:

* TPC-H, TPC-H-derived queries, a blog suggestion or a query that is slow on our
  laptop are never presented as a production incident.
* Runtime is not cost. Local PostgreSQL produces no bill, and cloud billing
  depends on the pricing model (compute time, bytes scanned, provisioned
  capacity). No runtime is converted to dollars.
* Positives are never manufactured. The release excludes `pg_sleep` (also
  blocked by the safety validator), Cartesian products and unbounded statements;
  every statement is bounded by the statement timeout and `temp_file_limit`.

## Summary finding

**No level A source was found.** Every production study reviewed either
publishes statistics without SQL text (Redset, Snowset) or publishes nothing
(LinkedIn/Trino, Redshift Stage). The studies that do publish real code
(SQLCheck, the Rails study) timed it on synthetic data. Production literature
is therefore used only to **motivate** benchmark families (level B), and the
families are measured on our own snapshots (level C). The expanded dataset is a
constructed benchmark dataset, not a production workload.

## Sources reviewed

| ID | Source (date) | Engine | Production workload? | Exact SQL public? | Timings public? | Schema / data and reuse | What it supports | Limits |
|---|---|---|---|---|---|---|---|---|
| S1 | PostgreSQL 16 docs, *Using EXPLAIN*, <https://www.postgresql.org/docs/16/using-explain.html> (v16, read 2026-10-03) | PostgreSQL 16 | no (vendor documentation) | examples only | examples only | PostgreSQL License | costs are "arbitrary units determined by the planner's cost parameters" (seq_page_cost = 1 by convention); Execution Time excludes parsing, rewriting and planning; EXPLAIN ANALYZE omits network transmission and adds measurement overhead; per-loop averaging | not a workload |
| S2 | PostgreSQL 16 docs, *pg_stat_statements*, <https://www.postgresql.org/docs/16/pgstatstatements.html> | PostgreSQL 16 | no | no | no | PostgreSQL License | how production statements are tracked: planning and execution statistics, constants normalized (`queryid`), temp blocks, `shared_preload_libraries` needed | a method, not a dataset; not enabled in this project |
| S3 | PostgreSQL 16 docs, *Resource Consumption*, <https://www.postgresql.org/docs/16/runtime-config-resource.html> | PostgreSQL 16 | no | no | no | PostgreSQL License | `work_mem` default 4 MB; sorts, hash joins, hash aggregation and memoize write temporary files beyond it; `hash_mem_multiplier` default 2.0; `temp_file_limit` cancels the transaction; `shared_buffers` default 128 MB | mechanism only |
| S4 | PostgreSQL 16 docs, *Client Connection Defaults*, <https://www.postgresql.org/docs/16/runtime-config-client.html> | PostgreSQL 16 | no | no | no | PostgreSQL License | `statement_timeout` runs "from the time a command arrives at the server until it is completed" (basis of the censoring audit) | mechanism only |
| S5 | PostgreSQL 16 docs, *Index Types*, <https://www.postgresql.org/docs/16/indexes-types.html> | PostgreSQL 16 | no | no | no | PostgreSQL License | B-tree serves `LIKE` only for constant patterns anchored at the start ("not col LIKE '%bar'") | mechanism only |
| S6 | PostgreSQL 16 docs, *Indexes on Expressions*, <https://www.postgresql.org/docs/16/indexes-expressional.html> | PostgreSQL 16 | no | no | no | PostgreSQL License | a predicate on a function of a column needs an index on that expression | mechanism only |
| S7 | Song, Bouguerra, Krogen, Abadi, *Evaluating Learned Query Performance Prediction Models at LinkedIn*, arXiv:2504.17181, DOI 10.48550/arXiv.2504.17181 (v1 2025-04-24) | Trino | **yes**: one week of LinkedIn logs (359,870 training and 35,050 test queries) | no | no (no release found) | internal; arXiv licence covers the paper only | production latency is skewed toward short queries with a tail from seconds to hours; CPU time is more predictable than latency in a multi-tenant system | level B; not reproducible; distributed engine, not PostgreSQL |
| S8 | van Renen et al., *Why TPC Is Not Enough: An Analysis of the Amazon Redshift Fleet*, PVLDB 17 (2024) p. 3694, DOI 10.14778/3681954.3682031; Redset, <https://github.com/amazon-science/redset> | Amazon Redshift | **yes**: 200 provisioned and 200 serverless clusters, three months | **no**: a `feature_fingerprint` hash replaces the text | yes, as statistics (`execution_duration_ms`, `compile_duration_ms`, `queue_duration_ms`, `num_joins`, `num_scans`, `num_aggregations`, `mbytes_scanned`, `mbytes_spilled`) | Redset under CC BY-NC 4.0 | runtime p50 0.1 s, p90 1.8 s, p99 26.1 s, p99.9 203 s; queries over 1 h are under 0.1 % but use 25 % of resources; in 50 % of clusters 80 % of queries repeat exactly; function-call scans more frequent than in TPC-H/DS | level B; no SQL, no schema, no data; cloud engine with writes and ETL |
| S9 | Vuppalapati et al., *Building An Elastic Query Engine on Disaggregated Storage*, NSDI 2020; Snowset, <https://github.com/resource-disaggregation/snowset> | Snowflake | **yes**: about 70 million queries, 2018-02-21 to 2018-03-07 | not listed (field list not verified in this review) | yes, as statistics (timing, I/O, resources) | "open to the public for use in research and education purposes"; repository lists Apache-2.0 | production query statistics exist publicly | level B; field list and absence of SQL not verified here |
| S10 | Wu et al., *Stage: Query Execution Time Prediction in Amazon Redshift*, SIGMOD-Companion 2024, arXiv:2403.02286 | Amazon Redshift | yes (production predictor) | no release found | no release found | - | production systems use execution-time prediction for admission, scheduling and resource control; cache + local + global models | level B; nothing reusable |
| S11 | Dintyala, Narechania, Arulraj, *SQLCheck: Automated Detection and Diagnosis of SQL Anti-Patterns*, SIGMOD 2020, arXiv:2004.10232, DOI 10.1145/3318464.3389754 | PostgreSQL 11.2 (timings) | anti-patterns counted in 1,406 GitHub repositories (about 174k SQL statements), 15 Django applications and 31 Kaggle databases | per-repository code, not curated | yes, but on a **synthetic** 10 M-record (19 GB) Globaleaks database | GitHub licences vary | index underuse, pattern matching, unnecessary work are common anti-patterns; fixing "index underuse" made a low-cardinality scan about 3x **slower** | level B; production-motivated, synthetic timings |
| S12 | Yang, Subramaniam, Lu, Yan, Cheung, *How not to structure your database-backed web applications*, ICSE 2018, DOI 10.1145/3180155.3180194 | Rails/ORM (MySQL/PostgreSQL) | 12 open-source applications (bug trackers) | application code only | yes, on **synthetic** data (200 / 2,000 / 20,000 records) | open-source licences | 64 fixed issues: median speed-up 2x, maximum 39x; N+1 access, missing indexes, unnecessary computation | level B; synthetic data |
| S13 | Leis et al., *How Good Are Query Optimizers, Really?*, PVLDB 9(3) 2015 | PostgreSQL and others | **no**: 113 hand-written JOB queries on real IMDb data | yes (JOB) | yes (paper) | IMDb data licence restrictions | cardinality errors grow with joins; PostgreSQL's nested-loop choices under underestimation caused very slow queries and timeouts | benchmark, not production; not used as data here |
| S14 | This project, poster smoke and rewrite verification, 2026-09-23 (`reports/poster/smoke_validation.json`, `rewrite_verification_summary.json`, raw JSONL under `C:\qcp_data`) | PostgreSQL 16.15 | no (TPC-H SF 0.1) | yes (manifest) | yes | TPC-H-derived data | measured local mechanisms (below) | level C; laptop, SF 0.1 only, no key reached 1 s |

## Candidate patterns

| Pattern | Level | Sources | Operational mechanism (as stated by the source) | PostgreSQL adaptation | Project family | Limitations |
|---|---|---|---|---|---|---|
| Expensive long tail: large scans and aggregations | B | S8, S7 | a small share of long queries consumes a large share of resources | wide (about 47 %) date ranges over lineitem at SF 1; TPC-H Q1, Q18 | `structural` regime `R3_wide`; `tpch_derived` q01, q18 | production tails include ETL and writes; this release is read-only, at most SF 1, and bounded at 15 s |
| Non-sargable predicates (function, cast, arithmetic, coalesce on an indexed column) | B, and C locally | S11, S8 (function-call scans), S6 | the predicate cannot use the column's index | `EXTRACT`, `date_trunc`, casts, numeric literal on an integer column, date arithmetic, `coalesce` | `rewrite` rw01-rw06, rw24 | local evidence is SF 0.1 only (case 1 of the brief); the measured gain may be CPU per row rather than I/O |
| Leading-wildcard `LIKE` | B | S11, S8 (string-matching scans), S5 | B-tree cannot serve `'%x%'` | `p_name LIKE '%color%'`, `NOT LIKE` comment filters | `tpch_derived` q09, q13, q16 | no trigram index in the physical design; no equivalent rewrite exists, so no pair |
| Correlated subqueries evaluated per outer row | B, and C locally | S12 (N+1 access), S1 (loops) | the subplan runs once per outer row | correlated SUM/COUNT/MAX with an indexed correlation (o_custkey, l_partkey) or a non-leading one (l_suppkey) | `rewrite` rw08-rw10, rw17, rw19; `tpch_derived` q02, q17, q20 | direction depends on indexes and outer size: locally the correlated form was **faster** (case 2 of the brief) |
| Multi-way joins (misestimation risk) | benchmark (S13), B weakly (S8 join counts) | S13, S8 | cardinality errors grow with joins and drive join choice | FK joins of up to six tables, including cyclic customer-supplier-nation joins | `structural` p06-p16; `tpch_derived` q05, q07, q08, q09, q21 | TPC-H data is uniform and independent, so it under-represents correlation-driven misestimates (S13) |
| Wide sorts and large hash aggregation above `work_mem` | B, and C locally | S3, S8 (`mbytes_spilled`) | sorts and hash tables spill to temporary files | full sorts by `l_comment`, high-cardinality GROUP BY, DISTINCT, COUNT(DISTINCT), window ranks; C1 (4 MB) versus C2 (256 kB) | `structural` shapes s3, s4, s5, s7, s8 | `work_mem` values are our choice, not observed production settings; locally 4 smoke keys spilled, none reached 1 s |
| NOT IN versus NOT EXISTS | C only after measurement | planner behaviour (hashed versus plain SubPlan), not verified against a document in this review | - | `NOT IN (subquery)` under C1 and C2 | `rewrite` rw12 (RQ2 holdout) | mechanism stated as a hypothesis |
| Unnecessary work (DISTINCT over a join, UNION, redundant ORDER BY, redundant joins) | B | S11, S12 | extra deduplication, sorting or joining | DISTINCT to EXISTS, UNION to UNION ALL on disjoint ranges, redundant ORDER BY, inner and left join elimination | `rewrite` rw13, rw15, rw16, rw20-rw22 | PostgreSQL already removes some of these (left-join removal, HAVING push-down), so "equivalent, no change" outcomes are expected and kept |
| Short-query majority (fast regime) | B | S8, S7 | most production queries are short | primary-key, foreign-key and one-day lookups, IN-lists, LIMIT early stops | `probe` (24 groups) | single-query probes, not an OLTP benchmark |
| Repeated identical queries | B | S8 | exact repetition invites caching | not a family: repetitions are measured runs of one key | evaluation note: the seen-template diagnostic resembles repeated templates and is never reported as unseen-structure performance | - |

## Local measured cases (level C, SF 0.1, 2026-09-23)

| Case | Measured | Mechanism status |
|---|---|---|
| Non-sargable year filter (`rp_nonsargable_year`) | original 13.0-13.5 ms, rewrite 4.1-5.1 ms (smoke medians); paired median speedups 3.17x and 3.30x | measured: original is a sequential scan with an `EXTRACT` filter (750 rows estimated, 22,958 actual); rewrite is a Bitmap Index Scan on `idx_orders_date` reading about the same buffers (2,705 vs 2,712). Hypothesis: the gain is per-row expression cost, not I/O |
| Correlated sum (`rp_correlated_sum`) | original 3.5-3.7 ms, join rewrite 8.5-8.8 ms; paired median speedups 0.414x and 0.484x | measured: original runs 621 index probes on `idx_orders_customer`; rewrite hash-joins a full scan of orders (150,000 rows). Planner total cost ranked the original about 8x more expensive (40,744 vs 5,042 planner units) |
| Repeated correlated scan (`correlated_repeated_scan`, nation 7) | 38.6 ms median | measured: 50 Bitmap Index Scans of `idx_lineitem_part_supp` with an index condition on its non-leading column; planner total cost 472,373 units. Hypothesis: at SF 1 (about 400 suppliers per nation, 10x larger index) this pattern may exceed 1 s; only collection decides |
