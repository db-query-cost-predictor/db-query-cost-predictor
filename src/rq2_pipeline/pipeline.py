"""General selected-query orchestration with one attempt and durable evidence."""
from hashlib import sha256
from pathlib import Path
import platform
from importlib.metadata import version
import json

from . import __version__
from .contract import validate_config, load_queries
from .journal import AppendOnlyJournal
from .response import GenerationError
from .reports import write_report
from .storage import digest, read_json, write_json, utc_now


def sources():
    root = Path(__file__).parent
    return {p.name: sha256(p.read_bytes()).hexdigest() for p in sorted(root.iterdir()) if p.suffix in {".py", ".json"}}


def case_key(query, case):
    return query["id"] + "/" + case["database"] + "/" + case["configuration_name"]


def row(query, case, status="NOT_STARTED", reason="Not started"):
    return {"query_id": query["id"], "query_type": query["query_type"], "database": case["database"],
            "configuration": case["configuration_name"], "status": status, "reason": reason,
            "verification_passed": False, "original_median_ms": None, "rewrite_median_ms": None}


def commit(journal, key, folder, operation, identity):
    journal.append("STARTED", key, {"identity_sha256": identity["identity_sha256"]})
    try:
        result = operation()
        result_hash = write_json(folder / "result.json", result)
    except BaseException as exc:
        journal.append("FINISHED", key, {"status": "INCONCLUSIVE", "exception_class": type(exc).__name__})
        raise
    journal.append("FINISHED", key, {"status": result["status"], "result_sha256": result_hash})
    return result


def committed(journal, key, folder):
    finish = next(e for e in reversed(journal.entries) if e["case_id"] == key and e["event"] == "FINISHED")
    expected = finish["payload"].get("result_sha256")
    if expected is None:
        return {"status": "INCONCLUSIVE", "reason": "A started item failed or was interrupted; no retry"}
    path = folder / "result.json"
    if sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Committed result differs from its journal hash")
    return read_json(path)


def rows_for(identity, journal, output):
    rows = []
    for query in identity["queries"]:
        for case in query["cases"]:
            value = row(query, case)
            key = case_key(query, case)
            if key in journal.states:
                value.update(committed(journal, key, output / "cases" / key))
            rows.append(value)
    return rows


def run(manifest, config, output, backend, *, resume=False):
    """The backend owns live I/O; tests can exercise the same runner with fakes."""
    config = validate_config(config)
    queries = load_queries(manifest)
    output = Path(output)
    if not resume:
        output.mkdir(parents=True, exist_ok=False)
    elif not output.is_dir():
        raise ValueError("Resume requires an existing run")
    # One writer is acquired before preparation or any model/SQL operation.
    with AppendOnlyJournal(output / "execution-journal.jsonl") as journal:
        if resume:
            identity = read_json(output / "identity.json")
            if (identity["config"] != config or identity["queries"] != queries or identity["sources"] != sources()
                    or digest({k: v for k, v in identity.items() if k != "identity_sha256"}) != identity["identity_sha256"]):
                raise ValueError("Resume input, configuration, source or identity changed")
            journal.recover_incomplete()
            closed = output / "evidence-manifest.json"
            if closed.exists():
                if any(sha256((output / name).read_bytes()).hexdigest() != expected for name, expected in read_json(closed)["files"].items()):
                    raise ValueError("Closed run evidence changed")
                return read_json(output / "summary.json")
        else:
            identity = {"scope": "NEW_PIPELINE_RUN", "release": __version__, "created_at": utc_now(),
                        "queries": queries, "config": config, "sources": sources(),
                        "runtime": {"python": platform.python_version(), "psycopg": version("psycopg")}}
            write_json(output / "selected-input.json", manifest)
            try:
                identity["baseline"] = backend.prepare(queries, config, output) if any(q["input_status"] == "SUPPORTED" for q in queries) else None
            except Exception as exc:
                write_json(output / "preflight-failure.json", {"stage": "PREPARATION", "exception_class": type(exc).__name__, "model_calls": 0})
                write_report(output, [row(q, c, "INCONCLUSIVE", "Preparation failed before generation") for q in queries for c in q["cases"]], queries,
                             scope="PREPARATION_FAILED", identity_sha256=None)
                raise
            identity["identity_sha256"] = digest(identity)
            write_json(output / "identity.json", identity)
        backend.bind(identity)
        try:
            for query in queries:
                generation_key = query["id"] + "/generation"
                folder = output / "generation" / query["id"]
                if generation_key not in journal.states:
                    def operation():
                        if sources() != identity["sources"]:
                            raise ValueError("Runtime source changed")
                        if query["input_status"] != "SUPPORTED":
                            return {"status": query["input_status"], "reason": query["input_reason"]}
                        backend.check(identity)
                        try:
                            return backend.generate(query, config["generation"], folder)
                        except GenerationError:
                            return {"status": "GENERATION_FAILED", "reason": "Provider call or response contract failed; no retry"}
                    commit(journal, generation_key, folder, operation, identity)
                candidate = committed(journal, generation_key, folder)
                for case in query["cases"]:
                    key = case_key(query, case)
                    if key in journal.states:
                        continue
                    def evaluate():
                        if sources() != identity["sources"]:
                            raise ValueError("Runtime source changed")
                        if candidate["status"] != "GENERATED":
                            return row(query, case, candidate["status"], candidate.get("reason", "Generation did not complete"))
                        backend.check(identity)
                        return backend.evaluate(identity, query, case, candidate, output / "cases" / key)
                    commit(journal, key, output / "cases" / key, evaluate, identity)
        finally:
            summary = write_report(output, rows_for(identity, journal, output), queries,
                                   scope=identity["scope"], identity_sha256=identity["identity_sha256"])
    manifest_files = {p.relative_to(output).as_posix(): sha256(p.read_bytes()).hexdigest()
                      for p in sorted(output.rglob("*")) if p.is_file() and not p.name.endswith(".lock") and p.name != "evidence-manifest.json"}
    if not (output / "evidence-manifest.json").exists():
        write_json(output / "evidence-manifest.json", {"files": manifest_files})
    return summary
