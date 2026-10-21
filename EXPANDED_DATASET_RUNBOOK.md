# Expanded Dataset Runbook

> **Status:** the code, configuration, tests, notebook and scripts for this phase
> are **UNEXECUTED**. They were written but never run. No expanded manifest,
> sample, label or report exists until you run the commands below. **Do not
> claim a 4,704-key dataset until stage 6 produces a full validation report with
> status PASS that shows the count.** A manifest entry is a *proposed* key, not a
> label.

Design: [docs/expanded_release_spec.md](docs/expanded_release_spec.md).
Audit and risks: [docs/next_phase_audit.md](docs/next_phase_audit.md).
Why each family exists: [docs/query_workload_evidence_register.md](docs/query_workload_evidence_register.md).

## Conventions

* Run every command from the repository root in a PowerShell terminal. In each
  new terminal, first run:
  ```powershell
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
  ```
* All expanded steps use one script, `.\scripts\powershell\08_expanded_release.ps1 -Step <Name>`,
  which ends with a status line from the Python command (for example
  `EXPANDED-MANIFEST: PASS`).
* Raw evidence, manifests, derived data and the frozen release live under
  `QCP_DATA_ROOT` (outside OneDrive and Git). Reports go to `reports\expanded\`
  (git-ignored). Nothing writes to the poster-smoke release or `reference_pilot`.
* Protocol values are frozen in the spec: 15 s timeout, 1 s and 10 s thresholds,
  C2 `work_mem` = 256 kB. Changing any of them after stage 2 means a new spec
  version and a new protocol version, not an edit.
* Before stage 1, run the new unit tests once (they have never been run):
  ```powershell
  .\.venv\Scripts\python.exe -m pip install -e ".[notebook,dev]"
  .\.venv\Scripts\python.exe -m pytest
  ```
  Expected: all tests pass. **STOP** on any failure and share the complete output.

---

## Stage 1: fresh preflight and snapshot readiness

| # | Command | Expected output | STOP if |
|---|---|---|---|
| 1.1 | `.\scripts\powershell\01_preflight.ps1 -OutFile reports\expanded\environment\preflight.json` | last line `PREFLIGHT: PASS`; report written | `PREFLIGHT: FAIL` |
| 1.2 | `.\scripts\powershell\02_start_databases.ps1` | `START-DATABASES: PASS` | health check timeout, port conflict, server version not 16 |
| 1.3 | `.\scripts\powershell\03_initialize_databases.ps1` | `DB-INIT: PASS`; `reports\poster\environment\db_init.json` lists V001-V007 `already_applied` and **V008 `applied`** | `checksum_mismatch` or `DB-INIT: FAIL` |
| 1.4 | `.\scripts\powershell\03b_load_tpch.ps1 -ScaleFactor 1` (once; first builds the tools image and needs internet; confirm the TPC `dbgen` license first) | `LOAD-TPCH: PASS`; lineitem 6001215, orders 1500000; `reports\poster\environment\snapshot_tpch_sf1.json` | any row-count mismatch or refusal to overwrite |
| 1.5 | `.\scripts\powershell\08_expanded_release.ps1 -Step Readiness` | `EXPANDED-READINESS: PASS`; `reports\expanded\readiness.json` | any `FAIL`: R-01 data root inside OneDrive, R-02 < 20 GB free, R-03 preflight missing, older than 24 h or not PASS, R-06 V008 missing, R-07 a snapshot missing, row counts off or statistics drift |

A `WARN` from R-04 (free RAM < 3 GB) or R-05 (Docker memory < 6 GB) is allowed,
but memory pressure adds timing noise. Close other applications, keep the laptop
on AC power, pause OneDrive sync and disable sleep, then re-run 1.1 and 1.5.

## Stage 2: generate and inspect the manifest (nothing is executed)

| # | Command | Expected output | STOP if |
|---|---|---|---|
| 2.1 | `.\scripts\powershell\08_expanded_release.ps1 -Step Arithmetic` | `EXPANDED-ARITHMETIC: PLANNED (meets target; ...)`; totals 4,944 proposed keys, 199 groups, 352 templates, 1,332 instances, 160 RQ2-reserved keys; `reports\expanded\release_arithmetic.json` | `BELOW TARGET` or totals different from `docs/expanded_release_spec.md` |
| 2.2 | `.\scripts\powershell\08_expanded_release.ps1 -Step Manifest` | `EXPANDED-MANIFEST: PASS`; `C:\qcp_data\manifests\expanded\manifest_<id>.json`, `CURRENT_MANIFEST.txt`; `reports\expanded\manifest_summary_<id>.json` (folds per semantic group, sample of 110 keys in 22 strata) | `ManifestError` (snapshot drift, V008 missing, template id registered with different SQL, a failed manifest check) |
| 2.3 | Open the manifest file and read the `sql_text` of at least one entry per component, the `holdout_groups`, the `rewrite_pairs` (all `NOT_YET_VERIFIED`) and `sample.strata` | every SQL is a single read-only SELECT you understand | anything unexpected: do not continue |
| 2.4 | `.\scripts\powershell\08_expanded_release.ps1 -Step PlanCheck` | `EXPANDED-PLAN-CHECK: PASS`; `reports\expanded\plan_check_<id>.md`: 2,664 plain plans, 0 failures; estimated costs per component and snapshot in planner units | any failure: fix the template **under a new template id**, rebuild the manifest (2.2) and re-run 2.4 |

The plan check runs plain `EXPLAIN` only, so nothing is executed. Its costs are
planner units, not milliseconds, and do not predict which keys will be slow.

## Stage 3: collect and validate a small, bounded sample

| # | Command | Expected output | STOP if |
|---|---|---|---|
| 3.1 | `.\scripts\powershell\08_expanded_release.ps1 -Step CollectSample` | `EXPANDED-COLLECT: FINISHED`; new `C:\qcp_data\raw\expanded\expanded-v1\session_<utc>_<id>.jsonl`; at most 110 keys (worst case about 1.8 h) | `EXPANDED-COLLECT: FAILED` (drift, lost connection after retries). If interrupted, re-run the same command: completed runs are skipped, never duplicated |
| 3.2 | `.\scripts\powershell\08_expanded_release.ps1 -Step DeriveSample` | `EXPANDED-DERIVE: PASS`; `C:\qcp_data\derived\expanded\<m16>\expanded-label-v1\<inputs16>\` with `keys.csv`, `executions.csv`, `labels.csv`, `estimates.csv`, `features.csv`, `splits.csv`, `derived_manifest.json` | duplicate-run, checksum or non-deterministic-rebuild error |
| 3.3 | `.\scripts\powershell\08_expanded_release.ps1 -Step ValidateSample` | `EXPANDED-VALIDATE: PASS`; `reports\expanded\validation_sample_<id>.md` and `budget_projection_<id>.md` | `FAIL`: any SQL error in the sample, failed share > 5 %, an undetermined sample key, or any integrity check |

## Stage 4: review runtime and class coverage and the projected budget

Read before deciding anything:

1. `reports\expanded\validation_sample_<id>.md`: observation statuses, failure
   classes, high-runtime class counts **in the sample**, censored keys and spills.
   A sample with no 10 s positives is an observation, not a failure.
2. `reports\expanded\budget_projection_<id>.md`: expected, p90-based and
   worst-case hours for the remaining keys, timeout share, raw bytes per key,
   projected disk, and strata that had no measurements.

| # | Command | Expected output | STOP if |
|---|---|---|---|
| 4.1 | decide whether the expected hours and disk fit your schedule and machine | written decision | the projection is unacceptable: stop and revise the design as a **new** spec version; never shorten the protocol silently |
| 4.2 | `.\scripts\powershell\08_expanded_release.ps1 -Step ApproveFull -BudgetHours <H>` with `<H>` at least the projected expected hours | `EXPANDED-APPROVE-FULL: APPROVED`; `C:\qcp_data\manifests\expanded\approvals\FULL_COLLECTION_<m16>.json` | `REFUSED` (budget below the projection, or plan check or sample validation not PASS) |

## Stage 5: resumable batches toward the full release

```powershell
.\scripts\powershell\08_expanded_release.ps1 -Step CollectBatch -MaxKeys 250 -MaxMinutes 120
```

| Exit / status line | Meaning | Next |
|---|---|---|
| `EXPANDED-COLLECT: PARTIAL (re-run to continue)` (exit 3) | batch bound reached, keys remain | run the same command again (later, if you like) |
| `EXPANDED-COLLECT: FINISHED` (exit 0) | every key determined | stage 6 |
| `EXPANDED-COLLECT: INTERRUPTED` (exit 130) | Ctrl+C, sleep or restart | run the same command; the next session reconciles raw files first |
| `EXPANDED-COLLECT: FAILED` | snapshot or configuration drift, exhausted retries, disk below 20 GB | **STOP**: do not continue into this manifest; share the console output and the last 20 lines of the newest session JSONL |

Each batch writes a new session JSONL; earlier files are never reopened.
Statistics drift is checked every 25 keys. Keep the machine in the same state
across batches (AC power, same Docker resources, no other heavy work).

## Stage 6: derive, validate, freeze and summarize

| # | Command | Expected output | STOP if |
|---|---|---|---|
| 6.1 | `.\scripts\powershell\08_expanded_release.ps1 -Step Derive` (run twice) | `EXPANDED-DERIVE: PASS`; the second run reports `identical_rebuild_verified` | any rebuild difference |
| 6.2 | `.\scripts\powershell\08_expanded_release.ps1 -Step Validate` | `EXPANDED-VALIDATE: PASS` (exit 0); `reports\expanded\validation_full_<id>.md` with every gate PASS and the usable-key count | `STOP` (exit 4): a gate is not met (stage 7). `FAIL` (exit 1): an integrity check failed; do not freeze |
| 6.3 | `.\scripts\powershell\08_expanded_release.ps1 -Step Freeze` | `EXPANDED-FREEZE: FROZEN`; `C:\qcp_data\releases\expanded\<m16>\<inputs16>\RELEASE_FROZEN.json` | `REFUSED` (no full PASS, or derived files changed after validation) |
| 6.4 | `.\scripts\powershell\08_expanded_release.ps1 -Step Eda` | `EXPANDED-EDA: COMPLETED`; `reports\expanded\eda_summary_<id>.md` (status by component, runtime by snapshot and configuration, matched-pair ratios for `work_mem`, scale and index availability, planner-cost rank agreement) | missing derived files |
| 6.5 | `.\.venv\Scripts\python.exe -m jupyter lab notebooks\02_expanded_release_eda.ipynb`, run one cell at a time | figures from the derived files; a missing input raises `MissingEvidenceError` naming the step | - |
| 6.6 | `.\scripts\powershell\07_stop_databases.ps1` | `STOP-DATABASES: PASS` (volumes kept) | - |

Only after 6.2 PASS and 6.3 FROZEN may anyone say the release exists, quoting
the usable-key count from the validation report.

## Stage 7: when a gate says STOP

| Gate | Meaning | What to do (never relabel, pad or duplicate) |
|---|---|---|
| G-01 | keys still undetermined | continue stage 5 |
| G-02 / G-03 | fewer usable keys than required | report the failure classes; a fix (for example a corrected template) is a new spec version |
| G-04 | fewer than 150 groups usable on both snapshots | report which groups fail and why |
| G-05 / G-06 | one template or family dominates the usable keys | report; redesign as a new version |
| G-07 / G-08 | too few high-runtime positives at 1 s or 10 s | that classifier is `NOT_YET_EVALUABLE`: do not train, report or claim it. The thresholds stay frozen. A future version may add a verified larger snapshot after a capacity check, but this release is never relabelled |

Share back: `validation_full_<id>.md`, the gate table and the console output.
