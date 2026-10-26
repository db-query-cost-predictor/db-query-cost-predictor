"""Expanded release specification (``config/expanded_release_v1.yaml``).

The spec is parsed into the same :class:`~query_cost_predictor.config.TemplateSpec`
and :class:`~query_cost_predictor.config.ProtocolConfig` objects the poster-smoke
pipeline already uses, so manifest expansion, registration, collection and
derivation are shared rather than duplicated. This module adds:

* deterministic, library-independent parameter generation (SHA-256 draws from
  declared domains; no dependence on ``random`` implementation details);
* generated structural groups: join path x output shape, each with an
  *indexed* and an *unindexed* predicate variant over the same selectivity
  regimes (the index-availability contrast is held inside one semantic group);
* release policies: frozen high-runtime thresholds, the censoring margin, the
  split, sample, gate and budget policies;
* planning arithmetic (counts by component, family, template, regime, snapshot,
  configuration and split role) computed without a database.

Nothing in this module connects to a database or executes SQL.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import yaml

from query_cost_predictor.config import (
    ConfigError,
    ConfigurationSpec,
    ProtocolConfig,
    SnapshotSpec,
    TemplateSpec,
    parse_configurations,
    parse_protocol,
    parse_snapshots,
    render_sql,
)
from query_cost_predictor.hashing import canonical_json, sha256_file
from query_cost_predictor.safety import validate_read_only_sql

COMPONENTS = ("tpch_derived", "structural", "probe", "rewrite")
SPLIT_DEVELOPMENT = "development"
SPLIT_HELDOUT_RQ2 = "heldout_rq2"
INDEXED = "indexed_predicate"
UNINDEXED = "unindexed_predicate"
SLOT_PATTERN = re.compile(r"<<([a-z_]+)>>")
MAX_DRAW_ATTEMPTS = 500


# ----------------------------------------------------------------------------------
# Data classes
# ----------------------------------------------------------------------------------
@dataclass(frozen=True)
class ReleaseTemplate:
    """A TemplateSpec plus the release annotations (never model inputs)."""

    spec: TemplateSpec
    component: str
    structure_family: str
    output_shape: str | None
    index_variant: str | None
    regimes: tuple[str, ...]  # one label per parameter set, aligned with spec.parameter_sets
    holdout_reason: str | None
    pair_role: str | None
    equivalence_argument: str | None
    preconditions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class LabelPolicy:
    thresholds_ms: tuple[float, ...]
    censor_margin_ms: float
    planning_fallback_ms: float


@dataclass(frozen=True)
class SplitPolicy:
    n_folds: int
    seed: int


@dataclass(frozen=True)
class SamplePolicy:
    per_stratum: int
    seed: int


@dataclass(frozen=True)
class PositiveGate:
    min_keys: int
    min_groups: int
    min_components: int


@dataclass(frozen=True)
class ReleaseGates:
    min_usable_keys: int
    min_development_usable_keys: int
    min_groups_usable_on_all_snapshots: int
    max_template_share: float
    max_structure_family_share: float
    positives: PositiveGate
    max_sql_error_keys_in_sample: int
    max_failed_share_in_sample: float


@dataclass(frozen=True)
class BudgetPolicy:
    min_free_gb_data_root: float
    disk_check_every_n_keys: int
    max_preflight_age_hours: float
    min_free_memory_gb_warn: float
    min_docker_memory_gb_warn: float


@dataclass(frozen=True)
class RegimeSpec:
    name: str
    days: int
    start_from: date
    start_to: date


@dataclass(frozen=True)
class ReleaseSpec:
    path: Path
    sha256: str
    release_name: str
    id_prefix: str
    target_min_proposed_keys: int
    protocol: ProtocolConfig
    snapshots: dict[str, SnapshotSpec]
    expected_row_counts: dict[str, dict[str, int]]
    configurations: dict[str, ConfigurationSpec]
    labels: LabelPolicy
    splits: SplitPolicy
    sample: SamplePolicy
    gates: ReleaseGates
    budget: BudgetPolicy
    release_templates: tuple[ReleaseTemplate, ...]

    @property
    def templates(self) -> tuple[TemplateSpec, ...]:
        return tuple(rt.spec for rt in self.release_templates)

    def template_meta(self) -> dict[str, ReleaseTemplate]:
        return {rt.spec.template_id: rt for rt in self.release_templates}

    def holdout_reasons(self) -> dict[str, str]:
        return {rt.spec.semantic_group_id: rt.holdout_reason for rt in self.release_templates if rt.holdout_reason}


# ----------------------------------------------------------------------------------
# Deterministic parameter generation
# ----------------------------------------------------------------------------------
@dataclass(frozen=True)
class _Domain:
    size: int
    value: Callable[[int], Any]


def draw_index(seed: int, scope: str, draw: int, attempt: int, name: str, size: int) -> int:
    """Library-independent deterministic draw in ``[0, size)``."""
    if size <= 0:
        raise ConfigError(f"{scope}: empty domain for {name}")
    digest = hashlib.sha256(f"{seed}|{scope}|{draw}|{attempt}|{name}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % size


def _iso_date(text: Any, where: str) -> date:
    try:
        return date.fromisoformat(str(text))
    except ValueError as exc:
        raise ConfigError(f"{where}: {text!r} is not an ISO date") from exc


def make_domain(raw: Any, where: str) -> _Domain:
    """Build a value domain from one ``{kind: spec}`` mapping."""
    if not isinstance(raw, dict) or len(raw) != 1:
        raise ConfigError(f"{where}: a domain must be a single {{kind: spec}} mapping")
    kind, spec = next(iter(raw.items()))
    if kind == "int_range":
        lo, hi = int(spec[0]), int(spec[1])
        if lo > hi:
            raise ConfigError(f"{where}: int_range lower bound exceeds upper bound")
        return _Domain(hi - lo + 1, lambda i: lo + i)
    if kind == "choice":
        values = list(spec or [])
        if not values or len(values) != len({canonical_json(v) for v in values}):
            raise ConfigError(f"{where}: choice needs distinct values")
        return _Domain(len(values), lambda i: values[i])
    if kind == "decimal_range":
        try:
            lo, hi, step = (Decimal(str(v)) for v in spec)
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ConfigError(f"{where}: decimal_range needs [low, high, step]") from exc
        if step <= 0 or lo > hi:
            raise ConfigError(f"{where}: decimal_range needs low <= high and a positive step")
        count = int((hi - lo) / step) + 1
        return _Domain(count, lambda i: str(lo + step * i))
    if kind == "date_range":
        first, last = _iso_date(spec[0], where), _iso_date(spec[1], where)
        if first > last:
            raise ConfigError(f"{where}: date_range start is after its end")
        return _Domain((last - first).days + 1, lambda i: (first + timedelta(days=i)).isoformat())
    if kind == "month_start":
        try:
            y1, m1 = (int(p) for p in str(spec[0]).split("-"))
            y2, m2 = (int(p) for p in str(spec[1]).split("-"))
        except ValueError as exc:
            raise ConfigError(f"{where}: month_start needs ['YYYY-MM', 'YYYY-MM']") from exc
        count = (y2 * 12 + m2) - (y1 * 12 + m1) + 1
        if count <= 0 or not (1 <= m1 <= 12 and 1 <= m2 <= 12):
            raise ConfigError(f"{where}: invalid month_start range")
        return _Domain(count, lambda i: date(y1 + (m1 - 1 + i) // 12, (m1 - 1 + i) % 12 + 1, 1).isoformat())
    if kind == "year_start":
        y1, y2 = int(spec[0]), int(spec[1])
        if y1 > y2:
            raise ConfigError(f"{where}: year_start range is reversed")
        return _Domain(y2 - y1 + 1, lambda i: date(y1 + i, 1, 1).isoformat())
    raise ConfigError(f"{where}: unknown domain kind {kind!r}")


def generate_parameter_sets(raw: Any, parameter_types: dict[str, str], scope: str,
                            where: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Return ``(parameter_sets, regime_labels)`` for an ``explicit`` or ``sample`` generator."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: 'parameters' must be a mapping")
    kind = raw.get("kind")
    if kind == "explicit":
        sets = [dict(p) for p in (raw.get("sets") or [])]
        if not sets:
            raise ConfigError(f"{where}: explicit parameters need at least one set")
        keys = [canonical_json(s) for s in sets]
        if len(keys) != len(set(keys)):
            raise ConfigError(f"{where}: explicit parameter sets must be distinct")
        return sets, [str(raw.get("regime", "explicit"))] * len(sets)
    if kind == "sample":
        count, seed = int(raw["count"]), int(raw["seed"])
        raw_domains = raw.get("domains") or {}
        if set(raw_domains) != set(parameter_types):
            raise ConfigError(f"{where}: sample domains {sorted(raw_domains)} must match parameters {sorted(parameter_types)}")
        domains = {name: make_domain(raw_domains[name], f"{where} {name}") for name in parameter_types}
        distinct_groups = [tuple(str(n) for n in group) for group in (raw.get("distinct") or [])]
        for group in distinct_groups:
            if not set(group) <= set(parameter_types):
                raise ConfigError(f"{where}: distinct group {group} names unknown parameters")
        sets: list[dict[str, Any]] = []
        seen: set[str] = set()
        draw = 0
        while len(sets) < count:
            for attempt in range(MAX_DRAW_ATTEMPTS):
                values = {name: domains[name].value(draw_index(seed, scope, draw, attempt, name, domains[name].size))
                          for name in sorted(parameter_types)}
                if any(len({canonical_json(values[n]) for n in group}) != len(group) for group in distinct_groups):
                    continue
                fingerprint = canonical_json(values)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                sets.append(values)
                break
            else:
                raise ConfigError(f"{where}: could not draw {count} distinct parameter sets (domain too small?)")
            draw += 1
        return sets, [str(raw.get("regime", "spec_domain_sample"))] * count
    raise ConfigError(f"{where}: parameters.kind must be 'explicit' or 'sample'")


def regime_parameter_sets(regimes: dict[str, RegimeSpec], names: list[str], draws: int, seed: int,
                          scope: str) -> tuple[list[dict[str, Any]], list[str]]:
    """``{start_date, days}`` sets: ``draws`` per selectivity regime, shared by both index variants."""
    sets: list[dict[str, Any]] = []
    labels: list[str] = []
    for name in names:
        regime = regimes[name]
        window = (regime.start_to - regime.start_from).days + 1
        for d in range(draws):
            offset = draw_index(seed, scope, d, 0, name, window)
            sets.append({"days": regime.days, "start_date": (regime.start_from + timedelta(days=offset)).isoformat()})
            labels.append(name)
    return sets, labels


# ----------------------------------------------------------------------------------
# Structural composition
# ----------------------------------------------------------------------------------
def compose_structural_sql(shape_sql: str, path: dict[str, str], predicate: str) -> str:
    """Fill ``<<from>>``, ``<<pred>>``, ``<<dim_low>>`` and ``<<dim_high>>`` slots."""
    slots = {"from": path["from"], "pred": predicate, "dim_low": path["dim_low"], "dim_high": path["dim_high"]}
    unknown = set(SLOT_PATTERN.findall(shape_sql)) - set(slots)
    if unknown:
        raise ConfigError(f"unknown composition slots {sorted(unknown)}")
    composed = SLOT_PATTERN.sub(lambda m: slots[m.group(1)], shape_sql)
    if "<<" in composed or ">>" in composed:
        raise ConfigError("unfilled composition slot")
    return composed.strip() + "\n"


# ----------------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------------
def _req(mapping: dict[str, Any], key: str, where: str) -> Any:
    if not isinstance(mapping, dict) or key not in mapping or mapping[key] is None:
        raise ConfigError(f"{where}: missing '{key}'")
    return mapping[key]


def _validated_template(template_id: str, group_id: str, family: str, form: str, rewrite_source: str,
                        pair_id: str | None, sql: str, parameter_types: dict[str, str],
                        parameter_sets: list[dict[str, Any]], snapshots: tuple[str, ...],
                        configurations: tuple[str, ...], coverage: tuple[str, ...], mechanism: str) -> TemplateSpec:
    for kind in parameter_types.values():
        if kind not in ("int", "date", "word", "decimal", "token"):
            raise ConfigError(f"{template_id}: unknown parameter type {kind}")
    for params in parameter_sets:
        validate_read_only_sql(render_sql(sql, parameter_types, params))  # placeholders, values and safety
    return TemplateSpec(template_id=template_id, semantic_group_id=group_id, family=family, form=form,
                        rewrite_source=rewrite_source, rewrite_pair_id=pair_id, sql=sql,
                        parameter_types=dict(parameter_types), parameter_sets=tuple(parameter_sets),
                        snapshots=snapshots, configurations=configurations, coverage=coverage,
                        expected_mechanism=mechanism.strip())


def _component_targets(raw: dict[str, Any], where: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return tuple(_req(raw, "snapshots", where)), tuple(_req(raw, "configurations", where))


def _simple_groups(raw: dict[str, Any], component: str, prefix: str, short: str) -> list[ReleaseTemplate]:
    snapshots, configurations = _component_targets(raw, component)
    out = []
    for group in _req(raw, "groups", component):
        name = str(_req(group, "name", component))
        where = f"{component} {name}"
        types = {str(k): str(v) for k, v in (_req(group, "parameter_types", where) or {}).items()}
        sets, regimes = generate_parameter_sets(_req(group, "parameters", where), types,
                                                f"{prefix}:{short}:{name}", where)
        spec = _validated_template(
            f"tpl_{prefix}_{short}_{name}_v1", f"sg_{prefix}_{short}_{name}", f"{short}_{name}", "standalone",
            "NOT_A_REWRITE", None, str(_req(group, "sql", where)), types, sets, snapshots, configurations,
            tuple(group.get("coverage") or []), str(_req(group, "expected_mechanism", where)))
        out.append(ReleaseTemplate(spec=spec, component=component, structure_family=f"{short}_{name}",
                                   output_shape=None, index_variant=None, regimes=tuple(regimes),
                                   holdout_reason=None, pair_role=None, equivalence_argument=None, preconditions=()))
    return out


def _rewrite_pairs(raw: dict[str, Any], prefix: str) -> list[ReleaseTemplate]:
    snapshots, configurations = _component_targets(raw, "rewrite")
    out = []
    for pair in _req(raw, "pairs", "rewrite"):
        name = str(_req(pair, "name", "rewrite"))
        where = f"rewrite {name}"
        if str(pair.get("rewrite_source", "MANUAL_REFERENCE_REWRITE")) != "MANUAL_REFERENCE_REWRITE":
            raise ConfigError(f"{where}: only MANUAL_REFERENCE_REWRITE pairs are allowed (no LLM output in this release)")
        types = {str(k): str(v) for k, v in (_req(pair, "parameter_types", where) or {}).items()}
        sets, regimes = generate_parameter_sets(_req(pair, "parameters", where), types, f"{prefix}:rw:{name}", where)
        group_id, pair_id, family = f"sg_{prefix}_rw_{name}", f"{prefix}_{name}", f"rw_{name}"
        mechanism = str(_req(pair, "expected_mechanism", where))
        argument = str(_req(pair, "equivalence_argument", where)).strip()
        preconditions = tuple((str(p["name"]), str(p["sql"])) for p in (pair.get("preconditions") or []))
        holdout = pair.get("holdout_reason")
        coverage = ("rewrite_pair",)
        for form, key in (("original", "original_sql"), ("reference_rewrite", "rewrite_sql")):
            source = "NOT_A_REWRITE" if form == "original" else "MANUAL_REFERENCE_REWRITE"
            suffix = "original" if form == "original" else "rewrite"
            spec = _validated_template(f"tpl_{prefix}_rw_{name}_{suffix}_v1", group_id, family, form, source, pair_id,
                                       str(_req(pair, key, where)), types, sets, snapshots, configurations, coverage,
                                       mechanism)
            out.append(ReleaseTemplate(spec=spec, component="rewrite", structure_family=family, output_shape=None,
                                       index_variant=None, regimes=tuple(regimes),
                                       holdout_reason=str(holdout) if holdout else None, pair_role="reference_pair",
                                       equivalence_argument=argument, preconditions=preconditions))
    return out


def _regimes(raw: dict[str, Any]) -> dict[str, RegimeSpec]:
    regimes = {}
    for name, spec in (raw or {}).items():
        window = list(_req(spec, "start_window", f"regime {name}"))
        regime = RegimeSpec(name=str(name), days=int(_req(spec, "days", f"regime {name}")),
                            start_from=_iso_date(window[0], f"regime {name}"), start_to=_iso_date(window[1], f"regime {name}"))
        if regime.days <= 0 or regime.start_from > regime.start_to:
            raise ConfigError(f"regime {name}: days must be positive and the start window ordered")
        regimes[str(name)] = regime
    if len({r.days for r in regimes.values()}) != len(regimes):
        raise ConfigError("parameter regimes must differ in their range length (days)")
    return regimes


def _structural(raw: dict[str, Any], regimes: dict[str, RegimeSpec], prefix: str) -> list[ReleaseTemplate]:
    snapshots, configurations = _component_targets(raw, "structural")
    names = [str(n) for n in _req(raw, "regimes", "structural")]
    unknown = [n for n in names if n not in regimes]
    if unknown:
        raise ConfigError(f"structural: unknown regimes {unknown}")
    draws, seed = int(_req(raw, "draws_per_regime", "structural")), int(_req(raw, "seed", "structural"))
    predicates = _req(raw, "predicates", "structural")
    variants = {INDEXED: str(_req(predicates, INDEXED, "structural predicates")),
                UNINDEXED: str(_req(predicates, UNINDEXED, "structural predicates"))}
    paths = _req(raw, "paths", "structural")
    shapes = _req(raw, "shapes", "structural")
    excluded = {tuple(item) for item in (raw.get("exclude") or [])}
    types = {"start_date": "date", "days": "int"}
    out = []
    for path_name, path in paths.items():
        for field in ("from", "dim_low", "dim_high", "description"):
            _req(path, field, f"structural path {path_name}")
        for shape_name, shape in shapes.items():
            if (path_name, shape_name) in excluded:
                continue
            group_id = f"sg_{prefix}_gen_{path_name}_{shape_name}"
            sets, labels = regime_parameter_sets(regimes, names, draws, seed, group_id)
            for variant, predicate in variants.items():
                short = "idx" if variant == INDEXED else "noidx"
                column = "indexed l_shipdate" if variant == INDEXED else "unindexed l_receiptdate"
                mechanism = (f"Generated structure: {path['description']}; output: {_req(shape, 'description', shape_name)}; "
                             f"date-range predicate on {column}. Expected cost is a hypothesis; only measurement decides.")
                sql = compose_structural_sql(str(_req(shape, "sql", shape_name)), path, predicate)
                spec = _validated_template(f"tpl_{prefix}_gen_{path_name}_{shape_name}_{short}_v1", group_id,
                                           f"gen_{shape_name}", "standalone", "NOT_A_REWRITE", None, sql, types, sets,
                                           snapshots, configurations,
                                           (f"path:{path_name}", f"shape:{shape_name}", "index_contrast"), mechanism)
                out.append(ReleaseTemplate(spec=spec, component="structural", structure_family=f"gen_{path_name}",
                                           output_shape=str(shape_name), index_variant=variant, regimes=tuple(labels),
                                           holdout_reason=None, pair_role=None, equivalence_argument=None,
                                           preconditions=()))
    return out


def parse_release_spec(document: dict[str, Any], path: Path, sha256: str) -> ReleaseSpec:
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ConfigError("release spec must be a mapping with version: 1")
    release = _req(document, "release", "spec")
    prefix = str(_req(release, "id_prefix", "release"))
    if not re.fullmatch(r"[a-z][a-z0-9]{0,7}", prefix):
        raise ConfigError("release.id_prefix must be a short lowercase identifier")
    protocol = parse_protocol(_req(document, "protocol", "spec"))
    if "SUMMARY ON" not in protocol.explain_estimate_options.upper():
        raise ConfigError("the expanded plain EXPLAIN must include SUMMARY ON (planning time bounds the censoring)")
    snapshots = parse_snapshots(_req(document, "snapshots", "spec"))
    if not all(s.required for s in snapshots.values()):
        raise ConfigError("every expanded-release snapshot is required (no silent skips)")
    configurations = parse_configurations(_req(document, "configurations", "spec"))

    raw_labels = _req(document, "labels", "spec")
    labels = LabelPolicy(
        thresholds_ms=tuple(float(v) for v in _req(raw_labels, "frozen_high_runtime_thresholds_ms", "labels")),
        censor_margin_ms=float(_req(raw_labels, "censor_margin_ms", "labels")),
        planning_fallback_ms=float(_req(raw_labels, "planning_fallback_ms", "labels")),
    )
    if labels.thresholds_ms != protocol.high_runtime_thresholds_ms:
        raise ConfigError("labels.frozen_high_runtime_thresholds_ms must equal protocol.high_runtime_thresholds_ms")
    if labels.thresholds_ms != (1000.0, 10000.0):
        raise ConfigError("the expanded release freezes the high-runtime thresholds at 1 s and 10 s")
    if protocol.statement_timeout_ms - labels.planning_fallback_ms - labels.censor_margin_ms < max(labels.thresholds_ms):
        raise ConfigError("statement_timeout_ms is too small: a censored key could not reach the highest threshold")

    raw_splits = _req(document, "splits", "spec")
    splits = SplitPolicy(n_folds=int(_req(raw_splits, "n_folds", "splits")), seed=int(_req(raw_splits, "seed", "splits")))
    raw_sample = _req(document, "sample", "spec")
    sample = SamplePolicy(per_stratum=int(_req(raw_sample, "per_stratum", "sample")),
                          seed=int(_req(raw_sample, "seed", "sample")))
    raw_gates = _req(document, "gates", "spec")
    raw_pos = _req(raw_gates, "positives", "gates")
    gates = ReleaseGates(
        min_usable_keys=int(_req(raw_gates, "min_usable_keys", "gates")),
        min_development_usable_keys=int(_req(raw_gates, "min_development_usable_keys", "gates")),
        min_groups_usable_on_all_snapshots=int(_req(raw_gates, "min_groups_usable_on_all_snapshots", "gates")),
        max_template_share=float(_req(raw_gates, "max_template_share", "gates")),
        max_structure_family_share=float(_req(raw_gates, "max_structure_family_share", "gates")),
        positives=PositiveGate(min_keys=int(_req(raw_pos, "min_keys", "positives")),
                               min_groups=int(_req(raw_pos, "min_groups", "positives")),
                               min_components=int(_req(raw_pos, "min_components", "positives"))),
        max_sql_error_keys_in_sample=int(_req(raw_gates, "max_sql_error_keys_in_sample", "gates")),
        max_failed_share_in_sample=float(_req(raw_gates, "max_failed_share_in_sample", "gates")),
    )
    raw_budget = _req(document, "budget", "spec")
    budget = BudgetPolicy(
        min_free_gb_data_root=float(_req(raw_budget, "min_free_gb_data_root", "budget")),
        disk_check_every_n_keys=int(_req(raw_budget, "disk_check_every_n_keys", "budget")),
        max_preflight_age_hours=float(_req(raw_budget, "max_preflight_age_hours", "budget")),
        min_free_memory_gb_warn=float(_req(raw_budget, "min_free_memory_gb_warn", "budget")),
        min_docker_memory_gb_warn=float(_req(raw_budget, "min_docker_memory_gb_warn", "budget")),
    )

    components = _req(document, "components", "spec")
    unknown = set(components) - set(COMPONENTS)
    if unknown:
        raise ConfigError(f"unknown components {sorted(unknown)}")
    regimes = _regimes(document.get("parameter_regimes"))
    templates: list[ReleaseTemplate] = []
    templates += _simple_groups(_req(components, "tpch_derived", "components"), "tpch_derived", prefix, "tpch")
    templates += _structural(_req(components, "structural", "components"), regimes, prefix)
    templates += _simple_groups(_req(components, "probe", "components"), "probe", prefix, "probe")
    templates += _rewrite_pairs(_req(components, "rewrite", "components"), prefix)

    ids = [t.spec.template_id for t in templates]
    duplicates = [i for i, n in Counter(ids).items() if n > 1]
    if duplicates:
        raise ConfigError(f"duplicate template ids: {duplicates[:5]}")
    group_family: dict[str, str] = {}
    group_component: dict[str, str] = {}
    for t in templates:
        if group_family.setdefault(t.spec.semantic_group_id, t.spec.family) != t.spec.family:
            raise ConfigError(f"semantic group {t.spec.semantic_group_id} is used by more than one family")
        if group_component.setdefault(t.spec.semantic_group_id, t.component) != t.component:
            raise ConfigError(f"semantic group {t.spec.semantic_group_id} spans components")
        for snap in t.spec.snapshots:
            if snap not in snapshots:
                raise ConfigError(f"{t.spec.template_id}: unknown snapshot {snap}")
        for cfg_name in t.spec.configurations:
            if cfg_name not in configurations:
                raise ConfigError(f"{t.spec.template_id}: unknown configuration {cfg_name}")
        if t.holdout_reason and t.component != "rewrite":
            raise ConfigError(f"{t.spec.template_id}: only rewrite groups are reserved for the RQ2 holdout")
    expected_counts = {str(sf): {str(table): int(n) for table, n in counts.items()}
                       for sf, counts in (document.get("expected_row_counts") or {}).items()}
    return ReleaseSpec(
        path=path, sha256=sha256, release_name=str(_req(release, "release_name", "release")), id_prefix=prefix,
        target_min_proposed_keys=int(_req(release, "target_min_proposed_keys", "release")), protocol=protocol,
        snapshots=snapshots, expected_row_counts=expected_counts, configurations=configurations, labels=labels,
        splits=splits, sample=sample, gates=gates, budget=budget, release_templates=tuple(templates),
    )


def default_release_spec_path() -> Path:
    from query_cost_predictor.paths import config_dir

    return config_dir() / "expanded_release_v1.yaml"


def load_release_spec(path: Path | None = None) -> ReleaseSpec:
    target = path or default_release_spec_path()
    with target.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return parse_release_spec(document, target, sha256_file(target))


# ----------------------------------------------------------------------------------
# Entry annotation and planning arithmetic (no database)
# ----------------------------------------------------------------------------------
def annotate_entries(spec: ReleaseSpec, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add release annotations to expanded manifest entries (annotations are never features)."""
    meta = spec.template_meta()
    regime_of: dict[tuple[str, str], str] = {}
    for rt in spec.release_templates:
        for params, regime in zip(rt.spec.parameter_sets, rt.regimes):
            regime_of[(rt.spec.template_id, canonical_json(params))] = regime
    annotated = []
    for entry in entries:
        rt = meta[entry["template_id"]]
        annotated.append({
            **entry,
            "component": rt.component,
            "structure_family": rt.structure_family,
            "output_shape": rt.output_shape,
            "index_variant": rt.index_variant,
            "parameter_regime": regime_of[(entry["template_id"], canonical_json(entry["parameters"]))],
            "split_role": SPLIT_HELDOUT_RQ2 if rt.holdout_reason else SPLIT_DEVELOPMENT,
            "holdout_reason": rt.holdout_reason,
        })
    return annotated


def planning_entries(spec: ReleaseSpec) -> list[dict[str, Any]]:
    """Expand the spec with placeholder snapshot/configuration ids (counts only; never registered)."""
    from query_cost_predictor.smoke_manifest import expand_entries

    snapshot_ids = {name: f"snap_planning_{name}" for name in spec.snapshots}
    config_ids = {name: f"cfg_planning_{name}" for name in spec.configurations}
    entries, skipped = expand_entries(spec, snapshot_ids, config_ids)
    if skipped:
        raise ConfigError("planning expansion skipped entries; every snapshot must be defined")
    return annotate_entries(spec, entries)


def _table(entries: list[dict[str, Any]], *fields: str) -> list[dict[str, Any]]:
    counts = Counter(tuple(e.get(f) for f in fields) for e in entries)
    rows = []
    for values, n in sorted(counts.items(), key=lambda item: tuple(str(v) for v in item[0])):
        subset = [e for e in entries if tuple(e.get(f) for f in fields) == values]
        rows.append({**dict(zip(fields, values)), "keys": n,
                     "semantic_groups": len({e["semantic_group_id"] for e in subset}),
                     "templates": len({e["template_id"] for e in subset})})
    return rows


def release_arithmetic(spec: ReleaseSpec, entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Counts by component, family, template, regime, snapshot, configuration and split role (PLANNED)."""
    rows = entries if entries is not None else planning_entries(spec)
    keys = [e["modeling_key"] for e in rows]
    if len(keys) != len(set(keys)):
        raise ConfigError("planning expansion produced duplicate modeling keys")
    dev = [e for e in rows if e["split_role"] == SPLIT_DEVELOPMENT]
    held = [e for e in rows if e["split_role"] == SPLIT_HELDOUT_RQ2]
    n = int(spec.protocol.measured_repetitions)
    timeout_s = spec.protocol.statement_timeout_ms / 1000.0
    per_template = Counter(e["template_id"] for e in rows)
    per_family = Counter(e["structure_family"] for e in rows)
    return {
        "evidence_status": "PLANNED",
        "note": "Planning arithmetic from the release spec. A manifest entry is a proposed modeling key, not a "
                "measured label; repetitions are child records and never extra keys.",
        "release_name": spec.release_name,
        "spec_file": {"path": str(spec.path), "sha256": spec.sha256},
        "totals": {
            "proposed_keys": len(rows),
            "development_keys": len(dev),
            "heldout_rq2_keys": len(held),
            "semantic_groups": len({e["semantic_group_id"] for e in rows}),
            "development_groups": len({e["semantic_group_id"] for e in dev}),
            "heldout_rq2_groups": len({e["semantic_group_id"] for e in held}),
            "structure_families": len(per_family),
            "templates": len(per_template),
            "query_instances": len({e["query_instance_id"] for e in rows}),
            "measured_runs_planned": len(rows) * n,
            "probe_runs_planned": len(rows),
            "plain_explains_planned": len(rows),
            "max_keys_per_template": max(per_template.values()),
            "max_template_share": max(per_template.values()) / len(rows),
            "max_structure_family_share": max(per_family.values()) / len(rows),
        },
        "meets_target_min_proposed_keys": len(rows) >= spec.target_min_proposed_keys,
        "target_min_proposed_keys": spec.target_min_proposed_keys,
        "worst_case_hours": {
            "measured_runs_only_at_timeout": round(len(rows) * n * timeout_s / 3600.0, 1),
            "probe_and_measured_runs_at_timeout": round(len(rows) * (n + 1) * timeout_s / 3600.0, 1),
            "reference_4704_keys_measured_only": round(4704 * n * timeout_s / 3600.0, 1),
            "note": "Upper bounds if every run hit the statement timeout; plain EXPLAIN, overhead and the watchdog "
                    "grace are excluded. The realistic budget is projected from the bounded sample.",
        },
        "by_component": _table(rows, "component"),
        "by_component_snapshot_configuration": _table(rows, "component", "snapshot_name", "configuration_name"),
        "by_component_regime": _table(rows, "component", "parameter_regime"),
        "by_index_variant": _table(rows, "component", "index_variant"),
        "by_structure_family": _table(rows, "component", "structure_family"),
        "by_family": _table(rows, "component", "family"),
        "by_split_role": _table(rows, "split_role"),
        "by_template": [{"template_id": t, "keys": c} for t, c in sorted(per_template.items())],
    }
