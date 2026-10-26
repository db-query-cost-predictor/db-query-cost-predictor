"""Command-line interface: ``python -m query_cost_predictor <command>``.

Each command prints a final status line that the PowerShell scripts and the
runbook check (for example ``PILOT-AUDIT: COMPLETED``). Commands never pick an
alternative port, path, Python or PostgreSQL version: they fail with a message.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from importlib import metadata
from pathlib import Path
from typing import Callable

from query_cost_predictor import __version__
from query_cost_predictor.envfile import load_repo_env

REQUIRED_DISTRIBUTIONS = {
    "numpy": "numpy",
    "scipy": "scipy",
    "scikit-learn": "sklearn",
    "pandas": "pandas",
    "matplotlib": "matplotlib",
    "PyYAML": "yaml",
    "psycopg": "psycopg",
}
MIN_PYTHON = (3, 11)


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


def cmd_env_check(args: argparse.Namespace) -> int:
    import importlib
    import os

    report: dict[str, object] = {"query_cost_predictor": __version__, "python": sys.version.split()[0],
                                 "executable": sys.executable}
    failures: list[str] = []
    warnings: list[str] = []
    if sys.version_info[:2] < MIN_PYTHON:
        failures.append(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, found {report['python']}")
    packages: dict[str, str] = {}
    for dist, module in REQUIRED_DISTRIBUTIONS.items():
        try:
            importlib.import_module(module)
            packages[dist] = metadata.version(dist)
        except Exception as exc:  # noqa: BLE001 - report every import problem
            packages[dist] = f"MISSING ({exc.__class__.__name__})"
            failures.append(f"package {dist} not importable")
    report["packages"] = packages
    try:
        from query_cost_predictor.contract import load_registry

        registry = load_registry()
        report["feature_registry"] = {"fields": len(registry.fields), "model_features": len(registry.feature_names())}
    except Exception as exc:  # noqa: BLE001
        failures.append(f"feature registry invalid: {exc}")
    report["env_file_loaded"] = bool(getattr(args, "_env_loaded", False))
    if not report["env_file_loaded"]:
        warnings.append(".env not found (create it in runbook step 3 before database steps)")
    if os.environ.get("QCP_DATA_ROOT"):
        try:
            from query_cost_predictor.paths import data_root

            report["data_root"] = str(data_root())
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"QCP_DATA_ROOT: {exc}")
    report["failures"] = failures
    report["warnings"] = warnings
    _print_json(report)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("ENV-CHECK: " + ("FAIL" if failures else "PASS"))
    return 1 if failures else 0


def cmd_db_init(args: argparse.Namespace) -> int:
    from query_cost_predictor.dbinit import run_db_init

    report = run_db_init()
    _print_json(report["summary"])
    print("DB-INIT: " + report["status"])
    return 0 if report["status"] == "PASS" else 1


def cmd_register_snapshot(args: argparse.Namespace) -> int:
    from query_cost_predictor.snapshot import register_snapshot

    report = register_snapshot(args.scale_factor)
    _print_json(report["summary"])
    print("REGISTER-SNAPSHOT: " + report["status"])
    return 0 if report["status"] == "PASS" else 1


def cmd_pilot_audit(args: argparse.Namespace) -> int:
    from query_cost_predictor.paths import pilot_output_dir, reports_dir
    from query_cost_predictor.pilot_audit import DEFAULT_SEED, run_pilot_audit

    pilot_dir = Path(args.pilot_dir) if args.pilot_dir else pilot_output_dir()
    out_dir = Path(args.out_dir) if args.out_dir else reports_dir()
    audit = run_pilot_audit(pilot_dir, out_dir, seed=args.seed or DEFAULT_SEED, n_folds=args.folds,
                            make_figures=not args.no_figures)
    statuses = [c["status"] for c in audit["data_quality"]]
    claims = [c["status"] for c in audit["claim_check"]]
    print(f"Outputs written to {out_dir}")
    print(f"data-quality checks: PASS={statuses.count('PASS')} WARN={statuses.count('WARN')} "
          f"FAIL={statuses.count('FAIL')} INFO={statuses.count('INFO')}")
    print(f"claim check: MATCH={claims.count('MATCH')} MISMATCH={claims.count('MISMATCH')} "
          f"NOT_COMPARABLE={claims.count('NOT_COMPARABLE')} MISSING_METRIC={claims.count('MISSING_METRIC')}")
    for name, item in audit["high_runtime_classification"].items():
        print(f"high-runtime class {name}: {item['status']} (positives={item['n_positive']})")
    print("PILOT-AUDIT: COMPLETED")
    return 0


def cmd_smoke_manifest(args: argparse.Namespace) -> int:
    from query_cost_predictor.smoke_manifest import build_and_register_manifest

    report = build_and_register_manifest(Path(args.config) if args.config else None)
    _print_json(report["summary"])
    print("SMOKE-MANIFEST: " + report["status"])
    return 0 if report["status"] == "PASS" else 1


def cmd_smoke_collect(args: argparse.Namespace) -> int:
    from query_cost_predictor.smoke_collector import run_collection

    return run_collection(args.manifest_id, max_keys=args.max_keys)


def cmd_smoke_derive(args: argparse.Namespace) -> int:
    from query_cost_predictor.smoke_derive import derive

    report = derive(args.manifest_id, write_db=not args.no_db)
    _print_json(report["summary"])
    print("SMOKE-DERIVE: " + report["status"])
    return 0 if report["status"] == "PASS" else 1


def cmd_smoke_validate(args: argparse.Namespace) -> int:
    from query_cost_predictor.smoke_validate import validate

    report = validate(args.manifest_id, use_db=not args.no_db)
    _print_json(report["summary"])
    print("SMOKE-VALIDATE: " + report["status"])
    return 0 if report["status"] == "PASS" else 1


def cmd_verify_rewrites(args: argparse.Namespace) -> int:
    from query_cost_predictor.rewrite_verify import run_verification

    report = run_verification(Path(args.config) if args.config else None)
    _print_json(report["summary"])
    print("REWRITE-VERIFY: " + report["status"])
    return 0 if report["status"] == "COMPLETED" else 1


def cmd_build_poster_evidence(args: argparse.Namespace) -> int:
    from query_cost_predictor.reporting.builder import build_poster_evidence

    report = build_poster_evidence(Path(args.out_dir) if args.out_dir else None)
    _print_json(report["summary"])
    print("BUILD-POSTER-EVIDENCE: " + report["status"])
    return 0 if report["status"] == "PASS" else 1


def cmd_final_plan(args: argparse.Namespace) -> int:
    from query_cost_predictor.final_plan import summarize_final_plan

    _print_json(summarize_final_plan())
    print("FINAL-PLAN: PLANNED (arithmetic from config/final_study_plan.yaml; nothing collected)")
    return 0


# ---------------------------------------------------------------------------------- expanded release
def cmd_expanded_arithmetic(args: argparse.Namespace) -> int:
    from query_cost_predictor.paths import ensure_dir, expanded_reports_dir
    from query_cost_predictor.release_spec import load_release_spec, release_arithmetic

    spec = load_release_spec(Path(args.spec) if args.spec else None)
    arithmetic = release_arithmetic(spec)
    out = ensure_dir(expanded_reports_dir()) / "release_arithmetic.json"
    out.write_text(json.dumps(arithmetic, indent=2, default=str), encoding="utf-8")
    _print_json({"totals": arithmetic["totals"], "by_component": arithmetic["by_component"],
                 "worst_case_hours": arithmetic["worst_case_hours"], "report": str(out)})
    verdict = "meets target" if arithmetic["meets_target_min_proposed_keys"] else "BELOW TARGET"
    print(f"EXPANDED-ARITHMETIC: PLANNED ({verdict}; proposed keys are not measured labels)")
    return 0 if arithmetic["meets_target_min_proposed_keys"] else 1


def cmd_expanded_readiness(args: argparse.Namespace) -> int:
    from query_cost_predictor.expanded_collect import readiness

    report = readiness(Path(args.spec) if args.spec else None)
    _print_json(report["summary"])
    print("EXPANDED-READINESS: " + report["status"])
    return 0 if report["status"] == "PASS" else 1


def cmd_expanded_manifest(args: argparse.Namespace) -> int:
    from query_cost_predictor.expanded_manifest import build_and_register_expanded_manifest

    report = build_and_register_expanded_manifest(Path(args.spec) if args.spec else None)
    _print_json(report["summary"])
    print("EXPANDED-MANIFEST: " + report["status"])
    return 0 if report["status"] == "PASS" else 1


def cmd_expanded_plan_check(args: argparse.Namespace) -> int:
    from query_cost_predictor.expanded_manifest import plan_check

    report = plan_check(args.manifest_id)
    _print_json(report["summary"])
    print("EXPANDED-PLAN-CHECK: " + report["status"])
    return 0 if report["status"] == "PASS" else 1


def cmd_expanded_collect(args: argparse.Namespace) -> int:
    from query_cost_predictor.expanded_collect import run_expanded_collection

    return run_expanded_collection(args.manifest_id, phase=args.phase, max_keys=args.max_keys,
                                   max_minutes=args.max_minutes)


def cmd_expanded_derive(args: argparse.Namespace) -> int:
    from query_cost_predictor.expanded_derive import derive_expanded

    report = derive_expanded(args.manifest_id, write_db=not args.no_db)
    _print_json(report["summary"])
    print("EXPANDED-DERIVE: " + report["status"])
    return 0 if report["status"] == "PASS" else 1


def cmd_expanded_validate(args: argparse.Namespace) -> int:
    from query_cost_predictor.expanded_validate import validate_expanded

    report = validate_expanded(args.manifest_id, scope=args.scope, use_db=not args.no_db)
    _print_json(report["summary"])
    print("EXPANDED-VALIDATE: " + report["status"])
    return {"PASS": 0, "STOP": 4}.get(report["status"], 1)


def cmd_expanded_approve_full(args: argparse.Namespace) -> int:
    from query_cost_predictor.expanded_collect import approve_full

    report = approve_full(args.manifest_id, args.budget_hours)
    _print_json(report["summary"])
    print("EXPANDED-APPROVE-FULL: " + report["status"])
    return 0 if report["status"] == "APPROVED" else 1


def cmd_expanded_eda(args: argparse.Namespace) -> int:
    from query_cost_predictor.expanded_validate import eda_summary

    report = eda_summary(args.manifest_id)
    _print_json(report["summary"])
    print("EXPANDED-EDA: " + report["status"])
    return 0


def cmd_expanded_freeze(args: argparse.Namespace) -> int:
    from query_cost_predictor.expanded_validate import freeze_release

    report = freeze_release(args.manifest_id)
    _print_json(report["summary"])
    print("EXPANDED-FREEZE: " + report["status"])
    return 0 if report["status"] == "FROZEN" else 1


# ---------------------------------------------------------------------------------- RQ1 modeling (Slice A)
def _rq1_evidence_dir(args: argparse.Namespace, config: object) -> Path | None:
    import os

    raw = args.evidence_dir or os.environ.get(config.evidence_dir_env, "").strip()  # type: ignore[attr-defined]
    return Path(raw) if raw else None


class Rq1ReportDestinationError(ValueError):
    """The requested --json-out destination is not a new file outside the repository and the evidence."""


def _path_within(child: Path, root: Path) -> bool:
    import os

    child_text, root_text = os.path.normcase(str(child)), os.path.normcase(str(root))
    try:
        return os.path.commonpath([child_text, root_text]) == root_text
    except ValueError:  # different drives
        return False


def _rq1_report_destination(raw: str, protected: dict[str, Path]) -> Path:
    """Validate a --json-out path; return its resolved form (symlinks and junctions followed)."""
    import os

    requested = Path(raw)
    requested = requested if requested.is_absolute() else Path.cwd() / requested
    resolved = requested.resolve(strict=False)
    for label, root in protected.items():
        for candidate, root_form in ((requested.absolute(), root.absolute()), (resolved, root.resolve(strict=False))):
            if _path_within(candidate, root_form):
                raise Rq1ReportDestinationError(f"--json-out must be outside the {label} ({root}); got {raw}")
    if os.path.lexists(requested) or os.path.lexists(resolved):
        raise Rq1ReportDestinationError(f"--json-out must name a new file; {raw} already exists")
    for parent in resolved.parents:
        if os.path.lexists(parent) and not parent.is_dir():
            raise Rq1ReportDestinationError(f"--json-out parent {parent} is not a directory")
    return resolved


def _write_new_report(destination: Path, document: object, protected: dict[str, Path]) -> None:
    """Create the report exclusively after re-checking containment of the (now existing) parent."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    parent = destination.parent.resolve(strict=True)
    for label, root in protected.items():
        if _path_within(parent, root.resolve(strict=False)):
            raise Rq1ReportDestinationError(f"--json-out parent resolves inside the {label}")
    with (parent / destination.name).open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(document, indent=2, default=str) + "\n")


def _rq1_source(repo: Path, config: object) -> tuple[dict[str, object] | None, list[str]]:
    from query_cost_predictor.rq1_provenance import SourceProvenanceError, collect_source_state, evidence_run_blockers

    try:
        state = collect_source_state(repo, config.allowed_untracked_paths, config.tested_sklearn_version)  # type: ignore[attr-defined]
    except SourceProvenanceError as exc:
        return None, [f"source state unavailable: {exc}"]
    return state.as_dict(), evidence_run_blockers(state)


def cmd_rq1_check_inputs(args: argparse.Namespace) -> int:
    from collections import Counter

    from query_cost_predictor.paths import repo_root
    from query_cost_predictor.rq1_config import load_rq1_config
    from query_cost_predictor.rq1_inputs import check_inputs, resolve_data_root

    config = load_rq1_config()
    evidence_dir = _rq1_evidence_dir(args, config)
    if evidence_dir is None:
        print(f"ERROR: pass --evidence-dir or set {config.evidence_dir_env}", file=sys.stderr)
        print("RQ1-CHECK-INPUTS: FAIL (no evidence directory)")
        return 2
    repo = repo_root()
    boundaries = {"repository": repo, "evidence directory": evidence_dir}
    destination = None
    if args.json_out:
        try:
            destination = _rq1_report_destination(args.json_out, boundaries)
        except Rq1ReportDestinationError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            print("RQ1-CHECK-INPUTS: FAIL (invalid --json-out; nothing was checked or written)")
            return 2
    protected = [repo / p.rstrip("/") for p in config.allowed_untracked_paths]
    report = check_inputs(config, evidence_dir, repo_root=repo, archive=Path(args.archive) if args.archive else None,
                          data_root=resolve_data_root(config.data_root_env), protected_paths=protected)
    source, blockers = _rq1_source(repo, config)
    summary = {
        "data_checks": report.status,
        "checks_passed": len(report.checks),
        "development_dataset": report.dataset.summary() if report.dataset is not None else None,
        "per_fold": report.counts.get("per_fold"),
        "required_packaged_files_verified": report.counts.get("required_packaged_files"),
        "provenance_classes": dict(Counter(item["provenance"] for item in report.identities)),
        "archive": report.archive_status,
        "supplementary_approval": report.supplementary_approval_status,
        "collection_control_annotations": report.collection_control_annotations,
        "original_release_validation": report.original_validation,
        "config_sha256": config.sha256,
        "source": None if source is None else {k: source[k] for k in (
            "source_revision", "tracked_dirty", "tracked_changes", "disallowed_untracked_paths",
            "ignored_execution_files", "package_location", "package_from_checkout", "sklearn_version")},
        "evidence_run_eligibility": "ELIGIBLE" if not blockers else "NOT_ELIGIBLE",
        "evidence_run_blockers": blockers,
    }
    if destination is not None:
        try:
            _write_new_report(destination, {**report.to_json(), "source": source, "evidence_run_blockers": blockers,
                                            "config_sha256": config.sha256}, boundaries)
        except (Rq1ReportDestinationError, FileExistsError, OSError) as exc:
            print(f"ERROR: the report was not written: {exc}", file=sys.stderr)
            print("RQ1-CHECK-INPUTS: FAIL (data checks passed, but --json-out could not be created safely)")
            return 2
        summary["json_out"] = str(destination)
    _print_json(summary)
    codes = ", ".join(report.original_validation.get("stop_codes", []))
    print(f"Original release validation: {report.original_validation.get('status')} ({codes}) - preserved, not PASS")
    print("Evidence-run eligibility: " + ("ELIGIBLE" if not blockers else "NOT_ELIGIBLE (" + "; ".join(blockers) + ")"))
    print("RQ1-CHECK-INPUTS: PASS (data checks only; this command writes no run directory)")
    return 0


def cmd_rq1_run(args: argparse.Namespace) -> int:
    """The single evidence-producing RQ1 run.

    Exit codes: 0 COMPLETE (and the report copy succeeded); 3 refused before any
    run directory was created; 4 COMPLETE and verified, but the convenience copy
    under reports/rq1/ failed (the run itself is unchanged); 1 the run FAILED.
    """
    from datetime import datetime, timezone

    from query_cost_predictor.paths import data_root, repo_root
    from query_cost_predictor.rq1_config import load_rq1_config
    from query_cost_predictor.rq1_evaluate import load_input_record
    from query_cost_predictor.rq1_inputs import resolve_data_root
    from query_cost_predictor.rq1_run import (
        RunRefusedError,
        assert_output_location_safe,
        assert_paths_fit,
        copy_report_after_complete,
        create_run_context,
        execute_run,
        initial_provenance,
        make_run_id,
        production_requirements,
        production_stages,
        read_complete_run,
    )

    config = load_rq1_config()
    try:
        stages = production_stages(config.stages)
    except RunRefusedError as exc:
        print(f"REFUSED: {exc}")
        print("RQ1-RUN: REFUSED (incomplete pipeline; no study was produced)")
        return 3
    evidence_dir = _rq1_evidence_dir(args, config)
    if evidence_dir is None:
        print(f"ERROR: pass --evidence-dir or set {config.evidence_dir_env}", file=sys.stderr)
        return 2
    repo = repo_root()
    source, blockers = _rq1_source(repo, config)
    if blockers:
        print("REFUSED: an evidence run needs clean, committed source: " + "; ".join(blockers))
        print("RQ1-RUN: REFUSED (source not eligible; no run directory was created)")
        return 3
    archive = Path(args.archive) if args.archive else None
    approval_root = resolve_data_root(config.data_root_env)
    approval = load_input_record(config, repo)["supplementary_provenance_outside_package"]["full_collection_approval"]
    inputs = {"evidence directory": evidence_dir, "archive": archive, "repository": repo,
              "supplementary approval record": (approval_root / approval["data_root_path"]) if approval_root else None}
    base = data_root() / "results" / "rq1" / config.spec_id
    report_root = repo / "reports" / "rq1"
    run_id = make_run_id(datetime.now(timezone.utc), config.sha256, (source or {}).get("source_revision"))
    requirements = production_requirements(config)
    try:
        assert_output_location_safe(base, inputs)
        assert_output_location_safe(report_root, {k: v for k, v in inputs.items() if k != "repository"})
        assert_paths_fit(base / run_id, requirements.outputs)
    except RunRefusedError as exc:
        print(f"REFUSED: {exc}")
        print("RQ1-RUN: REFUSED (unsafe output location; no run directory was created)")
        return 3
    context = create_run_context(base, run_id, args.run_label, config, evidence_dir=evidence_dir, archive=archive,
                                 data_root=approval_root, repo_root=repo, source_state=source or {},
                                 requirements=requirements)
    context.provenance.update(initial_provenance(config, source, blockers))
    execute_run(context, stages)  # raises (and leaves FAILED) on any stage or finalization failure
    read_complete_run(context.run_dir)
    print(f"run directory: {context.run_dir}")
    print("RQ1-RUN: COMPLETE (verified against output_manifest.json)")
    try:
        copy = copy_report_after_complete(context.run_dir, report_root,
                                          {k: v for k, v in inputs.items() if k != "repository"})
    except Exception as exc:  # noqa: BLE001 - the completed run stays valid; report the copy failure separately
        print(f"REPORT-COPY: FAILED ({exc.__class__.__name__}: {exc}). The COMPLETE run in {context.run_dir} is "
              "unchanged and remains the study output.")
        return 4
    print(f"report copy: {copy / 'report.md'} (figures copied alongside; relative links preserved)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="query_cost_predictor",
                                     description="Poster evidence milestone and expanded-release commands.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("env-check", help="report Python/package versions and configuration sanity")
    p.add_argument("--json-out", default=None)
    p.set_defaults(func=cmd_env_check)

    sub.add_parser("db-init", help="create roles, databases and apply evidence migrations").set_defaults(func=cmd_db_init)

    p = sub.add_parser("register-snapshot", help="register a loaded TPC-H database as an immutable snapshot")
    p.add_argument("--scale-factor", required=True, choices=["0.1", "1"])
    p.set_defaults(func=cmd_register_snapshot)

    p = sub.add_parser("pilot-audit", help="independently audit reference_pilot/output (read-only)")
    p.add_argument("--pilot-dir", default=None)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--no-figures", action="store_true")
    p.set_defaults(func=cmd_pilot_audit)

    p = sub.add_parser("smoke-manifest", help="build and register the poster-smoke manifest (no query execution)")
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_smoke_manifest)

    p = sub.add_parser("smoke-collect", help="run or resume the poster-smoke collection")
    p.add_argument("--manifest-id", default=None)
    p.add_argument("--max-keys", type=int, default=None, help="stop after this many keys (for staged runs)")
    p.set_defaults(func=cmd_smoke_collect)

    p = sub.add_parser("smoke-derive", help="rebuild labels and features from raw evidence")
    p.add_argument("--manifest-id", default=None)
    p.add_argument("--no-db", action="store_true", help="write derived files only")
    p.set_defaults(func=cmd_smoke_derive)

    p = sub.add_parser("smoke-validate", help="validate smoke evidence and write the validation report")
    p.add_argument("--manifest-id", default=None)
    p.add_argument("--no-db", action="store_true", help="skip database reconciliation (reported as not checked)")
    p.set_defaults(func=cmd_smoke_validate)

    p = sub.add_parser("verify-rewrites", help="manual reference-rewrite verification harness (no LLM)")
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_verify_rewrites)

    p = sub.add_parser("build-poster-evidence", help="build evidence-backed poster tables and figures")
    p.add_argument("--out-dir", default=None)
    p.set_defaults(func=cmd_build_poster_evidence)

    sub.add_parser("final-plan", help="print planned final-dataset arithmetic (PLANNED only)").set_defaults(func=cmd_final_plan)

    p = sub.add_parser("expanded-arithmetic", help="expanded release: proposed-key arithmetic from the spec (no database)")
    p.add_argument("--spec", default=None)
    p.set_defaults(func=cmd_expanded_arithmetic)

    p = sub.add_parser("expanded-readiness", help="expanded release: fresh preflight, V008, snapshots, disk")
    p.add_argument("--spec", default=None)
    p.set_defaults(func=cmd_expanded_readiness)

    p = sub.add_parser("expanded-manifest", help="expanded release: build and register the manifest (no query execution)")
    p.add_argument("--spec", default=None)
    p.set_defaults(func=cmd_expanded_manifest)

    p = sub.add_parser("expanded-plan-check", help="expanded release: plain EXPLAIN of every instance (nothing executed)")
    p.add_argument("--manifest-id", default=None)
    p.set_defaults(func=cmd_expanded_plan_check)

    p = sub.add_parser("expanded-collect", help="expanded release: bounded sample or approved, batched full collection")
    p.add_argument("--manifest-id", default=None)
    p.add_argument("--phase", required=True, choices=["sample", "full"])
    p.add_argument("--max-keys", type=int, default=None, help="batch bound (required for --phase full)")
    p.add_argument("--max-minutes", type=float, default=None, help="stop between keys after this many minutes")
    p.set_defaults(func=cmd_expanded_collect)

    p = sub.add_parser("expanded-derive", help="expanded release: rebuild labels, features and splits from raw evidence")
    p.add_argument("--manifest-id", default=None)
    p.add_argument("--no-db", action="store_true", help="write derived files only")
    p.set_defaults(func=cmd_expanded_derive)

    p = sub.add_parser("expanded-validate", help="expanded release: integrity checks (+ budget for sample, gates for full)")
    p.add_argument("--manifest-id", default=None)
    p.add_argument("--scope", required=True, choices=["sample", "full"])
    p.add_argument("--no-db", action="store_true", help="skip database reconciliation (reported as not checked)")
    p.set_defaults(func=cmd_expanded_validate)

    p = sub.add_parser("expanded-approve-full", help="expanded release: record the explicit decision to run the full collection")
    p.add_argument("--manifest-id", default=None)
    p.add_argument("--budget-hours", type=float, required=True)
    p.set_defaults(func=cmd_expanded_approve_full)

    p = sub.add_parser("expanded-eda", help="expanded release: descriptive summaries (no model is trained)")
    p.add_argument("--manifest-id", default=None)
    p.set_defaults(func=cmd_expanded_eda)

    p = sub.add_parser("expanded-freeze", help="expanded release: immutable release record after a full PASS")
    p.add_argument("--manifest-id", default=None)
    p.set_defaults(func=cmd_expanded_freeze)

    p = sub.add_parser("rq1-check-inputs",
                       help="RQ1: verify the pinned evidence package offline (no database; writes no run directory)")
    p.add_argument("--evidence-dir", default=None, help="extracted evidence package (or QCP_EXPANDED_RELEASE_DIR)")
    p.add_argument("--archive", default=None, help="optional original archive; its SHA-256 is then verified")
    p.add_argument("--json-out", default=None, help="optional path for the full check report (outside the evidence)")
    p.set_defaults(func=cmd_rq1_check_inputs)

    p = sub.add_parser("rq1-run", help="RQ1: the single evidence-producing run (refuses until every stage exists)")
    p.add_argument("--evidence-dir", default=None, help="extracted evidence package (or QCP_EXPANDED_RELEASE_DIR)")
    p.add_argument("--archive", default=None, help="optional original archive; its SHA-256 is then verified")
    p.add_argument("--run-label", required=True, help="short label recorded in the run record")
    p.set_defaults(func=cmd_rq1_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        env_path = load_repo_env()
    except Exception as exc:  # noqa: BLE001 - a malformed .env must stop every command
        print(f"ERROR: cannot read .env: {exc}", file=sys.stderr)
        return 2
    args._env_loaded = env_path is not None
    handler: Callable[[argparse.Namespace], int] = args.func
    try:
        return int(handler(args))
    except KeyboardInterrupt:
        print("INTERRUPTED by user", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - print a clear failure and the traceback for the user to share
        traceback.print_exc()
        print(f"{args.command.upper()}: FAIL ({exc.__class__.__name__}: {exc})", file=sys.stderr)
        return 1
