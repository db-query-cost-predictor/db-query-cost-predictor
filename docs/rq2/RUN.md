# Setup and execution

Use Python 3.11 or later. Validation was performed on CPython 3.12.14, psycopg 3.2.13 and pytest 9.1.1. Install from the repository root with `python -m pip install -c requirements/rq2_constraints_v1.0.1.txt -e ".[dev]"`. Run `python -m pytest tests/rq2 -q`; unit tests block live network, database and host calls.

## Credentials

Use standard libpq variables: PGHOST (default 127.0.0.1), PGPORT (default 55432), PGUSER (qcp_bench_reader) and PGPASSWORD or a libpq password file. Set GROQ_API_KEY privately. Alternatively, pass `--env-file` pointing to an existing private file. The loader accepts these reader/provider settings and maps QCP_DB_HOST, QCP_BENCH_PORT and QCP_BENCH_READER_PASSWORD for compatibility. Administrator credential fields are ignored. Existing terminal variables take precedence. No shell evaluation or variable interpolation is performed.

## Live prerequisites

- PostgreSQL 16.15 with the existing TPC-H schema, validated primary/foreign keys, NOT NULL declarations, indexes and qcp_meta load manifests.
- Databases tpch_sf0_1 and tpch_sf1, with expected row counts, populated statistics and autovacuum disabled as in the team's setup.
- The non-superuser qcp_bench_reader role, SELECT access and read-only transaction defaults. Every query transaction rolls back.
- The benchmark container running alone, with a two-CPU quota and 4 GiB RAM, and the Windows Balanced power plan on AC power. Docker Desktop executable discovery supports normal system and per-user installations. Explicit `--docker` and `--desktop` paths remain private bindings.

Environment, snapshot and effective settings are captured and checked. A database advisory lock prevents two instances of this pipeline from benchmarking concurrently. Do not run other database benchmarks alongside it. The live host probe currently supports Windows; offline validation/replay does not require Docker.

## Commands

```powershell
python -m rq2_pipeline validate --input examples/rq2/selected_queries.json
python -m rq2_pipeline run --input examples/rq2/selected_queries.json --output runs/team-run-001 --container benchmark-container --env-file ../private.env
python -m rq2_pipeline run --input examples/rq2/selected_queries.json --output runs/team-run-001 --container benchmark-container --env-file ../private.env --resume
```

A fresh run refuses an existing output directory. Resume requires unchanged inputs, configuration and source. Any started item that did not finish successfully remains inconclusive; it is not regenerated or retimed. A completed run is only verified and read on resume. Fix setup failures and use a new output directory when no identity was created. Detailed failure evidence is retained; private connection/provider error text is not echoed into summary output.

API calls and server executions are distinct costs. Generation wall-clock records include context collection and provider-stage handling; each live case records its wall time. Server execution-time speedups do not establish one-off end-to-end savings.

This package does not create roles, load data, change schema, start containers or tune RQ1 models. Use the team's existing database setup and integration process.
