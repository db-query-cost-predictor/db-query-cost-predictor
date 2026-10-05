-- V002 Query registry: semantic groups, query definitions (templates) and
-- query instances with explicit parameter values. Immutable once written.

CREATE TABLE qcp.semantic_group (
    semantic_group_id   text PRIMARY KEY,
    workload            text NOT NULL,
    family              text NOT NULL,
    description         text NOT NULL,
    is_holdout_reserved boolean NOT NULL DEFAULT false,
    holdout_reason      text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (NOT is_holdout_reserved OR holdout_reason IS NOT NULL)
);

CREATE TABLE qcp.query_definition (
    template_id        text PRIMARY KEY,
    semantic_group_id  text NOT NULL REFERENCES qcp.semantic_group (semantic_group_id),
    workload           text NOT NULL,
    family             text NOT NULL,
    form               text NOT NULL CHECK (form IN ('standalone', 'original', 'reference_rewrite', 'negative_control_rewrite')),
    rewrite_source     text NOT NULL CHECK (rewrite_source IN ('NOT_A_REWRITE', 'MANUAL_REFERENCE_REWRITE', 'LLM_REWRITE')),
    rewrite_pair_id    text,
    sql_template       text NOT NULL,
    template_sha256    text NOT NULL,
    parameter_types    jsonb NOT NULL,
    expected_mechanism text NOT NULL,
    coverage_tags      jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_config      text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (template_id, semantic_group_id),
    CHECK ((form = 'standalone') = (rewrite_pair_id IS NULL)),
    CHECK ((form IN ('standalone', 'original') AND rewrite_source = 'NOT_A_REWRITE')
        OR (form IN ('reference_rewrite', 'negative_control_rewrite') AND rewrite_source IN ('MANUAL_REFERENCE_REWRITE', 'LLM_REWRITE')))
);

CREATE TABLE qcp.query_instance (
    query_instance_id     text PRIMARY KEY,
    template_id           text NOT NULL,
    semantic_group_id     text NOT NULL,
    parameter_values      jsonb NOT NULL,
    sql_text              text NOT NULL,
    sql_sha256            text NOT NULL,
    normalized_sql_sha256 text NOT NULL,
    literal_fingerprint   text NOT NULL,
    safety_status         text NOT NULL CHECK (safety_status = 'PASSED_READ_ONLY_SINGLE_STATEMENT'),
    created_at            timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (template_id, semantic_group_id) REFERENCES qcp.query_definition (template_id, semantic_group_id)
);

SELECT qcp.make_append_only('qcp.semantic_group');
SELECT qcp.make_append_only('qcp.query_definition');
SELECT qcp.make_append_only('qcp.query_instance');
