"""Shared exception types for evidence handling."""

from __future__ import annotations


class MissingEvidenceError(RuntimeError):
    """Required evidence has not been generated yet.

    The message always names the runbook step that produces the evidence.
    Callers must stop; they must never substitute demonstration data.
    """


class EvidenceIntegrityError(RuntimeError):
    """Evidence exists but failed an integrity check (checksum, duplicate, drift)."""


class DuplicateRunError(EvidenceIntegrityError):
    """A second terminal record for the same ``modeling_key + repeat_no`` was offered."""


class UnsupportedClaimError(RuntimeError):
    """A poster claim is prohibited or lacks the evidence it requires."""
