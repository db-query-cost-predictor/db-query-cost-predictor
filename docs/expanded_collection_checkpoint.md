# Expanded collection checkpoint

This page identifies the expanded-release evidence and the source code that
reproduces its derivation. Every value was read from the evidence files and
re-checked on 2026-10-21. The machine-readable form used for RQ1 modeling is
[`config/rq1_modeling_input_v1.json`](../config/rq1_modeling_input_v1.json).

## 1. Status

| Item | Value |
|---|---|
| Determined cases | **4,944**: 4,940 `complete`, 4 `right_censored`; 0 failed, incomplete or not attempted |
| Split roles | **4,784 development** cases (194 semantic groups, all `complete`); **160 reserved RQ2** cases (5 rewrite groups: 156 complete, 4 right-censored) |
| Executions | 4,944 plain `EXPLAIN` estimates, 4,944 unmeasured probes, 14,828 measured runs. Each censored key stopped after two timeouts, by design |
| Full validation | **STOP at G-08**: no integrity check failed or warned, and gates G-01 to G-07 passed. No development case reaches 10 s; the four 10 s positives are all reserved RQ2 cases (`rw12`, original form, SF 1, C2) |
| Freeze | not run. `expanded-freeze` refuses a STOP release, and no `RELEASE_FROZEN.json` exists. This checkpoint does not change that |
| Next scoped modeling | runtime regression and the 1 s high-runtime classification, on development cases only ([rq1_evaluation_protocol.md](rq1_evaluation_protocol.md)) |
| Not evaluable | 10 s classification stays `NOT_YET_EVALUABLE` (G-08) |
| Pending | final expanded model scores; LLM rewrite results (RQ2). The 25 manual rewrite pairs remain `NOT_YET_VERIFIED` |

### Release-scoped and development-scoped counts

The counts in the full validation report and in `derived_manifest.json` cover
the **whole release** (4,944 cases). The positive-support gates G-07 and G-08
count **development** cases only. Both are correct, and they reconcile as
follows (read from `labels.csv`):

| Quantity | Release (validation report) | Development (gates, RQ1) | Reserved RQ2 |
|---|---:|---:|---:|
| 1 s positives | 407 | 393 (106 groups; G-07 PASS) | 14 |
| 10 s positives | 4 | 0 (G-08 STOP) | 4 (all right-censored `rw12` original-form cases) |

The historical validation report and the input record are left unchanged.

## 2. Source checkpoint

| Item | Value |
|---|---|
| Commit | `972c00bbb19f63a8f9b658ad471837089248276e` ("checkpoint: preserve expanded dataset collection implementation"), parent `cdfde49` |
| Role | the **audited source checkpoint that reproduces the derivation**. Rebuilding in memory at this commit from the packaged raw evidence and manifest reproduces all seven derived files byte-for-byte. The September 23 poster-smoke release also rebuilds byte-for-byte, so the shared derivation code did not regress |
| What it is not | a collection-time record. The commit was created on 2026-10-21, after collection. The raw `session_start` records store `package_version` 0.1.0 only, with no commit or source hash |
| Evidence for the link | two checks: the committed `config/expanded_release_v1.yaml` has the SHA-256 that the manifest recorded at collection, and the derivation reproduces exactly. A supporting observation, not proof: the file-system modification timestamps of the 36 committed files are all before 2026-10-03 20:32 local, which is before the manifest was built. Timestamps can be altered by copying or syncing, and they do not identify the exact source that ran during collection |
| Tests | the 2026-10-21 audit ran `pytest -q` on the source this commit preserves: **239 passed**, 0 failed, 0 skipped |

## 3. Release identities

| Identity | Value |
|---|---|
| Release name / protocol version | `expanded-v1` / `expanded-v1` |
| Manifest id | `mf_a75c6408fb59ab8f5265ee95404ae8d25419d1244b29c51210f8e013c3591899` (recomputes from the manifest body) |
| Label version / feature version | `expanded-label-v1` / `expanded-features-v1` |
| Derived inputs SHA-256 | `003c5455636b88436b0cd5d04a6fba67132981136cdfb3b645762f306fcf7b3a` (canonical JSON of `derived_manifest.json#inputs`) |
| Derived release id | `expanded:a75c6408fb59ab8f:expanded-label-v1:003c5455636b8843` |
| Frozen thresholds | 1,000 ms and 10,000 ms |
| Statement timeout | 15,000 ms; censoring bound = timeout - max observed planning time - 100 ms |

## 4. Hashes (SHA-256)

Paths are relative to the extracted evidence package (`derived/`, `manifest/`,
`raw/`, `reports/`), to the repository, or to `QCP_DATA_ROOT`, as labelled.

| File | SHA-256 |
|---|---|
| Archive `expanded_release.zip` (34 entries, identical to the extracted package) | `f4bc90bc74e02579ece9cbbdafb4b9b792f655d90742de143d5a5f1d60e0291a` |
| `derived/keys.csv` | `7e326e62933f7a9ada667d9ef51bbaf7ae5bf5d15496f6bca44a3d3b19c5558e` |
| `derived/executions.csv` | `6071de3e9341d1d597efbb208f2ddb562a58a67c0e6e15530ff3cd4a0868a916` |
| `derived/labels.csv` | `b6c59966520db901bbaadfb07c580df3b2fde20cf6b02ab692200d8236c24da3` |
| `derived/estimates.csv` | `474190139c27127fc6d0d550358ed0a1e3c8395cb12288dfc9b7c434254680c3` |
| `derived/features.csv` | `0504033443fa38f98216bf969f62338489febe4b9e6fdceaf62c008f9b6b3201` |
| `derived/splits.csv` | `0ccc262fc26e8862aadad7b5418fdf11057e7fdc2b0b50520ffce0b98bf68769` |
| `derived/derived_manifest.json` | `dbca951e7960e99f2ef60ccda46327132a80880f92bf5661760d84ffc1d8391d` |
| `manifest/manifest_a75c6408fb59ab8f.json` | `ea709574ba7b088c2c75ff6e784f867c0bfd4c7af0eeaecd6122ca2594b3aaec` |
| `reports/validation_full_a75c6408fb59ab8f.json` | `0489c2bb51de585ff15bc6447efc59dd1fb4010fa6024bfe56cb79070acb425d` |
| 12 raw session files under `raw/expanded-v1/` | listed in `derived_manifest.json#inputs.raw_files` and in the modeling-input record; all match |
| Repository `config/expanded_release_v1.yaml` (workload specification) | `1fc68f72fbd9eeda624892cdf280510fb55e695d690f0e23945e518cc033a791` |
| Repository `config/feature_registry.yaml` | `8b87b11d69e53d7470083b0ad6345966c9e3e94ca966a3bfec499a04a21e643b` |
| `QCP_DATA_ROOT`: `manifests/expanded/approvals/FULL_COLLECTION_a75c6408fb59ab8f.json` (**not in the archive**) | `9112e8346475789d18f4cddcc2253985570ce60ab7eaf16fa95abb8ee6db1100` |

The workload specification is pinned by the manifest. Editing it, even a
comment, breaks that link. A changed design needs a new spec version.

## 5. Audit findings (2026-10-21)

* The archive hash matches. Its contents equal the extracted package, with no
  unsafe paths and no CRC errors. The copies under `QCP_DATA_ROOT` are
  byte-identical to the package.
* All 29,708 raw records pass their checksums, with no duplicate or conflicting
  runs, no error records and no interruptions (12 sessions: one 110-key sample,
  then 11 batches).
* An independent recomputation, which did not use the project package,
  reproduces every label, censoring bound and 1 s / 10 s class. Keys, labels,
  features, estimates and splits each cover exactly the 4,944 manifest keys.
* RQ2 separation: no reserved case has a fold or is in the sample. No semantic
  group, SQL hash, literal fingerprint or structure family is shared with the
  development cases.
* Features: 69 registered A-D features (23 A, 1 B, 1 C, 44 D). None comes from
  labels, splits, identities or execution measurements. Estimates are plain
  `EXPLAIN (SETTINGS, SUMMARY ON, FORMAT JSON)`, and the plain plan shape equals
  the executed shape for all 4,940 comparable keys.
* The approval record is intact and links to the packaged plan-check,
  sample-validation and budget reports (approved 2026-10-04 02:55:34 UTC with a
  5 h budget).

## 6. Environments

| | Historical collection (2026-10-04 UTC) | Audited environment (2026-10-21) |
|---|---|---|
| Source revision | **not recorded**: collection ran from an uncommitted working tree, and no commit or source hash was stored. `972c00b` was created afterwards; it reproduces the derivation but is not proof of the exact source that collected | commit `972c00b` (audited checkpoint that reproduces the derivation) |
| OS | `Windows-10-10.0.26200-SP0` (as recorded by Python on Windows 11 Home) | Windows 11 Home 10.0.26300 |
| Python | 3.11.9 (recorded) | 3.11.9 |
| Package | `query-cost-predictor` 0.1.0 (recorded) | 0.1.0 |
| Third-party libraries | **not recorded** | numpy 2.4.6, scipy 1.17.1, scikit-learn 1.9.1, pandas 3.0.6, matplotlib 3.11.2, PyYAML 6.0.3, psycopg 3.2.13, pytest 9.1.1 |
| PostgreSQL | 16.15 (Debian 16.15-1.pgdg13+2), image `postgres:16` @ `sha256:a3b7f434b2dc57ce85a67e171163eb8ab1a1ebcb39d27484661f26b1dfbe30d6` (recorded in the manifest) | not started during the audit |
| Machine state at preflight | one observation at preflight (2026-10-04 01:56 UTC), not a measurement during collection: 15.71 GB RAM with **1.13 GB free** (readiness check R-04 warned); Docker Desktop 7.61 GB; Intel Core i9-14900HX (32 logical processors). The bench container's CPU and memory limits come from the git-ignored `.env` and are not recorded in the package | - |

The audited versions show what reproduces the derivation now. They are not
evidence of what was installed during collection.

## 7. Known provenance limitations

1. No commit or source hash was recorded during collection (section 2).
2. Third-party library versions at collection time were not recorded, and
   `pyproject.toml` sets only lower bounds. There is no lock file.
3. The full-collection approval record is in `QCP_DATA_ROOT` but not in the
   archive.
4. Snapshot provenance in the manifest is limited to snapshot ids, statistics
   hashes and capture times. The data-generator version, its seed and the row
   counts are only in the evidence database (`qcp.benchmark_snapshot`) and in
   the git-ignored load reports.
5. Free memory was low at the preflight (1.13 GB). That is a single
   observation before collection; memory was not monitored during collection,
   so its effect on timing noise is unknown. The median repetition CV of
   development cases is 0.046 (p90 0.136). The bench container's CPU and memory
   limits are not recorded in the package.
6. The raw `session_start` records contain the machine's host name, and four
   packaged reports contain absolute local paths. Raw records cannot be edited
   without breaking their checksums.
7. TPC terms for redistributing TPC-H-derived query text have not been
   confirmed. The data are derived from TPC-H; they are not TPC-H results.
