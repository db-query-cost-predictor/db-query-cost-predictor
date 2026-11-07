"""Live correctness and timing stages for one supported query/environment."""
from dataclasses import asdict
from hashlib import sha256
from rq2_pipeline.database import connect
from rq2_pipeline.host import capture_environment as probe, power_record
from rq2_pipeline.protocol import CONFIGS, OPTIONS, frozen_identity
from rq2_pipeline.executor import capture_effective_settings, run_statement
from rq2_pipeline.fixture_registry import capture_catalog, family_preconditions, evaluate_registry
from rq2_pipeline.verification import compare_on_snapshot
from rq2_pipeline.decisions import DecisionPolicy, Gate, Verification, evaluate_case
from rq2_pipeline.environment import EnvironmentChangedError, assert_environment_unchanged
from rq2_pipeline.journal import AppendOnlyJournal
from rq2_pipeline.storage import write_json
from rq2_pipeline.scheduler import measure, restore_pairs
from rq2_pipeline.timing import milliseconds_to_microseconds
from rq2_pipeline.reports import timing_medians
from rq2_pipeline.safety import SqlSafetyError, validate_read_only_sql

def execute(conn, sql, settings, *, measured=False, cap=None):
    statement = f"EXPLAIN ({OPTIONS if measured else 'SETTINGS, SUMMARY ON, FORMAT JSON'}) {sql}" if cap is None else sql
    return run_statement(conn, statement, settings=settings, statement_timeout_ms=15000,
                         lock_timeout_ms=5000, watchdog_grace_ms=2000, fetch_rows_cap=cap)


def base_row(query, case):
    return {"query_id": query["id"], "query_type": query["query_type"], "semantic_group_id": query["semantic_group_id"],
            "database": case["database"], "configuration": case["configuration_name"],
            "source_modeling_key": case["modeling_key"], "original_sql_sha256": query["original_sha256"],
            "status": "NOT_STARTED", "reason": "Not started", "verification_passed": False,
            "original_median_ms": None, "rewrite_median_ms": None}


def run_case(identity, query, case, candidate, folder, paths):
    row = base_row(query, case)
    verification = Verification(generation=Gate.PASSED)
    def decide():
        decision = evaluate_case(verification, (), ())
        row.update(status=decision.outcome.value, reason=decision.reason)
        return row
    try:
        original = validate_read_only_sql(query["sql"]).sql
        rewrite = validate_read_only_sql(candidate["sql"]).sql
    except SqlSafetyError as exc:
        verification = Verification(Gate.PASSED, Gate.FAILED)
        row["safety_code"] = exc.reason_code
        return decide()
    row.update(raw_sql_sha256=candidate["raw_sql_sha256"], executed_sql_sha256=sha256(rewrite.encode()).hexdigest())
    write_json(folder / "executed-sql.json", {"original": original, "candidate": rewrite,
               "raw_sql_sha256": candidate["raw_sql_sha256"], "executed_sql_sha256": row["executed_sql_sha256"]})
    settings = CONFIGS[case["configuration_name"]]
    with connect(case["database"]) as conn:
        before = probe(conn, paths["docker"], paths["container"], paths["desktop"])
        write_json(folder / "environment-before.json", before)
        assert_environment_unchanged(identity["environment"], before)
        if not frozen_identity(conn, identity["database_identities"][case["database"]]):
            raise EnvironmentChangedError("Database snapshot identity changed")
        effective = capture_effective_settings(conn, settings)
        write_json(folder / "effective-settings.json", effective)
        if effective != identity["effective_settings"][case["database"] + "/" + case["configuration_name"]]:
            raise EnvironmentChangedError("Effective database settings changed")
        # Schema-qualified base tables cannot be redirected into the synthetic fixture namespace.
        catalog = capture_catalog(conn)
        precondition = family_preconditions(query, catalog)
        checks = []
        for check in query["preconditions"]:
            value = execute(conn, validate_read_only_sql(check["sql"]).sql, settings, cap=1)
            checks.append({"name": check["name"], "execution": asdict(value),
                           "passed": value.status == "completed" and value.rows == [(True,)]})
        write_json(folder / "preconditions.json", {"family": precondition, "checks": checks})
        if any(check["execution"]["status"] != "completed" for check in checks):
            verification = Verification(Gate.PASSED, Gate.PASSED, Gate.INCONCLUSIVE)
            return decide()
        if not precondition["passed"] or not all(check["passed"] for check in checks):
            verification = Verification(Gate.PASSED, Gate.PASSED, Gate.FAILED)
            return decide()
        try:
            comparison = compare_on_snapshot(conn, original, rewrite, settings=settings, ordering_required=query["ordering_required"])
        except Exception as exc:
            comparison = {"status": "INCONCLUSIVE", "detail": "Snapshot-result check raised " + type(exc).__name__,
                          "exception_class": type(exc).__name__, "sqlstate": getattr(exc, "sqlstate", None), "executions": {}}
        write_json(folder / "snapshot-comparison.json", comparison)
        snapshot_gate = {"AGREED_ON_TESTED_INPUTS": Gate.PASSED, "MISMATCH": Gate.FAILED}.get(comparison["status"], Gate.INCONCLUSIVE)
        verification = Verification(Gate.PASSED, Gate.PASSED, Gate.PASSED, snapshot_gate)
        if snapshot_gate != Gate.PASSED:
            return decide()
        try:
            fixtures = evaluate_registry(conn, query, rewrite, catalog)
        except ValueError as exc:
            fixtures = {"status": "REVIEW_REQUIRED", "exception_class": type(exc).__name__}
        except Exception as exc:
            fixtures = {"status": "INCONCLUSIVE", "exception_class": type(exc).__name__,
                        "sqlstate": getattr(exc, "sqlstate", None)}
        write_json(folder / "fixtures.json", fixtures)
        fixture_gate = {"AGREED_ON_TESTED_FIXTURES": Gate.PASSED,
                        "REJECTED_COUNTEREXAMPLE": Gate.FAILED,
                        "INCONCLUSIVE": Gate.INCONCLUSIVE}.get(fixtures["status"], Gate.NOT_RUN)
        verification = Verification(Gate.PASSED, Gate.PASSED, Gate.PASSED, Gate.PASSED, fixture_gate)
        if fixture_gate != Gate.PASSED:
            return decide()
        row["verification_passed"] = True
        if original == rewrite:
            decision = evaluate_case(verification, (), (), unchanged_sql=True)
            row.update(status=decision.outcome.value, reason=decision.reason)
            return row
        power_baseline = identity["environment"]["windows_power"]
        with AppendOnlyJournal(folder / "measurement-journal.jsonl") as measurements:
            def measured(sql, label):
                # Power checks precede the execution, outside server execution time.
                if power_record() != power_baseline:
                    raise EnvironmentChangedError("Power mode or source changed")
                def operation():
                    outcome = execute(conn, sql, settings, measured=True)
                    record = {"status": outcome.status, "sql_sha256": sha256(sql.encode()).hexdigest(),
                              "execution": asdict(outcome), "execution_us": None}
                    if outcome.status == "completed":
                        try:
                            record["execution_us"] = milliseconds_to_microseconds(outcome.document["Execution Time"])
                        except ValueError:
                            record["status"] = "UNUSABLE_TIMING_RESOLUTION"
                    write_json(folder / "measurements" / (label + ".json"), record)
                    return record
                record = measurements.run_once(label, operation, identity={"study": identity["identity_sha256"]})
                return record["execution_us"]
            policy_values = identity["config"]["policy"]
            policy = DecisionPolicy(primary_reduction_percent=policy_values["primary_runtime_reduction_percent"],
                                    profile_percents=tuple(policy_values["supplementary_runtime_reduction_percents"]))
            timing = measure(original, rewrite, measured, policy=policy)
        write_json(folder / "timing.json", timing)
        after = probe(conn, paths["docker"], paths["container"], paths["desktop"])
        write_json(folder / "environment-after.json", after)
        assert_environment_unchanged(identity["environment"], after)
        if not frozen_identity(conn, identity["database_identities"][case["database"]]):
            raise EnvironmentChangedError("Database snapshot changed during case")
        decision = evaluate_case(verification, restore_pairs(timing["aa_pairs"]), restore_pairs(timing["ab_pairs"]), policy=policy)
        # A warm-up failure is incomplete, even when no first pair exists yet.
        if timing["status"] == "INCONCLUSIVE":
            row.update(status="INCONCLUSIVE", reason=timing["reason"])
        else:
            row.update(status=decision.outcome.value, reason=decision.reason,
                       timing_decision=asdict(decision.timing) if decision.timing else None,
                       **timing_medians(timing))
        return row
