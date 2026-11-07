# Verification and timing protocol

The final default is in src/rq2_pipeline/pipeline.json. The pinned prompt is rq2-generation-v2, template SHA-256 466f15745ba03ee5255dc4737be2dc0136db808b073bb4ca2df527444e906a11. Model: openai/gpt-oss-120b served by Groq; temperature 0.2, low reasoning and a 2,048-token completion cap. One attempt per supported original, without feedback or retries. Returning unchanged SQL is permitted.

Before generation, validate the registered original template, database readiness and environment. Reject the UNION/UNION ALL negative control in each of the four environments. Generate with SF0.1/4MB plain-plan context, then reuse the same candidate across all four cases.

1. Require single read-only SQL and declared schema preconditions.
2. Compare output names/order and PostgreSQL type OIDs, exact row multisets, duplicates and NULLs in a read-only repeatable-read snapshot. A row-cap breach or timeout is inconclusive. Numeric comparison preserves numeric values; type OIDs do not distinguish numeric display scale/typmod.
3. Execute the parameterized rq2-fixture-registry-v2 fixtures. Their PK/FK/NOT NULL assertions and original-self comparisons must pass. A schema-valid counterexample rejects the candidate. Fixtures target empty inputs, missing matches, boundaries, multiplicities and family-specific hazards. Unsupported cases require review.
4. With two warm-ups per form: original warm-ups, eleven A/A pairs, candidate warm-ups, then eleven A/B pairs. Pair indices are zero-based: even pairs (0, 2, ..., 10; six pairs) execute A then B; odd pairs (1, 3, ..., 9; five pairs) execute B then A. A is original and B is rewrite; both A/A forms use the original SQL.
5. Block timing acceptance when A/A has at least nine strict >10% effects in either direction. The primary A/B rule requires at least nine of eleven pairs satisfying 100 * B_us < 90 * A_us. Exact equality at the boundary is not a win. Incomplete measurements remain inconclusive, with no replacement pairs.

All four environments must meet the primary rule for a query-level recommendation: SF0.1/SF1 by work_mem 4MB/256kB; JIT off, fixed data/settings and a 15-second statement limit. Stored times are positive integer microseconds derived from instrumented EXPLAIN ANALYZE total Execution Time with TIMING OFF. Displayed per-form medians are not the decision rule.

The supplementary 0/5/10/15/20% profile uses the same fixed 10% A/A gate. The predeclared pair-0 diagnostic flags primary passes with exactly nine wins including pair 0. Only its use to withhold the two historical recommendations, giving the 6/15 sensitivity scenario, is post hoc; it does not change labels or the primary rule.

These are practical experimental rules, not a proof of equivalence, independent samples or a guarantee of production performance. The historical families and schema had already been inspected. Prompt effects and generalization to unseen schemas/families were not evaluated.
