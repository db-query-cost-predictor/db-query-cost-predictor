"""Database Query Cost Predictor — poster evidence milestone.

This package contains the code for the poster evidence milestone only:
an independent audit of the historical pilot, a small resumable PostgreSQL
smoke collector, a manual reference-rewrite verification harness and an
evidence-backed poster builder.

Nothing in this package was executed by its author. No result exists until the
user runs the commands in ``POSTER_RUNBOOK.md``.
"""

__version__ = "0.1.0"

#: Collection protocol identifier stored in every poster-smoke modeling key.
POSTER_PROTOCOL_VERSION = "poster-smoke-v1"

#: Label and feature versions produced by ``smoke-derive``.
POSTER_LABEL_VERSION = "poster-smoke-label-v1"
POSTER_FEATURE_VERSION = "poster-smoke-features-v1"

#: Audit identifier written into ``reports/poster/pilot_audit.json``.
PILOT_AUDIT_VERSION = "pilot-audit-v1"
