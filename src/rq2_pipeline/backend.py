"""Live PostgreSQL/Groq adapter; private host bindings never enter identity files."""
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
import os
import shutil

from psycopg import sql

from .database import connect
from .environment import assert_environment_unchanged, environment_identity, require_ac_power
from .executor import capture_effective_settings, run_statement
from .host import capture_environment
from .protocol import CONFIGS, readiness, frozen_identity
from .provider import generate
from .snapshot import TPCH_TABLES, index_manifest_sha256
from .stages import run_case
from .storage import write_json, read_json
from .verification import compare_on_snapshot


class LiveBackend:
    def __init__(self, container, *, docker=None, desktop=None):
        program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
        candidates = [Path.home() / "AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe",
                      program_files / "Docker/Docker/resources/bin/docker.exe"]
        docker = docker or shutil.which("docker") or next((str(p) for p in candidates if p.is_file()), "docker")
        root = Path(docker).parent.parent.parent
        desktop = desktop or str(root / "Docker Desktop.exe" if (root / "Docker Desktop.exe").is_file()
                                 else program_files / "Docker/Docker/Docker Desktop.exe")
        self.paths = {"container": container, "docker": docker, "desktop": desktop}
        self.identity = None
        self._lock = None

    def close(self):
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _environment(self, conn):
        return capture_environment(conn, self.paths["docker"], self.paths["container"], self.paths["desktop"])

    def prepare(self, queries, config, output):
        if not os.environ.get("GROQ_API_KEY", "").strip():
            raise ValueError("GROQ_API_KEY is missing")
        self._lock = connect("tpch_sf0_1")
        if not self._lock.execute("SELECT pg_try_advisory_lock(724081,1)").fetchone()[0]:
            raise RuntimeError("Another pipeline owns the benchmark lock")
        snapshots, settings, baseline = {}, {}, None
        for database in config["measurement"]["databases"]:
            with connect(database) as conn:
                snapshots[database] = readiness(conn, database)
                if not snapshots[database]["passed"]:
                    raise ValueError("Database readiness failed")
                current = self._environment(conn)
                require_ac_power(current)
                host = config["host"]
                if (current["postgresql"]["version_num"] != host["postgresql_version_num"]
                        or current["container_resource_limits"]["NanoCpus"] != host["container_nano_cpus"]
                        or current["container_resource_limits"]["Memory"] != host["container_memory_bytes"]
                        or environment_identity(current)["windows_power"]["scheme_guid"] != host["power_scheme_guid"]):
                    raise ValueError("Host differs from the declared measurement configuration")
                if baseline is None: baseline = current
                else: assert_environment_unchanged(baseline, current)
                for name, values in CONFIGS.items():
                    settings[database + "/" + name] = capture_effective_settings(conn, values)
                    control = compare_on_snapshot(conn, "SELECT 1 AS value UNION SELECT 1 AS value",
                                                  "SELECT 1 AS value UNION ALL SELECT 1 AS value", settings=values)
                    write_json(output / "preflight" / database / name / "negative-control.json", control)
                    if control["status"] != "MISMATCH":
                        raise ValueError("Negative control was not rejected")
        return {"environment": baseline, "database_identities": snapshots, "effective_settings": settings}

    def bind(self, identity):
        self.identity = identity
        if identity["baseline"] is not None and self._lock is None:
            self._lock = connect("tpch_sf0_1")
            if not self._lock.execute("SELECT pg_try_advisory_lock(724081,1)").fetchone()[0]:
                raise RuntimeError("Another pipeline owns the benchmark lock")

    def check(self, identity):
        baseline = identity["baseline"]
        for database in identity["config"]["measurement"]["databases"]:
            with connect(database) as conn:
                current = self._environment(conn)
                require_ac_power(current)
                assert_environment_unchanged(baseline["environment"], current)
                if not frozen_identity(conn, baseline["database_identities"][database]):
                    raise RuntimeError("Database snapshot changed; preserve this run")

    def generate(self, query, config, folder):
        started = perf_counter()
        with connect("tpch_sf0_1") as conn:
            columns = conn.execute("SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns "
                                   "WHERE table_schema='public' ORDER BY table_name,ordinal_position").fetchall()
            constraints = conn.execute("SELECT conrelid::regclass::text,pg_get_constraintdef(oid) FROM pg_constraint "
                                       "WHERE connamespace='public'::regnamespace ORDER BY 1,2").fetchall()
            counts = {t: conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()[0] for t in TPCH_TABLES}
            plain = run_statement(conn, "EXPLAIN (SETTINGS, SUMMARY ON, FORMAT JSON) " + query["sql"],
                                  settings=CONFIGS["C1_baseline"], statement_timeout_ms=15000, lock_timeout_ms=5000, watchdog_grace_ms=2000)
            if plain.status != "completed":
                raise RuntimeError("Plain EXPLAIN did not complete")
            context = {"database": "tpch_sf0_1", "settings": CONFIGS["C1_baseline"],
                       "schema": {"columns": columns, "constraints": constraints,
                                  "indexes": index_manifest_sha256(conn)[1], "row_counts": counts},
                       "plain_explain": plain.document}
        context_ms = (perf_counter()-started)*1000
        started = perf_counter()
        result = generate(query, context, config, folder)
        write_json(folder / "wall-clock.json", {"context_ms": context_ms, "provider_stage_ms": (perf_counter()-started)*1000,
                   "scope": "Wall time includes provider-stage evidence handling; distinct from server execution time"})
        return result

    def evaluate(self, identity, query, case, candidate, folder):
        started = perf_counter()
        stage_identity = {**identity, **identity["baseline"]}
        value = run_case(stage_identity, query, case, candidate, folder, self.paths)
        value["gates"] = case_gates(folder, value["status"])
        snapshot = folder / "snapshot-comparison.json"
        if value["status"] == "INCONCLUSIVE" and snapshot.exists():
            record = read_json(snapshot)
            for form, execution in record.get("executions", {}).items():
                if execution["status"] != "completed":
                    value["reason"] = form + " " + execution["status"] + " during snapshot verification"
                    if execution.get("sqlstate"): value["reason"] += "; SQLSTATE " + execution["sqlstate"]
                    break
        value["evidence"] = "cases/" + query["id"] + "/" + case["database"] + "/" + case["configuration_name"]
        value["case_wall_ms"] = (perf_counter()-started)*1000
        return value


def case_gates(folder, outcome):
    def read(name):
        path = folder / name
        return read_json(path) if path.exists() else None
    gates = {name: "NOT_RUN" for name in ("safety", "preconditions", "snapshot", "fixtures", "aa", "timed")}
    if read("executed-sql.json"): gates["safety"] = "PASSED"
    elif outcome == "UNSAFE": gates["safety"] = "FAILED"
    pre = read("preconditions.json")
    if pre:
        gates["preconditions"] = "PASSED" if pre["family"]["passed"] and all(c["passed"] for c in pre["checks"]) else "FAILED"
    for name, filename, passed in (("snapshot", "snapshot-comparison.json", "AGREED_ON_TESTED_INPUTS"),
                                   ("fixtures", "fixtures.json", "AGREED_ON_TESTED_FIXTURES")):
        value = read(filename)
        if value:
            gates[name] = "PASSED" if value["status"] == passed else "FAILED" if value["status"] in {"MISMATCH", "REJECTED_COUNTEREXAMPLE"} else "INCONCLUSIVE"
    timing = read("timing.json")
    if timing:
        aa = timing["aa_pairs"]
        gates["aa"] = "FAILED" if timing["status"] == "AA_BLOCKED" else "PASSED" if len(aa) == 11 and all(p["a_us"] and p["b_us"] for p in aa) else "INCONCLUSIVE"
        ab = timing["ab_pairs"]
        if ab: gates["timed"] = "PASSED" if len(ab) == 11 and all(p["a_us"] and p["b_us"] for p in ab) else "INCONCLUSIVE"
    return gates
