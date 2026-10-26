# Teammate setup (macOS and Linux, offline RQ1)

This guide is the macOS and Linux counterpart of Part A of
[teammate_setup.md](teammate_setup.md), the Windows guide. It covers four
offline activities:
- inspecting and verifying the official RQ1 v1 results;
- loading the eight hand-off models;
- validating the dataset;
- optionally, reproducing the evaluation and comparing it with the official
  run.

**No Docker or PostgreSQL is needed.** Fresh query collection and RQ2
execution do need a database; see
[Database collection and RQ2](#database-collection-and-rq2).

Results and their meaning: [rq1_results_v1.md](rq1_results_v1.md). Owners
and acceptance: [team_handoff.md](team_handoff.md). Asset hashes and
publication status: [handoff_assets_v1.json](handoff_assets_v1.json).

> **What has been exercised, and where** (2026-10-26)
>
> - **Windows** (Windows 11, Python 3.11.9 with the recorded environment):
>   - the extraction helper on both real archives; the extracted files are
>     byte-identical to the official extracted evidence and run directory;
>   - the input check on that extraction;
>   - the run verification;
>   - the Python bodies of steps 8 to 10 of this guide, taken from this file
>     and run against the official run, the run-against-itself comparison
>     included.
> - **Linux** (WSL2 Ubuntu 26.04 on the same laptop, its system Python
>   3.14.4):
>   - `sha256sum` on both archives;
>   - the extraction helper on both real archives, again byte-identical;
>   - the helper's refusals of unsafe raw-backslash entries;
>   - a `bash -n` syntax check of every shell block on this page.
> - **Not exercised on Linux:** steps 3 and 7 to 10. That image has no
>   Python 3.11 and cannot create virtual environments, and installing system
>   packages was out of scope.
> - **macOS:** nothing has been exercised. Treat the macOS notes as untested.
>
> Do not read this as full cross-platform reproduction. Record your own result
> with the [acceptance checklist](team_handoff.md#fresh-machine-acceptance-checklist).

## 0. Prerequisites

* **Git**, with a git that supports `git switch` (2.23 or later).
* **Python 3.11, 64-bit, with `venv`.** The study used **3.11.9** with
  **scikit-learn 1.9.1**, and an evidence run refuses any other scikit-learn
  version. Ways to get it:
  - **macOS:** the python.org 3.11 installer, or Homebrew `python@3.11`.
  - **Debian or Ubuntu:** the `python3.11` and `python3.11-venv` packages. If
    your release does not ship 3.11, use a source such as the deadsnakes PPA
    or pyenv. For example, Ubuntu 26.04 under WSL had only Python 3.14,
    without `ensurepip`.
  - **Other distributions:** the distribution's `python3.11` package, or
    pyenv.
* **A SHA-256 tool:** `sha256sum` on Linux, `shasum` on macOS.
* **Disk space:** about 2 GB, most of it for the virtual environment.
* **Internet:** only for cloning, installing packages and downloading the two
  ZIP files. Every later step runs offline.

## 1. Choose your folders

**Where to put things.**
- Keep every location outside any synced folder: iCloud Drive, OneDrive,
  Dropbox.
- Keep the data folders outside the repository.
- Paths may contain spaces; every command below quotes them.

**Which shell.** The blocks work in bash and zsh, the macOS default. They
contain no inline `#` comments, because interactive zsh does not treat `#` as a
comment unless `interactivecomments` is set.

Run this block **in each new terminal**. `QCP_WORK` is an example location;
choose your own. `QCP_DATA` becomes `QCP_DATA_ROOT` in step 6.

```bash
QCP_WORK="$HOME/qcp"
QCP_REPO="$QCP_WORK/db-query-cost-predictor"
QCP_ASSETS="$QCP_WORK/assets"
QCP_DATA="$QCP_WORK/data"
QCP_EVIDENCE="$QCP_ASSETS/expanded_release"
QCP_RESULTS="$QCP_ASSETS/rq1_results"
QCP_RUN_DIR="$QCP_RESULTS/20261022T144433Z_5db1608ab5d0bb69_ad666623822a"
PY="$QCP_REPO/.venv/bin/python"
qcp_sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
mkdir -p "$QCP_WORK"
```

`qcp_sha256` uses `sha256sum` on Linux and `shasum -a 256` on macOS.

## 2. Get the source

**Choose the branch first.**
- PR #2 has been pushed and is **open, not merged**. Until it is merged, use
  `feat/expanded-query-dataset`.
- After the merge, use `main`.
- Check the `publication` block in
  [handoff_assets_v1.json](handoff_assets_v1.json) for the current state.

The commands sit inside a function, so a failure stops the sequence with a
`STOP:` message without closing your terminal:

```bash
qcp_get_source() {
    local branch="${1:?usage: qcp_get_source <branch>}"
    git clone https://github.com/db-query-cost-predictor/db-query-cost-predictor.git "$QCP_REPO" \
        || { echo "STOP: git clone failed. Nothing else was done."; return 1; }
    cd "$QCP_REPO" || { echo "STOP: cannot enter $QCP_REPO"; return 1; }
    git switch "$branch" || { echo "STOP: cannot switch to '$branch'."; return 1; }
    git merge-base --is-ancestor ad666623822a4c5baa911cc29e410ed0abc9c14e HEAD \
        || { echo "STOP: '$branch' does not contain the study revision ad66662."; return 1; }
    echo "OK: on $(git branch --show-current), study revision present"
    git status --short
}
qcp_get_source feat/expanded-query-dataset
```

After the merge, call `qcp_get_source main` instead.

* **Expected:** `OK: on <branch>, study revision present`, and nothing after
  it.
* **If the clone step fails because `$QCP_REPO` already exists:** run the
  commands from `cd` onward by hand, or remove that folder first.

## 3. Python environment

Run these from `$QCP_REPO`.

```bash
cd "$QCP_REPO"
python3.11 --version
python3.11 -m venv .venv
"$PY" -m pip install -c requirements/rq1_constraints_v1.txt pip
"$PY" -m pip install -e ".[notebook,dev]" -c requirements/rq1_constraints_v1.txt
"$PY" -m pip check
"$PY" -c 'import sys, sklearn, joblib; print(sys.version.split()[0], sklearn.__version__, joblib.__version__)'
"$PY" -m query_cost_predictor env-check
```

Expected:
- `Python 3.11.x`;
- `pip check` reports no broken requirements;
- the version line reads `3.11.x 1.9.1 1.6.0`;
- `ENV-CHECK: PASS`. A missing-`.env` warning is normal until step 6.

**About the constraints file.**
- `requirements/rq1_constraints_v1.txt` is passed with `-c`. It **pins
  versions; it does not request packages**. `pip install -r` on it is the wrong
  command.
- It records the Windows study environment, so it contains a Windows-only pin
  (`pywinpty`). No package requests that pin on macOS or Linux, so it simply
  goes unused. Do not delete it.
- Some packages that pip installs only on macOS or Linux are not in that file,
  so pip picks a current compatible version for them. These are terminal and
  Jupyter support packages, for example `ptyprocess`, `pexpect` and, on macOS,
  `appnope`, not modeling libraries.
- If pip cannot install a pinned version on your platform, for example because
  no wheel exists for your processor:
  - **stop**;
  - save the complete error output and send it to the Data Scientist, with
    your OS, processor and Python version;
  - do not edit or loosen the pins.

## 4. Download and verify the two assets

The Data Engineer distributes the two files. **Distribution is still pending.**
Until a location is recorded in
[handoff_assets_v1.json](handoff_assets_v1.json) (`publication.distribution_location`),
get both files from the project owner. Code being on GitHub does **not** mean
these files are there. Put them in `$QCP_ASSETS`, **unchanged**.

| File | Bytes | SHA-256 |
|---|---:|---|
| `expanded_release.zip` (dataset) | 18966990 | `f4bc90bc74e02579ece9cbbdafb4b9b792f655d90742de143d5a5f1d60e0291a` |
| `rq1_results_review_20261022_100326_043.zip` (official RQ1 v1 results) | 13692737 | `e8271dbe03a95eea51ac9d7bdd91b521ada3bb04caf5231f2da634ec849c7490` |

```bash
mkdir -p "$QCP_ASSETS" && cd "$QCP_ASSETS"
wc -c expanded_release.zip rq1_results_review_20261022_100326_043.zip
qcp_sha256 -c - <<'EOF'
f4bc90bc74e02579ece9cbbdafb4b9b792f655d90742de143d5a5f1d60e0291a  expanded_release.zip
e8271dbe03a95eea51ac9d7bdd91b521ada3bb04caf5231f2da634ec849c7490  rq1_results_review_20261022_100326_043.zip
EOF
echo "hash check exit code: $?"
```

**Expected:** the byte counts above, then two `OK` lines, then exit code 0.
On any mismatch:
- delete that copy and fetch it again;
- never use a mismatching file.

## 5. Extract outside the repository

The ZIP entry names use **Windows backslash separators**. Some macOS and Linux
tools, `unzip` among them, would create files whose *names* contain
backslashes. Use the repository's extraction helper instead. It:
- checks the size and SHA-256 again against the catalogue;
- turns backslashes into directory separators;
- refuses unsafe or conflicting entries;
- writes into a **new** folder, refusing one that already exists;
- leaves the ZIP files unchanged.

```bash
cd "$QCP_REPO"
"$PY" scripts/python/extract_handoff_archive.py --asset-id expanded_release_dataset \
    --archive "$QCP_ASSETS/expanded_release.zip" --destination "$QCP_EVIDENCE"
"$PY" scripts/python/extract_handoff_archive.py --asset-id rq1_v1_results \
    --archive "$QCP_ASSETS/rq1_results_review_20261022_100326_043.zip" --destination "$QCP_RESULTS"
find "$QCP_EVIDENCE" -type f | wc -l
find "$QCP_RUN_DIR" -type f | wc -l
```

Each helper call ends with `EXTRACT-HANDOFF-ARCHIVE: PASS` and exit code 0.
The two counts are 34 and 38.
The helper uses only the standard library, so any Python 3.11 or later can run
it, including before step 3.

**Rules for the extracted folders**
* Treat both folders as **read-only**. Do not add, rename or open-and-save
  files in them; the run verification in step 8 rejects any extra file.
* On macOS, Finder can leave `.DS_Store` files in folders you browse. Inspect
  the run folder from the terminal. If an extra file appears, extract into a
  new folder.

## 6. Minimal machine-local configuration

```bash
mkdir -p "$QCP_DATA"
printf 'QCP_DATA_ROOT=%s\n' "$QCP_DATA" > "$QCP_REPO/.env"
cat "$QCP_REPO/.env"
```

* **`.env`** holds only `QCP_DATA_ROOT`, with no database credentials. It is
  git-ignored.
* **Instead of `.env`,** you can set the variable in your shell:
  `export QCP_DATA_ROOT="$QCP_DATA"`. An exported variable takes precedence
  over `.env`.
* **`QCP_DATA_ROOT` rules.** It must be an absolute, existing folder outside
  the repository. The input check does not need it; a reproduction run does.
* **Other variables.**
  - `QCP_EXPANDED_RELEASE_DIR` may replace `--evidence-dir`. This guide passes
    the flag instead.
  - No other variable is needed offline.
* **The optional approval record** belongs at
  `$QCP_DATA/manifests/expanded/approvals/FULL_COLLECTION_a75c6408fb59ab8f.json`
  and is in neither ZIP.
  - Absent is allowed: it is recorded as `absent`.
  - A present file with a different hash stops the input check and any run.

## 7. Validate the inputs (read-only)

```bash
cd "$QCP_REPO"
"$PY" -m query_cost_predictor rq1-check-inputs --evidence-dir "$QCP_EVIDENCE" \
    --archive "$QCP_ASSETS/expanded_release.zip"
echo "exit code: $?"
```

It writes no run directory. Expected values:
- `data_checks` `PASS`;
- `development_cases` 4784, `semantic_groups` 194, `query_instances` 1292,
  `positives_1s` 393, `negatives_1s` 4391;
- `required_packaged_files_verified` 21;
- `archive` `verified_this_run`;
- `supplementary_approval` `absent`, or `verified_this_run`;
- `original_release_validation` STOP with `G-08`. This is the release's
  recorded status, kept as is.

It ends with:

```text
Evidence-run eligibility: ELIGIBLE
RQ1-CHECK-INPUTS: PASS (data checks only; this command writes no run directory)
exit code: 0
```

Data checks and evidence-run eligibility are reported separately.
- **`NOT_ELIGIBLE`** (uncommitted edits, extra untracked files, another
  scikit-learn version) blocks only a new run in step 10. Inspection still
  works.
- **On macOS and Linux there is no 259-character output-path limit.** That
  guard applies on Windows only.

## 8. Verify the saved official run

```bash
cd "$QCP_REPO"
"$PY" - "$QCP_RUN_DIR" <<'PY'
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
PY
echo "exit code: $?"
qcp_sha256 "$QCP_RUN_DIR/output_manifest.json"
```

Expected:
- `36 files verified against output_manifest.json`;
- `run_id` `20261022T144433Z_5db1608ab5d0bb69_ad666623822a`;
- `source_revision` `ad666623822a4c5baa911cc29e410ed0abc9c14e`;
- `source_tracked_dirty: False`;
- `original_release_validation: STOP G-08`;
- exit code 0;
- manifest SHA-256 `e7c2ba2bb2f964d3995ba0d154fc3a581f0bf62741566c4ccce2bc9f34a9b4a3`.

This shows the files are unchanged since the run ended. It does not, by
itself, show that the models are good. Read `report.md` in `$QCP_RUN_DIR` with
any Markdown viewer, and [rq1_results_v1.md](rq1_results_v1.md).

## 9. Check and load all eight hand-off models

The `.joblib` files are Python pickles. Load them **only after** the hash check
below passes.

```bash
cd "$QCP_REPO"
"$PY" - "$QCP_RUN_DIR" <<'PY'
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
PY
echo "exit code: $?"
```

Expected: `label: REFIT_ON_ALL_DEVELOPMENT_NO_EVALUATION | scored: False`,
then eight model lines, then exit code 0.
- They run from `reg_cost_ols: LinearRegression, 1 inputs, predict -> ln(ms)`
  to `clf_hgb_all: HistGradientBoostingClassifier, 69 inputs, predict_proba -> probability in [0, 1]`.
- The models are unscored.
- The input/output contract is in
  [team_handoff.md](team_handoff.md#model-inputoutput-contract).

## 10. Optional: reproduce the study and compare

This recomputes the RQ1 evaluation from the published dataset. It needs **clean,
committed source**: `git status --short` must print nothing. It never replaces
the official run; it creates a new run with its own id under
`$QCP_DATA/results/rq1/rq1-modeling-v1/`.

```bash
cd "$QCP_REPO"
git status --short
git switch --detach ad666623822a4c5baa911cc29e410ed0abc9c14e
"$PY" -m query_cost_predictor rq1-run --evidence-dir "$QCP_EVIDENCE" \
    --archive "$QCP_ASSETS/expanded_release.zip" --run-label rq1-v1-repro
echo "exit code: $?"
git switch -
```

**Expected:**
- `RQ1-RUN: COMPLETE (verified against output_manifest.json)`, a
  `run directory:` line and a `report copy:` line, then exit code 0.
- The official run took 46 s on the original laptop.

**The exit codes:**

| Code | Meaning |
|---|---|
| 0 | complete |
| 1 | the run failed; see its `RUN_STATUS.json` |
| 2 | no evidence directory was given |
| 3 | refused before any run directory was created, for example because the source is not clean |
| 4 | complete, but the report copy failed |

See [rq1_run_guide.md](rq1_run_guide.md).

**Compare with the official run.** These are the same two checks as Windows
step A9, with the same rules, documented in full in
[teammate_setup.md](teammate_setup.md#a9-optional-reproduce-the-study-from-clean-committed-source).

**Check 1** requires the reproduction to use the official run's frozen
inputs:
- the configuration;
- the input record;
- the 21 packaged evidence files and 2 repository files;
- the archive, verified in the run;
- the release identities;
- STOP (G-08).

The run id, timestamps, machine details and whether the optional approval
record was present are not compared. An absent approval record is reported,
and the check continues.

```bash
NEW_RUN_DIR='PASTE THE PRINTED RUN DIRECTORY HERE'
"$PY" - "$QCP_RUN_DIR" "$NEW_RUN_DIR" <<'PY'
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
PY
rc=$?; [ "$rc" -eq 0 ] || echo "STOP: identity check failed (exit $rc). Do not use the reproduction; investigate."
```

**Check 2** compares the metric tables and calibration bins key by key. It
fails on:
- missing or duplicate keys;
- descriptor or null-reason differences;
- a value that is defined in one run and empty in the other;
- nonfinite values;
- numeric differences beyond `math.isclose(rel_tol=1e-9, abs_tol=1e-9)`.

```bash
"$PY" - "$QCP_RUN_DIR" "$NEW_RUN_DIR" <<'PY'
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
PY
rc=$?; [ "$rc" -eq 0 ] || echo "STOP: comparison failed (exit $rc). Investigate before using any reproduced number."
```

**If either check fails:**
- send the complete output, plus the new `run_record.json`, to the Data
  Scientist;
- remember that bit-identical gradient-boosting results on different hardware
  are not guaranteed;
- note that a failed comparison never authorizes replacing or editing the
  official results.

## 11. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| `python3.11: command not found` | Python 3.11 is not installed | install it (step 0); do not substitute another version for the modeling steps |
| `No module named ensurepip`, or venv creation fails | the distribution splits out `venv` | install `python3.11-venv` (Debian or Ubuntu) or use pyenv |
| pip cannot find a pinned version or wheel | no build of that version for your platform | stop and report the full output (step 3); do not loosen pins |
| files named like `derived\keys.csv` after `unzip` | backslash entry names | delete that folder and use the helper (step 5) |
| helper: `destination already exists` | the target folder exists | choose a new folder; the helper never reuses one |
| helper: `size mismatch` or `SHA-256 mismatch` | a wrong or incomplete download | fetch again (step 4) |
| input check fails at `H-02` | `--evidence-dir` does not directly contain `derived/`, `manifest/`, `raw/` and `reports/` | point it at the helper's destination folder |
| input check fails at `H-00` or `H-04` | repository config files were edited | run `git status`; restore them or clone afresh |
| `Evidence-run eligibility: NOT_ELIGIBLE (...)` | uncommitted edits, extra untracked files, or another scikit-learn version | fix the listed blockers; steps 7 to 9 still work |
| step 8: "files in the run directory differ" | an extra file, such as `.DS_Store`, in the run folder | extract again into a new folder |
| `ERROR: cannot read .env` | a malformed `.env` line | recreate it (step 6) |

The Windows guide's
[failure table](teammate_setup.md#a10-interpreting-failures) covers the shared
run failures and exit codes.

## 12. Acceptance

Record your outcome with the
[fresh-machine acceptance checklist](team_handoff.md#fresh-machine-acceptance-checklist).
Also record:
- the OS and its version;
- the processor (for example `x86_64` or `arm64`);
- the Python version;
- the outcome of each step above, with exact error text for any failure.

## Database collection and RQ2

Executing SQL and measuring runtimes needs Docker and PostgreSQL 16, and the
TPC-H data loaded into the databases. That covers fresh query collection,
rewrite verification and RQ2 timing. The repository's database runbooks and
scripts are **PowerShell only**; Bash versions are deferred. This guide
therefore does not cover them.

Use Part B of [teammate_setup.md](teammate_setup.md), with
[POSTER_RUNBOOK.md](../POSTER_RUNBOOK.md) and
[EXPANDED_DATASET_RUNBOOK.md](../EXPANDED_DATASET_RUNBOOK.md), and coordinate
with the Data Engineer. Fresh hardware produces **new** measurements. A new
collection is a separate dataset, never a replacement for `expanded-v1`.
