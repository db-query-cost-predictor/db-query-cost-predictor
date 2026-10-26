# Teammate setup (Windows PowerShell)

This guide is for a teammate on a Windows machine. It assumes no particular user
name or folder layout: you choose every location once, in
[A1](#a1-choose-your-folders). The commands are written for Windows PowerShell
5.1, the version built into Windows. On macOS or Linux, use
[teammate_setup_posix.md](teammate_setup_posix.md) instead.

There are three different activities. Keep them apart:

| Activity | What you get | What it needs | Docker or PostgreSQL? |
|---|---|---|---|
| **1. Inspect the saved results** | the official RQ1 v1 outputs, verified, and the eight hand-off models loaded | Python environment and the results ZIP | no |
| **2. Reproduce the evaluation from saved data** | a new RQ1 run, computed by the committed code from the published dataset | Python environment, the dataset ZIP and a clean checkout | no |
| **3. Recollect database measurements** | new runtimes measured on *your* hardware. This is a new dataset, not the published one | Docker Desktop, PostgreSQL 16, TPC-H data and database passwords | **yes** |

[Part A](#part-a-offline-modeling-and-results) covers activities 1 and 2.
[Part B](#part-b-database-collection-and-rq2-execution) covers activity 3 and
RQ2 execution.

> **What has been exercised.** These instructions were written from the
> repository and the saved artifacts. Three commands were run on the original
> machine, against the official run directory, in Windows PowerShell 5.1:
> - the A8 verification command;
> - the A8 model-check script;
> - the A9 identity check and results comparison, run on the official run
>   against itself, and on temporary synthetic copies with deliberate faults;
>   both checks reported those faults;
> - the A2 stop-on-failure block, run against a local clone of this
>   repository in place of the GitHub URL.
>
> On 2026-10-26, three more steps were run:
> - the extraction helper in A5, on both real archives;
> - the A7 input check, run on that extraction;
> - the A8 run verification, also on that extraction.
>
> No other step has been exercised for this guide. That includes cloning from
> GitHub, installation, download, `Expand-Archive`, and a reproduction run.
> **Nothing has yet been run on another machine.**
> Record your outcome with the
> [acceptance checklist](team_handoff.md#fresh-machine-acceptance-checklist).

Results and their meaning: [rq1_results_v1.md](rq1_results_v1.md). Who owns
what: [team_handoff.md](team_handoff.md). Assets and hashes:
[handoff_assets_v1.json](handoff_assets_v1.json).

---

## Part A: offline modeling and results

Docker is **not** required for Part A. Internet is needed only to clone the
repository, install Python packages and download the two ZIP files. After that,
every Part A step runs offline.

### A0. Prerequisites

* Windows 10 or 11, 64-bit, with Windows PowerShell 5.1.
* [Git for Windows](https://git-scm.com/download/win).
* **Python 3.11, 64-bit, with the `py` launcher.** The study environment used
  **Python 3.11.9**, from the python.org Windows installer, with
  **scikit-learn 1.9.1**. An evidence run refuses any other scikit-learn
  version.
* About 2 GB of free disk space: the extracted assets take about 246 MiB
  (204.3 MiB for the dataset and 42.0 MiB for the results), plus the virtual
  environment.
* **Not needed:** Docker, PostgreSQL or any database password.

### A1. Choose your folders

Paste this block into **every new PowerShell window**. Variables last only for
that window. Choose your own locations:
- keep the paths **short**: an RQ1 run refuses any output path longer than 259
  characters;
- keep them **outside OneDrive** or any other synced folder;
- keep the data folders **outside the repository**.

```powershell
$Repo     = 'C:\work\db-query-cost-predictor'  # Git checkout (example; choose your own)
$Assets   = 'C:\work\qcp_assets'               # the two ZIP files and their extracted copies
$Data     = 'C:\work\qcp_data'                 # QCP_DATA_ROOT, used by any new RQ1 run
$Python   = Join-Path $Repo '.venv\Scripts\python.exe'
$Evidence = Join-Path $Assets 'expanded_release'
$Results  = Join-Path $Assets 'rq1_results'
$RunDir   = Join-Path $Results '20261022T144433Z_5db1608ab5d0bb69_ad666623822a'
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
```

The last line allows the repository's `.ps1` scripts to run, in this window
only.

### A2. Get the source

**First, choose the branch.** Check the `publication` block in
[handoff_assets_v1.json](handoff_assets_v1.json), or ask the Data Engineer.

| Publication state | Branch to use |
|---|---|
| The pull request has been merged | `main` |
| Not merged yet, but `feat/expanded-query-dataset` has been pushed to GitHub | `feat/expanded-query-dataset` |
| Neither | none yet: the study source is not on GitHub, so wait for publication |

As of 2026-10-26, `feat/expanded-query-dataset` has been pushed, and PR #2 is
open but **not merged**. Use `feat/expanded-query-dataset` until the merge, and
`main` afterwards.

**Then run the block below, as one unit.** It is wrapped in `. { … }` so that
any `throw` ends the whole block. That holds even if PowerShell receives the
pasted lines one at a time. Set `$Branch` on its first line.

```powershell
. {
    $Branch = 'feat/expanded-query-dataset'   # use 'main' once PR #2 has been merged
    git clone https://github.com/db-query-cost-predictor/db-query-cost-predictor.git $Repo
    if ($LASTEXITCODE -ne 0) { throw "STOP: git clone failed (exit $LASTEXITCODE). Nothing else was done." }
    Set-Location -LiteralPath $Repo
    git switch $Branch
    if ($LASTEXITCODE -ne 0) { throw "STOP: cannot switch to '$Branch'. It may not be published yet." }
    git merge-base --is-ancestor ad666623822a4c5baa911cc29e410ed0abc9c14e HEAD
    if ($LASTEXITCODE -ne 0) { throw "STOP: '$Branch' does not contain the study revision ad66662." }
    "OK: on $(git branch --show-current), study revision present"
    git status --short
}
```

* **Expected:** `OK: on <branch>, study revision present`, and nothing after it,
  because `git status --short` prints nothing on a fresh clone.
* **If the block stops with a `STOP:` message,** fix that cause before going on:
  - **clone failed:** check the network, or remove an existing `$Repo` folder.
  - **switch failed:** the branch is not published.
  - **ancestry failed:** the branch does not yet contain `ad66662`; for example,
    `main` before the merge.

  If you are unsure, ask the Data Engineer. Do not continue from a checkout
  that fails the ancestry check.
* **Re-running after a failed switch or ancestry check.** The clone already
  exists, so the clone step will now fail. Run only the lines from
  `Set-Location` on, or delete `$Repo` and run the block again.

### A3. Python environment

Run these from `$Repo`. The constraints file pins every package of the recorded
study environment.

```powershell
py -3.11 --version
py -3.11 -m venv .venv
& $Python -m pip install -c requirements\rq1_constraints_v1.txt pip
& $Python -m pip install -e ".[notebook,dev]" -c requirements\rq1_constraints_v1.txt
& $Python -m pip check
& $Python -c "import sys, sklearn, joblib; print(sys.version.split()[0], sklearn.__version__, joblib.__version__)"
& $Python -m query_cost_predictor env-check
```

Expected:
- `py -3.11 --version` prints `Python 3.11.x`; 3.11.9 matches the study.
- The third command pins pip to the recorded version (26.2.1).
- `pip check` reports no broken requirements.
- The version line reads `3.11.x 1.9.1 1.6.0`.
- `env-check` ends with `ENV-CHECK: PASS`. A warning that `.env` was not found
  is expected until [A6](#a6-minimal-machine-local-configuration).

Optional: `& $Python -m pytest -q` runs the unit tests offline. Tests that need
live databases are deselected by default.

### A4. Download and verify the two assets

Both files are distributed by the Data Engineer, and **distribution is still
pending**. Until a location is recorded in
[handoff_assets_v1.json](handoff_assets_v1.json)
(`publication.distribution_location`), obtain both files from the project
owner. The code being on GitHub does not mean these files are. Copy them into
`$Assets` **unchanged**: do not rename, re-zip or open-and-save them.

| File | Bytes | SHA-256 |
|---|---:|---|
| `expanded_release.zip` (dataset) | 18966990 | `f4bc90bc74e02579ece9cbbdafb4b9b792f655d90742de143d5a5f1d60e0291a` |
| `rq1_results_review_20261022_100326_043.zip` (official RQ1 v1 results) | 13692737 | `e8271dbe03a95eea51ac9d7bdd91b521ada3bb04caf5231f2da634ec849c7490` |

```powershell
New-Item -ItemType Directory -Force -Path $Assets | Out-Null
# Put both ZIP files in $Assets, then:
$expected = @(
    @{ Name = 'expanded_release.zip'; Bytes = 18966990; Sha256 = 'f4bc90bc74e02579ece9cbbdafb4b9b792f655d90742de143d5a5f1d60e0291a' },
    @{ Name = 'rq1_results_review_20261022_100326_043.zip'; Bytes = 13692737; Sha256 = 'e8271dbe03a95eea51ac9d7bdd91b521ada3bb04caf5231f2da634ec849c7490' }
)
foreach ($asset in $expected) {
    $path = Join-Path $Assets $asset.Name
    $bytes = (Get-Item -LiteralPath $path).Length
    $sha = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower()
    $ok = ($bytes -eq $asset.Bytes) -and ($sha -eq $asset.Sha256)
    '{0}: {1} bytes, {2} -> {3}' -f $asset.Name, $bytes, $sha, $(if ($ok) { 'OK' } else { 'MISMATCH: do not use' })
}
```

**Expected:** two lines ending in `OK`. If you get a mismatch:
- delete that copy and fetch it again;
- never use a file that mismatches, and never "repair" one.

### A5. Extract outside the repository

```powershell
Expand-Archive -LiteralPath (Join-Path $Assets 'expanded_release.zip') -DestinationPath $Evidence
Expand-Archive -LiteralPath (Join-Path $Assets 'rq1_results_review_20261022_100326_043.zip') -DestinationPath $Results
(Get-ChildItem -LiteralPath $Evidence -Recurse -File | Measure-Object).Count
Test-Path -LiteralPath (Join-Path $Evidence 'derived\features.csv')
(Get-ChildItem -LiteralPath $RunDir -Recurse -File | Measure-Object).Count
```

Expected: `34`, `True`, `38`.

**Alternative: the repository's extraction helper.** It verifies the size and
SHA-256 again, extracts into a new folder, and refuses unsafe entries. It was
exercised on both real archives on 2026-10-26. Use it after A3, in place of
the two `Expand-Archive` lines:

```powershell
& $Python scripts\python\extract_handoff_archive.py --asset-id expanded_release_dataset --archive (Join-Path $Assets 'expanded_release.zip') --destination $Evidence
& $Python scripts\python\extract_handoff_archive.py --asset-id rq1_v1_results --archive (Join-Path $Assets 'rq1_results_review_20261022_100326_043.zip') --destination $Results
```

Each call ends with `EXTRACT-HANDOFF-ARCHIVE: PASS`.

**How the archives are laid out**
* The dataset ZIP has **no top-level folder**. It holds `derived\`,
  `manifest\`, `raw\expanded-v1\` and `reports\` directly, so it must go into
  its own folder (`$Evidence`).
* The results ZIP holds one run-id folder, plus a short PowerShell transcript
  (`rq1_official_run_20261022_094429_762.log`) at its root. The transcript is
  not part of the run directory.
* The entry names use Windows backslash separators. Extract with
  `Expand-Archive` on Windows.

**Rules for the extracted folders**
* Never extract into the repository.
* Never extract the dataset into `$Data` itself.
* Treat both extracted folders as **read-only**. Do not add, rename or
  open-and-save any file in them. The run verification in A8 rejects any extra
  file, such as an editor backup.

### A6. Minimal machine-local configuration

```powershell
New-Item -ItemType Directory -Force -Path $Data | Out-Null
Set-Content -LiteralPath (Join-Path $Repo '.env') -Value "QCP_DATA_ROOT=$Data" -Encoding UTF8
Get-Content -LiteralPath (Join-Path $Repo '.env')
```

* **What goes in `.env`.** Only `QCP_DATA_ROOT`, with **no database
  credentials**. Do not copy the password lines from `.env.example` for Part A.
  `.env` is git-ignored; never commit it.
* **`QCP_DATA_ROOT` rules.** It must be an absolute, existing folder outside
  the repository. The input check does not need it; a reproduction run (A9)
  does.
* **The supplementary approval record (optional).**
  - A run looks for the collection approval record at
    `$Data\manifests\expanded\approvals\FULL_COLLECTION_a75c6408fb59ab8f.json`.
    It is **not** in either ZIP.
  - **Missing is allowed**: it is recorded as `absent`.
  - **A present file with a different hash blocks** the input check and any
    run. Never create a placeholder there.

### A7. Validate the inputs (read-only)

```powershell
.\scripts\powershell\09_rq1_modeling.ps1 -Step CheckInputs -EvidenceDir $Evidence -Archive (Join-Path $Assets 'expanded_release.zip')
"exit code: $LASTEXITCODE"
```

This command writes no run directory. It prints a JSON summary, then three
status lines. Expected values:

| Field | Expected |
|---|---|
| `data_checks` | `PASS` |
| `development_dataset` | `development_cases` 4784, `semantic_groups` 194, `query_instances` 1292, `learned_features` 69, `positives_1s` 393, `negatives_1s` 4391, `median_ms_min` 0.011, `median_ms_max` 6906.413 |
| `per_fold.fold_group` | 960, 960, 960, 960 and 944 keys |
| `required_packaged_files_verified` | 21 |
| `archive` | `verified_this_run` |
| `supplementary_approval` | `absent` on a new machine (allowed), or `verified_this_run` |
| `original_release_validation` | `STOP`, stop code `G-08` |
| `evidence_run_eligibility` | `ELIGIBLE` on a clean checkout with scikit-learn 1.9.1 |

The last lines should be:

```text
Original release validation: STOP (G-08) - preserved, not PASS
Evidence-run eligibility: ELIGIBLE
RQ1-CHECK-INPUTS: PASS (data checks only; this command writes no run directory)
exit code: 0
```

The STOP (G-08) line is **expected**: it is the release's recorded status, kept
as is. `NOT_ELIGIBLE` blocks only a new run (A9); inspection (A8) still works.

### A8. Open and verify the completed results

**Step 1. Verify the run directory against its manifest.** Run this from
`$Repo`:

```powershell
$verify = @'
import json
import sys
from pathlib import Path

from query_cost_predictor.rq1_run import read_complete_run

run_dir = Path(sys.argv[1])
manifest = read_complete_run(run_dir)
record = json.loads((run_dir / "run_record.json").read_text(encoding="utf-8"))
print(len(manifest["files"]), "files verified against output_manifest.json")
for key in ("run_id", "run_label", "source_revision", "source_tracked_dirty",
            "production_inventory_checked", "config_sha256", "input_record_sha256"):
    print(f"{key}: {record[key]}")
print("completed_stages:", ", ".join(record["completed_stages"]))
validation = record["original_release_validation"]
print("original_release_validation:", validation["status"], ", ".join(validation["stop_codes"]))
versions = record["dependency_versions"]
print("python / scikit-learn:", versions["python"], "/", versions["scikit-learn"])
'@
$verify | & $Python - $RunDir
(Get-FileHash -LiteralPath (Join-Path $RunDir 'output_manifest.json') -Algorithm SHA256).Hash.ToLower()
```

Expected:

```text
36 files verified against output_manifest.json
run_id: 20261022T144433Z_5db1608ab5d0bb69_ad666623822a
run_label: rq1-v1-official
source_revision: ad666623822a4c5baa911cc29e410ed0abc9c14e
source_tracked_dirty: False
production_inventory_checked: True
config_sha256: 5db1608ab5d0bb6949d3d225d84853ea65c07e8964c8b157754e48333bf45ea0
input_record_sha256: 7a5e47a5ff7533b147b47e449e5263575df6a10a64d507dba8e99c3d32320453
completed_stages: input_check, regression, classification_1s, diagnostics, handoff
original_release_validation: STOP G-08
python / scikit-learn: 3.11.9 / 1.9.1
e7c2ba2bb2f964d3995ba0d154fc3a581f0bf62741566c4ccce2bc9f34a9b4a3
```

`read_complete_run` refuses anything that is not `COMPLETE`. It then checks
`output_manifest.json` against the hash in `RUN_STATUS.json`, and every file
against the manifest.

**Step 2. Read the report.** Open `report.md` in `$RunDir` with a Markdown
viewer, for example the VS Code preview. Its figure links are relative to the
run directory. Do not save the file. The plain-language summary is
[rq1_results_v1.md](rq1_results_v1.md).

**Step 3 (optional). Check and load the hand-off models.** The `.joblib` files
are Python pickles, so **load them only after this hash check passes**, and
never load a model file from an unverified source.

```powershell
$models = @'
import hashlib
import json
import sys
from pathlib import Path

import joblib

run_dir = Path(sys.argv[1])
manifest = json.loads((run_dir / "handoff" / "handoff_manifest.json").read_text(encoding="utf-8"))
print("label:", manifest["label"], "| scored:", manifest["scored"])
for model in manifest["models"]:
    path = run_dir / model["file"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != model["sha256"]:
        raise SystemExit(f"STOP: {model['file']} does not match handoff_manifest.json; do not load it")
    estimator = joblib.load(path)
    print(f"{model['model_id']}: {type(estimator).__name__}, {estimator.n_features_in_} inputs, "
          f"{model['method']} -> {model['output_units']}")
'@
$models | & $Python - $RunDir
```

Expected: `label: REFIT_ON_ALL_DEVELOPMENT_NO_EVALUATION | scored: False`,
followed by eight lines. For example:
- `reg_cost_ols: LinearRegression, 1 inputs, predict -> ln(ms)`
- `clf_hgb_all: HistGradientBoostingClassifier, 69 inputs, predict_proba -> probability in [0, 1]`

These models were never scored. No reported result comes from them. Their
input/output contract is in
[team_handoff.md](team_handoff.md#model-inputoutput-contract).

### A9. Optional: reproduce the study from clean committed source

This recomputes the RQ1 evaluation from the published dataset, with the
committed code. It touches no database and needs no network. It never replaces
or edits the official run: it creates a separate run with its own id.

**Preconditions:**
- A3 to A7 passed, with `Evidence-run eligibility: ELIGIBLE`;
- `git status --short` prints nothing. Keep notes and outputs **outside** the
  checkout.

To match the study revision exactly, run it from the study commit:

```powershell
git switch --detach ad666623822a4c5baa911cc29e410ed0abc9c14e
git status --short
.\scripts\powershell\09_rq1_modeling.ps1 -Step Run -EvidenceDir $Evidence -Archive (Join-Path $Assets 'expanded_release.zip') -RunLabel rq1-v1-repro
"exit code: $LASTEXITCODE"
git switch -
```

**Expected.**
- `RQ1-RUN: COMPLETE (verified against output_manifest.json)`, then a
  `report copy:` line, and exit code 0.
- The official run took 46 seconds on the original laptop; allow a few
  minutes.
- The final `git switch -` returns you to your branch.

**Where the outputs go:**
- the run directory, `$Data\results\rq1\rq1-modeling-v1\<UTC time>_5db1608ab5d0bb69_<HEAD, 12 characters>\`.
  From the study commit, the HEAD part is `ad666623822a`;
- a convenience copy, `$Repo\reports\rq1\<run id>\report.md`, with
  `figures\`. It is git-ignored.

**Compare with the official run.** There are two checks. Both read the two
run directories and change nothing. Set `$NewRunDir` to the path printed after
`run directory:`.

**Check 1: the inputs must be the same.** The reproduction must use exactly
the same frozen inputs as the official run.

What must be **equal** to the official `run_record.json`:

| Requirement | Fields compared |
|---|---|
| Frozen configuration | `config_sha256` (`5db1608a…`) |
| Input record | `input_record_sha256` and the `input_record` identity (`7a5e47a5…`) |
| Required packaged evidence | the SHA-256 of all 21 `evidence_files.*` identities: 7 derived files, the manifest, the full validation report and 12 raw files |
| Pinned repository files | the SHA-256 of both `repository_files.*` identities: the workload specification and the feature registry |
| Original archive | `original_archive.sha256` (`f4bc90bc…`), with provenance `verified_this_run`. This needs `-Archive` |
| Release identities | every `release_identity.*` value, plus the historical `source_checkpoint.commit_sha` |
| Original release status | `original_release_validation`: STOP, `G-08` |

**The supplementary approval record** (`supplementary.full_collection_approval`)
is optional.
* **Absent:** this is normal on a teammate's machine. The run records
  provenance `absent` and continues. Write the absence down in the acceptance
  record, then go on.
* **Present with the pinned SHA-256 (`9112e834…`):** recorded as
  `verified_this_run`.
* **Present with a different SHA-256:** the run must stop. The input check
  raises a provenance mismatch, so the run ends `FAILED` at `input_check`, and
  no metrics are produced. Remove the wrong file, or obtain the original, then
  start a new run.

**Expected to differ, and not compared:**
- the run id and run label;
- every timestamp and the status history;
- the machine and platform details;
- the full package list and thread-pool library details;
- the observed untracked paths;
- whether the optional approval record was present.

The source revision is printed for the record. From the study commit (A9) it
is `ad666623822a…`.

```powershell
$NewRunDir = 'PASTE THE PRINTED RUN DIRECTORY HERE'
$identities = @'
import json
import sys
from pathlib import Path

PINNED_APPROVAL = "9112e8346475789d18f4cddcc2253985570ce60ab7eaf16fa95abb8ee6db1100"
FROZEN_PREFIXES = ("input_record", "evidence_files.", "repository_files.", "release_identity.",
                   "original_archive.", "source_checkpoint.")
official = json.loads((Path(sys.argv[1]) / "run_record.json").read_text(encoding="utf-8"))
repro = json.loads((Path(sys.argv[2]) / "run_record.json").read_text(encoding="utf-8"))
problems = []
for key in ("config_sha256", "input_record_sha256", "original_release_validation"):
    if official.get(key) != repro.get(key):
        problems.append(f"{key}: official {official.get(key)!r}, reproduction {repro.get(key)!r}")
o = {i["item"]: i for i in official["input_identities"]}
r = {i["item"]: i for i in repro["input_identities"]}
frozen_o = {k for k in o if k.startswith(FROZEN_PREFIXES)}
frozen_r = {k for k in r if k.startswith(FROZEN_PREFIXES)}
for item in sorted(frozen_o ^ frozen_r):
    problems.append(f"{item}: present in only one run")
for item in sorted(frozen_o & frozen_r):
    for field in ("sha256", "value"):
        if o[item].get(field) != r[item].get(field):
            problems.append(f"{item}: {field} differs")
    if o[item]["provenance"] == "verified_this_run" and r[item]["provenance"] != "verified_this_run":
        problems.append(f"{item}: provenance {r[item]['provenance']} (expected verified_this_run)")
evidence = sum(k.startswith("evidence_files.") for k in frozen_r)
if evidence != 21:
    problems.append(f"{evidence} evidence_files identities (expected 21)")
approval = r.get("supplementary.full_collection_approval")
if approval is None:
    problems.append("supplementary approval identity missing from the run record")
elif approval["provenance"] == "absent":
    print("supplementary approval: absent on this machine (allowed; record it in the acceptance notes)")
elif approval["provenance"] == "verified_this_run" and approval.get("sha256") == PINNED_APPROVAL:
    print("supplementary approval: present and verified")
else:
    problems.append(f"supplementary approval present but not verified: {approval}")
print("source_revision:", repro.get("source_revision"), "| source_tracked_dirty:", repro.get("source_tracked_dirty"))
print(f"frozen identities compared: {len(frozen_o & frozen_r)}")
if problems:
    print("IDENTITY CHECK: FAIL")
    for line in problems:
        print("  " + line)
    sys.exit(1)
print("IDENTITY CHECK: PASS")
'@
$identities | & $Python - $RunDir $NewRunDir
if ($LASTEXITCODE -ne 0) { Write-Host "STOP: identity check failed (exit $LASTEXITCODE). Do not compare or use the reproduction; investigate." }
```

Expected: `frozen identities compared: 34`, then `IDENTITY CHECK: PASS`. The
34 identities are the input record, 21 evidence files, 2 repository files, 8
release identities, the archive and the source checkpoint. The
approval line reads either `absent` or `present and verified`.

**Check 2: the results must agree.** The script compares three tables between
the two runs, row by row and key by key:
- `metrics_regression.csv`;
- `metrics_classification.csv`;
- `calibration_bins.csv`.

**What counts as a failure**

| Kind of check | Fails when |
|---|---|
| Columns and keys | the column lists differ; a key is duplicated in either run; a key is missing from either run |
| Descriptor fields, compared as exact text | any difference in a field that is neither a key nor a numeric field. In the metric tables these are `n_keys`, `n_groups`, `keys_per_group`, `n_query_instances`, `n_positive`, `n_positive_groups`, `n_contributing_positive_folds`, `null_reason` and `flag`. In the calibration table they are `bin_lower`, `bin_upper`, `n_cases` and `n_positive` |
| Numeric fields | a value is defined in one run and empty (null) in the other; a value is not a finite number; two values differ beyond the tolerance |

The numeric fields are `value`, `ci_lower` and `ci_upper` in the metric
tables, and `mean_predicted_probability` and `observed_positive_fraction` in
the calibration table.

**Tolerance.** Two numbers *a* and *b* agree when
`math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)`. That is floating-point
rounding level, and it is set at the top of the script.

```powershell
$compare = @'
import math
import sys
from pathlib import Path

import pandas as pd

REL_TOL = 1e-9
ABS_TOL = 1e-9
METRIC_KEYS = ["task", "fold_scheme", "model_id", "scope", "fold", "stratum_column", "stratum_value", "metric"]
TABLES = {
    "metrics_regression.csv": (METRIC_KEYS, ["value", "ci_lower", "ci_upper"]),
    "metrics_classification.csv": (METRIC_KEYS, ["value", "ci_lower", "ci_upper"]),
    "calibration_bins.csv": (["fold_scheme", "model_id", "scope", "fold", "stratum_column", "stratum_value",
                              "bin_index"], ["mean_predicted_probability", "observed_positive_fraction"]),
}


def finite(text):
    try:
        x = float(text)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


official, repro = Path(sys.argv[1]), Path(sys.argv[2])
failures = []
for name, (keys, numeric) in TABLES.items():
    a = pd.read_csv(official / name, dtype=str, keep_default_na=False)
    b = pd.read_csv(repro / name, dtype=str, keep_default_na=False)
    if list(a.columns) != list(b.columns):
        failures.append(f"{name}: column lists differ")
        continue
    for label, table in (("official", a), ("reproduction", b)):
        dups = int(table.duplicated(keys).sum())
        if dups:
            failures.append(f"{name}: {dups} duplicate keys in the {label} run")
    m = a.merge(b, on=keys, how="outer", suffixes=("_o", "_r"), indicator=True)
    for side, label in (("left_only", "missing from the reproduction"), ("right_only", "missing from the official run")):
        n = int((m["_merge"] == side).sum())
        if n:
            failures.append(f"{name}: {n} keys {label}")
    both = m[m["_merge"] == "both"]
    for col in [c for c in a.columns if c not in keys and c not in numeric]:
        n = int((both[col + "_o"] != both[col + "_r"]).sum())
        if n:
            failures.append(f"{name}: {n} rows differ in {col}")
    worst = 0.0
    for col in numeric:
        null_mismatch = invalid = beyond = 0
        for x_text, y_text in zip(both[col + "_o"], both[col + "_r"]):
            if x_text == "" and y_text == "":
                continue
            if (x_text == "") != (y_text == ""):
                null_mismatch += 1
                continue
            x, y = finite(x_text), finite(y_text)
            if x is None or y is None:
                invalid += 1
                continue
            worst = max(worst, abs(x - y))
            if not math.isclose(x, y, rel_tol=REL_TOL, abs_tol=ABS_TOL):
                beyond += 1
        for count, what in ((null_mismatch, "defined-versus-null mismatches"),
                            (invalid, "invalid or nonfinite values"),
                            (beyond, "differences beyond tolerance")):
            if count:
                failures.append(f"{name}: {count} {what} in {col}")
    print(f"{name}: {len(a)} official rows, {len(b)} reproduction rows, {len(both)} matched; "
          f"largest numeric difference {worst:.3g}")
if failures:
    print("COMPARISON: FAIL")
    for line in failures:
        print("  " + line)
    sys.exit(1)
print(f"COMPARISON: PASS (rel_tol {REL_TOL}, abs_tol {ABS_TOL})")
'@
$compare | & $Python - $RunDir $NewRunDir
if ($LASTEXITCODE -ne 0) { Write-Host "STOP: comparison failed (exit $LASTEXITCODE). Investigate before using any reproduced number." }
```

Expected:

```text
metrics_regression.csv: 3240 official rows, 3240 reproduction rows, 3240 matched; largest numeric difference 0
metrics_classification.csv: 2592 official rows, 2592 reproduction rows, 2592 matched; largest numeric difference 0
calibration_bins.csv: 2700 official rows, 2700 reproduction rows, 2700 matched; largest numeric difference 0
COMPARISON: PASS (rel_tol 1e-09, abs_tol 1e-09)
```

On a different machine, the largest difference may be above 0 while still
within tolerance.

**If either check fails** (exit code 1 and a `FAIL` list):
* The reproduction does not yet agree with the official run, and the
  difference needs investigation. Bit-identical gradient-boosting results on
  different hardware are not guaranteed, even with the one-thread limit.
* Send the complete output, plus the new run's `run_record.json`, to the Data
  Scientist.
* A failed comparison **never** authorizes replacing, editing or "correcting"
  the official results, and no reproduced value may be copied into them. The
  official run stays the study output.

### A10. Interpreting failures

| Symptom | Likely cause | What to do |
|---|---|---|
| `py -3.11` is not found | Python 3.11 or the launcher is not installed | install 64-bit Python 3.11.9 from python.org, with the launcher |
| pip cannot satisfy the constraints | a different Python minor version, or 32-bit Python | use 64-bit Python 3.11 |
| "running scripts is disabled on this system" | the execution policy | run the `Set-ExecutionPolicy -Scope Process …` line from A1 |
| `ERROR: Python interpreter not found at …` | `.venv` is missing from `$Repo` | redo A3 from `$Repo` |
| `ERROR: cannot read .env` (exit 2) | a malformed `.env` line | recreate `.env` with A6 |
| A ZIP shows `MISMATCH` | an incomplete or altered copy | delete it and fetch it again |
| Input check fails at `H-02` (files missing) | wrong `-EvidenceDir`, or a nested extraction folder | pass the folder that directly contains `derived\`, `manifest\`, `raw\` and `reports\` |
| Input check fails at `H-03` or `H-05` | the extracted files or the archive differ from the pinned hashes | re-verify the ZIP (A4) and extract into a new empty folder |
| Input check fails at `H-00` or `H-04` | repository config files were edited | run `git status`; restore the files or clone afresh |
| Provenance mismatch for the approval record | a different file sits at the approval path under `QCP_DATA_ROOT` | remove that file (it is optional) or obtain the original |
| `Evidence-run eligibility: NOT_ELIGIBLE (…)` | uncommitted edits, extra untracked files, another scikit-learn version, or the package installed from elsewhere | fix the listed blockers. Inspection (A8) still works |
| `rq1-run` exit 3, `REFUSED` | source not eligible, or an unsafe or over-long output location | nothing was created. Fix the cause and run again |
| `rq1-run` exit 1, `RUN_STATUS.json` says `FAILED` | a stage failed: a hash or count mismatch, a `ConvergenceWarning`, or an invalid output | keep the failed directory as evidence. Fix the cause, then start a new run (runs are never resumed) |
| `rq1-run` exit 4 | the run is `COMPLETE`, but the report copy failed | the run directory is the result |
| A8 says "files in the run directory differ" | a file was added inside the run directory | extract the results ZIP again into a new folder |
| A8 says "… not COMPLETE" | `$RunDir` points at the wrong folder | point it at the run-id folder inside `$Results` |

See [rq1_run_guide.md](rq1_run_guide.md) for exit codes and output
locations in detail.

---

## Part B: database collection and RQ2 execution

Part B is needed only to **execute SQL and measure runtimes**:
- collecting new measurements;
- verifying rewrite pairs on a live snapshot;
- timing paired speedups for RQ2.

None of Part A depends on it.

### B1. Requirements

* **Docker Desktop** with the WSL2 backend and Docker Compose. It runs two
  PostgreSQL 16 containers on `127.0.0.1` only (`bench-db` and `evidence-db`).
* **A full `.env`** created from `.env.example`. Set `QCP_DATA_ROOT`, and
  replace all six `CHANGE_ME` passwords with letters and digits only, at least
  16 characters each. Never commit `.env`.
* **Disk and location.** `QCP_DATA_ROOT` must be outside OneDrive, with at
  least 20 GB free. The readiness check refuses otherwise (R-01 and R-02).
* **Internet**, for the first build of the TPC-H tools image. It clones a
  pinned `tpch-dbgen` commit. Confirm the TPC license terms first.
* **Memory.** The bench container defaults to 4 GB. Low free memory adds timing
  noise.

### B2. Runbooks to follow

| Goal | Where |
|---|---|
| Environment, preflight, start and initialize the databases, load TPC-H SF 0.1 and SF 1 | [POSTER_RUNBOOK.md](../POSTER_RUNBOOK.md), steps 1–6c |
| Readiness for expanded-style collection (preflight, migration V008, both snapshots, disk) | [EXPANDED_DATASET_RUNBOOK.md](../EXPANDED_DATASET_RUNBOOK.md), stage 1 |
| Manifest, sample, approval, batched collection, derive, validate | [EXPANDED_DATASET_RUNBOOK.md](../EXPANDED_DATASET_RUNBOOK.md), stages 2–7 |
| Manual reference-rewrite verification | [POSTER_RUNBOOK.md](../POSTER_RUNBOOK.md), step 11, and [rq2_verification_protocol.md](rq2_verification_protocol.md) |
| Stop the databases (volumes kept) | [POSTER_RUNBOOK.md](../POSTER_RUNBOOK.md), step 14 |

Never run `docker compose down --volumes`: it deletes the evidence database.

### B3. What new measurements mean

* **Fresh hardware gives new measurements.** A different machine, Docker
  configuration, OS build or background load gives different runtimes. They
  are never byte-identical to the published ones, and some labels near 1 s may
  differ.
* **A recollection is a new dataset.** It gets its own manifest and raw
  evidence. It is never merged into `expanded-v1`, never substituted for it and
  never used to relabel it. The published RQ1 v1 results stay tied to
  `expanded-v1`.
* **The reserved cases stay reserved.** The 160 reserved RQ2 cases (5 rewrite
  groups) remain reserved in any new work.

### B4. Do not start long collections automatically

* The full expanded collection was a multi-hour job. It had an explicitly
  approved 5-hour budget and ran in batches capped at 30 minutes.
* A full collection always needs the explicit `ApproveFull` step. Run batches
  by hand and inspect each one.
* Do not wrap collection in unattended loops or scheduled tasks.
* Coordinate with the Data Engineer before any collection. Keep other heavy
  work off the machine while it measures.
