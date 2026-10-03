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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="query_cost_predictor", description="Poster evidence milestone commands.")
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
