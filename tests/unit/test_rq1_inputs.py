"""RQ1 Slice A: input checker and typed loader on a SYNTHETIC package (no database)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from query_cost_predictor import splits as splits_module
from query_cost_predictor.contract import LeakageError, leakage_problems, load_registry
from query_cost_predictor.rq1_inputs import (
    ABSENT,
    COPIED,
    TABLES,
    VERIFIED,
    FeatureContractError,
    ProvenanceMismatchError,
    Rq1InputError,
    _read_table,
    assert_exact_feature_columns,
    build_development_dataset,
    check_inputs,
)
from rq1_fixtures import ALLOWLIST, REAL_REGISTRY, build_release


@pytest.fixture
def release(tmp_path: Path):
    return build_release(tmp_path)


def run(release, **kwargs):
    kwargs.setdefault("repo_root", release.repo)
    return check_inputs(kwargs.pop("config", release.config), release.evidence_dir, **kwargs)


def identity(report, item):
    return next(i for i in report.identities if i["item"] == item)


# ----------------------------------------------------------------------------- happy path and provenance
def test_valid_package_returns_development_rows_only(release):
    report = run(release)
    data = report.dataset
    assert report.status == "PASS"
    assert data.summary()["development_cases"] == 40 and data.summary()["semantic_groups"] == 10
    assert list(data.features.columns) == ALLOWLIST and data.features.shape[1] == 69
    assert not any(key.startswith("mk_10_") for key in data.keys)  # the heldout_rq2 group never appears
    for frame in (data.baseline_inputs, data.targets, data.metadata, data.folds):
        assert list(frame.index) == data.keys == sorted(data.keys)
    assert data.features.dtypes.unique().tolist() == [np.dtype("float64")]
    assert set(data.folds.dtypes) == {np.dtype("int64")}
    assert data.targets["hr_1s"].dtype == np.dtype("int8")
    assert np.allclose(data.baseline_inputs["ln_cost"], np.log(data.baseline_inputs["est_total_cost"]))
    assert "est_total_cost" not in data.features.columns and "in_sample" not in data.metadata.columns
    assert report.original_validation["status"] == "STOP" and report.original_validation["stop_codes"] == ["G-08"]
    assert report.counts["required_packaged_files"] == len(release.required_paths()) == 11


def test_archive_and_approval_absent_are_copied_or_absent(release):
    report = run(release)
    assert identity(report, "original_archive.sha256")["provenance"] == COPIED
    assert identity(report, "supplementary.full_collection_approval")["provenance"] == ABSENT
    assert identity(report, "source_checkpoint.commit_sha")["provenance"] == COPIED
    assert all(i["provenance"] == VERIFIED for i in report.identities if i["item"].startswith("evidence_files."))


def test_supplied_archive_and_present_approval_are_verified(release):
    report = run(release, archive=release.archive, data_root=release.data_root)
    assert identity(report, "original_archive.sha256")["provenance"] == VERIFIED
    assert report.supplementary_approval_status == VERIFIED


def test_wrong_archive_fails(release, tmp_path):
    other = tmp_path / "other.zip"
    other.write_bytes(b"not the archive")
    with pytest.raises(Rq1InputError, match="H-05"):
        run(release, archive=other)


def test_mismatched_approval_stops_with_provenance_error(release):
    approval = release.data_root / release.record["supplementary_provenance_outside_package"]["full_collection_approval"]["data_root_path"]
    approval.write_text("changed", encoding="utf-8")
    with pytest.raises(ProvenanceMismatchError):
        run(release, data_root=release.data_root)


def test_data_root_without_approval_records_absent(release, tmp_path):
    empty = tmp_path / "empty_root"
    empty.mkdir()
    report = run(release, data_root=empty)
    assert report.supplementary_approval_status == ABSENT


def test_checkpoint_sha_is_never_compared_with_head(release):
    release.rewrite_record(lambda r: r["source_checkpoint"].__setitem__("commit_sha", "0" * 40))
    config = release.rewrite_record(lambda r: None)
    report = run(release, config=config)
    assert identity(report, "source_checkpoint.commit_sha")["value"] == "0" * 40


# ----------------------------------------------------------------------------- hashes and identities
def test_every_required_file_is_hash_checked_and_named(release):
    for relative in release.required_paths():
        path = release.evidence_dir / relative
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with pytest.raises(Rq1InputError, match="H-03") as excinfo:
            run(release)
        assert relative in str(excinfo.value)
        path.write_bytes(original)


def test_missing_required_file_fails(release):
    (release.evidence_dir / release.required_paths()[0]).unlink()
    with pytest.raises(Rq1InputError, match="H-02"):
        run(release)


def test_changed_repository_file_fails(release):
    (release.repo / "config" / "expanded_release_v1.yaml").write_text("changed\n", encoding="utf-8")
    with pytest.raises(Rq1InputError, match="H-04"):
        run(release)


def test_wrong_input_record_hash_fails(release):
    config = release.reconfigure(lambda d: d["inputs"]["input_record"].__setitem__("sha256", "0" * 64))
    with pytest.raises(Rq1InputError, match="H-00"):
        run(release, config=config)


def test_wrong_release_identity_fails(release):
    config = release.rewrite_record(lambda r: r["release_identity"].__setitem__("label_version", "other"))
    with pytest.raises(Rq1InputError, match="I-01"):
        run(release, config=config)


def test_original_validation_must_stay_stop_g08(tmp_path):
    release = build_release(tmp_path, validation_status="PASS", stop_codes=())
    with pytest.raises(Rq1InputError, match="I-03"):
        run(release)


def test_count_mismatch_fails(release):
    config = release.rewrite_record(lambda r: r["expected_counts"].__setitem__("cases", 45))
    with pytest.raises(Rq1InputError, match="N-01"):
        run(release, config=config)


def test_evidence_directory_under_reference_path_is_refused(release):
    with pytest.raises(Rq1InputError, match="non-input reference path"):
        run(release, protected_paths=[release.evidence_dir.parent])


# ----------------------------------------------------------------------------- joins and metadata
def test_duplicate_keys_fail(tmp_path):
    release = build_release(tmp_path, mutate=lambda t: t["labels"].append(dict(t["labels"][0])))
    with pytest.raises(Rq1InputError, match="duplicate modeling_key"):
        run(release)


def test_unmatched_join_fails(tmp_path):
    release = build_release(tmp_path, mutate=lambda t: t["estimates"].pop())
    with pytest.raises(Rq1InputError, match="J-01"):
        run(release)


def test_repeated_metadata_must_agree_across_tables(tmp_path):
    release = build_release(tmp_path, mutate=lambda t: t["labels"][0].__setitem__("structure_family", "fam_other"))
    with pytest.raises(Rq1InputError, match="J-02"):
        run(release)


# ----------------------------------------------------------------------------- exact feature contract
def test_exact_contract_rejects_extra_missing_and_reordered_columns():
    with pytest.raises(FeatureContractError, match="extra columns \\['est_total_cost'\\]"):
        assert_exact_feature_columns(ALLOWLIST + ["est_total_cost"], ALLOWLIST)
    with pytest.raises(FeatureContractError, match="missing columns"):
        assert_exact_feature_columns(ALLOWLIST[:-1], ALLOWLIST)
    reordered = [ALLOWLIST[1], ALLOWLIST[0], *ALLOWLIST[2:]]
    with pytest.raises(FeatureContractError, match="reordered"):
        assert_exact_feature_columns(reordered, ALLOWLIST)
    assert_exact_feature_columns(list(ALLOWLIST), ALLOWLIST)


def test_est_total_cost_passes_the_registry_so_only_the_exact_rule_rejects_it():
    assert leakage_problems(ALLOWLIST + ["est_total_cost"], load_registry(REAL_REGISTRY)) == []
    with pytest.raises(FeatureContractError):
        assert_exact_feature_columns(ALLOWLIST + ["est_total_cost"], ALLOWLIST)


@pytest.mark.parametrize("extra", ["est_total_cost", "modeling_key", "semantic_group_id", "median_ms", "hr_1s_class", "fold_group"])
def test_extra_feature_column_in_features_csv_is_rejected(tmp_path, extra):
    def mutate(tables):
        for row in tables["features"]:
            row[extra] = "1.0"
    release = build_release(tmp_path, mutate=mutate)
    path = release.evidence_dir / "derived" / "features.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0] + f",{extra}"
    path.write_text("\n".join([header] + [line + ",1.0" for line in lines[1:]]) + "\n", encoding="utf-8")
    with pytest.raises(FeatureContractError, match=extra):
        tables = {name: _read_table(release.evidence_dir / "derived" / name, name) for name in TABLES}
        build_development_dataset(tables, ALLOWLIST, release.config)


@pytest.mark.parametrize("column", ["modeling_key", "median_ms", "execution_time_ms"])
def test_identity_or_execution_column_in_the_allowlist_raises_leakage_error(tmp_path, column):
    release = build_release(tmp_path, allowlist=ALLOWLIST + [column])
    with pytest.raises(LeakageError):
        run(release)


# ----------------------------------------------------------------------------- rows
def test_in_sample_is_recorded_but_never_changes_the_dataset(release):
    report = run(release)
    annotation = report.collection_control_annotations["in_sample"]
    assert annotation["used_for_rq1_selection_fitting_or_prediction"] is False
    assert annotation["development_counts"] == {"false": 30, "true": 10}
    tables = {name: _read_table(release.evidence_dir / "derived" / name, name) for name in TABLES}
    tables["keys.csv"]["in_sample"] = tables["keys.csv"]["in_sample"].map({"true": "false", "false": "true"})
    flipped, _ = build_development_dataset(tables, ALLOWLIST, release.config)
    for name in ("features", "baseline_inputs", "targets", "metadata", "folds"):
        assert getattr(flipped, name).equals(getattr(report.dataset, name))


def test_selection_uses_split_role_only(release):
    tables = {name: _read_table(release.evidence_dir / "derived" / name, name) for name in TABLES}
    tables["keys.csv"]["in_sample"] = "true"  # an annotation cannot add or remove rows
    dataset, _ = build_development_dataset(tables, ALLOWLIST, release.config)
    roles = tables["splits.csv"]["split_role"]
    assert dataset.keys == sorted(roles.index[roles == "development"])


def test_nonpositive_cost_fails(tmp_path):
    release = build_release(tmp_path, mutate=lambda t: t["estimates"][0].__setitem__("est_total_cost", "0.0"))
    with pytest.raises(Rq1InputError, match="R-07"):
        run(release)


@pytest.mark.parametrize("value", ["nan", "inf", "", "abc"])
def test_nonfinite_or_nonnumeric_feature_fails(tmp_path, value):
    release = build_release(tmp_path, mutate=lambda t: t["features"][0].__setitem__(ALLOWLIST[0], value))
    with pytest.raises(Rq1InputError, match="non-finite|not numeric"):
        run(release)


def test_development_status_must_be_complete(tmp_path):
    def mutate(tables):
        tables["labels"][0]["observation_status"] = "failed"
    release = build_release(tmp_path, mutate=mutate)
    with pytest.raises(Rq1InputError, match="R-03"):
        run(release)


def test_unknown_class_label_fails(tmp_path):
    release = build_release(tmp_path, mutate=lambda t: t["labels"][0].__setitem__("hr_1s_class", "indeterminate"))
    with pytest.raises(Rq1InputError, match="R-04"):
        run(release)


def test_runtime_outside_the_recorded_range_fails(release):
    config = release.reconfigure(lambda d: d["rows"]["development_runtime_range_ms"].__setitem__("max", 1000.0))
    with pytest.raises(Rq1InputError, match="R-06"):
        run(release, config=config)


# ----------------------------------------------------------------------------- folds
def test_folds_are_read_and_never_generated(release, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("fold generation is forbidden in RQ1")
    monkeypatch.setattr(splits_module, "group_kfold", forbidden)
    monkeypatch.setattr(splits_module, "random_kfold", forbidden)
    assert run(release).status == "PASS"


@pytest.mark.parametrize("scheme", ["fold_group", "fold_family", "fold_seen_template"])
def test_changed_per_fold_expectation_fails(release, scheme):
    def change(document):
        counts = document["folds"]["expected_per_fold"][scheme][0]
        counts["keys"] += 1
        counts["negative"] += 1
    with pytest.raises(Rq1InputError, match=f"F-{scheme}-counts"):
        run(release, config=release.reconfigure(change))


def test_group_split_across_fold_group_folds_fails(tmp_path):
    def mutate(tables):
        for name in ("keys", "labels", "splits"):
            tables[name][0]["fold_group"] = "4"  # one key of sg_dev_00 moves to another fold
    release = build_release(tmp_path, mutate=mutate)
    with pytest.raises(Rq1InputError, match="F-fold_group-disjoint"):
        run(release)


def test_semantic_group_split_across_fold_family_folds_fails(tmp_path):
    def mutate(tables):
        for name in ("keys", "labels", "splits"):
            tables[name][0]["fold_family"] = "0"
            tables[name][0]["structure_family"] = "fam_isolated"  # the family stays whole; the group does not
    release = build_release(tmp_path, mutate=mutate)
    with pytest.raises(Rq1InputError, match="F-fold_family-groups"):
        run(release)


def test_training_fold_with_one_class_fails(tmp_path):
    def mutate(tables):
        for row in tables["labels"]:
            if row["split_role"] == "development" and row["semantic_group_id"] != "sg_dev_00":
                row["hr_1s_class"] = "negative"
                row["median_ms"] = "5.0"
    release = build_release(tmp_path, mutate=mutate)
    with pytest.raises(Rq1InputError, match="train-classes"):
        run(release)


def test_heldout_rows_must_not_carry_folds(tmp_path):
    def mutate(tables):
        for name in ("keys", "labels", "splits"):
            for row in tables[name]:
                if row["split_role"] == "heldout_rq2":
                    row["fold_group"] = "0"
    release = build_release(tmp_path, mutate=mutate)
    with pytest.raises(Rq1InputError):
        run(release)


def test_report_json_is_serializable(release):
    document = run(release).to_json()
    assert json.loads(json.dumps(document, default=str))["status"] == "PASS"
