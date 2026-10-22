# RQ1 run guide

How to run the RQ1 v1 study ([`config/rq1_modeling_v1.yaml`](../config/rq1_modeling_v1.yaml)) and check what
it produced. Methodology: [rq1_evaluation_protocol.md](rq1_evaluation_protocol.md). Design:
[rq1_modeling_implementation_plan.md](rq1_modeling_implementation_plan.md).

> **Status: the official real-data study is pending.** It runs once, after the reviewed implementation is
> committed. Only a run whose status is `COMPLETE` counts as the RQ1 v1 study.

## Prerequisites

* **Offline.** No Docker, database or network is needed. The run reads only the extracted evidence package
  and the repository.
* **Environment.** Use the project virtual environment, with scikit-learn 1.9.1 as pinned in
  `requirements/rq1_constraints_v1.txt`. An evidence run refuses any other version.
* **Clean, committed source.**
  - no tracked modifications;
  - no untracked files except `reference_docs/` and `reference_pilot/README.md`;
  - no git-ignored files under `src/`, `config/` or `scripts/` (other than `__pycache__/` and `*.egg-info/`).
* **Evidence.** The extracted evidence package, for example `C:\qcp_share\expanded_release`. The original
  archive `C:\qcp_share\expanded_release.zip` is optional; when supplied, its SHA-256 is verified.
* **Data root.** `QCP_DATA_ROOT`, set in `.env`: an existing directory outside the repository.
  - It must be short. A planned output path longer than 259 characters is refused before anything is
    created.
  - The **output destinations** must not overlap a protected input. That means neither being inside one
    nor containing one, checked on resolved paths. The destinations are:
    - the run directory, under `QCP_DATA_ROOT\results\rq1\`;
    - the report copy, under `reports\rq1\`.

    The protected inputs are the evidence directory, the archive, the repository (for the run directory)
    and the supplementary approval record.
  - Sibling directories under `QCP_DATA_ROOT` are allowed. The approval record normally lives in
    `QCP_DATA_ROOT\manifests\expanded\approvals\`, next to `QCP_DATA_ROOT\results\`, which is fine.
  - The approval record is optional:
    - absent: recorded as `absent`, and the run continues;
    - present with a different hash: the run stops.

## Commands (PowerShell, from the repository root)

```powershell
# 1. Read-only input check: writes no run directory. Reports data checks and evidence-run eligibility separately.
.\scripts\powershell\09_rq1_modeling.ps1 -Step CheckInputs -EvidenceDir C:\qcp_share\expanded_release -Archive C:\qcp_share\expanded_release.zip

# 2. The single evidence-producing run (all five stages).
.\scripts\powershell\09_rq1_modeling.ps1 -Step Run -EvidenceDir C:\qcp_share\expanded_release -Archive C:\qcp_share\expanded_release.zip -RunLabel rq1-v1
```

The equivalent direct command is
`.\.venv\Scripts\python.exe -m query_cost_predictor rq1-run --evidence-dir <dir> --archive <zip> --run-label <label>`.

## Outputs

* **Run directory:** `QCP_DATA_ROOT\results\rq1\rq1-modeling-v1\<run_id>\`, where
  `run_id = <UTC time>_<config SHA-256, 16 characters>_<HEAD, 12 characters>`.
  - Lifecycle: `RUN_STATUS.json` and `output_manifest.json` (the SHA-256 of every other file).
  - `run_record.json`: provenance, environment and thread limit.
  - `input_check.json`, `fold_membership.csv`.
  - `oof_regression.csv`, `oof_classification.csv`.
  - `metrics_regression.{csv,json}`, `metrics_classification.{csv,json}`, `calibration_bins.csv`.
  - `bootstrap_groups.csv`, `bootstrap_indices.npz`, `bootstrap_replicates.csv`, `bootstrap_summary.json`.
  - `diagnostics_noise.json`, `diagnostics_threshold.json`.
  - `figures/`: nine PNG files plus `figure_index.json`.
  - `handoff/`: eight `.joblib` refits and `handoff_manifest.json`, labelled
    `REFIT_ON_ALL_DEVELOPMENT_NO_EVALUATION`. They are never scored.
  - `report.md`.
* **Convenience copy:** `reports\rq1\<run_id>\report.md`, with `figures\` beside it so the relative links
  work. It is git-ignored, and it is written only after the completed run has been verified.

## Exit codes and failures

| Code | Meaning |
|---|---|
| 0 | `COMPLETE`, verified against `output_manifest.json`, and the report copy succeeded |
| 2 | No evidence directory was given: neither `--evidence-dir` nor `QCP_EXPANDED_RELEASE_DIR` is set. Nothing was checked and no run directory was created |
| 3 | Refused before any run directory was created. Causes: an incomplete pipeline, ineligible source, or an unsafe or over-long output location |
| 4 | `COMPLETE` and verified, but the convenience copy failed. The run directory is unchanged and remains the study output; copy `report.md` and `figures\` by hand if needed |
| 1 | An error. Either the run `FAILED` (its `RUN_STATUS.json` says so), or setup failed before any run directory existed, for example an invalid `QCP_DATA_ROOT` or a changed input record. A given evidence path that does not exist fails the run at its `input_check` stage. Read the console message |

**After a failure**, `RUN_STATUS.json` records:
- `FAILED`;
- the failed stage and the last completed stage;
- the error.

Any `report.md` or `output_manifest.json` is renamed `*.FAILED`, so a failed directory never looks like a
study. Typical causes:
- an input hash or count mismatch (stage `input_check`);
- a `ConvergenceWarning` during any fit;
- an invalid model output;
- invalid measured runs (stage `diagnostics`);
- a missing required output or provenance field (stage `finalization`).

A failed run directory is evidence of the failure only. Fix the cause, then start a new run; runs are never
overwritten or resumed.

## Verifying a completed run

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; from query_cost_predictor.rq1_run import read_complete_run; m = read_complete_run(Path(r'<run directory>')); print(len(m['files']), 'files verified')"
```

`read_complete_run` refuses anything but `COMPLETE`. It then re-verifies `output_manifest.json` against the
SHA-256 recorded at completion, and every file against the manifest.

Also check that `run_record.json` shows:
- `source_tracked_dirty: false`;
- the expected `source_revision`;
- `production_inventory_checked: true`.
