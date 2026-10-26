"""Synthetic RQ1 evidence package for Slice A tests (SYNTHETIC values, never measurements).

``build_release`` writes a small, internally consistent package (derived tables,
manifest, validation report, raw files), the pinned repository files, the input
record and an RQ1 config whose pins match. A ``mutate`` hook can change the
table rows before anything is hashed, so a test can make the data invalid
while every hash stays consistent.
"""

from __future__ import annotations

import copy
import csv
import io
import json
import shutil
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from query_cost_predictor.hashing import sha256_bytes, sha256_file, sha256_json
from query_cost_predictor.rq1_config import Rq1Config, parse_rq1_config

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_INPUT_RECORD = REPO_ROOT / "config" / "rq1_modeling_input_v1.json"
REAL_CONFIG = REPO_ROOT / "config" / "rq1_modeling_v1.yaml"
REAL_REGISTRY = REPO_ROOT / "config" / "feature_registry.yaml"
ALLOWLIST: list[str] = json.loads(REAL_INPUT_RECORD.read_text(encoding="utf-8"))["features"]["feature_columns"]
SNAPSHOTS = (("tpch_sf0_1", "0.1"), ("tpch_sf1", "1.0"))
COMPONENTS = ["structural"] * 6 + ["tpch_derived"] * 2 + ["probe", "rewrite"]
POSITIVE_GROUPS = (0, 3, 6, 9)


SYNTHETIC_SOURCE_STATE = {"source_revision": "f" * 40, "tracked_dirty": False, "tracked_changes": [],
                          "untracked_paths": [], "disallowed_untracked_paths": [], "ignored_execution_files": [],
                          "package_from_checkout": True, "sklearn_version": "1.9.1"}


def small_bootstrap(document: dict[str, Any], replicates: int = 40) -> None:
    """Explicitly reduced bootstrap for the 10-group synthetic package (production stays at 2,000)."""
    document["uncertainty"]["n_clusters"] = 10
    document["uncertainty"]["replicates"] = replicates


def production_context(root: Path, release: "SyntheticRelease", config: Rq1Config, run_id: str = "run_prod"):
    """A run context set up as ``rq1-run`` does (requirements and initial provenance), plus the production stages."""
    from query_cost_predictor.rq1_run import (create_run_context, initial_provenance, production_requirements,
                                              production_stages)

    context = create_run_context(root / "results", run_id, "synthetic", config, evidence_dir=release.evidence_dir,
                                 repo_root=release.repo, data_root=release.data_root,
                                 requirements=production_requirements(config))
    context.provenance.update(initial_provenance(config, SYNTHETIC_SOURCE_STATE, []))
    return context, production_stages(config.stages)


@dataclass
class SyntheticRelease:
    root: Path
    evidence_dir: Path
    repo: Path
    data_root: Path
    archive: Path
    record_path: Path
    record: dict[str, Any]
    config: Rq1Config
    config_document: dict[str, Any]

    def required_paths(self) -> list[str]:
        files = self.record["evidence_files"]
        return [d["path"] for d in files["derived"]] + [files["manifest"]["path"], files["validation_full_report"]["path"]] + \
               [r["path"] for r in files["raw_evidence"]]

    def reconfigure(self, change: Callable[[dict[str, Any]], None]) -> Rq1Config:
        document = copy.deepcopy(self.config_document)
        change(document)
        return parse_rq1_config(document, self.root / "rq1_modeling_v1.yaml", "synthetic")

    def rewrite_record(self, change: Callable[[dict[str, Any]], None]) -> Rq1Config:
        change(self.record)
        self.record_path.write_text(json.dumps(self.record, indent=2), encoding="utf-8", newline="\n")
        sha = sha256_file(self.record_path)
        return self.reconfigure(lambda d: d["inputs"]["input_record"].__setitem__("sha256", sha))


def _rows() -> dict[str, list[dict[str, str]]]:
    tables: dict[str, list[dict[str, str]]] = {"keys": [], "labels": [], "features": [], "estimates": [], "splits": [],
                                               "executions": []}
    groups = [(f"sg_dev_{g:02d}", g, "development") for g in range(10)] + [("sg_rq2_00", 10, "heldout_rq2")]
    for group_id, g, role in groups:
        family = f"fam_{g // 2}" if role == "development" else "rw_heldout"
        component = COMPONENTS[g] if role == "development" else "rewrite"
        for i in range(2):
            instance = f"qi_{g:02d}_{i}"
            instance_index = 2 * g + i
            for s, (snapshot, scale) in enumerate(SNAPSHOTS):
                key = f"mk_{g:02d}_{i}_{s}"
                positive = role == "development" and g in POSITIVE_GROUPS and i == 0 and s == 1
                median = 1500.0 if positive else 5.0 + g + 0.5 * i + 0.25 * s
                folds = ({"fold_group": str(g // 2), "fold_family": str((g // 2 + 2) % 5),
                          "fold_seen_template": str(instance_index % 5)} if role == "development"
                         else {"fold_group": "", "fold_family": "", "fold_seen_template": ""})
                censored = role == "heldout_rq2" and i == 1 and s == 1
                common = {"modeling_key": key, "template_id": f"tpl_{g:02d}", "semantic_group_id": group_id,
                          "query_instance_id": instance, "structure_family": family, "split_role": role, **folds}
                tables["keys"].append({**common, "snapshot_name": snapshot, "scale_factor": scale,
                                       "configuration_name": "C1_baseline", "component": component,
                                       "in_sample": "true" if (role == "development" and s == 0 and i == 0) else "false"})
                tables["labels"].append({**common, "snapshot_name": snapshot, "scale_factor": scale,
                                         "configuration_name": "C1_baseline", "component": component,
                                         "status": "right_censored" if censored else "complete",
                                         "observation_status": "right_censored" if censored else "complete",
                                         "median_ms": "" if censored else repr(median),
                                         "hr_1s_class": "positive" if (positive or censored) else "negative",
                                         "hr_10s_class": "positive" if censored else "negative"})
                tables["splits"].append(dict(common))
                tables["features"].append({"modeling_key": key, **{
                    name: (scale if name == "snap_scale_factor" else "4096.0" if name == "cfg_work_mem_kb"
                           else repr(float((instance_index * 7 + col) % 13))) for col, name in enumerate(ALLOWLIST)}})
                tables["estimates"].append({"modeling_key": key, "est_total_cost": repr(10.0 + 3 * g + s)})
                # Three measured runs whose middle value is the label median (low, median, high); the
                # positive key of group 0 straddles 1,000 ms (low run 900 ms). The probe is never measured.
                low_factor = 0.6 if (positive and g == 0) else 0.9
                timings = {0: median * 1.3, 1: median * 1.1, 2: median, 3: median * low_factor}
                for repeat in range(4):
                    if censored and repeat == 3:
                        continue
                    tables["executions"].append({
                        "modeling_key": key, "repeat_no": str(repeat), "run_purpose": "probe" if repeat == 0 else "measured",
                        "status": "timeout" if censored else "completed", "is_censored": "true" if censored else "false",
                        "execution_time_ms": "" if censored else repr(timings[repeat])})
    return tables


def _csv(rows: list[dict[str, str]], columns: list[str]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in sorted(rows, key=lambda r: r["modeling_key"]):
        writer.writerow([row.get(c, "") for c in columns])
    return buffer.getvalue().encode("utf-8")


def _expected_counts(tables: dict[str, list[dict[str, str]]], allowlist: list[str]) -> dict[str, Any]:
    labels = {r["modeling_key"]: r for r in tables["labels"]}
    splits = tables["splits"]
    dev = [r for r in splits if r["split_role"] == "development"]
    held = [r for r in splits if r["split_role"] == "heldout_rq2"]
    dev_labels = [labels[r["modeling_key"]] for r in dev if r["modeling_key"] in labels]
    return {
        "cases": len(tables["labels"]),
        "observation_status": dict(sorted(Counter(r["observation_status"] for r in tables["labels"]).items())),
        "split_role": dict(sorted(Counter(r["split_role"] for r in splits).items())),
        "semantic_groups": {"development": len({r["semantic_group_id"] for r in dev}),
                            "heldout_rq2": len({r["semantic_group_id"] for r in held})},
        "development_observation_status": dict(sorted(Counter(r["observation_status"] for r in dev_labels).items())),
        "heldout_rq2_observation_status": dict(sorted(Counter(labels[r["modeling_key"]]["observation_status"]
                                                             for r in held if r["modeling_key"] in labels).items())),
        "development_keys_per_fold_group": dict(sorted(Counter(r["fold_group"] for r in dev).items())),
        "development_hr_1s_class": dict(sorted(Counter(r["hr_1s_class"] for r in dev_labels).items())),
        "development_hr_1s_positive_groups": len({r["semantic_group_id"] for r in dev_labels if r["hr_1s_class"] == "positive"}),
        "development_hr_10s_class": dict(sorted(Counter(r["hr_10s_class"] for r in dev_labels).items())),
        "measured_executions": sum(r["run_purpose"] == "measured" for r in tables["executions"]),
        "probe_executions": sum(r["run_purpose"] == "probe" for r in tables["executions"]),
        "feature_columns": len(allowlist),
    }


def _per_fold(tables: dict[str, list[dict[str, str]]]) -> dict[str, dict[int, dict[str, int]]]:
    labels = {r["modeling_key"]: r for r in tables["labels"]}
    out: dict[str, dict[int, dict[str, int]]] = {}
    for scheme in ("fold_group", "fold_family", "fold_seen_template"):
        counts = {f: {"keys": 0, "positive": 0, "negative": 0} for f in range(5)}
        for row in tables["splits"]:
            if row["split_role"] != "development" or row[scheme] == "":
                continue
            fold = int(row[scheme])
            positive = labels[row["modeling_key"]]["hr_1s_class"] == "positive"
            counts.setdefault(fold, {"keys": 0, "positive": 0, "negative": 0})
            counts[fold]["keys"] += 1
            counts[fold]["positive" if positive else "negative"] += 1
        out[scheme] = counts
    return out


def build_release(root: Path, *, mutate: Callable[[dict[str, list[dict[str, str]]]], None] | None = None,
                  allowlist: list[str] | None = None, validation_status: str = "STOP",
                  stop_codes: tuple[str, ...] = ("G-08",)) -> SyntheticRelease:
    allowlist = list(allowlist or ALLOWLIST)
    tables = _rows()
    if allowlist != ALLOWLIST:
        for row in tables["features"]:
            for name in allowlist:
                row.setdefault(name, "1.0")
    if mutate:
        mutate(tables)
    evidence, repo, data_root = root / "evidence", root / "repo", root / "data_root"
    for folder in (evidence / "derived", evidence / "manifest", evidence / "reports", evidence / "raw" / "expanded-v1",
                   repo / "config"):
        folder.mkdir(parents=True, exist_ok=True)

    key_columns = ["modeling_key", "template_id", "semantic_group_id", "snapshot_name", "scale_factor",
                   "configuration_name", "query_instance_id", "component", "structure_family", "split_role",
                   "fold_group", "fold_family", "fold_seen_template", "in_sample"]
    label_columns = ["modeling_key", "template_id", "semantic_group_id", "configuration_name", "scale_factor", "status",
                     "median_ms", "snapshot_name", "query_instance_id", "component", "structure_family", "split_role",
                     "fold_group", "fold_family", "fold_seen_template", "observation_status", "hr_1s_class", "hr_10s_class"]
    split_columns = ["modeling_key", "semantic_group_id", "structure_family", "query_instance_id", "template_id",
                     "split_role", "fold_group", "fold_family", "fold_seen_template"]
    outputs = {
        "keys.csv": _csv(tables["keys"], key_columns),
        "executions.csv": _csv(tables["executions"], ["modeling_key", "repeat_no", "run_purpose", "status", "is_censored",
                                                      "execution_time_ms"]),
        "labels.csv": _csv(tables["labels"], label_columns),
        "estimates.csv": _csv(tables["estimates"], ["modeling_key", "est_total_cost"]),
        "features.csv": _csv(tables["features"], ["modeling_key", *allowlist]),
        "splits.csv": _csv(tables["splits"], split_columns),
    }
    for name, data in outputs.items():
        (evidence / "derived" / name).write_bytes(data)

    raw_names = ["session_20260101T000000Z_aaaaaaaa.jsonl", "session_20260101T010000Z_bbbbbbbb.jsonl"]
    for index, name in enumerate(raw_names):
        (evidence / "raw" / "expanded-v1" / name).write_bytes(f'{{"synthetic": {index}}}\n'.encode())
    body = {"manifest_kind": "expanded_release", "protocol": {"protocol_version": "expanded-v1"}, "note": "synthetic"}
    manifest_id = "mf_" + sha256_json(body)
    m16 = manifest_id[3:19]
    manifest_path = evidence / "manifest" / f"manifest_{m16}.json"
    manifest_path.write_text(json.dumps({"manifest_id": manifest_id, "created_at_utc": "2026-01-01T00:00:00+00:00", **body}),
                             encoding="utf-8")
    inputs = {"manifest": {"manifest_id": manifest_id, "sha256": sha256_file(manifest_path)},
              "raw_files": [{"name": n, "sha256": sha256_file(evidence / "raw" / "expanded-v1" / n)} for n in raw_names]}
    derived_manifest = {"release_name": "expanded-v1", "label_version": "expanded-label-v1",
                        "feature_version": "expanded-features-v1", "feature_columns": allowlist, "inputs": inputs,
                        "outputs": {n: sha256_bytes(d) for n, d in sorted(outputs.items())},
                        "counts": {"measured_executions": 0, "probe_executions": 0}}
    (evidence / "derived" / "derived_manifest.json").write_text(json.dumps(derived_manifest, indent=2, sort_keys=True) + "\n",
                                                                 encoding="utf-8")
    derived_names = ["keys.csv", "executions.csv", "labels.csv", "estimates.csv", "features.csv", "splits.csv",
                     "derived_manifest.json"]
    derived_hashes = {n: sha256_file(evidence / "derived" / n) for n in derived_names}
    validation_path = evidence / "reports" / f"validation_full_{m16}.json"
    validation_path.write_text(json.dumps({
        "status": validation_status, "manifest_id": manifest_id, "checks": [],
        "gates": [{"id": "G-07", "status": "PASS"}] + [{"id": code, "status": "STOP"} for code in stop_codes],
        "derived_files_sha256": derived_hashes}), encoding="utf-8")

    (repo / "config" / "expanded_release_v1.yaml").write_text("synthetic: workload specification\n", encoding="utf-8")
    shutil.copyfile(REAL_REGISTRY, repo / "config" / "feature_registry.yaml")
    approval_rel = f"manifests/expanded/approvals/FULL_COLLECTION_{m16}.json"
    (data_root / approval_rel).parent.mkdir(parents=True, exist_ok=True)
    (data_root / approval_rel).write_text('{"synthetic": "approval"}\n', encoding="utf-8")
    archive = root / "expanded_release.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("README.txt", "synthetic archive")

    inputs_sha = sha256_json(inputs)
    record = {
        "schema": "qcp.rq1_modeling_input", "schema_version": 1, "record_id": "rq1-modeling-input-v1",
        "source_checkpoint": {"commit_sha": "972c00bbb19f63a8f9b658ad471837089248276e", "recorded_during_collection": False},
        "release_identity": {"release_name": "expanded-v1", "manifest_id": manifest_id, "manifest_short_id": m16,
                             "protocol_version": "expanded-v1", "label_version": "expanded-label-v1",
                             "feature_version": "expanded-features-v1", "derived_inputs_sha256": inputs_sha,
                             "derived_release_id": f"expanded:{m16}:expanded-label-v1:{inputs_sha[:16]}"},
        "original_archive": {"file_name": archive.name, "sha256": sha256_file(archive)},
        "evidence_files": {
            "derived": [{"path": f"derived/{n}", "sha256": derived_hashes[n]} for n in derived_names],
            "manifest": {"path": f"manifest/{manifest_path.name}", "sha256": sha256_file(manifest_path)},
            "validation_full_report": {"path": f"reports/{validation_path.name}", "sha256": sha256_file(validation_path)},
            "raw_evidence": [{"path": f"raw/expanded-v1/{r['name']}", "sha256": r["sha256"]} for r in inputs["raw_files"]],
        },
        "repository_files": {
            "workload_specification": {"path": "config/expanded_release_v1.yaml",
                                       "sha256": sha256_file(repo / "config" / "expanded_release_v1.yaml")},
            "feature_registry": {"path": "config/feature_registry.yaml", "sha256": sha256_file(repo / "config" / "feature_registry.yaml")},
        },
        "supplementary_provenance_outside_package": {"full_collection_approval": {
            "data_root_path": approval_rel, "sha256": sha256_file(data_root / approval_rel), "included_in_archive": False}},
        "expected_counts": _expected_counts(tables, allowlist),
        "original_validation": {"status": "STOP", "stop_codes": ["G-08"]},
        "scope": {"unsupported": [{"task": "high_runtime_classification_10s", "status": "NOT_YET_EVALUABLE", "gate": "G-08"}]},
        "features": {"feature_columns": allowlist},
    }
    record_path = repo / "config" / "rq1_modeling_input_v1.json"
    record_path.write_text(json.dumps(record, indent=2), encoding="utf-8", newline="\n")

    document = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
    document["inputs"]["input_record"]["sha256"] = sha256_file(record_path)
    dev_labels = [r for r in tables["labels"] if r["split_role"] == "development"]
    medians = [float(r["median_ms"]) for r in dev_labels if r["median_ms"] not in ("",)]
    document["rows"].update({
        "expected_development_cases": sum(r["split_role"] == "development" for r in tables["splits"]),
        "expected_development_groups": len({r["semantic_group_id"] for r in tables["splits"] if r["split_role"] == "development"}),
        "expected_development_query_instances": len({r["query_instance_id"] for r in tables["splits"]
                                                     if r["split_role"] == "development"}),
        "development_runtime_range_ms": {"min": min(medians) if medians else 1.0, "max": max(medians) if medians else 1.0},
    })
    document["folds"]["expected_per_fold"] = _per_fold(tables)
    config = parse_rq1_config(document, root / "rq1_modeling_v1.yaml", "synthetic")
    return SyntheticRelease(root=root, evidence_dir=evidence, repo=repo, data_root=data_root, archive=archive,
                            record_path=record_path, record=record, config=config, config_document=document)
