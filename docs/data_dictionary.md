# Data dictionary

Readable mirror of [`config/feature_registry.yaml`](../config/feature_registry.yaml)
(authoritative). Classes: **A** SQL structure · **B** schema/statistics ·
**C** environment/configuration · **D** plain `EXPLAIN` · **E** execution-only.
Schema: `sql/evidence/V001`–`V007` (evidence database `qcp_evidence`, schema `qcp`).
No table contains data until you run the runbook.

## Raw evidence (append-only: UPDATE, DELETE and TRUNCATE raise an error)

### `semantic_group`
| Column | Class | Role | Meaning |
|---|---|---|---|
| semantic_group_id | A | group_key | structural/semantic group for grouped splits and reporting |
| workload | B | group_key | benchmark workload (`tpch`) |
| family | A | group_key | query family |
| description | A | annotation | human description |
| is_holdout_reserved | C | split_control | reserved for held-out rewrite evaluation |
| holdout_reason | C | annotation | reason for the reservation |
| created_at | C | provenance | registration time |

### `query_definition` (templates)
| Column | Class | Role | Meaning |
|---|---|---|---|
| template_id | A | group_key | template identity (never a predictor) |
| semantic_group_id, workload, family | A/B/A | group_key | grouping |
| form | A | annotation | standalone, original, reference_rewrite, negative_control_rewrite |
| rewrite_source | A | annotation | NOT_A_REWRITE or MANUAL_REFERENCE_REWRITE (LLM_REWRITE reserved) |
| rewrite_pair_id | A | group_key | links original and rewrite |
| sql_template, parameter_types | A | provenance | template text and declared parameter types |
| template_sha256 | A | identifier | template hash |
| expected_mechanism, coverage_tags | A | annotation | designer hypotheses (never predictors) |
| source_config, created_at | C | provenance | origin and time |

### `query_instance`
| Column | Class | Role | Meaning |
|---|---|---|---|
| query_instance_id | A | identifier | hash of template, explicit parameters and SQL |
| template_id, semantic_group_id | A | group_key | grouping |
| parameter_values | A | provenance | explicit parameter values |
| sql_text | A | provenance | exact SQL executed |
| sql_sha256, normalized_sql_sha256 | A | identifier | SQL hashes (exact; literal-sensitive token stream) |
| literal_fingerprint | A | group_key | literal-insensitive structural fingerprint |
| safety_status | A | provenance | `PASSED_READ_ONLY_SINGLE_STATEMENT` |
| created_at | C | provenance | registration time |

### `benchmark_snapshot`
| Column | Class | Role | Meaning |
|---|---|---|---|
| snapshot_id | B | identifier | hash of the components below |
| workload, scale_factor | B | group_key | workload and TPC-H scale factor |
| database_name | B | identifier | `tpch_sf0_1` or `tpch_sf1` |
| server_version, server_version_num | C | provenance | PostgreSQL 16 version |
| schema_sha256, index_manifest_sha256, statistics_sha256, data_sha256 | B | provenance | identity components |
| table_row_counts, load_manifest | B | provenance | counts and loader file hashes |
| captured_at | C | provenance | capture time |

### `database_configuration`
| Column | Class | Role | Meaning |
|---|---|---|---|
| config_id | C | identifier | hash of name, effective-settings hash and server version |
| configuration_name | C | group_key | `C1_baseline`, `C2_reduced_work_mem` |
| session_settings, effective_settings | C | provenance | applied and captured settings |
| effective_settings_sha256 | C | identifier | settings hash |
| server_version, server_version_num | C | provenance | server version |
| postgres_image, postgres_image_digest, image_capture_status | C | provenance | image reference and captured digest (`CAPTURED`/`NOT_CAPTURED`) |
| captured_at | C | provenance | capture time |

### `collection_manifest`, `modeling_key`, `manifest_entry`
| Column | Class | Role | Meaning |
|---|---|---|---|
| manifest_id | C | identifier | hash of the manifest document |
| manifest_name, protocol_version, cache_protocol | C | provenance | `poster-smoke`, `poster-smoke-v1`, `warm` |
| statement_timeout_ms, planned_final_timeout_ms, lock_timeout_ms | C | provenance | 15000, 60000 (recorded only), 5000 |
| warmup_probes, measured_repetitions, order_seed | C | provenance | 1, 3, seed |
| config_file_sha256, manifest_file_sha256, manifest_json | C | provenance | provenance of the definition |
| modeling_key.modeling_key | C | identifier | hash(query instance, snapshot, configuration, cache protocol, protocol version) |
| modeling_key.query_instance_id / snapshot_id / config_id | A/B/C | identifier | components |
| modeling_key.template_id / semantic_group_id / workload / scale_factor / configuration_name | A/A/B/B/C | group_key | grouping (never predictors) |
| manifest_entry.execution_order | C | provenance | seeded execution position |
| created_at columns | C | provenance | registration times |

### `collection_session`, `collection_heartbeat`, `raw_evidence_file`
All **E** (they exist only once collection runs): session identity, host, process,
Python and package versions, raw file path, seed (C); heartbeat state and key
counts; raw file path, SHA-256, size, record count and close time.

### `estimated_plan` (plain `EXPLAIN`, class D)
| Column | Class | Role | Meaning |
|---|---|---|---|
| modeling_key | C | identifier | parent key (one estimate per key) |
| session_id | E | identifier | capturing session |
| captured_at, explain_options, plan_json, raw_record_sha256 | D | provenance | capture time, `SETTINGS, FORMAT JSON`, full plan, raw link |
| plan_sha256 | D | identifier | plan hash |
| plan_shape_sha256 | D | group_key | operator-structure hash |
| settings_block | C | provenance | non-default planner settings reported by `EXPLAIN (SETTINGS)` |
| est_total_cost, est_startup_cost, est_plan_rows, est_plan_width, est_node_count, est_workers_planned | D | feature | root estimates |

### `query_execution` (class E; one row per terminal outcome; `UNIQUE (modeling_key, repeat_no)`)
| Column | Class | Role | Meaning |
|---|---|---|---|
| run_id | E | identifier | execution id |
| modeling_key | C | identifier | parent key |
| repeat_no, run_purpose | E | provenance | 0 = probe (never labelled), 1–3 = measured |
| session_id, attempt_no, started_at, finished_at | E | identifier/provenance | where and when |
| status | E | label | `completed`, `timeout`, `error` |
| is_censored, censoring_type, censor_lower_bound_ms | E | label | right-censoring by the statement timeout (bound = timeout) |
| timeout_ms | C | provenance | timeout in force |
| execution_time_ms | E | label | instrumented execution time; **NULL** unless completed (never 0 for failures) |
| planning_time_ms, client_elapsed_ms, actual_rows | E | diagnostic | execution diagnostics |
| workers_planned | D | diagnostic | planned workers in the executed plan |
| workers_launched | E | diagnostic | launched workers |
| shared_hit/read/dirtied/written_blocks, temp_read/written_blocks | E | diagnostic | buffer and spill counters |
| io_read_time_ms, io_write_time_ms | E | diagnostic | I/O timing (`track_io_timing = on`) |
| wal_records, wal_fpi, wal_bytes | E | diagnostic | WAL counters |
| spill_detected | E | diagnostic | temp blocks, disk sort, hash batches > 1, or hash-aggregate disk use |
| sqlstate, error_message | E | diagnostic | timeout/error detail |
| settings_block | C | provenance | settings reported with the execution |
| plan_json, plan_sha256, plan_shape_sha256, raw_record_sha256 | E | provenance/identifier/diagnostic | analyzed plan and raw link |

### `collection_error` (class E; preserves query identity)
error_id, session_id, manifest_id (C), modeling_key (C), query_instance_id (A),
repeat_no, attempt_no, phase, error_class, sqlstate, message, is_retryable,
occurred_at, raw_record_sha256.

### `rewrite_verification` (manual reference rewrites)
Identity and design columns are A/B/C (`rewrite_pair_id`, `semantic_group_id`,
`rewrite_source = MANUAL_REFERENCE_REWRITE`, `pair_role`, `expected_equivalent`,
`snapshot_id`, `config_id`, `parameter_values`, SQL hashes, `ordering_required`,
`min_speedup_required`); results are E (`original/rewrite_row_count`,
`multiset_equal`, `ordering_status`, `equivalence_status`, `timing_status`,
`timing_pairs`, `median_speedup`, `recommendation`, `reason`, `created_at`,
`raw_record_sha256`). A `CHECK` forbids `RECOMMEND` unless equivalence passed,
the pair is a reference pair and the median speedup meets the threshold.

## Derived, versioned, rebuildable (no append-only guard)

### `runtime_label` (per `modeling_key`, `label_version`)
status (`complete`, `right_censored`, `failed`, `incomplete`),
measured_repetitions_planned (C), n_successful, n_censored, n_error, median_ms,
mad_ms, cv (when defined), p90_ms, min_ms, max_ms, censor_lower_bound_ms,
timeout_ms (C), has_censored_runs, probe_status, inputs_sha256, built_at — all
**E** labels/diagnostics/provenance. Built from measured runs only.

### `feature_record` (per `modeling_key`, `feature_version`, `feature_name`)
availability_class restricted to A–D by `CHECK`; value_numeric / value_text
(`feature_store`); inputs_sha256, built_at.

### `dataset_release`
release id, kind (`poster_smoke`/`final_study`), manifest, versions, inputs
hash, key/labelled-key/group/template/measured-execution counts, status
(`draft`, `validated`, `rejected`), files with SHA-256, timestamps.

### Runner table `schema_migration`
version, filename, sha256, applied_at (C, provenance).

## Model features (unqualified names)

* **A (23):** `sql_length_chars`, `sql_token_count`, `sql_literal_count`,
  `sql_select_count`, `sql_subquery_count`, `sql_join_keyword_count`,
  `sql_where_count`, `sql_exists_count`, `sql_in_count`, `sql_not_count`,
  `sql_like_count`, `sql_group_by_count`, `sql_order_by_count`, `sql_has_limit`,
  `sql_distinct_count`, `sql_has_cte`, `sql_set_operation_count`,
  `sql_window_count`, `sql_case_count`, `sql_and_count`, `sql_or_count`,
  `sql_function_call_count`, `sql_aggregate_call_count`.
* **B:** `snap_scale_factor`. **C:** `cfg_work_mem_kb`.
* **D (44):** `est_total_cost_log`, `est_startup_cost_log`, `est_plan_rows_log`,
  `est_plan_width`, `est_node_count`, `est_max_depth`, `est_sum_node_rows_log`,
  `est_max_node_rows_log`, `est_join_count`, `est_scan_count`,
  `est_subplan_count`, `est_relation_count`, `est_workers_planned`,
  `est_parallel_aware_count`, `est_hashed_aggregate_count`, and 29 node-type
  counts `est_n_<category>`; plus the raw baseline input `est_total_cost`.

## Derived smoke files (`QCP_DATA_ROOT/derived/poster_smoke/<manifest>/<label_version>/<inputs>/`)

| File | Content |
|---|---|
| `keys.csv` | one row per modeling key: identities, group keys, parameters, key state |
| `executions.csv` | one row per terminal probe/measured execution (E) |
| `labels.csv` | one runtime label per key (E) |
| `estimates.csv` | plain-plan scalars, plan hashes, operator list (D) |
| `features.csv` | `modeling_key` + A–D features only (contract-checked) |
| `derived_manifest.json` | inputs (manifest + raw file hashes), outputs (hashes), counts |

## Pilot audit outputs (`reports/poster/`)

`pilot_audit.json` (all recomputed values, per-template table, fold
assignments, out-of-fold predictions, claim check), `pilot_metrics.csv` (per
model × split × scope, with keys, groups and keys per group), `pilot_data_quality.csv`,
`pilot_claim_check.md`, `figures/pilot/*.png`.
