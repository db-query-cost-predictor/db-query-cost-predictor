"""Database Query Cost Predictor — poster evidence and expanded release.

This package contains the poster evidence milestone (an independent audit of
the historical pilot, a small resumable PostgreSQL smoke collector, a manual
reference-rewrite verification harness and an evidence-backed poster builder)
and the collection code for the expanded release defined in
``config/expanded_release_v1.yaml`` (manifest, bounded sample, resumable
batches, derivation, validation and release gates).

The code was written without being executed by its author; results come from
the user's runs of ``POSTER_RUNBOOK.md`` and ``EXPANDED_DATASET_RUNBOOK.md``.
The expanded release was collected on 2026-10-04 (UTC): 4,944 determined cases,
full validation STOP at G-08 (see ``docs/expanded_collection_checkpoint.md``).
RQ1 modeling on the expanded release is not implemented yet.
"""

__version__ = "0.1.0"

#: Collection protocol identifier stored in every poster-smoke modeling key.
POSTER_PROTOCOL_VERSION = "poster-smoke-v1"

#: Label and feature versions produced by ``smoke-derive``.
POSTER_LABEL_VERSION = "poster-smoke-label-v1"
POSTER_FEATURE_VERSION = "poster-smoke-features-v1"

#: Audit identifier written into ``reports/poster/pilot_audit.json``.
PILOT_AUDIT_VERSION = "pilot-audit-v1"

#: Expanded release (config/expanded_release_v1.yaml). The protocol version is
#: read from the spec; these label/feature versions are written by expanded-derive.
EXPANDED_LABEL_VERSION = "expanded-label-v1"
EXPANDED_FEATURE_VERSION = "expanded-features-v1"
