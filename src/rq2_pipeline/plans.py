"""Validate an EXPLAIN JSON document."""
from typing import Any

def plan_document(explain_result: Any) -> dict[str, Any]:
    """Return the top-level plan document from an EXPLAIN JSON result."""
    doc = explain_result[0] if isinstance(explain_result, list) else explain_result
    if not isinstance(doc, dict) or not isinstance(doc.get("Plan"), dict):
        raise ValueError("EXPLAIN result does not contain a 'Plan' object")
    return doc
