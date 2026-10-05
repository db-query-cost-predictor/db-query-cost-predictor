# Poster Evidence Runbook

> **Status of this repository:** code, SQL, configuration, documentation and an
> *unexecuted* notebook only. **Nothing here has been executed or verified by
> Claude Code.** No dataset, metric, figure or table exists until you run the
> commands below. The existence of code is not evidence that any outcome occurred.

This runbook produces the **Poster Evidence Milestone** — not the final study.
It lets you (1) audit the existing pilot from its raw files, (2) run a small,
controlled PostgreSQL smoke collection, (3) verify two manual reference
rewrites, and (4) build only evidence-backed poster tables and figures.

Every poster statement must carry one of these labels (see
[docs/poster_claim_register.md](docs/poster_claim_register.md)):

| Label | Meaning |
|---|---|
| `RECOMPUTED_PILOT` | Recomputed by `pilot-audit` from `reference_pilot/output/` raw files |
| `NEW_POSTER_SMOKE` | Collected by you in this milestone (steps 9–11) |
| `PLANNED` | Final-study design; nothing collected |
| `NOT_YET_EVALUABLE` | Cannot be evaluated with current evidence (e.g., RQ2 with an LLM) |
| `PROHIBITED` | Must not appear on the poster |

---

## Conventions

* Run every command from the **repository root** in a VS Code PowerShell
  terminal. The path contains spaces; the scripts quote paths internally.
* Windows blocks unsigned `.ps1` files by default. In **each new terminal** run
  (affects only that terminal process, not the machine):

  ```powershell
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
  ```

* Scripts stop at the first failure and print `PASS` or `FAIL` on the last line.
  They never choose a different port, path, Python or PostgreSQL version.
* Raw evidence (manifests, JSONL, database rows) is append-only. Never edit or
  delete files under `QCP_DATA_ROOT\raw`.
* Generated poster outputs go to `reports\poster\` (git-ignored).

Upper-bound timing arithmetic (not a measurement): the smoke manifest has at most
50 keys; each key runs at most 1 probe + 3 measured executions, each capped by a
15-second statement timeout, so collection cannot exceed roughly
50 × 4 × 15 s ≈ 50 minutes plus overhead. Most keys are expected to be far
shorter, but only your run will show that.

---

## Step 1 — Inspect Python and create the virtual environment

**What it does:** lists installed Python versions and creates `.venv` with an
explicit version. Nothing is installed yet.

```powershell
py --list
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe --version
```

* **Expected output files:** `.venv\` folder in the repository root.
* **Expected console output:** `py --list` prints one line per installed Python
  (for example a line containing `3.12`); the last command prints `Python 3.12.x`.
* **Pass:** the last command prints `Python 3.12.x` (3.11 is also supported; if
  you use 3.11, replace `-3.12` with `-3.11`).
* **Stop:** `py` is not found, or no Python ≥ 3.11 is listed. Do not substitute
  another interpreter silently.
* **Share back if it fails:** the output of `py --list`.
* **Note (OneDrive):** this folder is synced by OneDrive. A `.venv` here works but
  syncing thousands of files is slow. Optionally create the venv outside OneDrive
  (for example `py -3.12 -m venv C:\qcp_venv`) and set `QCP_PYTHON` in `.env`
  (step 3) to `C:\qcp_venv\Scripts\python.exe`. Adjust the commands below to that
  path.

## Step 2 — Install dependencies

**What it does:** installs this package in editable mode with notebook and test
extras, then prints the environment check (Python and package versions).

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[notebook,dev]"
.\.venv\Scripts\python.exe -m query_cost_predictor env-check
```

* **Expected output files:** none (the check prints to the console).
* **Pass:** the last command ends with `ENV-CHECK: PASS` and lists numpy, pandas,
  scipy, scikit-learn, matplotlib, PyYAML and psycopg versions.
* **Stop:** any pip error, or `ENV-CHECK: FAIL`.
* **Share back if it fails:** the full pip error text and the `env-check` output.

### Step 2b (recommended) — Run the unit tests

**What it does:** runs the written-but-never-run unit tests (no database needed;
all fixtures are synthetic and live in pytest's temporary folders).

```powershell
.\.venv\Scripts\python.exe -m pytest
```

* **Expected output files:** none (pytest cache only).
* **Pass:** pytest reports all tests passed.
* **Stop:** any failure or error — the code has never been executed before, so a
  failure here is a real defect to fix before collecting anything.
* **Share back if it fails:** the complete pytest output.

## Step 3 — Copy and review `.env`

**What it does:** creates your private `.env` (git-ignored) and the external data
root. You choose all values; no password is embedded anywhere in the repository.

```powershell
Copy-Item .env.example .env
New-Item -ItemType Directory -Path "C:\qcp_data"
# Print six random 24-character alphanumeric passwords to paste into .env:
1..6 | ForEach-Object { -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ }) }
code .env
```

Edit in `.env`:

1. `QCP_DATA_ROOT` — an existing folder **outside** the repository (the command
   above creates `C:\qcp_data`; use another path if you prefer).
2. The six `*_PASSWORD` values — replace every `CHANGE_ME...` placeholder.
   Use letters and digits only (the scripts reject other characters).
3. `QCP_BENCH_PORT` / `QCP_EVIDENCE_PORT` — keep the defaults unless preflight
   reports a conflict.
4. `QCP_BENCH_CPUS` / `QCP_BENCH_MEMORY` — must not exceed what Docker Desktop
   provides (preflight reports it).

* **Expected output files:** `.env` (never commit it), the data-root folder.
* **Pass:** `.env` exists and contains no `CHANGE_ME` text (checked in step 4).
* **Stop:** you are unsure where `QCP_DATA_ROOT` should live — decide first.
* **Share back if it fails:** never share `.env`. Share only the variable *names*
  that preflight rejects.

## Step 4 — Run preflight

**What it does:** reports Windows, PowerShell, Python, Docker, Docker Compose, WSL,
memory, CPU, free disk (repository and `QCP_DATA_ROOT`), port availability,
required reference files and — if the image is already present — the resolved
PostgreSQL image tag and digest. It changes nothing on the machine; the `-OutFile`
switch only writes the report file.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\powershell\01_preflight.ps1 -OutFile reports\poster\environment\preflight.json
```

* **Expected output files:** `reports\poster\environment\preflight.json`.
* **Pass:** last line `PREFLIGHT: PASS`. `WARN` lines are allowed but read them.
  Before step 5 the image digest is reported as `NOT_AVAILABLE_YET`; that is
  expected.
* **Stop:** last line `PREFLIGHT: FAIL`.
* **Share back if it fails:** the console output and `preflight.json`.

## Step 5 — Start PostgreSQL

**What it does:** pulls the configured PostgreSQL 16 image if absent, starts
`bench-db` (measured server) and `evidence-db` (evidence store) bound to
`127.0.0.1`, waits for both health checks, confirms `server_version_num` is 16xxxx
on both, and records the resolved image tag, image ID and repository digests.

```powershell
.\scripts\powershell\02_start_databases.ps1
```

* **Expected output files:** `reports\poster\environment\postgres_image.json`.
* **Pass:** last line `START-DATABASES: PASS`; the JSON lists a non-empty
  `repo_digests` array and `server_version_num` beginning with `16` for both
  services.
* **Stop:** a health check times out, a port is already bound by another program,
  or the server version is not 16.
* **Share back if it fails:** console output and
  `docker compose ps --all` / `docker compose logs --tail 100 bench-db evidence-db`.

Optional: re-run step 4 now; the digest line should change from
`NOT_AVAILABLE_YET` to the captured value.

## Step 6 — Initialize roles, databases and the evidence schema

**What it does:** as the container administrator (only for initialization) it
creates the restricted roles (`qcp_bench_owner`, read-only `qcp_bench_reader`,
`qcp_evidence_owner`, `qcp_evidence_writer`), the benchmark databases
`tpch_sf0_1` and `tpch_sf1`, the evidence database `qcp_evidence`, applies the
evidence migrations `V001`–`V007` with checksum tracking and grants privileges.
Re-running is safe: applied migrations are skipped when their checksum matches and
the command fails if a migration file changed after it was applied.

```powershell
.\scripts\powershell\03_initialize_databases.ps1
```

* **Expected output files:** `reports\poster\environment\db_init.json`.
* **Pass:** last line `DB-INIT: PASS`; the report shows 7 migrations
  `applied` or `already_applied`, and `qcp_bench_reader` with `rolsuper = false`
  and `default_transaction_read_only = on`.
* **Stop:** `DB-INIT: FAIL`, or any `checksum_mismatch`.
* **Share back if it fails:** console output and `db_init.json`.

### Step 6b — Generate and load TPC-H SF 0.1, then register the snapshot

**What it does:** builds the `tpch-tools` image (first time only: clones the
pinned `tpch-dbgen` commit, so internet access is needed), runs `dbgen` for
SF 0.1 inside Docker, strips trailing delimiters, loads the eight tables with
`COPY`, adds keys and the baseline index set, runs `VACUUM (FREEZE, ANALYZE)`,
disables autovacuum on benchmark tables (so statistics cannot drift during
collection), grants `SELECT` to the reader, and records per-table row counts and
SHA-256 hashes of the loaded files. Then it registers an immutable
`benchmark_snapshot` (schema, index, statistics and data hashes).

```powershell
.\scripts\powershell\03b_load_tpch.ps1 -ScaleFactor 0.1
```

* **Expected output files:** `reports\poster\environment\snapshot_tpch_sf0_1.json`.
* **Pass:** last line `LOAD-TPCH: PASS`; row counts equal
  region 5, nation 25, part 20000, supplier 1000, partsupp 80000,
  customer 15000, orders 150000, lineitem 600572.
* **Stop:** any count mismatch, a refusal to overwrite existing data, or a build
  failure (for example no internet during the first build).
* **Share back if it fails:** console output and
  `docker compose --profile tools logs --tail 100 tpch-tools` if relevant.
* **License note:** the tools image builds a third-party copy of TPC's `dbgen`
  (the same pinned commit the pilot used). Confirm the TPC license terms are
  acceptable to you before running. Results are *derived from* TPC-H; never call
  them TPC-H benchmark results.

### Step 6c (optional) — Load TPC-H SF 1 for the performance-risk probes

Only needed if you want the SF 1 risk probes. It generates about 1 GB of raw data
and takes longer than SF 0.1. If you skip it, the SF 1 entries are excluded from
the smoke manifest and listed as `SKIPPED_SNAPSHOT_UNAVAILABLE`.

```powershell
.\scripts\powershell\03b_load_tpch.ps1 -ScaleFactor 1
```

* **Pass:** `LOAD-TPCH: PASS`; lineitem count 6001215 and orders 1500000.
* **Stop / share back:** as in step 6b.

## Step 7 — Run the independent pilot audit

**What it does:** reads `reference_pilot\output\` **read-only** (it refuses to
write inside `reference_pilot`), verifies the recorded SHA-256 checksums, and
independently recomputes counts, repetitions, runtime statistics, variance
decomposition, disk-read/spill/parallel-worker evidence, SQL/plan/parameter
diversity, repeated-run noise q-error, the raw and calibrated PostgreSQL-cost
baselines and one nonlinear model under identical random and
template-grouped folds. It never uses template identifiers as model inputs and
emits `NOT_YET_EVALUABLE` for high-runtime classifiers without positive cases.
It does not need the databases.

```powershell
.\scripts\powershell\04_run_pilot_audit.ps1
```

* **Expected output files:**
  `reports\poster\pilot_audit.json`,
  `reports\poster\pilot_metrics.csv`,
  `reports\poster\pilot_claim_check.md`,
  `reports\poster\pilot_data_quality.csv`,
  `reports\poster\figures\pilot\*.png`.
* **Pass:** last line `PILOT-AUDIT: COMPLETED`, all five outputs exist, and
  `pilot_data_quality.csv` has no `FAIL` rows.
* **Stop:** a non-zero exit code, missing outputs, or any `FAIL` row.
* **Share back if it fails:** console output and `pilot_data_quality.csv`.

## Step 8 — Stop and inspect the audit outputs

Do not continue until you have read:

1. `reports\poster\pilot_claim_check.md` — every previously reported pilot value
   next to its recomputed value. `MISMATCH` rows are listed first. A mismatch is
   never overwritten; decide how the poster handles it.
2. `reports\poster\pilot_data_quality.csv` — integrity checks.
3. `reports\poster\pilot_metrics.csv` — random versus template-grouped metrics.
   The random split is a leakage diagnostic only.
4. The figures in `reports\poster\figures\pilot\`.

**Share back:** `pilot_claim_check.md`, `pilot_data_quality.csv`,
`pilot_metrics.csv` and the console summary.

## Step 9 — Run the fresh poster-smoke collection (four inspectable sub-steps)

### 9a — Build and register the manifest (no queries are executed)

**What it does:** captures the effective settings of configurations
`C1_baseline` and `C2_reduced_work_mem`, renders every SQL instance from
`config\poster_smoke.yaml`, validates each as a single read-only statement,
computes deterministic `query_instance_id` and `modeling_key` values, assigns a
seeded execution order, writes the immutable manifest file and registers it.

```powershell
.\scripts\powershell\05_run_poster_smoke.ps1 -Step Manifest
```

* **Expected output files:**
  `QCP_DATA_ROOT\manifests\poster_smoke\manifest_<id>.json` and
  `QCP_DATA_ROOT\manifests\poster_smoke\CURRENT_MANIFEST.txt`.
* **Pass:** `SMOKE-MANIFEST: PASS`; 20–50 keys; every required coverage tag listed;
  SF 1 entries either included or listed as skipped.
* **Stop:** a safety-validation failure, fewer than 20 keys, or a missing snapshot.
* **Inspect before 9b:** open the manifest JSON and read every `sql_text`.
* **Share back if it fails:** the console output (it names the failing template or
  snapshot).

### 9b — Collect (resumable)

**What it does:** for each key in manifest order: plain `EXPLAIN (SETTINGS, FORMAT
JSON)`, one unmeasured probe, then three measured `EXPLAIN (ANALYZE, BUFFERS, WAL,
TIMING OFF, SUMMARY ON, SETTINGS, FORMAT JSON)` executions in read-only
transactions with a 15-second statement timeout, as the read-only role. Timeouts
are stored as right-censored runs; errors are stored with their status; nothing is
overwritten. Evidence is written to raw JSONL first, then to the evidence database.

```powershell
.\scripts\powershell\05_run_poster_smoke.ps1 -Step Collect
```

* **Expected output files:**
  `QCP_DATA_ROOT\raw\poster_smoke\poster-smoke-v1\session_<utc>_<id>.jsonl` (one
  new file per session; earlier files are never reopened) plus evidence-database
  rows.
* **Pass:** `SMOKE-COLLECT: FINISHED` and the summary reports zero keys
  `not_attempted`.
* **Resume:** if interrupted (Ctrl+C, restart, crash), run the same command again.
  Completed `modeling_key + repeat_no` pairs are skipped and never duplicated.
* **Stop:** `SMOKE-COLLECT: FAILED` (for example lost database connection after
  retries, snapshot or configuration drift).
* **Share back if it fails:** console output and the last 20 lines of the newest
  session JSONL file.

### 9c — Derive labels and features (rebuildable)

**What it does:** rebuilds runtime labels (from measured runs only) and A–D
features from the raw JSONL, writes versioned CSVs to a content-addressed folder,
and records a draft `dataset_release`. Rebuilding from unchanged raw evidence must
produce byte-identical files; the command fails if it does not.

```powershell
.\scripts\powershell\05_run_poster_smoke.ps1 -Step Derive
```

* **Expected output files:**
  `QCP_DATA_ROOT\derived\poster_smoke\<manifest>\poster-smoke-label-v1\<inputs>\`
  containing `keys.csv`, `executions.csv`, `labels.csv`, `estimates.csv`,
  `features.csv`, `derived_manifest.json`.
* **Pass:** `SMOKE-DERIVE: PASS`. Running it twice reports
  `identical_rebuild_verified` the second time.
* **Stop:** any duplicate-run, checksum, non-deterministic-rebuild or leakage error.
* **Share back if it fails:** the console output and the traceback.

## Step 10 — Validate the smoke evidence and stop to inspect it

```powershell
.\scripts\powershell\05_run_poster_smoke.ps1 -Step Validate
```

* **Expected output files:** `reports\poster\smoke_validation.json` and
  `reports\poster\smoke_validation.md`.
* **Pass:** `SMOKE-VALIDATE: PASS`. Coverage gaps that are *observations* (for
  example "no key reached 10 s") are reported honestly as `OBSERVED_ABSENT` and do
  not fail validation; integrity problems do.
* **Stop:** `SMOKE-VALIDATE: FAIL`.
* **Inspect:** key status counts (planned / attempted / complete / censored /
  failed), settings consistency, plan changes between repetitions, workers
  planned versus launched, spill evidence and the slow/censored examples.
* **Share back:** `smoke_validation.md` and `smoke_validation.json`.

## Step 11 — Run the manual reference-rewrite verification harness

**What it does:** for each manually written reference pair (and one clearly
labelled negative control) it safety-checks both SQL texts, checks stated
preconditions, fetches both result sets on the same snapshot, compares them as
exact multisets (duplicates and NULLs retained; ordering compared only when the
SQL requires it), runs paired, order-alternated timing measurements and computes
the median speedup. It refuses to recommend a rewrite whose equivalence check does
not pass. All results are labelled `MANUAL_REFERENCE_REWRITE`, never
`LLM_REWRITE`. No LLM is called.

```powershell
.\scripts\powershell\05b_run_rewrite_verification.ps1
```

* **Expected output files:**
  `QCP_DATA_ROOT\raw\rewrite_verification\session_<utc>_<id>.jsonl`,
  `reports\poster\rewrite_verification_summary.json`.
* **Pass:** `REWRITE-VERIFY: COMPLETED`; each reference pair has an equivalence
  status; the negative control is `NOT_EQUIVALENT` and `DO_NOT_RECOMMEND`.
* **Stop:** the negative control is reported equivalent (the method check failed),
  or any `ERROR` status.
* **Share back:** the summary JSON and console output.

## Step 12 — Build the poster evidence tables and figures

**What it does:** reads only the outputs of steps 7, 10 and 11. It refuses to draw
a figure when its evidence is missing or failed validation, stamps every figure
with source, evidence status, key/execution/group counts, configuration, scale
factor, timestamp and metric definition, renders the claim table and lints it for
prohibited claims.

```powershell
.\scripts\powershell\06_build_poster_evidence.ps1
```

* **Expected output files:** `reports\poster\figures\*.png`,
  `reports\poster\tables\slow_query_examples.csv`,
  `reports\poster\tables\manual_reference_rewrite_results.csv`,
  `reports\poster\poster_evidence_summary.md`,
  `reports\poster\evidence_manifest.json`.
* **Pass:** `BUILD-POSTER-EVIDENCE: PASS`. Skipped figures are listed with reasons
  in the summary and manifest — a skip is correct behaviour when evidence is
  absent.
* **Stop:** `BUILD-POSTER-EVIDENCE: FAIL` (for example a prohibited-claim lint
  error).
* **Share back:** `poster_evidence_summary.md` and `evidence_manifest.json`.

## Step 13 — Open and run the notebook manually

```powershell
.\.venv\Scripts\python.exe -m jupyter lab notebooks\01_poster_evidence.ipynb
```

(Or open the notebook in VS Code and select the `.venv` interpreter as kernel.)
Run the cells **one at a time**, top to bottom. A cell that cannot find its
evidence raises `MissingEvidenceError` naming the step to run; it never
substitutes demonstration data. Check the counts at each checkpoint cell.

* **Pass:** every section either displays actual evidence or states precisely
  which evidence is missing.
* **Share back:** any error message, and screenshots of the checkpoint cells.

## Step 14 — Stop the databases

```powershell
.\scripts\powershell\07_stop_databases.ps1
```

Stops the containers but **keeps** the volumes (benchmark data and evidence
store). Restart later with step 5.

* **Expected output files:** none.
* **Pass:** `STOP-DATABASES: PASS`; `docker compose ps --all` shows both services exited.
* **Stop:** `STOP-DATABASES: FAIL`.
* **Share back if it fails:** the console output.

---

## Recovery and resume

* **Collection interrupted:** re-run step 9b. A new session file is created;
  earlier files are never reopened for writing. Raw records that reached the
  JSONL but not the database are re-ingested before any new execution.
* **Derived files look wrong:** re-run step 9c. Derived outputs are rebuildable;
  raw evidence is not modified.
* **Snapshot or configuration drift detected:** the collector stops. Do not
  continue collecting into the same manifest; share the message.
* **Destructive reset (only if you decide to discard everything collected):**
  `docker compose down --volumes` deletes both databases, including the evidence
  store. Raw JSONL under `QCP_DATA_ROOT` remains. This runbook never runs it.

## What exists before and after you run this

| Item | Before you run anything | After the runbook |
|---|---|---|
| Pilot data (220 instances, 660 runs, SF 0.1) | Collected earlier by the team (historical) | Recomputed and cross-checked (`RECOMPUTED_PILOT`) |
| Poster smoke data (20–50 keys) | Does not exist | Collected by you (`NEW_POSTER_SMOKE`) |
| Final dataset (~4,000–5,000 keys) | Planned only | Still planned only (`PLANNED`) |
| RQ2 with an LLM | Not implemented | Still not evaluable (`NOT_YET_EVALUABLE`) |
