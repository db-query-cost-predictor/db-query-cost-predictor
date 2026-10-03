# TPC-H dataset validation report

Verified 2026-09-13T19:49:19.633333+00:00. Real PostgreSQL EXPLAIN ANALYZE measurements; sample CSV was not used.

Successful queries: **220**. Failed queries: **0**. Templates: **22**. Dataset: **220 rows × 36 columns**. Measured runs: **660**.

Primary prediction target: `execution_time_mean_ms`. `optimizer_total_cost` is the PostgreSQL baseline estimate, not ground truth.

Every successful query received one unrecorded warm-up and three recorded runs using `EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON)`. All JSONL lines parsed successfully; an empty errors file is valid. Aggregated runtime targets and optimizer costs were checked against the raw plans.

## Validation results

```json
{
  "validated_at_utc": "2026-09-13T19:49:19.633333+00:00",
  "successful_queries": 220,
  "failed_queries": 0,
  "dimensions": [
    220,
    36
  ],
  "unique_query_ids": 220,
  "unique_template_ids": 22,
  "queries_generated": 220,
  "measured_runs": 660,
  "errors": 0,
  "duplicate_rows": 0,
  "duplicate_query_ids": 0,
  "duplicate_sql": 14,
  "missing_values": {
    "query_id": 0,
    "query_file": 0,
    "template_id": 0,
    "seed": 0,
    "scale_factor": 0,
    "successful_runs": 0,
    "sql": 0,
    "execution_time_mean_ms": 0,
    "execution_time_median_ms": 0,
    "execution_time_std_ms": 0,
    "execution_time_p95_ms": 0,
    "optimizer_total_cost": 0,
    "optimizer_startup_cost": 0,
    "plan_rows": 0,
    "actual_rows": 0,
    "planning_time_ms": 0,
    "plan_node_count": 0,
    "join_count": 0,
    "scan_count": 0,
    "index_scan_count": 0,
    "sort_count": 0,
    "aggregate_count": 0,
    "max_plan_depth": 0,
    "shared_hit_blocks": 0,
    "shared_read_blocks": 0,
    "shared_dirtied_blocks": 0,
    "temp_read_blocks": 0,
    "temp_written_blocks": 0,
    "io_read_time_ms": 0,
    "io_write_time_ms": 0,
    "wal_records": 0,
    "wall_time_ms": 0,
    "postgres_version": 0,
    "host_os": 0,
    "collected_at_utc": 0,
    "quality_flag": 0
  },
  "runtime_mean_ms_summary": {
    "min": 6.707333333333334,
    "median": 61.719833333333334,
    "mean": 82.00549393939394,
    "max": 284.395,
    "std": 68.32264995287372
  },
  "quality_flags": {
    "ok": 213,
    "high_variance": 7
  },
  "table_counts": {
    "region": 5,
    "nation": 25,
    "part": 20000,
    "supplier": 1000,
    "partsupp": 80000,
    "customer": 15000,
    "orders": 150000,
    "lineitem": 600572
  },
  "tbl_files": {
    "region": {
      "bytes": 384,
      "rows": 5
    },
    "nation": {
      "bytes": 2199,
      "rows": 25
    },
    "part": {
      "bytes": 2371090,
      "rows": 20000
    },
    "supplier": {
      "bytes": 138625,
      "rows": 1000
    },
    "partsupp": {
      "bytes": 11648193,
      "rows": 80000
    },
    "customer": {
      "bytes": 2411114,
      "rows": 15000
    },
    "orders": {
      "bytes": 16743122,
      "rows": 150000
    },
    "lineitem": {
      "bytes": 73646424,
      "rows": 600572
    }
  },
  "postgres_settings": {
    "effective_cache_size": "524288",
    "max_parallel_workers_per_gather": "2",
    "random_page_cost": "4",
    "seq_page_cost": "1",
    "server_version": "16.14 (Debian 16.14-1.pgdg13+1)",
    "shared_buffers": "16384",
    "track_io_timing": "on",
    "work_mem": "4096"
  },
  "indexes": [
    "customer_pkey",
    "idx_customer_nation",
    "idx_lineitem_part_supp",
    "idx_lineitem_shipdate",
    "idx_orders_customer",
    "idx_orders_date",
    "idx_supplier_nation",
    "lineitem_pkey",
    "nation_pkey",
    "orders_pkey",
    "part_pkey",
    "partsupp_pkey",
    "region_pkey",
    "supplier_pkey"
  ],
  "toolkit_commit": "32f1c1b92d1664dba542e927d23d86ffa57aa253",
  "collector_settings": {
    "jit": "off",
    "read_only": true,
    "timeout_ms": 300000,
    "warmups": 1,
    "measured_runs": 3,
    "scale_factor": 0.1,
    "order": "query filename sorted"
  },
  "output_sha256": {
    "query_dataset.csv": "2facd5fcf4b8ae3a9a97b47c7ecaeab6658e8e9df6750947d1f10b780954c50d",
    "query_runs.jsonl": "ed3af89a378e508ea3ac2539eab8e3dcaa3ce64f4313225f0bfb2f3f818dc081",
    "errors.jsonl": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  }
}
```

## Exact CSV schema

`query_id`, `query_file`, `template_id`, `seed`, `scale_factor`, `successful_runs`, `sql`, `execution_time_mean_ms`, `execution_time_median_ms`, `execution_time_std_ms`, `execution_time_p95_ms`, `optimizer_total_cost`, `optimizer_startup_cost`, `plan_rows`, `actual_rows`, `planning_time_ms`, `plan_node_count`, `join_count`, `scan_count`, `index_scan_count`, `sort_count`, `aggregate_count`, `max_plan_depth`, `shared_hit_blocks`, `shared_read_blocks`, `shared_dirtied_blocks`, `temp_read_blocks`, `temp_written_blocks`, `io_read_time_ms`, `io_write_time_ms`, `wal_records`, `wall_time_ms`, `postgres_version`, `host_os`, `collected_at_utc`, `quality_flag`

## Interpretation and known limitations

No unresolved query failures in this collection. Q15 uses a materialized CTE instead of CREATE/DROP VIEW; row limits use PostgreSQL LIMIT, and Q1 omits the unsupported DAY precision. SF 0.1 and these adaptations are for ML experimentation, not an audited TPC-H result.

Query identity is template plus seed. Duplicate SQL parameter draws are retained and counted above. Buffer/I/O/WAL columns use inclusive root-plan counters; non-runtime plan features come from measured run 3. Warm caches, local Docker/WSL scheduling and small sample sizes affect timing. High-variance rows remain flagged, not excluded. p95 is interpolated from only three runs.

Duplicate SQL occurs in Q6, Q13 and Q18. The upstream Q18 parameter domain is only 312-315, so ten seeded instances cannot all be distinct without changing benchmark semantics.

Ready for exploratory Data Science. Split by template and group identical SQL together. For pre-execution prediction use only SQL/provenance and optimizer-estimated features; actual rows, buffers, I/O, wall/planning time, runtime aggregates and quality flags are post-execution observations and can leak the target. More scales, seeds and repeat sessions are needed for production generalization.

See README.md for exact reproduction commands.

## Setup verification evidence

```json
{
  "fresh_dbgen_scale_factor": 0.1,
  "all_eight_normalized_files_match": true,
  "regenerated_directory": "/tmp/tpch-dbgen-check.zFikAp",
  "setup_rerun_exit_code": 0,
  "setup_rerun_reloaded_tables": false,
  "setup_rerun_dropped_tables": false,
  "note": "Observed successful verify_dbgen.sh and second setup_tpch.sh invocation after collection on 2026-09-13."
}
```
