"""Validate selected inputs, run the live pipeline, or replay stored evidence."""
import argparse
import json
from pathlib import Path

from .contract import default_config, validate_config, load_queries


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "run"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--input", type=Path, required=True)
        cmd.add_argument("--config", type=Path)
        if name == "run":
            cmd.add_argument("--output", type=Path, required=True)
            cmd.add_argument("--container", required=True, help="Docker benchmark container name or ID")
            cmd.add_argument("--docker", help="Private local Docker CLI path, if needed")
            cmd.add_argument("--desktop", help="Private Docker Desktop executable path, if needed")
            cmd.add_argument("--resume", action="store_true")
            cmd.add_argument("--env-file", type=Path, help="Private local credentials file; never copied into output")
    replay = sub.add_parser("replay")
    replay.add_argument("--evidence", type=Path, required=True, help="Separate final evidence ZIP or folder")
    replay.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "replay":
            from .replay import replay
            result = replay(args.evidence, args.output)
        else:
            manifest = json.loads(args.input.read_text(encoding="utf-8"))
            config = validate_config(json.loads(args.config.read_text(encoding="utf-8")) if args.config else default_config())
            queries = load_queries(manifest)
            if args.command == "validate":
                result = {"status": "INPUT_CHECKED", "queries": [{"id": q["id"], "status": q["input_status"], "reason": q["input_reason"]} for q in queries],
                          "maximum_model_calls": sum(q["input_status"] == "SUPPORTED" for q in queries),
                          "planned_environment_cases": len(queries)*4, "model_calls": 0, "sql_executions": 0}
            else:
                from .credentials import load_private_env, reader_environment
                if args.env_file: load_private_env(args.env_file)
                reader_environment()
                from .backend import LiveBackend
                from .pipeline import run
                with LiveBackend(args.container, docker=args.docker, desktop=args.desktop) as backend:
                    summary = run(manifest, config, args.output, backend, resume=args.resume)
                result = {"scope": summary["scope"], "planned_cases": summary["planned_cases"], "case_counts": summary["case_counts"],
                          "primary_all_four": sum(q["recommended_in_all_four"] for q in summary["queries"])}
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        # Connection/provider errors may contain local paths or credentials.
        print(json.dumps({"status": "STOPPED", "exception_class": type(exc).__name__,
                          "action": "Inspect the saved stage evidence and setup guide; no automatic retry"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
