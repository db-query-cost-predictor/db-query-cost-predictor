# Final dataset plan

Four kinds of data must never be confused:

| Kind | Status | Size | Where it lives |
|---|---|---|---|
| **Collected pilot data** | historical, collected by the team; recomputed by `pilot-audit` (`RECOMPUTED_PILOT`) | 220 query instances, 22 templates, 660 measured runs, SF 0.1 (values as recorded in the pilot files; the audit re-checks them) | `reference_pilot/output/` (read-only) |
| **Fresh poster-smoke data** | collected only when you run the runbook (`NEW_POSTER_SMOKE`) | 20–50 modeling keys, three measured runs each | `QCP_DATA_ROOT` + evidence database |
| **Planned development data** | `PLANNED` — nothing collected | approximately 4,544 modeling keys | — |
| **Planned held-out rewrite families** | `PLANNED` — nothing collected | approximately 160 modeling keys (5 anti-pattern families) | — |

## Planned composition (arithmetic, not observations)

Computed from [`config/final_study_plan.yaml`](../config/final_study_plan.yaml) by
`python -m query_cost_predictor final-plan`. These totals are planning
arithmetic; they are not facts until a manifest-generation program for the
final study expands and validates them.

| Component | Groups | Variants | Snapshots × configurations × forms | Planned keys |
|---|---:|---:|---|---:|
| TPC-H controlled templates | 22 | 12 (Q18: 4) | 2 × 2 × 1 | about 1,024 |
| Audited published-workload groups (e.g. STATS-CEB; source and license to be confirmed) | 60 | 8 | 1 × 2 × 1 | about 960 |
| Generated structural families | 50 | 12 | 1 × 2 × 1 | about 1,200 |
| Point/range probe families (not an OLTP benchmark) | 30 | 12 | 1 × 2 × 1 | about 720 |
| Anti-pattern/rewrite families (5 held out) | 25 | 8 | 1 × 2 × 2 | about 800 (about 160 held out) |
| **Total** | **about 187** | | | **approximately 4,704** |

* Approximately **4,704** total planned modeling keys.
* Approximately **4,544** development keys.
* Approximately **160** completely held-out rewrite/evaluation keys (never used
  for training, preprocessing fitting, calibration or hyperparameter selection).
* At least **150** structural/semantic groups (about 187 planned).
* Repeated executions are stored separately as child records and are **not**
  counted as additional modeling keys.
* Q18 has only four in-spec threshold values, so 12 distinct Q18 variants are
  impossible; the plan uses 4.

## Acceptance gates for the final dataset (planned)

* At least 4,000 labelled (complete or right-censored) development keys; 4,500–5,000 preferred.
* At least 150 independent structural/semantic groups; about 180+ literal-insensitive fingerprints preferred.
* At least two snapshots and two configurations.
* Timeouts retained as right-censored observations; no synthetic padding or duplicated rows.
* At least two keys from different groups and different cost mechanisms that
  either complete in ≥ 10 s or reach the timeout (60 s planned).
* The validator distinguishes planned, attempted, completed, censored and
  failed keys, and reports keys, groups and keys per group.

## Not yet decided (explicitly deferred)

Published-workload source and license (STATS-CEB and/or JOB/IMDb), the C2
`work_mem` value, runtime-bin boundaries and the recommendation threshold are
frozen only after calibration.
