# DB Query Cost Predictor

The goal is to predict a SQL query's execution cost before it runs, and to recommend a cheaper rewrite only after checking that its results match and that it is faster.

> **Current state: expanded dataset collected; RQ1 modeling next.** This
> repository holds a reproducible PostgreSQL 16 / TPC-H evidence pipeline: an
> independent audit of the historical pilot, a small smoke dataset, a harness
> that verifies hand-written query rewrites, and the **expanded release**
> (4,944 determined cases, collected on 2026-10-04). Its full validation is
> **STOP at gate G-08**: no development case reaches 10 s. Runtime regression
> and 1 s high-runtime classification are the next scoped modeling tasks; 10 s
> classification stays `NOT_YET_EVALUABLE`. Final expanded model scores and
> LLM-generated rewrite results are **pending**. [Evidence status](#evidence-status)
> separates what has been measured from what is planned.

---

## Goal

Databases estimate query cost with optimizer heuristics and statistics that
often break down on complex workloads. The project aims to:

1. **Predict** a query's runtime, and flag likely high-runtime queries, using
   only information available *before* the query runs.
2. **Remediate** flagged queries by proposing rewrites. A rewrite is recommended
   only after its results have been compared with the original's and it has
   been measured to be faster.

This milestone builds the measurement and verification foundation for both.
It does not yet train or deploy a production model, and it does not call an LLM.

---

## Evidence status

The repository refers to four kinds of evidence. They come from different
sources and are never pooled into one statistic.

| Kind | Status | Size | Where it lives |
|---|---|---|---|
| **Historical reference pilot** | Collected earlier by the team on TPC-H SF 0.1 | 220 query instances, 22 templates, 660 measured runs | `reference_pilot/` (read-only, kept byte-for-byte) |
| **Poster smoke dataset** | Newly collected; results previously reported on **September 23, 2026** | 30 modeling keys, 17 templates, 14 semantic groups, 90 measured executions | Raw evidence in `QCP_DATA_ROOT`, outside the repository |
| **Manual rewrite verification** | Results previously reported on **September 23, 2026** | 4 snapshot-equivalent cases: 2 rewrites faster, 2 slower. 1 non-equivalent negative control, which the harness rejected | Raw evidence in `QCP_DATA_ROOT`; summary in `reports/poster/` (git-ignored) |
| **Expanded release (final study data)** | Collected on 2026-10-04 (UTC); audited 2026-10-21; full validation **STOP at G-08** (only the 10 s gate); not frozen | 4,944 determined cases (4,940 complete, 4 right-censored) in 199 semantic groups: 4,784 development cases and 160 reserved RQ2 cases | [docs/expanded_collection_checkpoint.md](docs/expanded_collection_checkpoint.md) |

How to read these figures:

* The smoke-dataset and rewrite-verification figures are **previously reported
  results from September 23, 2026**. They were not re-measured while this
  README was written. The raw evidence and generated reports behind them are not
  committed (see [Generated outputs and data](#generated-outputs-and-data)).
  [POSTER_RUNBOOK.md](POSTER_RUNBOOK.md) regenerates them.
* The pilot figures are the values recorded in `reference_pilot/output/`.
  `pilot-audit` recomputes them independently from the raw files. It lists every
  mismatch with a previously reported value and never overwrites one.
* The expanded-release figures come from its full validation report and were
  re-verified in the audit of 2026-10-21. That audit rebuilt every derived file
  byte-for-byte from the raw evidence at source checkpoint `972c00b` and ran the
  unit-test suite: **239 passed**. (The poster milestone earlier reported 142
  passing tests.)
* Several files (`POSTER_RUNBOOK.md`, `docs/`, `tests/fixtures/README.md`) say
  the code was written but not executed by its author. That describes how the
  code was written. The results above come from later manual runs.

---

## Prediction inputs and runtime labels

The pipeline keeps what a model may see apart from what it must predict.

* **Inputs are pre-execution features only.** Every field is registered in
  [config/feature_registry.yaml](config/feature_registry.yaml) with an
  availability class. Classes A–D may be used as features: **A** is SQL
  structure, **B** is schema and statistics, **C** is environment and
  configuration, and **D** is a plain `EXPLAIN` estimate, which does not run the
  query. `contract.py` rejects unregistered columns and any class-E field before
  a feature matrix is built.
* **Labels are measured with `EXPLAIN ANALYZE`.** Each smoke key gets one plain
  `EXPLAIN (SETTINGS, FORMAT JSON)`, one unmeasured probe, then three measured
  `EXPLAIN (ANALYZE, BUFFERS, WAL, TIMING OFF, SUMMARY ON, SETTINGS, FORMAT JSON)`
  executions. The measured runtime is the label (class **E**). Actual rows,
  buffers, spills and other execution-only values are never model inputs.
* Template, semantic-group and query identifiers are used only for grouping and
  splitting, never as predictors.
* Runs that hit the statement timeout (15 s in the smoke protocol) are kept as
  right-censored observations. They are never dropped or imputed.
* Instrumented `EXPLAIN ANALYZE` server time is not end-to-end client latency.
* **Pilot caveat:** the pilot captured only `EXPLAIN ANALYZE` plans, so its
  D-class features come from the estimate view of those plans with every
  execution-only key removed. New collections capture a plain `EXPLAIN` before
  any execution.

Details: [docs/data_contract.md](docs/data_contract.md),
[docs/rq1_evaluation_protocol.md](docs/rq1_evaluation_protocol.md).

---

## Rewrite verification and its limits

`verify-rewrites` checks hand-written rewrite pairs defined in
[config/poster_smoke.yaml](config/poster_smoke.yaml). Results are labelled
`MANUAL_REFERENCE_REWRITE`; no LLM is called. For each pair the harness:

1. safety-checks both SQL texts (each must be a single read-only statement);
2. checks the schema preconditions that the pair's written equivalence argument
   relies on;
3. runs both queries on the same snapshot and compares their results as exact
   multisets, keeping duplicates and NULLs (order is compared only when the SQL
   requires it);
4. runs paired, order-alternated timings and computes the median speedup.

A rewrite is recommended only if its results match on the snapshot and its
median speedup is at least the configured 1.10×. A deliberately non-equivalent
negative control (`UNION` versus `UNION ALL`) tests that the harness rejects an
incorrect rewrite.

**Snapshot result equivalence is not a universal semantic proof.** Identical
results on one database snapshot show only that the two queries agree on that
data. A different database state could still separate them. The written
equivalence arguments and precondition checks support the claim but do not
prove it. See [docs/rq2_verification_protocol.md](docs/rq2_verification_protocol.md).

---

## Repository structure

```text
.
├── POSTER_RUNBOOK.md          Step-by-step reproduction of the poster evidence
├── pyproject.toml             Package metadata, dependencies, `qcp` entry point, pytest settings
├── docker-compose.yml         PostgreSQL 16 bench-db and evidence-db (127.0.0.1 only) and TPC-H tools
├── .env.example               Template for the git-ignored .env (placeholder values only)
├── config/                    Smoke and expanded-release specs, feature registry, claim registers, RQ1 input record
├── docker/tpch-tools/         Image that builds the pinned TPC-H dbgen and loads the data
├── docs/                      Architecture, data contract and dictionary, RQ1/RQ2 protocols, claim register
├── notebooks/                 Poster-evidence and expanded-release EDA notebooks (committed without outputs)
├── reference_docs/            Planning documents cited by the claim registers (read-only)
├── reference_pilot/           Historical pilot: scripts, raw outputs, validation files (read-only)
├── reports/                   Generated poster outputs (git-ignored except reports/README.md)
├── scripts/powershell/        Numbered runbook scripts 01–08
├── sql/evidence/              Evidence-database migrations V001–V008
├── src/query_cost_predictor/  Python package: CLI, collector, pilot audit, rewrite verification, reporting
└── tests/                     Unit tests with synthetic fixtures only
```

[docs/architecture.md](docs/architecture.md) maps each module to its role.

`reference_pilot/` and `reference_docs/` are historical inputs. The code refuses
to write inside them. `pilot-audit` checks the pilot files' SHA-256 hashes
against the recorded values, so `.gitattributes` turns off line-ending
conversion for both folders.

---

## Requirements

From [pyproject.toml](pyproject.toml) and [POSTER_RUNBOOK.md](POSTER_RUNBOOK.md):

* Windows with PowerShell. The scripts are PowerShell; Bash versions are deferred.
* Docker Desktop with Docker Compose.
* Python 3.11 or later. The runbook uses 3.12.
* Internet access for the first build of the `tpch-tools` image, which clones a
  pinned `tpch-dbgen` commit.

Python dependencies are declared in `pyproject.toml`: numpy, scipy,
scikit-learn, pandas, matplotlib, PyYAML and `psycopg[binary]` 3.2. The
`notebook` extra adds JupyterLab and ipykernel; the `dev` extra adds pytest.

---

## Setup

Run these from the repository root in PowerShell (runbook steps 1–3). Each new
terminal first needs:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
```

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[notebook,dev]"
.\.venv\Scripts\python.exe -m query_cost_predictor env-check
.\.venv\Scripts\python.exe -m pytest
Copy-Item .env.example .env
```

Then edit `.env`:

* set `QCP_DATA_ROOT` to an existing folder **outside** the repository;
* replace every `CHANGE_ME` password with letters and digits only, at least
  16 characters (the runbook includes a generator command).

Never commit `.env`. It is git-ignored.

---

## Reproducing the evidence

Follow [POSTER_RUNBOOK.md](POSTER_RUNBOOK.md). Each step lists its outputs and
its pass and stop conditions.

| Runbook step | Command | Purpose |
|---|---|---|
| 4 | `.\scripts\powershell\01_preflight.ps1 -OutFile reports\poster\environment\preflight.json` | Check the machine; changes nothing |
| 5 | `.\scripts\powershell\02_start_databases.ps1` | Start `bench-db` and `evidence-db` |
| 6 | `.\scripts\powershell\03_initialize_databases.ps1` | Create roles and databases; apply migrations |
| 6b | `.\scripts\powershell\03b_load_tpch.ps1 -ScaleFactor 0.1` | Generate and load TPC-H SF 0.1; register the snapshot (SF 1 is optional, step 6c) |
| 7 | `.\scripts\powershell\04_run_pilot_audit.ps1` | Audit `reference_pilot/output/` read-only |
| 9a–9c | `.\scripts\powershell\05_run_poster_smoke.ps1 -Step Manifest`, then `-Step Collect`, then `-Step Derive` | Build the manifest, collect (resumable), derive labels and features |
| 10 | `.\scripts\powershell\05_run_poster_smoke.ps1 -Step Validate` | Validate the smoke evidence |
| 11 | `.\scripts\powershell\05b_run_rewrite_verification.ps1` | Verify the manual reference rewrites |
| 12 | `.\scripts\powershell\06_build_poster_evidence.ps1` | Build poster tables and figures |
| 13 | `.\.venv\Scripts\python.exe -m jupyter lab notebooks\01_poster_evidence.ipynb` | Run the notebook one cell at a time |
| 14 | `.\scripts\powershell\07_stop_databases.ps1` | Stop the containers; keep the volumes |

The scripts call the package CLI, `python -m query_cost_predictor <command>`.
The package also installs it as `qcp`.

---

## Generated outputs and data

* Raw evidence, manifests and derived datasets are written to `QCP_DATA_ROOT`,
  outside the repository. Raw evidence is append-only.
* Poster tables, figures and validation reports go to `reports/poster/`, which
  is git-ignored.
* The evidence database lives in a Docker volume. `docker compose down --volumes`
  deletes it; the runbook never runs that command.
* Data are *derived from* TPC-H; they are not TPC-H benchmark results. The tools
  image builds a third-party copy of TPC's `dbgen` at the same pinned commit the
  pilot used. Confirm that the TPC license terms are acceptable before building it.

---

## Planned work

The expanded dataset itself is collected (see [Evidence status](#evidence-status)
and [docs/expanded_collection_checkpoint.md](docs/expanded_collection_checkpoint.md)):
4,944 determined cases on TPC-H SF 0.1 and SF 1 from 22 TPC-H-derived templates,
128 generated structural groups, 24 point/range probes and 25 manual rewrite
pairs, five of them reserved for RQ2. Its design is in
[docs/expanded_release_spec.md](docs/expanded_release_spec.md), and the
collection steps actually run are in
[EXPANDED_DATASET_RUNBOOK.md](EXPANDED_DATASET_RUNBOOK.md). No model has been
trained on it yet. None of the following has been evaluated yet.

* **RQ1 runtime regression (next).** Grouped cross-validation on unseen query
  structures over the 4,784 development cases, compared against calibrated
  PostgreSQL cost, cost-only and scale-plus-cost baselines, using the frozen
  folds in `splits.csv`. Inputs are pinned by
  [config/rq1_modeling_input_v1.json](config/rq1_modeling_input_v1.json). The
  random split serves only as a leakage diagnostic. See
  [docs/rq1_evaluation_protocol.md](docs/rq1_evaluation_protocol.md).
* **High-runtime detection.** The 1 s classifier is evaluable (393 development
  positives in 106 groups, all at SF 1) and is evaluated mainly by PR-AUC,
  recall and false-negative rate. The 10 s classifier remains
  `NOT_YET_EVALUABLE` (gate G-08); it is not trained, reported or claimed.
* **LLM-generated rewrite evaluation (RQ2).** Primary question: do LLM-assisted
  rewrites preserve SQL semantics and lower measured runtime? Every candidate goes
  through the same verification harness, and the full denominator is reported
  from generated candidates to recommended ones. A secondary experiment compares
  uncertainty-aware gating policies. Manual reference results are never pooled
  with LLM results. See
  [docs/rq2_verification_protocol.md](docs/rq2_verification_protocol.md).
* **Deferred:** API, dashboard and CI.

---

## Documentation

| Document | Contents |
|---|---|
| [POSTER_RUNBOOK.md](POSTER_RUNBOOK.md) | Reproduction steps with pass/stop conditions |
| [docs/architecture.md](docs/architecture.md) | Pipeline, components, roles and safety, code map |
| [docs/data_contract.md](docs/data_contract.md) | Feature-availability classes and enforcement |
| [docs/data_dictionary.md](docs/data_dictionary.md) | Field definitions |
| [docs/poster_evidence_scope.md](docs/poster_evidence_scope.md) | What the milestone covers and excludes |
| [docs/poster_claim_register.md](docs/poster_claim_register.md) | Evidence labels and permitted poster claims |
| [docs/rq1_evaluation_protocol.md](docs/rq1_evaluation_protocol.md) | RQ1 prediction evaluation protocol (modeling pending) |
| [docs/rq2_verification_protocol.md](docs/rq2_verification_protocol.md) | Rewrite verification protocol |
| [docs/final_dataset_plan.md](docs/final_dataset_plan.md) | Earlier final-dataset plan (superseded for collection) |
| [EXPANDED_DATASET_RUNBOOK.md](EXPANDED_DATASET_RUNBOOK.md) | Staged runbook for the expanded release (run 2026-10-03/04) |
| [docs/expanded_release_spec.md](docs/expanded_release_spec.md) | Expanded release design, arithmetic, labels, splits, gates |
| [docs/expanded_collection_checkpoint.md](docs/expanded_collection_checkpoint.md) | Collected release: identities, hashes, audit findings, provenance limits |
| [config/rq1_modeling_input_v1.json](config/rq1_modeling_input_v1.json) | Static RQ1 modeling-input record (pinned evidence and permitted scope) |
| [docs/next_phase_audit.md](docs/next_phase_audit.md) | Audit of reuse, gaps, differences from the plan, risks |
| [docs/query_workload_evidence_register.md](docs/query_workload_evidence_register.md) | Evidence behind each expensive-query family |
| [docs/tuesday_technical_brief.md](docs/tuesday_technical_brief.md) | Short brief on observed results |

---

## License

MIT. See [LICENSE](LICENSE).
