-- V003 Environment identities: benchmark snapshots and database configurations.
-- A snapshot is identified by schema, index-manifest, statistics and data
-- hashes; a configuration by its effective settings. Immutable once written.

CREATE TABLE qcp.benchmark_snapshot (
    snapshot_id           text PRIMARY KEY,
    workload              text NOT NULL,
    database_name         text NOT NULL,
    scale_factor          numeric NOT NULL,
    server_version        text NOT NULL,
    server_version_num    integer NOT NULL CHECK (server_version_num / 10000 = 16),
    schema_sha256         text NOT NULL,
    index_manifest_sha256 text NOT NULL,
    statistics_sha256     text NOT NULL,
    data_sha256           text NOT NULL,
    table_row_counts      jsonb NOT NULL,
    load_manifest         jsonb NOT NULL,
    captured_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE qcp.database_configuration (
    config_id                 text PRIMARY KEY,
    configuration_name        text NOT NULL,
    session_settings          jsonb NOT NULL,
    effective_settings        jsonb NOT NULL,
    effective_settings_sha256 text NOT NULL,
    server_version            text NOT NULL,
    server_version_num        integer NOT NULL CHECK (server_version_num / 10000 = 16),
    postgres_image            text,
    postgres_image_digest     text,
    image_capture_status      text NOT NULL CHECK (image_capture_status IN ('CAPTURED', 'NOT_CAPTURED')),
    captured_at               timestamptz NOT NULL DEFAULT now()
);

SELECT qcp.make_append_only('qcp.benchmark_snapshot');
SELECT qcp.make_append_only('qcp.database_configuration');
