-- V005 Raw evidence: plain pre-execution plans (D), executions (E), errors and
-- manual reference-rewrite verifications. Append-only.
-- A failed execution has a NULL runtime plus a status; never a runtime of zero.
-- A timeout is right-censored: lower bound = timeout, runtime NULL.

CREATE TABLE qcp.estimated_plan (
    modeling_key        text PRIMARY KEY REFERENCES qcp.modeling_key (modeling_key),
    session_id          text NOT NULL REFERENCES qcp.collection_session (session_id),
    captured_at         timestamptz NOT NULL,
    explain_options     text NOT NULL,
    plan_json           jsonb NOT NULL,
    plan_sha256         text NOT NULL,
    plan_shape_sha256   text NOT NULL,
    settings_block      jsonb NOT NULL,
    est_total_cost      double precision NOT NULL,
    est_startup_cost    double precision NOT NULL,
    est_plan_rows       double precision NOT NULL,
    est_plan_width      integer NOT NULL,
    est_node_count      integer NOT NULL,
    est_workers_planned integer NOT NULL,
    raw_record_sha256   text NOT NULL
);

CREATE TABLE qcp.query_execution (
    run_id                text PRIMARY KEY,
    modeling_key          text NOT NULL REFERENCES qcp.modeling_key (modeling_key),
    repeat_no             integer NOT NULL CHECK (repeat_no >= 0),
    run_purpose           text NOT NULL CHECK (run_purpose IN ('probe', 'measured')),
    session_id            text NOT NULL REFERENCES qcp.collection_session (session_id),
    attempt_no            integer NOT NULL CHECK (attempt_no >= 1),
    started_at            timestamptz NOT NULL,
    finished_at           timestamptz NOT NULL,
    status                text NOT NULL CHECK (status IN ('completed', 'timeout', 'error')),
    is_censored           boolean NOT NULL,
    censoring_type        text CHECK (censoring_type IS NULL OR censoring_type = 'right_statement_timeout'),
    timeout_ms            integer NOT NULL CHECK (timeout_ms > 0),
    censor_lower_bound_ms double precision,
    execution_time_ms     double precision,
    planning_time_ms      double precision,
    client_elapsed_ms     double precision NOT NULL,
    actual_rows           double precision,
    workers_planned       integer,
    workers_launched      integer,
    shared_hit_blocks     bigint,
    shared_read_blocks    bigint,
    shared_dirtied_blocks bigint,
    shared_written_blocks bigint,
    temp_read_blocks      bigint,
    temp_written_blocks   bigint,
    io_read_time_ms       double precision,
    io_write_time_ms      double precision,
    wal_records           bigint,
    wal_fpi               bigint,
    wal_bytes             numeric,
    spill_detected        boolean,
    sqlstate              text,
    error_message         text,
    settings_block        jsonb,
    plan_json             jsonb,
    plan_sha256           text,
    plan_shape_sha256     text,
    raw_record_sha256     text NOT NULL,
    UNIQUE (modeling_key, repeat_no),
    CHECK ((repeat_no = 0) = (run_purpose = 'probe')),
    CHECK (
        CASE status
            WHEN 'completed' THEN execution_time_ms IS NOT NULL AND execution_time_ms >= 0 AND NOT is_censored
                AND censoring_type IS NULL AND censor_lower_bound_ms IS NULL AND plan_json IS NOT NULL
            WHEN 'timeout' THEN execution_time_ms IS NULL AND is_censored
                AND censoring_type = 'right_statement_timeout' AND censor_lower_bound_ms = timeout_ms
            WHEN 'error' THEN execution_time_ms IS NULL AND NOT is_censored
                AND censoring_type IS NULL AND error_message IS NOT NULL
        END
    )
);

CREATE TABLE qcp.collection_error (
    error_id          text PRIMARY KEY,
    session_id        text REFERENCES qcp.collection_session (session_id),
    manifest_id       text,
    modeling_key      text,
    query_instance_id text,
    repeat_no         integer,
    attempt_no        integer,
    phase             text NOT NULL CHECK (phase IN ('manifest', 'validation', 'estimate', 'probe', 'measured', 'record',
                                                     'snapshot_check', 'configuration_check', 'rewrite_verification', 'other')),
    error_class       text NOT NULL,
    sqlstate          text,
    message           text NOT NULL,
    is_retryable      boolean NOT NULL,
    occurred_at       timestamptz NOT NULL,
    raw_record_sha256 text NOT NULL
);

CREATE TABLE qcp.rewrite_verification (
    verification_id      text PRIMARY KEY,
    session_id           text NOT NULL REFERENCES qcp.collection_session (session_id),
    rewrite_pair_id      text NOT NULL,
    semantic_group_id    text NOT NULL,
    rewrite_source       text NOT NULL CHECK (rewrite_source IN ('MANUAL_REFERENCE_REWRITE', 'LLM_REWRITE')),
    pair_role            text NOT NULL CHECK (pair_role IN ('reference_pair', 'negative_control')),
    expected_equivalent  boolean NOT NULL,
    snapshot_id          text NOT NULL REFERENCES qcp.benchmark_snapshot (snapshot_id),
    config_id            text NOT NULL REFERENCES qcp.database_configuration (config_id),
    parameter_values     jsonb NOT NULL,
    original_sql_sha256  text NOT NULL,
    rewrite_sql_sha256   text NOT NULL,
    safety_status        text NOT NULL,
    precondition_status  text NOT NULL,
    original_row_count   bigint,
    rewrite_row_count    bigint,
    multiset_equal       boolean,
    ordering_required    boolean NOT NULL,
    ordering_status      text NOT NULL,
    equivalence_status   text NOT NULL CHECK (equivalence_status IN ('EQUIVALENT_ON_SNAPSHOT', 'NOT_EQUIVALENT', 'INCONCLUSIVE', 'ERROR')),
    timing_status        text NOT NULL,
    timing_pairs         jsonb NOT NULL,
    median_speedup       double precision,
    min_speedup_required double precision NOT NULL,
    recommendation       text NOT NULL CHECK (recommendation IN ('RECOMMEND', 'DO_NOT_RECOMMEND')),
    reason               text NOT NULL,
    created_at           timestamptz NOT NULL,
    raw_record_sha256    text NOT NULL,
    CHECK (recommendation <> 'RECOMMEND'
        OR (equivalence_status = 'EQUIVALENT_ON_SNAPSHOT' AND pair_role = 'reference_pair'
            AND median_speedup IS NOT NULL AND median_speedup >= min_speedup_required))
);

SELECT qcp.make_append_only('qcp.estimated_plan');
SELECT qcp.make_append_only('qcp.query_execution');
SELECT qcp.make_append_only('qcp.collection_error');
SELECT qcp.make_append_only('qcp.rewrite_verification');
