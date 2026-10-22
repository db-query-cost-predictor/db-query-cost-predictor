"""RQ1 input checker and typed development-dataset loader (offline; read-only).

:func:`check_inputs` verifies the pinned evidence before any modeling:

1. the input record's own SHA-256 (pinned in the RQ1 YAML);
2. the 21 required packaged files (7 derived, 1 manifest, 1 full validation
   report, 12 raw) and the 2 pinned repository files;
3. release identities recomputed from verified bytes, the original validation
   status (STOP, G-08) and the declared scope;
4. unique keys, identical key sets, one-to-one joins and agreement of repeated
   grouping/split metadata across tables;
5. expected counts (input record and YAML), the exact 69-column feature
   contract (names and order) plus the registry check, numeric validity,
   observation statuses, class labels, the runtime range and the stored folds.

Every identity carries one provenance class: ``verified_this_run``,
``copied_from_input_record`` or ``absent``. Development rows are selected by
``split_role`` only; ``in_sample`` is recorded as a collection-control
annotation and never used. Folds are read from ``splits.csv`` and never
generated. Nothing here connects to a database, executes SQL, writes evidence
or freezes a release.
"""

from __future__ import annotations

import csv
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from query_cost_predictor.contract import LeakageError, assert_feature_matrix_allowed, load_registry
from query_cost_predictor.errors import EvidenceIntegrityError
from query_cost_predictor.hashing import sha256_file, sha256_json
from query_cost_predictor.rq1_config import SCHEMES, Rq1Config
from query_cost_predictor.splits import SplitError, assert_groups_disjoint

VERIFIED = "verified_this_run"
COPIED = "copied_from_input_record"
ABSENT = "absent"
TABLES = ("keys.csv", "labels.csv", "features.csv", "estimates.csv", "splits.csv")
METADATA_TABLES = ("keys.csv", "labels.csv", "splits.csv")
SHARED_METADATA_COLUMNS = ("template_id", "semantic_group_id", "query_instance_id", "structure_family",
                           "component", "snapshot_name", "configuration_name", "split_role",
                           "fold_group", "fold_family", "fold_seen_template")
METADATA_COLUMNS = ("semantic_group_id", "structure_family", "query_instance_id", "template_id", "component",
                    "snapshot_name", "configuration_name")
APPROVAL_DIR = ("manifests", "expanded", "approvals")


class Rq1InputError(EvidenceIntegrityError):
    """A pinned RQ1 input failed a check. The message names the file or check."""


class ProvenanceMismatchError(Rq1InputError):
    """A supplementary provenance file is present but its hash differs from the pinned value."""


class FeatureContractError(LeakageError):
    """The learned-model columns are not exactly the pinned allowlist (names and order)."""


# ----------------------------------------------------------------------------------
# Result types
# ----------------------------------------------------------------------------------
@dataclass
class DevelopmentDataset:
    """Development rows only, aligned on one sorted ``modeling_key`` index.

    ``features`` holds exactly the 69 allowlisted learned-model columns;
    ``baseline_inputs`` the raw cost and scale used only by calibrated
    baselines; ``targets`` the regression and 1 s labels; ``metadata`` the
    grouping/reporting columns; ``folds`` the three stored fold schemes.
    """

    features: pd.DataFrame
    baseline_inputs: pd.DataFrame
    targets: pd.DataFrame
    metadata: pd.DataFrame
    folds: pd.DataFrame

    @property
    def keys(self) -> list[str]:
        return list(self.features.index)

    def summary(self) -> dict[str, Any]:
        return {
            "development_cases": int(len(self.features)),
            "semantic_groups": int(self.metadata["semantic_group_id"].nunique()),
            "query_instances": int(self.metadata["query_instance_id"].nunique()),
            "learned_features": int(self.features.shape[1]),
            "positives_1s": int(self.targets["hr_1s"].sum()),
            "negatives_1s": int((self.targets["hr_1s"] == 0).sum()),
            "median_ms_min": float(self.targets["median_ms"].min()),
            "median_ms_max": float(self.targets["median_ms"].max()),
        }


@dataclass
class InputCheckReport:
    status: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    identities: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, Any] = field(default_factory=dict)
    original_validation: dict[str, Any] = field(default_factory=dict)
    collection_control_annotations: dict[str, Any] = field(default_factory=dict)
    supplementary_approval_status: str = ABSENT
    archive_status: str = COPIED
    dataset: DevelopmentDataset | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "original_release_validation": self.original_validation,
            "counts": self.counts,
            "collection_control_annotations": self.collection_control_annotations,
            "supplementary_approval_status": self.supplementary_approval_status,
            "archive_status": self.archive_status,
            "identities": self.identities,
            "checks": self.checks,
            "development_dataset": self.dataset.summary() if self.dataset is not None else None,
        }


class _Recorder:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.identities: list[dict[str, Any]] = []

    def check(self, check_id: str, name: str, ok: bool, detail: Any, *, error: type[Exception] = Rq1InputError) -> None:
        self.checks.append({"id": check_id, "check": name, "status": "PASS" if ok else "FAIL", "detail": detail})
        if not ok:
            raise error(f"{check_id} {name}: {detail}")

    def identity(self, item: str, provenance: str, **values: Any) -> None:
        self.identities.append({"item": item, "provenance": provenance, **values})


# ----------------------------------------------------------------------------------
# Pure guards (tested directly)
# ----------------------------------------------------------------------------------
def assert_exact_feature_columns(observed: Sequence[str], allowlist: Sequence[str]) -> None:
    """Raise unless ``observed`` equals ``allowlist`` exactly (names and order)."""
    observed, allowlist = list(observed), list(allowlist)
    if observed == allowlist:
        return
    extra = [c for c in observed if c not in allowlist]
    missing = [c for c in allowlist if c not in observed]
    duplicated = sorted(c for c, n in Counter(observed).items() if n > 1)
    problems = []
    if extra:
        problems.append(f"extra columns {extra}")
    if missing:
        problems.append(f"missing columns {missing}")
    if duplicated:
        problems.append(f"duplicated columns {duplicated}")
    if not problems:
        first = next(i for i, (a, b) in enumerate(zip(observed, allowlist)) if a != b)
        problems.append(f"reordered columns (first difference at position {first}: {observed[first]!r} "
                        f"instead of {allowlist[first]!r})")
    raise FeatureContractError("learned-model feature columns differ from the pinned allowlist: " + "; ".join(problems))


def _numeric(frame: pd.DataFrame, column: str, table: str) -> pd.Series:
    try:
        values = pd.to_numeric(frame[column], errors="raise").astype("float64")
    except (ValueError, TypeError) as exc:
        raise Rq1InputError(f"{table}:{column} is not numeric ({exc})") from exc
    if not np.isfinite(values.to_numpy()).all():
        raise Rq1InputError(f"{table}:{column} contains non-finite values")
    return values


def _read_table(path: Path, name: str) -> pd.DataFrame:
    with path.open("r", encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle), [])
    repeated = sorted(c for c, n in Counter(header).items() if n > 1)
    if repeated:  # checked before pandas could rename duplicates silently
        error = FeatureContractError if name == "features.csv" else Rq1InputError
        raise error(f"{name} repeats header columns {repeated}")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False, encoding="utf-8")
    if "modeling_key" not in frame.columns:
        raise Rq1InputError(f"{name} has no modeling_key column")
    duplicated = frame["modeling_key"][frame["modeling_key"].duplicated()].tolist()
    if duplicated:
        raise Rq1InputError(f"{name} has duplicate modeling_key values: {duplicated[:3]}")
    return frame.set_index("modeling_key", drop=True).sort_index()


def check_fold_assignments(folds: pd.DataFrame, metadata: pd.DataFrame, hr_1s: pd.Series, config: Rq1Config,
                           recorder: _Recorder | None = None) -> dict[str, Any]:
    """Stored-fold guards for every scheme; never generates folds."""
    rec = recorder or _Recorder()
    values = set(config.fold_values)
    observed: dict[str, Any] = {}
    for scheme in SCHEMES:
        column = folds[scheme]
        bad = sorted(set(column.tolist()) - values)
        rec.check(f"F-{scheme}-values", f"{scheme} values within {sorted(values)}", not bad, bad or "ok")
        counts = {}
        for fold in sorted(values):
            mask = column == fold
            positive = int(hr_1s[mask].sum())
            counts[fold] = {"keys": int(mask.sum()), "positive": positive, "negative": int(mask.sum()) - positive}
        rec.check(f"F-{scheme}-nonempty", f"every {scheme} fold is non-empty", all(c["keys"] > 0 for c in counts.values()),
                  {f: c["keys"] for f, c in counts.items()})
        expected = config.expected_per_fold[scheme]
        rec.check(f"F-{scheme}-counts", f"{scheme} per-fold keys/positives/negatives match the YAML", counts == expected,
                  {"observed": counts, "expected": expected})
        total_pos, total = int(hr_1s.sum()), int(len(hr_1s))
        training = {f: {"positive": total_pos - c["positive"], "negative": (total - c["keys"]) - (total_pos - c["positive"])}
                    for f, c in counts.items()}
        rec.check(f"F-{scheme}-train-classes", f"every {scheme} training fold contains both classes",
                  all(t["positive"] > 0 and t["negative"] > 0 for t in training.values()), training)
        group_column = config.disjoint_group_columns[scheme]
        try:
            assert_groups_disjoint(column.tolist(), metadata[group_column].tolist())
            ok, detail = True, f"{group_column} disjoint"
        except SplitError as exc:
            ok, detail = False, str(exc)[:300]
        rec.check(f"F-{scheme}-disjoint", f"{scheme}: {group_column} lies in exactly one fold", ok, detail)
        observed[scheme] = counts
    try:
        assert_groups_disjoint(folds["fold_family"].tolist(), metadata["semantic_group_id"].tolist())
        ok, detail = True, "semantic groups whole"
    except SplitError as exc:
        ok, detail = False, str(exc)[:300]
    rec.check("F-fold_family-groups", "fold_family keeps every semantic group in one fold", ok, detail)
    return observed


def build_development_dataset(tables: dict[str, pd.DataFrame], allowlist: Sequence[str], config: Rq1Config,
                              recorder: _Recorder | None = None) -> tuple[DevelopmentDataset, dict[str, Any]]:
    """Validate the joined tables and return the typed development dataset.

    ``tables`` maps file name to a frame indexed by ``modeling_key`` with all
    values as strings (as read by :func:`_read_table`).
    """
    rec = recorder or _Recorder()
    keys, labels, features, estimates, splits = (tables[name] for name in TABLES)

    key_sets = {name: set(frame.index) for name, frame in tables.items()}
    reference = key_sets["keys.csv"]
    rec.check("J-01", "every table holds the same modeling_key set (one-to-one joins)",
              all(s == reference for s in key_sets.values()),
              {name: len(s) for name, s in key_sets.items()} if any(s != reference for s in key_sets.values())
              else f"{len(reference)} keys in each of {len(tables)} tables")

    # Feature contract: exact names and order BEFORE any selection or comparison.
    assert_exact_feature_columns(list(features.columns), allowlist)
    rec.check("C-01", "features.csv columns equal the 69-column allowlist (names and order)", True,
              f"{len(allowlist)} columns")

    disagreements: dict[str, int] = {}
    for column in SHARED_METADATA_COLUMNS:
        holders = [name for name in METADATA_TABLES if column in tables[name].columns]
        for other in holders[1:]:
            left, right = tables[holders[0]][column], tables[other][column].reindex(tables[holders[0]].index)
            different = int((left != right).sum())
            if different:
                disagreements[f"{column}: {holders[0]} vs {other}"] = different
    rec.check("J-02", "repeated grouping/split metadata agree across tables", not disagreements, disagreements or "agree")
    scale_tables = [name for name in ("keys.csv", "labels.csv") if "scale_factor" in tables[name].columns]
    scale_ok = all(np.allclose(_numeric(tables[name], "scale_factor", name).reindex(features.index).to_numpy(),
                               _numeric(features, "snap_scale_factor", "features.csv").to_numpy(), rtol=0, atol=0)
                   for name in scale_tables)
    rec.check("J-03", "scale_factor agrees with features.snap_scale_factor", scale_ok, scale_tables)

    # Row selection: split_role only.
    roles = splits["split_role"]
    unknown_roles = sorted(set(roles) - {config.rows["include"], config.rows["exclude"]})
    rec.check("R-01", "split_role values are development or heldout_rq2", not unknown_roles, unknown_roles or "ok")
    dev_keys = sorted(roles.index[roles == config.rows["include"]])
    heldout = roles.index[roles == config.rows["exclude"]]
    rec.check("R-02", "heldout_rq2 rows carry no fold", bool((splits.loc[heldout, list(SCHEMES)] == "").all().all()),
              f"{len(heldout)} heldout rows")

    in_sample_dev = Counter(keys.loc[dev_keys, "in_sample"]) if "in_sample" in keys.columns else Counter()
    annotations = {"in_sample": {
        "present": "in_sample" in keys.columns,
        "role": "collection-control annotation",
        "used_for_rq1_selection_fitting_or_prediction": False,
        "development_counts": dict(sorted(in_sample_dev.items())),
        "all_counts": dict(sorted(Counter(keys["in_sample"]).items())) if "in_sample" in keys.columns else {},
    }}

    dev_labels = labels.loc[dev_keys]
    statuses = sorted(set(dev_labels["observation_status"]))
    rec.check("R-03", f"every development row is {config.rows['required_observation_status']}",
              statuses == [config.rows["required_observation_status"]], statuses)
    mapping = config.class_mapping
    unmapped = sorted(set(dev_labels["hr_1s_class"]) - set(mapping))
    rec.check("R-04", "development hr_1s_class values map to {positive: 1, negative: 0}", not unmapped, unmapped or "ok")

    median_ms = _numeric(dev_labels, "median_ms", "labels.csv")
    rec.check("R-05", "development median_ms > 0", bool((median_ms > 0).all()), float(median_ms.min()))
    lo, hi = config.runtime_range_ms
    observed_range = (float(median_ms.min()), float(median_ms.max()))
    rec.check("R-06", "development median_ms range equals the recorded range", observed_range == (lo, hi),
              {"observed": observed_range, "recorded": (lo, hi)})
    cost = _numeric(estimates.loc[dev_keys], "est_total_cost", "estimates.csv")
    rec.check("R-07", "development est_total_cost > 0", bool((cost > 0).all()), float(cost.min()))

    feature_frame = features.loc[dev_keys, list(allowlist)].apply(
        lambda column: _numeric(features.loc[dev_keys], column.name, "features.csv"))
    feature_frame = feature_frame.astype("float64")
    scale = feature_frame["snap_scale_factor"]
    rec.check("R-08", "development snap_scale_factor > 0", bool((scale > 0).all()), sorted(set(scale.tolist())))
    rec.check("R-09", "all development feature values are finite", bool(np.isfinite(feature_frame.to_numpy()).all()),
              f"{feature_frame.shape[0]} x {feature_frame.shape[1]}")

    fold_frame = pd.DataFrame(index=pd.Index(dev_keys, name="modeling_key"))
    for scheme in SCHEMES:
        raw = splits.loc[dev_keys, scheme]
        try:
            numeric = pd.to_numeric(raw, errors="raise")
        except (ValueError, TypeError) as exc:
            raise Rq1InputError(f"splits.csv:{scheme} has a non-integer development fold ({exc})") from exc
        if not (numeric == numeric.round()).all():
            raise Rq1InputError(f"splits.csv:{scheme} has a non-integer development fold")
        fold_frame[scheme] = numeric.astype("int64")

    metadata = splits.loc[dev_keys, ["semantic_group_id", "structure_family", "query_instance_id", "template_id"]].copy()
    for column in ("component", "snapshot_name", "configuration_name"):
        metadata[column] = labels.loc[dev_keys, column]
    metadata = metadata[list(METADATA_COLUMNS)].astype(str)
    targets = pd.DataFrame({"median_ms": median_ms, "ln_median_ms": np.log(median_ms),
                            "hr_1s": dev_labels["hr_1s_class"].map(mapping).astype("int8")}, index=fold_frame.index)
    baseline = pd.DataFrame({"est_total_cost": cost, "ln_cost": np.log(cost), "snap_scale_factor": scale,
                             "ln_scale": np.log(scale)}, index=fold_frame.index)
    for frame in (feature_frame, metadata, targets, baseline):
        frame.index = fold_frame.index

    rows = config.rows
    rec.check("R-10", "development cases, groups and query instances match the YAML",
              (len(dev_keys), metadata["semantic_group_id"].nunique(), metadata["query_instance_id"].nunique())
              == (rows["expected_development_cases"], rows["expected_development_groups"],
                  rows["expected_development_query_instances"]),
              {"cases": len(dev_keys), "groups": int(metadata["semantic_group_id"].nunique()),
               "query_instances": int(metadata["query_instance_id"].nunique())})

    per_fold = check_fold_assignments(fold_frame, metadata, targets["hr_1s"], config, rec)
    dataset = DevelopmentDataset(features=feature_frame, baseline_inputs=baseline, targets=targets,
                                 metadata=metadata, folds=fold_frame)
    return dataset, {"annotations": annotations, "per_fold": per_fold}


# ----------------------------------------------------------------------------------
# Full check over the packaged evidence
# ----------------------------------------------------------------------------------
def _observed_counts(tables: dict[str, pd.DataFrame], executions: Counter, feature_columns: int) -> dict[str, Any]:
    labels, splits = tables["labels.csv"], tables["splits.csv"]
    dev = splits.index[splits["split_role"] == "development"]
    held = splits.index[splits["split_role"] == "heldout_rq2"]
    dev_labels = labels.loc[dev]
    return {
        "cases": int(len(labels)),
        "observation_status": dict(sorted(Counter(labels["observation_status"]).items())),
        "split_role": dict(sorted(Counter(splits["split_role"]).items())),
        "semantic_groups": {"development": int(splits.loc[dev, "semantic_group_id"].nunique()),
                            "heldout_rq2": int(splits.loc[held, "semantic_group_id"].nunique())},
        "development_observation_status": dict(sorted(Counter(dev_labels["observation_status"]).items())),
        "heldout_rq2_observation_status": dict(sorted(Counter(labels.loc[held, "observation_status"]).items())),
        "development_keys_per_fold_group": dict(sorted(Counter(splits.loc[dev, "fold_group"]).items())),
        "development_hr_1s_class": dict(sorted(Counter(dev_labels["hr_1s_class"]).items())),
        "development_hr_1s_positive_groups": int(splits.loc[dev_labels.index[dev_labels["hr_1s_class"] == "positive"],
                                                            "semantic_group_id"].nunique()),
        "development_hr_10s_class": dict(sorted(Counter(dev_labels["hr_10s_class"]).items())),
        "measured_executions": int(executions.get("measured", 0)),
        "probe_executions": int(executions.get("probe", 0)),
        "feature_columns": int(feature_columns),
    }


def _count_executions(path: Path) -> Counter:
    counter: Counter = Counter()
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            counter[row["run_purpose"]] += 1
    return counter


def _resolve_inside(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise Rq1InputError(f"pinned path escapes its root: {relative}")
    return target


def check_inputs(config: Rq1Config, evidence_dir: Path, *, repo_root: Path, archive: Path | None = None,
                 data_root: Path | None = None, protected_paths: Iterable[Path] = ()) -> InputCheckReport:
    """Run every RQ1 input check and return the report with the development dataset.

    Raises :class:`Rq1InputError` (or :class:`ProvenanceMismatchError`,
    :class:`FeatureContractError`) at the first failing check group.
    """
    rec = _Recorder()
    evidence_dir = Path(evidence_dir)
    if not evidence_dir.is_dir():
        raise Rq1InputError(f"evidence directory not found: {evidence_dir}")
    for protected in protected_paths:
        if evidence_dir.resolve().is_relative_to(Path(protected).resolve()):
            raise Rq1InputError(f"the evidence directory lies under a non-input reference path: {protected}")

    # 1. Input record pinned by the YAML.
    record_path = _resolve_inside(repo_root, config.input_record_path)
    record_sha = sha256_file(record_path)
    rec.check("H-00", "input record matches the SHA-256 pinned in the RQ1 YAML", record_sha == config.input_record_sha256,
              {"path": config.input_record_path, "expected": config.input_record_sha256, "observed": record_sha})
    rec.identity("input_record", VERIFIED, path=config.input_record_path, sha256=record_sha)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    rec.check("H-01", "input record schema", (record.get("schema"), record.get("schema_version")) == config.input_record_schema,
              {"schema": record.get("schema"), "schema_version": record.get("schema_version")})

    # 2. Required packaged files and pinned repository files.
    evidence = record["evidence_files"]
    required = list(evidence["derived"]) + [evidence["manifest"], evidence["validation_full_report"]] + list(evidence["raw_evidence"])
    mismatches, missing = [], []
    verified: dict[str, str] = {}
    for item in required:
        path = _resolve_inside(evidence_dir, item["path"])
        if not path.is_file():
            missing.append(item["path"])
            continue
        observed = sha256_file(path)
        verified[item["path"]] = observed
        if observed != item["sha256"]:
            mismatches.append({"file": item["path"], "expected": item["sha256"], "observed": observed})
    rec.check("H-02", f"{len(required)} required packaged files are present", not missing, missing or len(required))
    rec.check("H-03", "required packaged files match their pinned SHA-256", not mismatches,
              mismatches if mismatches else f"{len(required)} files verified")
    for path, sha in verified.items():
        rec.identity(f"evidence_files.{path}", VERIFIED, sha256=sha)
    repo_mismatches = []
    for name, item in record["repository_files"].items():
        path = _resolve_inside(repo_root, item["path"])
        observed = sha256_file(path) if path.is_file() else None
        if observed != item["sha256"]:
            repo_mismatches.append({"file": item["path"], "expected": item["sha256"], "observed": observed})
        else:
            rec.identity(f"repository_files.{name}", VERIFIED, path=item["path"], sha256=observed)
    rec.check("H-04", "pinned repository files match their SHA-256", not repo_mismatches,
              repo_mismatches or f"{len(record['repository_files'])} files verified")

    # Optional archive and supplementary approval.
    archive_info = record["original_archive"]
    if archive is not None:
        archive_sha = sha256_file(Path(archive))
        rec.check("H-05", "supplied archive matches the pinned SHA-256", archive_sha == archive_info["sha256"],
                  {"file": Path(archive).name, "expected": archive_info["sha256"], "observed": archive_sha})
        rec.identity("original_archive.sha256", VERIFIED, sha256=archive_sha, file_name=Path(archive).name)
        archive_status = VERIFIED
    else:
        rec.identity("original_archive.sha256", COPIED, sha256=archive_info["sha256"],
                     note="archive not supplied; value copied from the input record, not verified")
        archive_status = COPIED
    approval = record["supplementary_provenance_outside_package"]["full_collection_approval"]
    approval_status, reason = ABSENT, None
    if data_root is None:
        reason = "no supplementary approval location (QCP_DATA_ROOT not set)"
    elif not Path(data_root).is_dir():
        reason = "QCP_DATA_ROOT is not an existing directory"
    else:
        approval_path = _resolve_inside(Path(data_root), approval["data_root_path"])
        if not approval_path.is_file():
            reason = "approval record not present under QCP_DATA_ROOT"
        else:
            observed = sha256_file(approval_path)
            rec.check("H-06", "supplementary approval record matches the pinned SHA-256", observed == approval["sha256"],
                      {"file": approval["data_root_path"], "expected": approval["sha256"], "observed": observed},
                      error=ProvenanceMismatchError)
            approval_status = VERIFIED
            rec.identity("supplementary.full_collection_approval", VERIFIED, sha256=observed)
    if approval_status == ABSENT:
        rec.identity("supplementary.full_collection_approval", ABSENT, reason=reason,
                     pinned_sha256=approval["sha256"])
    rec.identity("source_checkpoint.commit_sha", COPIED, value=record["source_checkpoint"]["commit_sha"],
                 note="historical derivation checkpoint; never compared with the current HEAD")

    # 3. Release identities, validation status and scope.
    manifest_path = _resolve_inside(evidence_dir, evidence["manifest"]["path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    body = {k: v for k, v in manifest.items() if k not in ("manifest_id", "created_at_utc")}
    derived_manifest = json.loads(_resolve_inside(evidence_dir, "derived/derived_manifest.json").read_text(encoding="utf-8"))
    validation = json.loads(_resolve_inside(evidence_dir, evidence["validation_full_report"]["path"]).read_text(encoding="utf-8"))
    identity = record["release_identity"]
    inputs_sha = sha256_json(derived_manifest["inputs"])
    m16 = identity["manifest_id"][3:19]
    observed_identity = {
        "release_name": derived_manifest.get("release_name"),
        "manifest_id": "mf_" + sha256_json(body),
        "manifest_short_id": m16,
        "protocol_version": (manifest.get("protocol") or {}).get("protocol_version"),
        "label_version": derived_manifest.get("label_version"),
        "feature_version": derived_manifest.get("feature_version"),
        "derived_inputs_sha256": inputs_sha,
        "derived_release_id": f"expanded:{m16}:{derived_manifest.get('label_version')}:{inputs_sha[:16]}",
    }
    rec.check("I-01", "release identities recompute to the input record", observed_identity == identity,
              {k: {"expected": identity.get(k), "observed": v} for k, v in observed_identity.items() if identity.get(k) != v}
              or "all identical")
    for key, value in observed_identity.items():
        rec.identity(f"release_identity.{key}", VERIFIED, value=value)
    links = {
        "manifest file id": manifest.get("manifest_id") == identity["manifest_id"],
        "derived inputs manifest id": derived_manifest["inputs"]["manifest"]["manifest_id"] == identity["manifest_id"],
        "derived inputs manifest sha": derived_manifest["inputs"]["manifest"]["sha256"] == verified.get(evidence["manifest"]["path"]),
        "derived inputs raw files": [{"name": Path(r["path"]).name, "sha256": r["sha256"]} for r in evidence["raw_evidence"]]
                                    == derived_manifest["inputs"]["raw_files"],
        "derived outputs hashes": all(derived_manifest["outputs"].get(Path(d["path"]).name) == d["sha256"]
                                      for d in evidence["derived"] if Path(d["path"]).name != "derived_manifest.json"),
        "derived feature columns": derived_manifest.get("feature_columns") == record["features"]["feature_columns"],
        "validation manifest id": validation.get("manifest_id") == identity["manifest_id"],
        "validation derived files": all(validation.get("derived_files_sha256", {}).get(Path(d["path"]).name) == d["sha256"]
                                        for d in evidence["derived"]),
    }
    rec.check("I-02", "manifest, derived manifest and validation report are mutually consistent", all(links.values()),
              {k: v for k, v in links.items() if not v} or "consistent")
    stop_codes = [g["id"] for g in validation.get("gates", []) if g.get("status") == "STOP"]
    original = {"status": validation.get("status"), "stop_codes": stop_codes}
    rec.check("I-03", "original release validation is STOP with stop code G-08 (preserved, never reinterpreted)",
              original == config.original_validation == {"status": record["original_validation"]["status"],
                                                          "stop_codes": record["original_validation"]["stop_codes"]},
              original)
    unsupported = {item["task"]: item["status"] for item in record["scope"]["unsupported"]}
    rec.check("I-04", "10 s classification is NOT_YET_EVALUABLE in the record and the YAML",
              unsupported.get("high_runtime_classification_10s") == "NOT_YET_EVALUABLE"
              == config.raw["targets"]["classification_10s"]["status"], unsupported)

    # 4-5. Tables, counts, contract, rows and folds.
    tables = {name: _read_table(_resolve_inside(evidence_dir, f"derived/{name}"), name) for name in TABLES}
    executions = _count_executions(_resolve_inside(evidence_dir, "derived/executions.csv"))
    observed_counts = _observed_counts(tables, executions, len(tables["features.csv"].columns))
    expected_counts = record["expected_counts"]
    differing = {k: {"expected": expected_counts.get(k), "observed": observed_counts.get(k)}
                 for k in sorted(set(expected_counts) | set(observed_counts)) if expected_counts.get(k) != observed_counts.get(k)}
    rec.check("N-01", "expected counts in the input record match the packaged tables", not differing,
              differing or f"{len(expected_counts)} counts identical")

    registry_path = _resolve_inside(repo_root, record["repository_files"]["feature_registry"]["path"])
    allowlist = list(record["features"]["feature_columns"])
    assert_feature_matrix_allowed(allowlist, load_registry(registry_path))
    rec.check("C-00", "every allowlisted column is a registered A-D feature (verified registry)", True, len(allowlist))
    dataset, extras = build_development_dataset(tables, allowlist, config, rec)

    report = InputCheckReport(status="PASS", checks=rec.checks, identities=rec.identities,
                              original_validation={**original, "note": "release-scoped; preserved as recorded; not PASS"},
                              collection_control_annotations=extras["annotations"],
                              supplementary_approval_status=approval_status, archive_status=archive_status,
                              dataset=dataset)
    report.counts = {**observed_counts, "per_fold": {s: {str(f): c for f, c in folds.items()} for s, folds in extras["per_fold"].items()},
                     "development_dataset": dataset.summary(), "required_packaged_files": len(required)}
    return report


def resolve_data_root(env_name: str = "QCP_DATA_ROOT") -> Path | None:
    """``QCP_DATA_ROOT`` as a path if set (no existence requirement); otherwise ``None``."""
    raw = os.environ.get(env_name, "").strip()
    return Path(raw) if raw else None
