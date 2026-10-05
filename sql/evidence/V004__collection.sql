-- V004 Collection manifest, modeling keys, sessions, heartbeats and raw files.
-- modeling_key = hash(query instance, snapshot, configuration, cache protocol,
-- protocol version). Repeated executions are children of a key (V005).

CREATE TABLE qcp.collection_manifest (
    manifest_id              text PRIMARY KEY,
    manifest_name            text NOT NULL,
    protocol_version         text NOT NULL,
    cache_protocol           text NOT NULL CHECK (cache_protocol = 'warm'),
    statement_timeout_ms     integer NOT NULL CHECK (statement_timeout_ms > 0),
    planned_final_timeout_ms integer NOT NULL CHECK (planned_final_timeout_ms > 0),
    lock_timeout_ms          integer NOT NULL CHECK (lock_timeout_ms > 0),
    warmup_probes            integer NOT NULL CHECK (warmup_probes = 1),
    measured_repetitions     integer NOT NULL CHECK (measured_repetitions >= 1 AND measured_repetitions % 2 = 1),
    order_seed               bigint NOT NULL,
    config_file_sha256       text NOT NULL,
    manifest_file_sha256     text NOT NULL,
    manifest_json            jsonb NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE qcp.modeling_key (
    modeling_key       text PRIMARY KEY,
    query_instance_id  text NOT NULL REFERENCES qcp.query_instance (query_instance_id),
    snapshot_id        text NOT NULL REFERENCES qcp.benchmark_snapshot (snapshot_id),
    config_id          text NOT NULL REFERENCES qcp.database_configuration (config_id),
    cache_protocol     text NOT NULL,
    protocol_version   text NOT NULL,
    template_id        text NOT NULL,
    semantic_group_id  text NOT NULL,
    workload           text NOT NULL,
    scale_factor       numeric NOT NULL,
    configuration_name text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (query_instance_id, snapshot_id, config_id, cache_protocol, protocol_version)
);

CREATE TABLE qcp.manifest_entry (
    manifest_id     text NOT NULL REFERENCES qcp.collection_manifest (manifest_id),
    modeling_key    text NOT NULL REFERENCES qcp.modeling_key (modeling_key),
    execution_order integer NOT NULL CHECK (execution_order >= 1),
    PRIMARY KEY (manifest_id, modeling_key),
    UNIQUE (manifest_id, execution_order)
);

CREATE TABLE qcp.collection_session (
    session_id        text PRIMARY KEY,
    session_kind      text NOT NULL CHECK (session_kind IN ('poster_smoke_collection', 'rewrite_verification')),
    manifest_id       text REFERENCES qcp.collection_manifest (manifest_id),
    started_at        timestamptz NOT NULL,
    host_name         text NOT NULL,
    process_id        integer NOT NULL,
    python_version    text NOT NULL,
    package_version   text NOT NULL,
    raw_evidence_path text NOT NULL,
    order_seed        bigint NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (session_kind <> 'poster_smoke_collection' OR manifest_id IS NOT NULL)
);

CREATE TABLE qcp.collection_heartbeat (
    heartbeat_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id           text NOT NULL REFERENCES qcp.collection_session (session_id),
    emitted_at           timestamptz NOT NULL,
    state                text NOT NULL CHECK (state IN ('started', 'running', 'stalled', 'interrupted', 'finished', 'failed')),
    keys_total           integer NOT NULL,
    keys_done            integer NOT NULL,
    keys_complete        integer NOT NULL,
    keys_censored        integer NOT NULL,
    keys_failed          integer NOT NULL,
    current_modeling_key text,
    note                 text
);

CREATE TABLE qcp.raw_evidence_file (
    session_id   text NOT NULL REFERENCES qcp.collection_session (session_id),
    file_path    text NOT NULL,
    file_sha256  text NOT NULL,
    byte_count   bigint NOT NULL,
    record_count integer NOT NULL,
    closed_at    timestamptz NOT NULL,
    PRIMARY KEY (session_id, file_path)
);

SELECT qcp.make_append_only('qcp.collection_manifest');
SELECT qcp.make_append_only('qcp.modeling_key');
SELECT qcp.make_append_only('qcp.manifest_entry');
SELECT qcp.make_append_only('qcp.collection_session');
SELECT qcp.make_append_only('qcp.collection_heartbeat');
SELECT qcp.make_append_only('qcp.raw_evidence_file');
