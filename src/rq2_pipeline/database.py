"""Standard libpq environment settings and the team's restricted reader role."""
from decimal import Decimal
import json
import os
import psycopg
from psycopg.types.json import set_json_loads


def connect(database):
    if database not in {"tpch_sf0_1", "tpch_sf1"}:
        raise ValueError("Unsupported database")
    if os.environ.get("PGUSER") != "qcp_bench_reader":
        raise ValueError("Set PGUSER=qcp_bench_reader; do not use an administrator")
    conn = psycopg.connect(dbname=database, autocommit=True, connect_timeout=10, application_name="rq2_pipeline")
    conn.read_only = True
    set_json_loads(lambda raw: json.loads(raw, parse_float=Decimal), conn)
    try:
        with conn.transaction(force_rollback=True):
            value = conn.execute("SELECT current_user,r.rolsuper,current_setting('default_transaction_read_only'),"
                                 "current_setting('transaction_read_only') FROM pg_roles r WHERE r.rolname=current_user").fetchone()
            if value != ("qcp_bench_reader", False, "on", "on"):
                raise ValueError("Require the non-superuser reader with read-only defaults")
    except BaseException:
        conn.close()
        raise
    return conn


def server_identity(conn):
    row = conn.execute("SELECT current_setting('server_version'),current_setting('server_version_num')::int").fetchone()
    return {"server_version": row[0], "server_version_num": int(row[1])}
