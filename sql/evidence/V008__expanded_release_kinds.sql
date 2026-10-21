-- V008 Expanded release: session kind and release kind (applied by `db-init` as qcp_evidence_owner).
-- Applied migrations are never edited. This migration replaces two CHECK constraints
-- so the expanded collection records its own session kind ('expanded_collection')
-- and its own release kind ('expanded_release'). No table or column is added.
-- Existing rows (poster smoke, rewrite verification) satisfy the new constraints.

DO $$
DECLARE
    found text;
BEGIN
    SELECT conname INTO STRICT found
    FROM pg_constraint
    WHERE conrelid = 'qcp.collection_session'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%session_kind = ANY%';
    EXECUTE format('ALTER TABLE qcp.collection_session DROP CONSTRAINT %I', found);
END
$$;

ALTER TABLE qcp.collection_session
    ADD CONSTRAINT collection_session_session_kind_check
    CHECK (session_kind IN ('poster_smoke_collection', 'rewrite_verification', 'expanded_collection'));

ALTER TABLE qcp.collection_session
    ADD CONSTRAINT collection_session_expanded_manifest_check
    CHECK (session_kind <> 'expanded_collection' OR manifest_id IS NOT NULL);

DO $$
DECLARE
    found text;
BEGIN
    SELECT conname INTO STRICT found
    FROM pg_constraint
    WHERE conrelid = 'qcp.dataset_release'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%release_kind = ANY%';
    EXECUTE format('ALTER TABLE qcp.dataset_release DROP CONSTRAINT %I', found);
END
$$;

ALTER TABLE qcp.dataset_release
    ADD CONSTRAINT dataset_release_release_kind_check
    CHECK (release_kind IN ('poster_smoke', 'final_study', 'expanded_release'));
