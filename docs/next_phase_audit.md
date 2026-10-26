# Next-phase audit: expanded query dataset

Audit date 2026-10-03, branch `feat/expanded-query-dataset` (from `cdfde49`).
This is a **historical pre-collection audit**. When it was written, nothing this
phase adds (code, configuration, tests, notebook, scripts) had been run: no
Python, test, SQL, Docker or notebook was executed while writing it. Numbers
marked *previously reported* come from the September 23, 2026 runs. Where local
files existed, they were compared read-only on 2026-10-03, which is not a
re-measurement.

> **Update 2026-10-21.** The phase has since been run. SF 1 was loaded and
> registered, and the expanded release was collected on 2026-10-04 (UTC): 4,944
> determined cases (4,940 complete, 4 right-censored), 4,784 development and
> 160 reserved RQ2 cases. Full validation is **STOP at G-08**: no development
> case reaches 10 s, so 10 s classification remains `NOT_YET_EVALUABLE`.
> Runtime regression and 1 s classification are the next scoped modeling tasks.
> Final expanded model scores and LLM rewrite results are pending. The
> 2026-10-21 audit ran the unit suite (239 passed) and rebuilt every derived file
> byte-for-byte at source checkpoint `972c00b`. See
> [expanded_collection_checkpoint.md](expanded_collection_checkpoint.md). The
> sections below are left as written on 2026-10-03.

## 1. What exists and is reused

| Component | Files | Reused as |
|---|---|---|
| Identity hashing | `hashing.py` | unchanged: `query_instance_id`, `modeling_key`, canonical JSON |
| Read-only SQL validator | `safety.py`, `sqltext.py` | unchanged; applied to every expanded instance at spec load, manifest and collection |
| Execution primitives | `executor.py` | unchanged: read-only, always-rolled-back transactions, per-transaction timeouts, watchdog |
| Raw evidence ledger | `evidence.py`, `evidence_store.py` | extended: per-key index (performance only), reconciliation unchanged |
| Collector | `smoke_collector.py` | extended: collection profile, selected-key subset, wall-clock and disk bounds |
| Manifest builder | `smoke_manifest.py` | refactored into reusable snapshot, configuration, file and registration helpers; smoke output byte-compatible |
| Labels | `labels.py` | extended: execution-time censoring bound, frozen high-runtime classes, failure classes |
| Derivation | `smoke_derive.py` | reused (`build_tables`, deterministic CSV, byte-identical rebuild); kind-aware paths |
| Feature contract | `contract.py`, `config/feature_registry.yaml` | unchanged rules; new release columns registered as non-features |
| Snapshot identity | `snapshot.py`, `docker/tpch-tools` | unchanged; SF 1 load path already exists (`03b_load_tpch.ps1 -ScaleFactor 1`) |
| Grouped folds | `splits.py` | unchanged `group_kfold`, applied at three levels |
| Evidence schema | `sql/evidence/V001`-`V007` | unchanged; new `V008` replaces two CHECK constraints only |

## 2. What this phase adds (not yet run when this audit was written)

| File | Purpose |
|---|---|
| `config/expanded_release_v1.yaml` | versioned release spec: protocol, snapshots, configurations, frozen labels, splits, sample, gates, budget, 199 semantic groups |
| `src/query_cost_predictor/release_spec.py` | spec parser, deterministic parameter draws, structural composition, planning arithmetic |
| `src/query_cost_predictor/expanded_manifest.py` | grouped splits, stratified sample, manifest checks, registration, plain-EXPLAIN plan check |
| `src/query_cost_predictor/expanded_collect.py` | readiness, full-collection approval, sample and batched full collection |
| `src/query_cost_predictor/expanded_derive.py` | labels, classes, failures, splits; content-addressed outputs; database release rows |
| `src/query_cost_predictor/expanded_validate.py` | integrity checks, release gates, budget projection, EDA summaries, freeze |
| `sql/evidence/V008__expanded_release_kinds.sql` | allows session kind `expanded_collection` and release kind `expanded_release` |
| `scripts/powershell/08_expanded_release.ps1` | one script, thirteen inspectable steps |
| `tests/unit/test_release_spec.py`, `test_expanded_manifest.py`, `test_expanded_labels.py`, `test_expanded_resume.py`, `test_release_reconciliation.py`, `test_expanded_features.py` | static tests |
| `notebooks/02_expanded_release_eda.ipynb` | EDA notebook over derived files (committed without outputs) |
| `EXPANDED_DATASET_RUNBOOK.md`, `docs/expanded_release_spec.md`, `docs/query_workload_evidence_register.md`, `docs/tuesday_technical_brief.md` | runbook, design, evidence register, brief |

Small fixes to existing code (behaviour-preserving):

* `EvidenceLedger.runs_for` scanned every execution for each key, and so did
  `smoke_derive.build_tables`. That is quadratic: about 5,000 keys times about
  20,000 runs at each heartbeat. Both now use a per-key index.
* `verify_database_matches_ledger` rebuilt a Python set inside a comprehension
  for every record; the set is now built once.

## 3. What remains unimplemented

| Item | Status |
|---|---|
| RQ1 model training and evaluation at scale (calibrated-cost baseline, Elastic Net, XGBoost/CatBoost, AFT) | not implemented; only the pilot diagnostic exists |
| High-runtime classifier | not implemented; gated by the positive-coverage gate (section 6 of the spec) |
| Verification of the 25 expanded rewrite pairs | `verify-rewrites` reads `config/poster_smoke.yaml` only; the expanded pairs are recorded as `NOT_YET_VERIFIED` |
| LLM rewrite generation and the gated policy study (RQ2) | not implemented; `LLM_REWRITE` stays reserved |
| Published workloads (STATS-CEB, JOB/IMDb) | not loaded; source, license and snapshot provenance unverified |
| Client-latency calibration, noise audit, bin freezing | not implemented |
| Bash parity, CI, API, dashboard | deferred |

## 4. Identities

| Term | Definition | Count in v1 (planned) |
|---|---|---:|
| Semantic group | one query structure; the unit held out together for unseen-structure evaluation | 199 |
| Structure family | coarser unit (for generated groups, one join path with all output shapes) | 87 |
| Template | one SQL text with `{placeholders}`; a group holds 1 (standalone) or 2 (rewrite forms, or indexed/unindexed predicate) | 352 |
| Query instance | template + explicit parameter values; `query_instance_id = "qi_" + SHA-256(canonical JSON{template_id, parameters, normalized SQL})` | 1,332 |
| Modeling key | `"mk_" + SHA-256(canonical JSON{query_instance_id, snapshot_id, config_id, cache_protocol, protocol_version})` | 4,944 proposed |
| Probe | one unmeasured warm-up execution per key (`repeat_no = 0`); never used for labels | 1 per key |
| Measured repetition | `repeat_no = 1..3`, children of a key; never extra keys | 3 per key |
| Plain estimate | one plain `EXPLAIN (SETTINGS, SUMMARY ON, FORMAT JSON)` per key before execution | 1 per key |

Because `protocol_version` is part of the key, an expanded key never collides
with a poster-smoke key, even for identical SQL on the same snapshot. The smoke
release and its raw files are never written to: expanded evidence goes to
`QCP_DATA_ROOT/raw/expanded/expanded-v1/`.

## 5. Differences between the planned ~4,704-key release and this implementation

| Plan (`config/final_study_plan.yaml`, `docs/final_dataset_plan.md`) | Expanded release v1 | Reason |
|---|---|---|
| 60 audited published-workload groups (STATS-CEB, 960 keys) | none | source, license and snapshot provenance unverified; replaced by generated TPC-H structural groups |
| TPC-H 22 groups x 12 variants (Q18: 4) x 2 snapshots x 2 configurations = 1,024 | 22 x 8 (Q18: 4) x 2 x 2 = 688 | less concentration on 22 templates |
| 50 generated structural groups x 12 x 1 snapshot x 2 = 1,200 | 128 groups x 2 index variants x 3 regimes x 2 snapshots x 2 configurations = 3,072 | more independent groups; scale, index and selectivity contrasts inside groups |
| 30 probe groups x 12 x 1 x 2 = 720 | 24 x 8 x 2 snapshots x 1 configuration = 384 | `work_mem` has no mechanism in index lookups |
| 25 rewrite groups x 8 x 1 x 2 x 2 forms = 800 (160 held out) | 25 x 4 x 2 snapshots x 2 x 2 forms = 800 (160 held out) | rewrites also observed at SF 1 |
| about 187 groups, 4,704 keys | 199 groups, 4,944 proposed keys (4,784 development) | margin for failures: the gate requires at least 4,704 *usable* keys |
| final timeout 60 s | 15 s, frozen for this release | at 60 s the worst-case bound quadruples (about 330 h) on a laptop; 15 s still decides the 10 s class |
| thresholds frozen "after calibration" | frozen now at 1 s and 10 s | must not move to manufacture positives |
| plain EXPLAIN `SETTINGS, FORMAT JSON` | adds `SUMMARY ON` (planning time, nothing executed) | needed for the censoring bound (section 8) |
| no expanded session or release kind in the schema | `V008` | CHECK constraints admitted only smoke and rewrite sessions |
| smoke-only paths in the collector, derivation and validation | collection profile and kind parameters | reuse instead of a parallel pipeline |

`final_study_plan.yaml` and the `final-plan` command remain the historical plan
(still 4,704). Collection uses `config/expanded_release_v1.yaml`.

## 6. Current known evidence

All values below are **previously reported (September 23, 2026)**. The right
column shows what was verified read-only on 2026-10-03 against local files.

| Evidence | Previously reported | Local verification |
|---|---|---|
| Historical pilot | 220 keys (instances), 22 templates, 660 measured runs, SF 0.1 | `reports/poster/pilot_claim_check.md`: PR-001-PR-004 MATCH (220, 220, 22, 660); slowest per-instance mean 284.395 ms (PR-033 MATCH), so no instance reaches 1 s |
| Poster smoke release | 30 modeling keys, 14 semantic groups, 17 templates, 90 measured executions | `reports/poster/smoke_validation.json` (2026-09-23T20:11:09Z, PASS): keys 30, groups 14, templates 17, measured 90, probes 30, complete 30, censored 0 |
| High-runtime positives | none at 1 s or 10 s | same report: O-02 and O-03 `OBSERVED_ABSENT`; largest median 102.899 ms (`high_cardinality_group_by`, C1) |
| Spill | - | O-01: 4 keys (wide sort and high-cardinality grouping, both configurations) |
| Manual rewrites | 4 snapshot-equivalent cases: 2 faster, 2 slower; 1 non-equivalent negative control rejected | `reports/poster/rewrite_verification_summary.json`: `rp_nonsargable_year` median speedups 3.171 and 3.300 (RECOMMEND); `rp_correlated_sum` 0.414 and 0.484 (DO_NOT_RECOMMEND); negative control NOT_EQUIVALENT (5 vs 30 rows). All `MANUAL_REFERENCE_REWRITE`; no LLM |
| Snapshot coverage | - | on 2026-10-03 only `snapshot_tpch_sf0_1.json` existed: **SF 1 had not been loaded** (loaded before the 2026-10-04 collection) |
| Evidence integrity | - | SHA-256 of `C:\qcp_data\manifests\poster_smoke\manifest_d87c28c4c677163a.json` and of the raw smoke JSONL equal the hashes recorded in `derived_manifest.json`. The manifest file's modification time (22:29) is later than its creation, but its bytes are unchanged |
| Unit tests | 142 passing | not re-run on 2026-10-03; the new tests had not yet been run (2026-10-21 audit: 239 passed) |

## 7. Hardware and time risks

* **Preflight of 2026-09-23** (`reports/poster/environment/preflight.json`):
  15.71 GB RAM with only **1.68 GB free**; Docker Desktop with 7.61 GB and 32
  CPUs visible; bench container limited to 2 CPUs and 4 GB; 260 GB free disk.
  The readiness check requires a **fresh** preflight (younger than 24 h,
  status PASS). It warns below 3 GB of free RAM or 6 GB of Docker memory.
* **SF 1 was not loaded on 2026-10-03** (it was loaded and registered before
  the 2026-10-04 collection). Loading it requires the first-time `tpch-tools`
  build, about 1 GB of generated data, the load, `VACUUM (FREEZE, ANALYZE)`
  and registration. Its time and disk use have not been measured here.
* **Memory pressure** on the WSL2 VM adds timing noise. `shared_buffers` keeps
  the PostgreSQL default of 128 MB, so SF 1 relies on the OS page cache and the
  warm-cache protocol is weaker there; every key gets an unmeasured probe first.
* **Time.** Worst case if every run hit the 15 s timeout: 61.8 h for the measured
  runs of 4,944 keys, 82.4 h including probes (58.8 h for 4,704 keys and
  measured runs only). The realistic budget is projected from the sample, not
  assumed. Collection spans several sessions and days, so thermal throttling,
  power plans, background OneDrive sync, Windows updates and sleep add drift.
  Statistics drift is checked every 25 keys and configuration drift at each
  session start.
* **Disk.** The smoke raw file was about 20 KB per key (612,876 bytes, 30 keys),
  which suggests about 0.1 GB of raw evidence for the release. SF 1 plans are
  larger, so the projection measures this. Spills can use up to
  `temp_file_limit` (5 GB per process) inside the Docker VM. Collection stops
  between keys if `QCP_DATA_ROOT` falls below 20 GB free.

## 8. Timeout audit: is a timeout right-censored at the timeout?

Not exactly. PostgreSQL measures `statement_timeout` "from the time a command
arrives at the server until it is completed by the server"
([runtime-config-client](https://www.postgresql.org/docs/16/runtime-config-client.html)).
The label is EXPLAIN ANALYZE "Execution Time", which "does not include parsing,
rewriting, or planning time"
([using-explain](https://www.postgresql.org/docs/16/using-explain.html)). A
timed-out run therefore proves only:

```
execution_time >= timeout - (parse + rewrite + planning + EXPLAIN output time)
```

The expanded release stores two bounds. The raw record keeps the statement-level
bound (= timeout), which the database CHECK requires. The derived label uses
`timeout - max(observed planning time) - 100 ms`, where planning time comes from
the plain `EXPLAIN (SUMMARY ON)` and from completed runs. Without an observed
planning time, a flagged 1,000 ms fallback is used. A censored key is positive
at 10 s only if this bound is at least 10,000 ms. Other outcomes are never
treated as censored:

* a client watchdog cancel is a **failure** (`watchdog_cancel`);
* `temp_file_limit` and memory errors are **failures** (`resource_limit`).

No timeout value is ever stored as a completed runtime.

## 9. Decisions for the user

1. Load and register SF 1 (runbook stage 1); expanded collection needs both snapshots.
2. Confirm that the TPC `dbgen` license terms are acceptable before the SF 1 build.
3. Approve, or change, the frozen protocol before the manifest: 15 s timeout,
   1 s and 10 s thresholds, `work_mem` 256 kB for C2, and the 4,944-key design.
   Any change after the manifest means a new protocol version.
4. Decide whether to extend `verify-rewrites` to the 25 expanded pairs (RQ2
   preparation) before or after the dataset collection.
