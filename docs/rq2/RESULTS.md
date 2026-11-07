# Final RQ2 results

Derived from TPC-H; these are not official or comparable TPC-H benchmark results.

One fresh `openai/gpt-oss-120b` output, served by Groq, was generated per original for Q06–Q20 (15 outputs). Each candidate was checked under SF0.1 and SF1 at work_mem 4MB and 256kB (60 cases). All five SQL families had already been inspected during development; these are new instances within seen families.

Read-only validation, preconditions, snapshot agreement and schema-valid fixtures precede timing. There are two warm-ups per form: two original-query warm-ups, eleven A/A pairs, two rewritten-query warm-ups, then eleven A/B pairs, with alternating order within each paired phase. Pair indices are zero-based: 0, 2, ..., 10 run A first (six pairs), and 1, 3, ..., 9 run B first (five pairs). The primary rule requires a strict runtime reduction above 10% in at least nine of eleven pairs: 100 × B < 90 × A. A/A blocks the case if such effects occur in either direction in at least nine of eleven pairs. Every environment must pass for a query-level recommendation.

## Outcomes, retaining failures

- 8/15 rewrites met the primary rule in all four environments.
- Case labels: 39 MEETS_PRIMARY_CRITERION, 8 CONSISTENTLY_FASTER, 1 CONSISTENTLY_SLOWER, 1 NO_CLEAR_DIFFERENCE, 8 FIXTURE_REJECTED, 3 INCONCLUSIVE. Total: 60.
- Funnel: 15/15 generations and valid responses (query units); 60/60 safety and preconditions, 57/60 snapshot passes, 49/60 fixture passes, 49/60 A/A passes and completed timings (case units).
- Q12 and Q17 agreed on current snapshots but changed NULL to zero in schema-valid missing-match fixtures: two outputs and eight cases. Snapshot checks would have classified these as matching on tested data; fixtures rejected them before timing. We did not time them and do not claim the full pipeline would otherwise have recommended them.
- The other thirteen outputs showed no counterexample in completed checks. Three had incomplete snapshot evidence because the original hit the declared 15-second limit at SF1/256kB. No imputed timing is assigned.

| Family | All-four queries | Primary cases | Timed cases | Conditional paired magnitude: median (min–max), except Q07 |
| --- | ---: | ---: | ---: | --- |
| Date arithmetic | 3/3 | 12/12 | 12/12 | 19.985x (15.411–25.463x) |
| Correlated aggregate | 1/3 | 4/12 | 4/12 | One rewrite (Q07): SF0.1: 1.292x / 1.336x; SF1: 8.086x / 8.096x |
| NOT IN subquery | 0/3 | 4/12 | 9/12 | 1.063x (0.836–1001.011x); upper endpoint: the single Q18/SF0.1/256kB plan-cliff case |
| Join elimination (original INNER JOIN) | 3/3 | 12/12 | 12/12 | 1.705x (1.278–1.988x) |
| Materialized CTE | 1/3 | 7/12 | 12/12 | 1.262x (1.067–1.884x) |

Magnitudes use medians of individual paired A/B ratios, then summarize completed case medians. They are conditional on successful correctness-gated timing, not all planned cases. Min–max is descriptive, not a confidence interval. For Q07 the two values per scale are ordered 4MB then 256kB; all four observations describe one rewrite. No pooled 4.711x typical-case claim or overall arithmetic-mean speedup is used.

## Diagnostics and limitations

The predeclared pair-0 diagnostic identified Q14 and Q15 at SF1/256kB: each had exactly nine primary wins, including pair 0. Conservatively withholding both query recommendations gives 6/15 in a post-hoc sensitivity scenario; the declared primary count and labels remain 8/15. Q14 also had two reverse >10% pairs.

Across 539 identical-query A/A pairs, B was strictly faster in 288 (53.43%), A in 251, with no ties. This is descriptive only, without a statistical test or an independence assumption. The supplementary 0% row still uses a 10% A/A gate. At 10%, B/A directional counts were 85/65; the largest per-case directional count was six, below the gate of nine.

If slot B had a systematic advantage, it would favour rewrites because B holds the rewrite in A/B; the observed count asymmetry is small, but such an advantage would matter most in narrow-margin cases such as Q14 and Q15.

The supplementary 0/5/10/15/20% profile yielded 47/42/39/36/34 cases and 10/8/8/6/5 all-four queries, using the same fixed A/A gate. This supplementary profile does not choose a new primary threshold.

Q18 had a work_mem-associated plan cliff; its magnitude would dominate an overall mean. Stored plans also changed when some originals were faster at lower work_mem. These observations do not establish a general benefit from lower memory. See the reviewed report and stored plan signatures.

These findings apply to the tested local workload and finite fixtures. They do not prove equivalence, independent timing samples, generalization to unseen families/schemas, or an effect of changing the prompt. Generation, safety, result comparison and timings are pipeline stages, not a fully evaluated predictor-to-rewriter deployment. Predictor-gated integration and end-to-end overhead remain outside this study.

## Configuration and provenance

The configuration below is recorded in the frozen identity and run/completion files.

| Item | Recorded value |
| --- | --- |
| Freeze identity | `c3c7b0e3f04e506ada4f106403a95fafa03ab5516ceba46ad93602636f1ed24c` |
| Run date and interval (UTC) | 2026-11-06T19:23:02.080821+00:00 to 2026-11-06T19:43:49.653519+00:00 |
| Model and provider | `openai/gpt-oss-120b` served by Groq |
| Prompt version | `rq2-generation-v2` |
| Prompt template SHA-256 | `466f15745ba03ee5255dc4737be2dc0136db808b073bb4ca2df527444e906a11` |
| Generation parameters | temperature 0.2; reasoning effort `low`; max_completion_tokens 2048; 1 call per original; automatic feedback/retries: false |
| Fixture registry | `rq2-fixture-registry-v2` |
| PostgreSQL | 16.15 (Debian 16.15-1.pgdg13+2) |
| Database execution settings | JIT off; statement limit 15000 ms |
| Docker container limits | 2 CPUs (NanoCpus=2000000000); 4 GiB RAM (4294967296 bytes) |
| Docker engine reported resources | 14 logical CPUs; 8061816832 bytes memory; these are distinct from the container limits |
| Docker Desktop version | 4.93.0.240920 |
| Power plan and supply | Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced); AC line status 1 (connected) |

The Q01–Q05 development rehearsal and the pilot are excluded from all final counts. Handwritten reference rewrites and negative controls are not counted as LLM outputs.

Publication artifacts: [final table](results.csv), [figure](Final_Results_Matrix.png), [order-splits.csv](order-splits.csv), [frozen identity](study_identity.json), and [evidence catalogue](assets.json). The reviewed detailed report and replayable raw records are in the separate evidence archive; see [the evidence guide](EVIDENCE.md).

Decisions and reports can be replayed from stored evidence. Exact regeneration of model outputs and live execution timings is not guaranteed. See [offline replay instructions](EVIDENCE.md) for offline replay and its limits.
