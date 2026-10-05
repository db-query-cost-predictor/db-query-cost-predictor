# Dataset Data Dictionary

The canonical ML table is `query_dataset.csv`; the lossless raw table is `query_runs.jsonl`.

| Field group | Columns | Meaning |
| --- | --- | --- |
| Identity | `query_id`, `query_file`, `template_id`, `seed` | Template/seed instance ID and TPC-H generation provenance |
| Experiment | `scale_factor`, `successful_runs`, `postgres_version`, `host_os`, `collected_at_utc` | Reproducibility metadata |
| SQL | `sql` | Normalized executed SQL text |
| Ground-truth labels | `execution_time_mean_ms`, `execution_time_median_ms`, `execution_time_std_ms`, `execution_time_p95_ms` | Repeated actual runtime measurements |
| Optimizer baseline | `optimizer_total_cost`, `optimizer_startup_cost`, `plan_rows` | PostgreSQL estimates, not ground truth |
| Actual plan outcome | `actual_rows`, `planning_time_ms`, `wall_time_ms` | Observed execution metadata |
| Structure | `plan_node_count`, `join_count`, `scan_count`, `index_scan_count`, `sort_count`, `aggregate_count`, `max_plan_depth` | Parsed execution-plan features |
| Resource proxies | `shared_*_blocks`, `temp_*_blocks`, `io_*_time_ms`, `wal_records` | Buffer, disk-spill, I/O, and WAL measurements |
| Quality | `quality_flag` | `ok`, `high_variance`, or sample-only marker |

For modeling, use `execution_time_mean_ms` as the initial target. Do not use the raw JSON plan or target-derived aggregate runtime columns as input features. Split by `template_id` so parameter variants of the same TPC-H template cannot leak between train and test sets.


Non-runtime plan features and resource counters come from measured run 3. Resource counters use the inclusive root plan, not sums over child nodes. There are 220 instances but 206 distinct SQL statements; use template-grouped splits. See the validation report for the exact 36-column schema and all missing-value counts.
