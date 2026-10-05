-- V007 Convenience view and privileges for the evidence writer role.
-- The writer may INSERT/SELECT everywhere, but UPDATE/DELETE only on the
-- derived, versioned tables. Raw tables additionally carry append-only triggers.

CREATE VIEW qcp.v_key_execution_summary AS
SELECT mk.modeling_key,
       count(qe.run_id) FILTER (WHERE qe.run_purpose = 'probe') AS probe_runs,
       count(qe.run_id) FILTER (WHERE qe.run_purpose = 'measured' AND qe.status = 'completed') AS measured_completed,
       count(qe.run_id) FILTER (WHERE qe.run_purpose = 'measured' AND qe.status = 'timeout') AS measured_timeout,
       count(qe.run_id) FILTER (WHERE qe.run_purpose = 'measured' AND qe.status = 'error') AS measured_error
FROM qcp.modeling_key mk
LEFT JOIN qcp.query_execution qe ON qe.modeling_key = mk.modeling_key
GROUP BY mk.modeling_key;

GRANT USAGE ON SCHEMA qcp TO qcp_evidence_writer;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA qcp TO qcp_evidence_writer;
GRANT UPDATE, DELETE ON qcp.feature_record, qcp.runtime_label, qcp.dataset_release TO qcp_evidence_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA qcp TO qcp_evidence_writer;
