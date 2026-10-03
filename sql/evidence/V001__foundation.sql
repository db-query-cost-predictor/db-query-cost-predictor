-- V001 Evidence-store foundation (applied by `db-init` as qcp_evidence_owner).
-- Raw evidence is append-only: UPDATE, DELETE and TRUNCATE raise an error.

CREATE SCHEMA IF NOT EXISTS qcp;

CREATE OR REPLACE FUNCTION qcp.forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'qcp: % on %.% is forbidden: raw evidence is append-only',
        TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

-- Attach row-level UPDATE/DELETE and statement-level TRUNCATE guards.
CREATE OR REPLACE FUNCTION qcp.make_append_only(target regclass) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE format(
        'CREATE TRIGGER append_only_rows BEFORE UPDATE OR DELETE ON %s FOR EACH ROW EXECUTE FUNCTION qcp.forbid_mutation()',
        target);
    EXECUTE format(
        'CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON %s FOR EACH STATEMENT EXECUTE FUNCTION qcp.forbid_mutation()',
        target);
END;
$$;
