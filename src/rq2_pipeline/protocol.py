"""Fixed supported settings and read-only database readiness."""
from rq2_pipeline.snapshot import capture_identity, row_counts, autovacuum_disabled, statistics_coverage

OPTIONS = "ANALYZE, BUFFERS, WAL, TIMING OFF, SUMMARY ON, SETTINGS, FORMAT JSON"


CONFIGS = {"C1_baseline": {"jit": "off", "work_mem": "4MB"},
           "C2_reduced_work_mem": {"jit": "off", "work_mem": "256kB"}}


EXPECTED = {
    "tpch_sf0_1": dict(zip(("region", "nation", "part", "supplier", "partsupp", "customer", "orders", "lineitem"),
                          (5, 25, 20000, 1000, 80000, 15000, 150000, 600572))),
    "tpch_sf1": dict(zip(("region", "nation", "part", "supplier", "partsupp", "customer", "orders", "lineitem"),
                        (5, 25, 200000, 10000, 800000, 150000, 1500000, 6001215)))}


def readiness(conn, database):
    captured = capture_identity(conn, workload="tpch", database_name=database,
                                scale_factor="0.1" if database.endswith("0_1") else "1")
    counts = row_counts(conn)
    checks = {"expected_counts": counts == EXPECTED[database],
              "manifest_counts": counts == {r["table_name"]: r["row_count"] for r in captured["load_manifest"]},
              "analyzed": all(n > 0 for n in statistics_coverage(conn).values()),
              "autovacuum_disabled": all(autovacuum_disabled(conn).values()),
              "validated_constraints": not conn.execute("SELECT count(*) FROM pg_constraint WHERE connamespace='public'::regnamespace AND NOT convalidated").fetchone()[0]}
    return {**captured, "row_counts": counts, "checks": checks, "passed": all(checks.values())}


def frozen_identity(conn, expected):
    current = capture_identity(conn, workload="tpch", database_name=expected["identity"]["database_name"],
                               scale_factor=expected["identity"]["scale_factor"])
    return current["snapshot_id"] == expected["snapshot_id"]
