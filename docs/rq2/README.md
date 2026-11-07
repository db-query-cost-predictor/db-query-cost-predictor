# RQ2: verified SQL rewriting

Mehri Sattari's RQ2 component extends the team's PostgreSQL/TPC-H workflow with candidate generation, correctness checks and paired runtime decisions. It uses the existing prepared databases and restricted reader role. RQ1 code, fitted models and evidence remain unchanged.

## Start here

From the repository root:

```powershell
python -m pip install -c requirements/rq2_constraints_v1.0.1.txt -e ".[dev]"
python -m rq2_pipeline validate --input examples/rq2/selected_queries.json
python -m pytest tests/rq2 -q
```

Input validation and unit tests are offline. The example inputs are synthetic template instances. A live run requires the [setup conditions](RUN.md), private credentials and a new output directory:

```powershell
python -m rq2_pipeline run --input examples/rq2/selected_queries.json --output runs/team-run-001 --container benchmark-container --env-file ../private.env
```

Each supported original gets at most one model attempt. Unsafe or unsupported originals stop before generation. Other failures remain in the denominator. No query is deployed automatically.

```mermaid
flowchart LR
  A[Selected original SQL] --> B[Input and read-only checks]
  B --> C[One Groq model request]
  C --> D[Safety and schema preconditions]
  D --> E[Snapshot and fixture comparisons]
  E --> F[A/A control and paired A/B timing]
  F --> G[Decision with recorded evidence]
```

## Local study and engineering release

The closed study generated 15 fresh outputs for Q06-Q20 and retained all 60 environment cases. Eight rewrites met the primary rule in all four environments; withholding two borderline recommendations gives the disclosed post-hoc 6/15 scenario. Two outputs agreed on current snapshots but failed schema-valid fixtures. All five families were previously inspected during development. See [the results](RESULTS.md) and [the final table](results.csv).

The current runtime is engineering release 1.0.1, a post-study refactor. It supports five registered original templates and a documented selected-query contract. Its source is distinct from the original measurement source preserved in the evidence archive. The single executable default configuration is [pipeline.json](../../src/rq2_pipeline/pipeline.json); [study_identity.json](study_identity.json) is an archival record, not a second run configuration.

The API/rejection path and a separate handwritten positive timing control were tested live. Engineering checks are excluded from the study counts. Independent teammate installation and predictor-to-rewriter integration remain pending.

## Review and handoff

| Document | Purpose |
| --- | --- |
| [RUN.md](RUN.md) | Installation, credentials, database/host prerequisites and commands |
| [CONTRACT.md](CONTRACT.md) | Selected-query input, output fields, statuses and backend integration |
| [PROTOCOL.md](PROTOCOL.md) | Final correctness and paired-timing rules |
| [RESULTS.md](RESULTS.md) | Reviewed results, denominators, diagnostics and limitations |
| [EVIDENCE.md](EVIDENCE.md) | Separate archive, hashes and offline decision replay |
| [PROVENANCE.json](PROVENANCE.json) | Relationship between measurement source and engineering release |
| [VALIDATION.json](VALIDATION.json) | Standalone checks and repository integration validation |
| [REVIEW.md](REVIEW.md) | Release 1.0.1 boundary corrections and scope |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | Attribution, TPC-derived-work notice and source terms |

Primary timing uses two warm-ups per form, eleven A/A pairs and eleven A/B pairs. A candidate must take strictly less than 90% of the original runtime in at least nine pairs, with the A/A gate passing. All four environments must pass for a query-level recommendation. Passing finite checks means agreement on tested data and fixtures, not general equivalence. This replaces the earlier planning rule for the LLM study; the original manual-reference harness keeps its historical rule.

The secondary prediction-gating comparison and end-to-end overhead study were not performed. The RQ1/backend owner can supply selected originals through the contract; that integration remains team work.
