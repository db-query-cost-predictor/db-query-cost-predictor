-- V006 Derived, versioned, rebuildable tables. No append-only guards: a rebuild
-- replaces every row of one version (manifest keys + version) in one
-- transaction. The CHECK on feature_record makes class E storage impossible.

CREATE TABLE qcp.feature_record (
    modeling_key       text NOT NULL REFERENCES qcp.modeling_key (modeling_key),
    feature_version    text NOT NULL,
    feature_name       text NOT NULL,
    availability_class char(1) NOT NULL CHECK (availability_class IN ('A', 'B', 'C', 'D')),
    value_numeric      double precision,
    value_text         text,
    inputs_sha256      text NOT NULL,
    built_at           timestamptz NOT NULL,
    PRIMARY KEY (modeling_key, feature_version, feature_name),
    CHECK (value_numeric IS NOT NULL OR value_text IS NOT NULL)
);

CREATE TABLE qcp.runtime_label (
    modeling_key                 text NOT NULL REFERENCES qcp.modeling_key (modeling_key),
    label_version                text NOT NULL,
    status                       text NOT NULL CHECK (status IN ('complete', 'right_censored', 'failed', 'incomplete')),
    measured_repetitions_planned integer NOT NULL,
    n_successful                 integer NOT NULL,
    n_censored                   integer NOT NULL,
    n_error                      integer NOT NULL,
    median_ms                    double precision,
    mad_ms                       double precision,
    cv                           double precision,
    p90_ms                       double precision,
    min_ms                       double precision,
    max_ms                       double precision,
    censor_lower_bound_ms        double precision,
    timeout_ms                   integer NOT NULL,
    has_censored_runs            boolean NOT NULL,
    probe_status                 text,
    inputs_sha256                text NOT NULL,
    built_at                     timestamptz NOT NULL,
    PRIMARY KEY (modeling_key, label_version),
    CHECK (
        CASE status
            WHEN 'complete' THEN median_ms IS NOT NULL AND censor_lower_bound_ms IS NULL
            WHEN 'right_censored' THEN median_ms IS NULL AND censor_lower_bound_ms IS NOT NULL
            ELSE median_ms IS NULL AND censor_lower_bound_ms IS NULL
        END
    )
);

CREATE TABLE qcp.dataset_release (
    release_id               text PRIMARY KEY,
    release_kind             text NOT NULL CHECK (release_kind IN ('poster_smoke', 'final_study')),
    manifest_id              text REFERENCES qcp.collection_manifest (manifest_id),
    label_version            text NOT NULL,
    feature_version          text NOT NULL,
    inputs_sha256            text NOT NULL,
    key_count                integer NOT NULL,
    labeled_key_count        integer NOT NULL,
    group_count              integer NOT NULL,
    template_count           integer NOT NULL,
    measured_execution_count integer NOT NULL,
    status                   text NOT NULL CHECK (status IN ('draft', 'validated', 'rejected')),
    files                    jsonb NOT NULL,
    created_at               timestamptz NOT NULL,
    updated_at               timestamptz NOT NULL
);
