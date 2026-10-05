"""Poster claim rendering and prohibited-claim linting.

* Claims are defined in ``config/poster_claims.yaml`` with one status each
  (``RECOMPUTED_PILOT``, ``NEW_POSTER_SMOKE``, ``PLANNED``,
  ``NOT_YET_EVALUABLE``). Placeholders such as ``{pilot.instances}`` are filled
  only from evidence values; a claim with any missing value is rendered as
  ``EVIDENCE_MISSING`` and must not be used.
* :func:`lint_text` rejects wording that the claim register prohibits. The
  builder refuses to finish if any rendered claim violates a rule.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

CLAIM_STATUSES = ("RECOMPUTED_PILOT", "NEW_POSTER_SMOKE", "PLANNED", "NOT_YET_EVALUABLE")
PLACEHOLDER = re.compile(r"\{([a-z_]+\.[a-z0-9_]+)\}")


@dataclass(frozen=True)
class ClaimSpec:
    id: str
    status: str
    template: str
    zero_key: str | None = None
    template_if_zero: str | None = None


@dataclass(frozen=True)
class RenderedClaim:
    id: str
    status: str
    render_status: str  # READY | EVIDENCE_MISSING | PROHIBITED_WORDING
    text: str
    missing: tuple[str, ...]
    violations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_claim_specs(path: Path | None = None) -> list[ClaimSpec]:
    from query_cost_predictor.paths import config_dir

    target = path or (config_dir() / "poster_claims.yaml")
    document = yaml.safe_load(target.read_text(encoding="utf-8"))
    specs = []
    seen: set[str] = set()
    for raw in document["claims"]:
        spec = ClaimSpec(id=str(raw["id"]), status=str(raw["status"]), template=str(raw["template"]).strip(),
                         zero_key=raw.get("zero_key"), template_if_zero=(str(raw["template_if_zero"]).strip()
                                                                          if raw.get("template_if_zero") else None))
        if spec.status not in CLAIM_STATUSES:
            raise ValueError(f"claim {spec.id}: status {spec.status} is not allowed (PROHIBITED claims are never rendered)")
        if spec.id in seen:
            raise ValueError(f"duplicate claim id {spec.id}")
        seen.add(spec.id)
        specs.append(spec)
    return specs


def _format(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value != value:  # NaN
            return "n/a"
        return f"{value:,.3f}" if abs(value) < 1000 else f"{value:,.0f}"
    return str(value)


# ---------------------------------------------------------------------------- linting
_RULES: list[tuple[str, str, Any]] = [
    ("X-01", "R-squared described as accuracy",
     lambda t: bool(re.search(r"(r\^?2|r²|r-squared|coefficient of determination)", t)) and "accura" in t),
    ("X-02", "random split presented as unseen-structure performance",
     lambda t: "random" in t and bool(re.search(r"unseen|generali[sz]|new (quer|struct|templat)", t))
     and "leakage diagnostic" not in t),
    ("X-03", "financial or dollar savings without a documented pricing model",
     lambda t: bool(re.search(r"\$\s?\d|\busd\b|dollar|cost saving|save[sd]? money|financial|cloud bill", t))),
    ("X-04", "manual/deterministic rewrite presented as an LLM result",
     lambda t: bool(re.search(r"\bllm\b|language model", t))
     and not re.search(r"\bnot (an? )?llm\b|\bno llm\b|not yet evaluable|not_yet_evaluable|planned|will ", t)),
    ("X-05", "planned dataset counts presented as collected",
     lambda t: bool(re.search(r"4,?704|4,?544|4,?000|5,?000", t)) and bool(re.search(r"collected|measured|observed", t))
     and "planned" not in t),
    ("X-07", "point/range probes called an OLTP benchmark", lambda t: "oltp benchmark" in t),
    ("X-08", "semantic equivalence claimed as proven", lambda t: bool(re.search(r"proven equivalen|prove[sd]? .{0,30}equivalen", t))),
    ("X-09", "results called TPC-H benchmark results", lambda t: bool(re.search(r"tpc-h (benchmark )?result", t))),
    ("X-11", "instrumented time presented as production or end-to-end latency",
     lambda t: bool(re.search(r"production latency|end-to-end latency", t)) and " not " not in t),
    ("X-12", "a mechanism claimed as guaranteed", lambda t: bool(re.search(r"guaranteed|always spills|always slow", t))),
]


def lint_text(text: str, *, positives_10s: int | None = None) -> list[str]:
    """Return the ids of prohibited-claim rules violated by ``text``."""
    lowered = text.lower()
    violations = [rule_id for rule_id, _, check in _RULES if check(lowered)]
    mentions_10s_classifier = bool(re.search(r"(10 ?s|10-second|10,?000 ?ms)", lowered)) and bool(
        re.search(r"classif|pr-auc|recall|precision|false-negative", lowered))
    if mentions_10s_classifier and (positives_10s is None or positives_10s == 0) and "not yet evaluable" not in lowered:
        violations.append("X-06")
    return violations


def rule_descriptions() -> dict[str, str]:
    descriptions = {rule_id: description for rule_id, description, _ in _RULES}
    descriptions["X-06"] = "10-second classifier reported without positive examples"
    return descriptions


# ---------------------------------------------------------------------------- rendering
def render_claim(spec: ClaimSpec, values: dict[str, Any], *, positives_10s: int | None = None) -> RenderedClaim:
    template = spec.template
    if spec.zero_key and spec.template_if_zero and values.get(spec.zero_key) == 0:
        template = spec.template_if_zero
    needed = PLACEHOLDER.findall(template)
    missing = tuple(key for key in needed if values.get(key) is None)
    if missing:
        return RenderedClaim(spec.id, spec.status, "EVIDENCE_MISSING", template, missing, ())
    text = PLACEHOLDER.sub(lambda m: _format(values[m.group(1)]), template)
    violations = tuple(lint_text(text, positives_10s=positives_10s))
    status = "PROHIBITED_WORDING" if violations else "READY"
    return RenderedClaim(spec.id, spec.status, status, text, (), violations)


def render_claims(specs: list[ClaimSpec], values: dict[str, Any], *, positives_10s: int | None = None) -> list[RenderedClaim]:
    rendered = []
    for spec in specs:
        needs_pilot = any(k.startswith("pilot.") for k in PLACEHOLDER.findall(spec.template))
        needs_smoke = any(k.startswith(("smoke.", "rewrite.")) for k in PLACEHOLDER.findall(spec.template))
        if spec.status == "RECOMPUTED_PILOT" and needs_smoke:
            raise ValueError(f"claim {spec.id}: a RECOMPUTED_PILOT claim cannot use smoke evidence")
        if spec.status == "NEW_POSTER_SMOKE" and needs_pilot:
            raise ValueError(f"claim {spec.id}: a NEW_POSTER_SMOKE claim cannot use pilot evidence")
        rendered.append(render_claim(spec, values, positives_10s=positives_10s))
    return rendered
