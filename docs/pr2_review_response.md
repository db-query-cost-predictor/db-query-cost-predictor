# PR #2 review response

PR #2 is [Expanded Dataset Pipeline and RQ1 Evaluation](https://github.com/db-query-cost-predictor/db-query-cost-predictor/pull/2).
This page responds to Tushar's review.

**Status of this follow-up:** written on 2026-10-26 on top of `c32a247`, and
**not yet committed**.

**Merge scope (decided 2026-10-26):** external documents (the poster, the
proposal, the Overview PDF and the slides) are out of scope for PR #2. The
poster is already completed, and updates to the others are deferred. None of
them is a prerequisite for merging. Nothing has been staged, committed, pushed or merged.
The branch `feat/expanded-query-dataset` was pushed, and PR #2 was open and
unmerged, when checked on 2026-10-26.

> **Source of the review points.** The review text was not readable from the
> environment that prepared this patch. GitHub's public API listed no reviews,
> review comments or conversation comments on PR #2 when checked on
> 2026-10-26, so the review may have been delivered another way. The eight
> points and two housekeeping comments below follow the review summary that
> came with the change request. The numbering is ours. Please check that it
> matches the review.

## Summary of changes

| Kind | Files |
|---|---|
| **Code (new, standalone)** | `scripts/python/extract_handoff_archive.py`: a standard-library archive verifier and extractor, independent of the modeling pipeline |
| **Tests (new)** | `tests/unit/test_extract_handoff_archive.py`: 45 tests on synthetic archives |
| **Documentation (new)** | `docs/teammate_setup_posix.md`, `docs/proposal_poster_update_notes.md`, this file |
| **Documentation (changed)** | `README.md`, `docs/rq1_results_v1.md`, `docs/teammate_setup.md`, `docs/team_handoff.md`, `docs/handoff_assets_v1.json` |
| **Unchanged** | all modeling code, configuration and metric implementations; the research questions, splits, labels, features, thresholds and calibration; both official ZIP files; the official run outputs and models; the six frozen files (hashes below) |

No existing code path changed behaviour. The only executable addition is the
standalone extraction helper.

## Review points

### R1. Planner-cost features

* **Action.** We now state, consistently, what the study uses and tests:
  - `est_total_cost_log` and `est_startup_cost_log` stay learned-model
    features. Plain-`EXPLAIN` costs are known before execution.
  - Post-execution outcomes are excluded as inputs.
  - RQ1 tests whether more pre-execution features and learned relationships
    improve on calibrated cost-only baselines. It is not an experiment that
    excludes planner information.
* **Files.**
  - `docs/rq1_results_v1.md`, section 3 ("Planner-cost information is a
    permitted input");
  - `README.md`, "Prediction inputs and runtime labels";
  - `docs/proposal_poster_update_notes.md`, section 1.
* **Evidence.**
  - **Repository docs:** a search of every editable, non-frozen document
    found no "plan cost is never a feature" wording.
  - **`reference_docs/`, read only:**
    - The Research and Implementation Plan treats PostgreSQL total cost as a
      "Baseline feature and baseline predictor".
    - The Forward Plan says to keep the optimizer's estimates as features.
    - The **Overview PDF** is the one source with conflicting wording ("Instead
      of relying on the database's built-in … cost estimate"; features from
      `EXPLAIN ANALYZE`). Optional replacement text is in the notes file.
* **Deferred; not a merge prerequisite.**
  - Updates to external documents (the Overview PDF, the proposal and any
    slides) are deferred by the 2026-10-26 project decision.
  - The poster is already completed, and it was neither inspected nor
    edited.
  - The wording in
    [proposal_poster_update_notes.md](proposal_poster_update_notes.md) is
    optional reference material for any future revision.

### R2. Pilot versus expanded results

* **Action.** A labelled historical comparison now sits beside the regression
  results.
  - **Pilot:** cost baseline 1.694 and HGB 1.794 median q-error, under
    template-grouped evaluation over 22 template groups.
  - **RQ1 v1, primary `fold_group`:** cost baseline 1.5161, ridge 1.3576, HGB
    1.1258.

  The page says that the direction of the finding changed. It also says the
  two are different workloads, feature and evidence conditions, and evaluation
  populations, so this is not a controlled before/after experiment.
* **Files.**
  - `docs/rq1_results_v1.md`, section 6, "Historical comparison with the
    pilot";
  - `docs/proposal_poster_update_notes.md`, section 2.
* **Evidence.**
  - The pilot figures are quoted from `docs/rq1_evaluation_protocol.md`, lines
    262–264, which is frozen and unchanged.
  - The RQ1 figures come from the official `metrics_regression.csv`.
  - The pilot was **not** recomputed in this patch, and the page says so.

### R3. Metric floor

* **Action.** The 0.001 ms floor is explained beside the metric definitions
  and beside Table 1. The page says the discrepancy comes from the metric
  definition, not from a model change or better predictions.
* **Files.** `docs/rq1_results_v1.md`, section 5 ("The 0.001 ms floor") and the
  note under Table 1; `docs/proposal_poster_update_notes.md`, section 3.
* **Evidence.** These were recomputed from the saved `oof_regression.csv`
  (read only), using the unrounded `pred_ln`:

  | Quantity | Value |
  |---|---|
  | `reg_cost_ols` predictions below 0.001 ms | **16** of 4,784, all in fold 4, for actual times of 0.015–0.030 ms |
  | `reg_cost_ols` floored log-MAE | **0.6027312922**, equal to the stored value and to `metrics.regression_metrics` |
  | `reg_cost_ols` direct, unfloored log-MAE | **0.6074873091** |
  | `reg_scale_cost_ols` | 16 below the floor; 0.6023475505 floored against 0.6071329660 direct |
  | Learned models and the constant baseline | 0 below the floor; identical under both definitions |
  | Median, p90 and p95 q-error | unchanged by the floor |

  The metric implementation, configuration and outputs are unchanged.

### R4. The unverifiable review attribution

* **Action.**
  - Section 15, "External review", is replaced by **"Verification and
    available evidence"**. For each re-runnable check it lists what it
    establishes and what it does not.
  - The saved artifacts available for inspection are listed.
  - The section states the limits: there is no packaged script that
    recomputes every metric, bootstrap interval or calibration bin, and some
    steps of the 2026-10-21 audit used code that is not in the repository.
  - The ChatGPT attribution is removed everywhere. It is not replaced by
    another reviewer's endorsement.
* **Files.**
  - `docs/rq1_results_v1.md`, section 15;
  - `docs/handoff_assets_v1.json`: `independent_review` replaced by
    `verification`;
  - `docs/team_handoff.md`, the "Complete" table;
  - `README.md`, the evidence-status table;
  - `docs/proposal_poster_update_notes.md`, section 4.
* **Evidence.**
  - A search of the repository documentation finds no remaining
    endorsement. The only mentions describe the removal: section 15's
    one-sentence note, the catalogue's `verification.summary`, this file, and
    the update notes.
  - The anchor `#15-external-review` became
    `#15-verification-and-available-evidence`; every link to it was updated,
    and the anchor checker passes.
* **Optional.**
  - The **PR #2 description** on GitHub still says "Independent review checked
    packaged evidence…". Suggested replacement text is in
    [Suggested PR description update](#suggested-pr-description-update);
    whether to apply it is the user's decision.
  - Wording in slides or other external documents is deferred (see R1).

### R5. macOS and Linux setup

* **Action.** We added `docs/teammate_setup_posix.md`, linked from the README,
  the Windows guide and the team handoff. It covers:
  - Git and Python 3.11;
  - quoted paths and a project virtual environment;
  - installation with `-c requirements/rq1_constraints_v1.txt`, used as
    constraints, not as `pip install -r`;
  - version checks;
  - obtaining the assets from the owner while distribution is pending;
  - hash and size checks, and extraction with the helper;
  - `QCP_DATA_ROOT`, `.env` and `QCP_EXPANDED_RELEASE_DIR`;
  - the `rq1-check-inputs` and `rq1-run` commands with their real flags;
  - run verification, and loading all eight models;
  - the identity and results comparisons, which reuse the Windows logic;
  - troubleshooting and acceptance.

  The database collection and RQ2 path is described separately as needing
  Docker and PostgreSQL.
* **Evidence.** What was and was not exercised, by platform:

  | Platform | Exercised | Not exercised |
  |---|---|---|
  | **Windows** (Windows 11, Python 3.11.9, recorded packages) | the guide's four Python heredoc bodies, taken from the file and run against the official run and the helper's extraction: verification 36 files, 8 models loaded, `IDENTITY CHECK: PASS` with 34 identities, `COMPARISON: PASS` | the install steps on a fresh machine |
  | **Linux** (WSL2 Ubuntu 26.04, system Python 3.14.4, GNU bash 5.3.9) | `sha256sum` on both archives; the helper on both real archives; `bash -n` on all 12 shell blocks | steps 3 and 7 to 10: the image has no Python 3.11 and no `ensurepip`/venv, and installing system packages was out of scope. **zsh syntax was not checked** (zsh not installed) |
  | **macOS** | nothing | everything |

  On the constraints file:
  - Its Windows-only `pywinpty` pin is left in place, not removed.
  - Some macOS- and Linux-only dependencies are not in the file, so pip will
    pick current versions for them; the guide says so.
  - No dependency installation was attempted on Linux, so no install failure
    was observed.
* **Pending.** A real fresh-machine run on macOS and on Linux with Python
  3.11.

### R6. Backslash ZIP entries

* **Action.** We added `scripts/python/extract_handoff_archive.py`:
  - Its CLI takes `--asset-id`, with values `expanded_release_dataset` or
    `rq1_v1_results`, plus `--archive` and `--destination`.
  - An optional `--catalogue` defaults to `docs/handoff_assets_v1.json`.
  - It reads the expected size and SHA-256 from the catalogue and verifies
    them before anything else.
  - It inspects every entry and checks every CRC before writing.
  - It turns backslash separators into directory separators.
  - It refuses:
    - an existing destination;
    - traversal and absolute, drive or UNC paths;
    - symbolic links and other special entries, and encrypted entries;
    - Windows-reserved names and trailing dots or spaces;
    - duplicates after case and Unicode normalization;
    - file/directory conflicts.
  - It writes with exclusive create, so it never overwrites or deletes.
  - It re-hashes the archive afterwards.
  - It exits 1 with a message on failure and 2 on usage errors.

  Both ZIP files are unchanged.
* **Files.**
  - the script and `tests/unit/test_extract_handoff_archive.py`;
  - a usage block in `docs/handoff_assets_v1.json` (`extraction_helper`);
  - `docs/teammate_setup.md` A5 (an alternative to `Expand-Archive`);
  - `docs/teammate_setup_posix.md`, step 5.
* **Evidence.**
  - **Unit tests:** 45 passed. They cover:
    - backslash and forward-slash archives, with byte-identical output;
    - directory entries and stored compression;
    - size mismatch and hash mismatch;
    - existing-destination refusal for an empty folder, a folder with data and
      a file, with the existing content unchanged;
    - a missing parent folder;
    - 21 unsafe path forms;
    - symbolic-link, FIFO and character-device entries;
    - four duplicate forms and four file/directory conflicts;
    - a CRC failure caught before writing;
    - an invalid asset id;
    - the repository catalogue's values for both archives.
  - **Real archives on Windows:** both PASS, 34 and 39 entries.
    - The output is byte-identical to the existing extracted evidence and to
      the official run directory.
    - `rq1-check-inputs` on the new extraction: data checks PASS, 43 checks,
      21 files, archive and approval verified.
    - `read_complete_run` on the new run folder: 36 files verified.
    - A re-run into the same folder is refused, and a wrong asset id is
      refused on size before any folder is created.
  - **Real archives on Linux** (Python 3.14.4):
    - the raw names keep their backslashes, for example
      `derived\\derived_manifest.json`, and the helper normalizes them;
    - both extractions match the Windows fingerprints byte for byte;
    - no extracted name contains a backslash;
    - raw-backslash traversal, UNC, drive and duplicate entries are refused
      before a folder is created.
  - The original extracted evidence was never modified. All temporary
    extractions were deleted.

### R7. Classification limitations

* **Action.** Beside the headline classification results, the page now states:
  - all 393 development positives at 1 s are at SF 1, and there are none at
    SF 0.1;
  - detection at SF 0.1 is therefore not established;
  - the scale factor is also an input feature, which limits generalization
    but is not, by itself, evidence of leakage;
  - the 160 RQ2 holdout cases remain excluded.

  The scale factor and the splits are unchanged.
* **Files.**
  - `docs/rq1_results_v1.md`: the summary bullet and section 7, "Scope of
    these classification results";
  - `README.md`, "High-runtime detection";
  - `docs/proposal_poster_update_notes.md`, section 3.
* **Evidence.** These were recomputed from the saved artifacts:
  - positives by snapshot: `tpch_sf0_1` 0 of 2,392, `tpch_sf1` 393 of 2,392;
  - split roles from `input_check.json`: 4,784 development and 160
    `heldout_rq2`.

### R8. Calibration and boundary errors

* **Action.** A compact "Calibration at a glance" table now sits beside the
  classification results. It shows the lowest and highest bins for HGB,
  logistic and cost logistic: the bin counts, mean predicted probability and
  observed positive fraction. It links to the full section 11.
  - It states that ranking performance does not establish reliable
    probabilities.
  - It reports the boundary concentration of false alarms.
  - No post-hoc calibration or threshold optimization was added.
* **Files.** `docs/rq1_results_v1.md`, section 7;
  `docs/proposal_poster_update_notes.md`, section 3.
* **Evidence.** These were re-verified from the saved `calibration_bins.csv`
  and the out-of-fold files (`fold_group`, pooled):

  | Item | Value |
  |---|---|
  | HGB lowest bin | 4,361 cases, 23 positives, mean 0.0009, observed 0.0053 |
  | HGB highest bin | 315 cases, 297 positives, mean 0.9928, observed 0.9429 |
  | HGB middle bins | 108 cases |
  | Logistic middle bins | 402 cases |
  | HGB false alarms in the 800–1,250 ms band | 27 of 29; band size 245 |
  | Logistic false alarms in the band | 27 of 29 |

### Additional: publication status

* **Action.** The stale "not committed / not pushed / PR not created" text is
  updated.
  - The handoff documentation commit is recorded as `c32a247b4bbd…`.
  - The branch is pushed, and PR #2 is open and **not merged** as of
    2026-10-26.
  - The merge, the asset distribution location and fresh-machine acceptance
    stay explicitly pending.
  - The official study revision `ad666623822a…` is unchanged.
  - This follow-up's own commit id is not recorded inside its contents.
  - The documents say that code on GitHub does not mean the ZIP files have
    been uploaded.
  - The Windows A2 default branch is now `feat/expanded-query-dataset`, with
    `main` after the merge.
* **Files.** `docs/handoff_assets_v1.json` (`repository`, `publication`);
  `docs/team_handoff.md` (sections 1 and 2); `docs/teammate_setup.md` (A2 and
  A4); `docs/teammate_setup_posix.md` (steps 2 and 4).
* **Evidence.**
  - Read-only GitHub API checks: PR #2 head was `c32a247…`, state open,
    `merged: false`.
  - The changed A2 block was re-run from the document against local clones:
    - the default branch completes;
    - a missing branch stops at the switch;
    - a branch without `ad66662` stops at the ancestry check;
    - an existing folder stops at the clone.

## Housekeeping

### H1. Pilot data file

* **Action.** None in this patch, as requested. The tracked pilot files under
  `reference_pilot/output/` are not new in PR #2: none of PR #2's 76 changed
  files is under `reference_pilot/`. The largest are `query_runs.jsonl`
  (7,015,334 bytes) and `query_dataset.csv` (183,577 bytes). We recorded a
  separate cleanup item in `docs/team_handoff.md`, under Remaining.
* **Dependency checks needed before any migration:**
  1. `src/query_cost_predictor/pilot_audit.py` and
     `reporting/pilot_figures.py` read these files. `cli.py` exposes
     `pilot-audit --pilot-dir`.
  2. `config/pilot_reported_claims.yaml` pins their SHA-256. Any
     re-encoding, or a line-ending change, breaks the pilot audit.
  3. `.gitattributes` turns off text conversion for `reference_pilot/**`.
     Git LFS or an external copy must preserve the bytes exactly.
  4. Other references, to be updated together:
     - `scripts/powershell/01_preflight.ps1` and `04_run_pilot_audit.ps1`;
     - `POSTER_RUNBOOK.md`;
     - `README.md`;
     - `docs/architecture.md`, `docs/poster_evidence_scope.md`,
       `docs/poster_claim_register.md` and `docs/final_dataset_plan.md`;
     - `tests/unit/test_rq1_provenance.py`.
  5. `paths.py` refuses writes inside `reference_pilot/`, and the RQ1
     source-cleanliness gate allows only `reference_pilot/README.md` as
     untracked. Removing tracked files from that folder changes what a clean
     checkout looks like.
  6. Any history rewrite to drop the blobs would change commit ids, including
     the study revision `ad66662`. Do not rewrite history; only stop tracking
     going forward, if at all.

### H2. PR scope and history

* **Action.** None. We kept the reviewed commits intact. No history was
  rewritten, and the PR was not split retrospectively. This follow-up adds
  only new commits on top of `c32a247`, made by the user.

## Validation

All commands were run on 2026-10-26 on Windows 11, using:
- Windows PowerShell 5.1.26100;
- Python 3.11.9, scikit-learn 1.9.1, numpy 2.4.6, pandas 3.0.6, joblib 1.6.0
  and pytest 9.1.1;
- git 2.53.0.

The Linux items used WSL2 Ubuntu 26.04 with Python 3.14.4.

| Check | Command | Outcome |
|---|---|---|
| Baseline before the patch, at HEAD `c32a247` | `.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider` | **568 passed**, 0 skipped, exit 0 (159 s) |
| Focused tests | the same, on `test_extract_handoff_archive.py`, `test_rq1_inputs.py`, `test_rq1_run.py`, `test_rq1_safety.py`, `test_rq1_handoff.py`, `test_rq1_provenance.py` and `test_rq1_no_database.py` | **179 passed**, 0 skipped (45 + 48 + 13 + 54 + 5 + 10 + 4) |
| Full default suite after the final code changes | `.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider` (`pyproject` addopts `-m "not integration"`) | **613 passed**, 0 skipped, exit 0 (147 s) |
| Whitespace | `git --no-pager diff --check` | exit 0, no output |
| JSON | the link checker's JSON parse of `docs/handoff_assets_v1.json` | valid |
| Links and anchors | the link checker on the eight changed or new documents, plus `docs/rq1_run_guide.md` and `docs/rq1_modeling_implementation_plan.md` | every relative link and section anchor resolves; no BOM, CRLF or trailing whitespace; PowerShell and Python code blocks are ASCII-only |
| Shell blocks of the macOS/Linux guide | `bash -n` on each of its 12 bash blocks (WSL, GNU bash 5.3.9), plus a scan for inline `#` comments outside heredocs | all pass; zsh not available, so **not checked** |
| Frozen files | SHA-256 before and after | identical (table below) |
| Archives, official run directory (38 files), extracted official evidence (34 files) | SHA-256 per file, before and after | identical |
| Evidence-run eligibility | `rq1-check-inputs` | **data checks PASS**, but **`NOT_ELIGIBLE`**, because this patch's files are untracked. That is expected until it is committed. No evidence-producing run was attempted |

**Test counts as separate observations:**
- An earlier implementation-stage run recorded **563 passed**.
- The reviewer reported **565 passed / 3 skipped**.
- The pre-patch baseline on this Windows machine was **568 passed, 0 skipped**.
- After this patch it is **613 passed, 0 skipped**.

565 + 3 = 568, which is consistent with three platform-conditional tests
skipping in the reviewer's environment. Several tests skip off Windows or
without junction or symlink privileges. That reading is an inference; the
reviewer's environment was not inspected.

**Frozen files (SHA-256, unchanged):**

| File | SHA-256 |
|---|---|
| `config/rq1_modeling_v1.yaml` | `5db1608ab5d0bb6949d3d225d84853ea65c07e8964c8b157754e48333bf45ea0` |
| `config/rq1_modeling_input_v1.json` | `7a5e47a5ff7533b147b47e449e5263575df6a10a64d507dba8e99c3d32320453` |
| `config/expanded_release_v1.yaml` | `1fc68f72fbd9eeda624892cdf280510fb55e695d690f0e23945e518cc033a791` |
| `config/feature_registry.yaml` | `8b87b11d69e53d7470083b0ad6345966c9e3e94ca966a3bfec499a04a21e643b` |
| `docs/rq1_evaluation_protocol.md` | `07b11c5340e0d3211b589f1f13f486ece39de2fcad14211f739787571be1931f` |
| `requirements/rq1_constraints_v1.txt` | `63280e58b35fa8f22263172bcc590e1971feadea9f59c93d848ef1f4338a2ea4` |

**Archives (unchanged):**

| File | Bytes | SHA-256 |
|---|---:|---|
| `expanded_release.zip` | 18966990 | `f4bc90bc…e0291a` |
| `rq1_results_review_20261022_100326_043.zip` | 13692737 | `e8271dbe…c7490` |

Running the tests created `scripts/python/__pycache__/`. It is git-ignored,
and the RQ1 eligibility gate allows `__pycache__` folders.

## Remaining steps and open items

**To complete PR #2.** These are the user's actions:
- review, stage, commit and push this follow-up;
- merge PR #2 with a merge commit.

Updating the PR #2 description with the suggested text below is optional.

**Deferred; not merge prerequisites.**
- **External documents:** the poster is already completed. Updates to the
  Overview PDF, the proposal and the slides are deferred.
  [proposal_poster_update_notes.md](proposal_poster_update_notes.md) keeps the
  optional reference wording.

**Open items this patch does not change.** They are tracked in
[team_handoff.md](team_handoff.md).
- **Distribution:** no download location for the two assets is recorded yet.
- **Fresh-machine acceptance:** none has been done on Windows, on Linux with
  Python 3.11, or on macOS.
  - Full modeling setup on Linux and macOS (install, input check, run
    verification, model loading, reproduction) **remains untested**.
  - Only the steps listed under R5 were exercised.
- **Housekeeping H1:** a decision on the pilot data, in a separate change.

## Suggested PR description update

This replaces the "Validation" bullets in the PR #2 description. The user
edits the PR on GitHub.

> ## Validation
>
> - An implementation-stage Windows unit suite run recorded 563 passed. A
>   review run reported 565 passed / 3 skipped. On Windows, 568 passed before
>   the review follow-up and 613 passed with it (0 skipped).
> - The official study completed from source revision
>   `ad666623822a4c5baa911cc29e410ed0abc9c14e`; all 36 manifest-covered output
>   files verify against `output_manifest.json`.
> - Anyone with the two assets can repeat the checks: the SHA-256 checks of
>   both packages, `rq1-check-inputs` (pinned inputs), `read_complete_run`
>   (output manifest), the hand-off model hash checks, and the documented
>   reproduction comparison (`docs/rq1_results_v1.md`, section 15). These
>   confirm identity and integrity, not model quality by themselves.
> - Documentation checks pass: relative links, section anchors and JSON.
