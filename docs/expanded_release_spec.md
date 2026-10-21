# Expanded release v1: specification

Status: **design, collected.** This page is the design as specified before
collection; sections 2-8 are unchanged plans and rules. The machine-readable
source is [`config/expanded_release_v1.yaml`](../config/expanded_release_v1.yaml)
(SHA-256 `1fc68f72…`, pinned by the manifest; never edited).
`python -m query_cost_predictor expanded-arithmetic` recomputes every count below
from that file, and the unit tests pin them. **A manifest with 4,944 entries is not
4,944 labels.** A key counts only when the full validation report shows it as
`complete` or `right_censored`.

**Collected outcome (2026-10-04 UTC, audited 2026-10-21):** all 4,944 proposed
keys were determined: 4,940 `complete` and 4 `right_censored`, with 4,784
development cases and 160 reserved RQ2 cases. Full validation is **STOP at
G-08** (no development case at or above 10 s), so the release is not frozen and
the 10 s classifier is `NOT_YET_EVALUABLE`. Gates G-01 to G-07 passed. See
[expanded_collection_checkpoint.md](expanded_collection_checkpoint.md).

## 1. Identities

| Unit | Definition |
|---|---|
| semantic group | one query structure; held out as a whole (all parameterizations, snapshots, configurations, rewrite forms and index variants) |
| structure family | coarser unit used by the stricter split (a join path with all of its output shapes) |
| template | one SQL text with placeholders |
| query instance | template + explicit parameters |
| modeling key | instance + snapshot + configuration + cache protocol (`warm`) + protocol version (`expanded-v1`) |
| probe | one unmeasured execution per key (`repeat_no = 0`), never a label |
| measured repetitions | three per key (`repeat_no = 1..3`), children of the key, never extra keys |

## 2. Arithmetic (proposed keys)

| Component | Groups | Templates | Instances | Snapshots x configurations | Keys | Keys per template |
|---|---:|---:|---:|---|---:|---:|
| TPC-H-derived | 22 | 22 | 172 (21 x 8 + Q18 x 4) | 2 x 2 | 688 | 32 (Q18: 16) |
| Generated structural | 128 (16 paths x 8 shapes) | 256 (2 index variants) | 768 (x 3 regimes) | 2 x 2 | 3,072 | 12 |
| Point/range probes | 24 | 24 | 192 (x 8) | 2 x 1 (C1) | 384 | 16 |
| Manual rewrite pairs | 25 (5 RQ2-reserved) | 50 (2 forms) | 200 (x 4) | 2 x 2 | 800 | 16 |
| **Total** | **199** | **352** | **1,332** | | **4,944** | max 32 (0.65 %) |

| Cut | Keys |
|---|---|
| By snapshot | SF 0.1: 2,472, SF 1: 2,472 |
| By configuration | C1 baseline: 2,664, C2 reduced `work_mem`: 2,280 |
| Structural by regime | R1 narrow (3 days): 1,024, R2 medium (90 days): 1,024, R3 wide (1,200 days): 1,024 |
| Structural by index variant | indexed `l_shipdate`: 1,536, unindexed `l_receiptdate`: 1,536 |
| By split role | development: 4,784 (194 groups), RQ2-reserved holdout: 160 (5 groups) |
| Structure families | 87 (22 TPC-H, 16 join paths, 24 probes, 25 rewrite pairs) |
| Largest structure family | one join path: 8 groups x 24 = 192 keys (3.9 %) |
| Planned executions | 4,944 plain EXPLAINs, 4,944 probes, 14,832 measured runs |

Worst-case time if every run hit the 15 s timeout: 61.8 h for measured runs,
82.4 h with probes (the reference figure for 4,704 keys and measured runs only
is 58.8 h). Early stopping (a key is right-censored after two timeouts) and the
mostly short runs make the realistic time much smaller. Only the sample
projection (section 7) gives a defensible number.

## 3. Fast and slow regimes, and the factors kept separate

| Factor | How it varies | Where it is held fixed (for paired analysis) |
|---|---|---|
| Data scale | SF 0.1 versus SF 1 (registered snapshots; row counts checked) | same query instance and configuration |
| Selectivity | regimes R1/R2/R3 (date-range length) in structural groups; spec domains elsewhere | same group, index variant, snapshot, configuration |
| Index availability | `l_shipdate` (indexed) versus `l_receiptdate` (unindexed), same distribution shifted by 1-30 days | same group, regime, snapshot, configuration |
| Plan choice | consequence of the above; recorded as the plain plan (class D) and the executed plan (class E) | measured, never assigned |
| `work_mem` | C1 (4 MB default) versus C2 (256 kB) | same instance and snapshot |

`expanded-eda` reports matched-pair ratios for each factor (complete keys only),
so a runtime change is not attributed to the wrong factor. Probes and the
smallest regime supply the fast regime. Wide regimes, multi-way joins, sorts and
aggregations at SF 1 supply the candidates for the slow regime. These are
hypotheses, never assumed labels.

## 4. Splits

| Split | Unit kept together | Use |
|---|---|---|
| `fold_group` (primary) | semantic group | unseen-structure evaluation (RQ1) |
| `fold_family` (stricter) | structure family | robustness check: are near-structures leaking? |
| `fold_seen_template` | query instance | **seen-template diagnostic only**; templates appear in training and test; never reported as unseen-structure performance |
| `heldout_rq2` | 5 rewrite groups (160 keys) | never used for RQ1 training, preprocessing, calibration or tuning, and never sampled |

All folds are 5-fold, seeded (`splits.seed`), computed once at manifest time and
stored in the manifest, so every model uses identical folds. No two semantic
groups share a literal-insensitive SQL fingerprint (a unit test checks this).
Some groups still share an access path (for example, orders date-range scans).
This residual similarity is why the stricter family split is reported alongside.

## 5. Observations and labels

| Observation status | Meaning | Usable? | Runtime value |
|---|---|---|---|
| `complete` | three measured runs ended, at least two completed | yes | median of measured runs (a timed-out run sorts last) |
| `right_censored` | at least two measured runs timed out | yes, as a lower bound | NULL; lower bound = execution-time bound |
| `failed` | a probe or measured run ended in an error | no | NULL; `failure_class` records why |
| `incomplete` | started, label not yet determined (interruption) | no | NULL; the next session resumes it |
| `not_attempted` | no evidence yet | no | NULL |

Censoring bound: the raw record keeps the statement-level bound (= 15,000 ms).
The label uses `15,000 - max(observed planning time) - 100 ms`, because the
statement timeout also counts parsing and planning (see
[next_phase_audit.md](next_phase_audit.md), section 8).

Frozen high-runtime definitions (never changed to obtain positives):

| Class at threshold T (1,000 ms and 10,000 ms) | Rule |
|---|---|
| positive | complete with median >= T, or right-censored with bound >= T |
| negative | complete with median < T |
| indeterminate | right-censored with bound < T (never imputed) |
| unlabeled | failed, incomplete or not attempted (a failure is never a positive) |

Failure classes: `sql_error`, `resource_limit` (for example `temp_file_limit`),
`lock_timeout`, `watchdog_cancel`, `protocol_error`, `other_error`.

## 6. Gates

| Gate | Requirement | On failure |
|---|---|---|
| G-01 | every key determined (no incomplete or not-attempted keys) | STOP: run more batches |
| G-02 | usable keys >= 4,704 | STOP: report; do not pad |
| G-03 | development usable keys >= 4,000 | STOP |
| G-04 | >= 150 development groups with usable keys on **both** snapshots | STOP |
| G-05 / G-06 | no template > 2 % and no structure family > 10 % of usable keys | STOP |
| G-07 / G-08 | at 1 s and at 10 s: >= 10 positive development keys from >= 5 groups and >= 2 components | STOP: that classifier is `NOT_YET_EVALUABLE`; do not train, report or claim it |
| sample | 0 SQL errors; failed share <= 5 %; every sample key determined | FAIL: fix before approval |

Integrity checks (manifest hash, raw checksums, database equals raw,
byte-identical rebuild, label recomputation, settings, contract, censoring and
failure rules) must pass in both scopes. `expanded-freeze` refuses anything
except a full PASS.

## 7. Bounded sample and budget

The sample takes 5 development keys per stratum (component x snapshot x
configuration, x regime for structural groups): 22 strata, 110 keys, ranked by
SHA-256 of `seed|modeling_key`. Worst case: 110 x 4 x 15 s, about 1.8 h. After
sample validation, `budget_projection_<manifest>.md` reports the expected,
p90-based and worst-case hours for the remaining keys, the raw bytes per key,
and the projected disk. `expanded-approve-full` refuses a budget below the
projected expected hours. The full collection then runs only in explicit
batches (`-MaxKeys`), which stop between keys at the batch, wall-clock or
free-disk bound.

## 8. Safety

Every instance is validated as a single read-only `SELECT`/`WITH` statement at
spec load, manifest and collection, and runs as `qcp_bench_reader` in a
`READ ONLY` transaction that is always rolled back. Every join is an FK
equi-join with an `ON` condition; there is no `pg_sleep`, no Cartesian product
and no unbounded statement. The 15 s statement timeout, a client watchdog and
`temp_file_limit` bound each execution.
