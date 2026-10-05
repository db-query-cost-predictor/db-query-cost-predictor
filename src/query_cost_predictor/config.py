"""Typed, validated configuration.

* :func:`load_poster_smoke_config` reads ``config/poster_smoke.yaml`` (the
  poster-smoke definition — not a dataset).
* :func:`database_settings_from_env` reads connection settings from the
  environment (loaded from ``.env``) and rejects placeholders, non-local hosts
  and malformed passwords. It never chooses a fallback port or host.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from query_cost_predictor.hashing import sha256_file


class ConfigError(ValueError):
    """Invalid or incomplete configuration."""


LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PASSWORD_PATTERN = re.compile(r"^[A-Za-z0-9]{16,}$")
TEMP_FILE_LIMIT_PATTERN = re.compile(r"^\d+(kB|MB|GB|TB)$")
SETTING_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9._]+$")
PLACEHOLDER_PATTERN = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
ALLOWED_SESSION_SETTINGS = frozenset(
    {
        "jit", "work_mem", "hash_mem_multiplier", "max_parallel_workers_per_gather", "enable_hashjoin",
        "enable_mergejoin", "enable_nestloop", "enable_seqscan", "enable_indexscan", "enable_bitmapscan",
        "enable_sort", "enable_hashagg", "random_page_cost",
    }
)
PARAMETER_TYPES = frozenset({"int", "date", "word", "decimal"})
REWRITE_ROLES = frozenset({"reference_pair", "negative_control"})


# ----------------------------------------------------------------------------------
# Poster smoke configuration
# ----------------------------------------------------------------------------------
@dataclass(frozen=True)
class ProtocolConfig:
    protocol_version: str
    cache_protocol: str
    statement_timeout_ms: int
    planned_final_timeout_ms: int
    lock_timeout_ms: int
    warmup_probes: int
    measured_repetitions: int
    order_seed: int
    watchdog_grace_ms: int
    snapshot_check_every_n_keys: int
    max_transient_retries: int
    high_runtime_thresholds_ms: tuple[float, ...]
    explain_estimate_options: str
    explain_measured_options: str
    min_keys: int
    max_keys: int


@dataclass(frozen=True)
class SnapshotSpec:
    name: str
    database: str
    workload: str
    scale_factor: str
    required: bool


@dataclass(frozen=True)
class ConfigurationSpec:
    name: str
    description: str
    session_settings: dict[str, str]


@dataclass(frozen=True)
class TemplateSpec:
    """One SQL template (a standalone family or one form of a rewrite pair)."""

    template_id: str
    semantic_group_id: str
    family: str
    form: str  # standalone | original | reference_rewrite | negative_control_rewrite
    rewrite_source: str  # NOT_A_REWRITE | MANUAL_REFERENCE_REWRITE
    rewrite_pair_id: str | None
    sql: str
    parameter_types: dict[str, str]
    parameter_sets: tuple[dict[str, Any], ...]
    snapshots: tuple[str, ...]
    configurations: tuple[str, ...]
    coverage: tuple[str, ...]
    expected_mechanism: str


@dataclass(frozen=True)
class RewritePairSpec:
    pair_id: str
    family: str
    semantic_group_id: str
    role: str
    rewrite_source: str
    expected_equivalent: bool
    ordering_required: bool
    order_by_columns: tuple[int, ...]
    equivalence_argument: str
    preconditions: tuple[tuple[str, str], ...]
    original: TemplateSpec
    rewrite: TemplateSpec
    verification_snapshots: tuple[str, ...]


@dataclass(frozen=True)
class RewriteVerificationConfig:
    timing_pairs: int
    warmup_each: int
    min_speedup_to_recommend: float
    max_result_rows: int
    statement_timeout_ms: int
    order_seed: int
    configuration: str


@dataclass(frozen=True)
class PosterSmokeConfig:
    path: Path
    sha256: str
    protocol: ProtocolConfig
    snapshots: dict[str, SnapshotSpec]
    expected_row_counts: dict[str, dict[str, int]]
    configurations: dict[str, ConfigurationSpec]
    required_coverage: tuple[str, ...]
    families: tuple[TemplateSpec, ...]
    rewrite_pairs: tuple[RewritePairSpec, ...]
    rewrite_verification: RewriteVerificationConfig
    templates: tuple[TemplateSpec, ...] = field(default=())

    def template(self, template_id: str) -> TemplateSpec:
        for spec in self.templates:
            if spec.template_id == template_id:
                return spec
        raise KeyError(template_id)


def default_poster_smoke_path() -> Path:
    from query_cost_predictor.paths import config_dir

    return config_dir() / "poster_smoke.yaml"


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping or mapping[key] is None:
        raise ConfigError(f"{where}: missing '{key}'")
    return mapping[key]


def render_parameter(value: Any, kind: str, name: str) -> str:
    """Validate one parameter value and return its SQL text (quotes come from the template)."""
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"parameter {name}: expected int, got {value!r}")
        return str(value)
    if kind == "date":
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ConfigError(f"parameter {name}: expected a quoted ISO date string, got {value!r}")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(f"parameter {name}: {value!r} is not a valid calendar date") from exc
        return value
    if kind == "word":
        if not isinstance(value, str) or not re.fullmatch(r"[a-z]{1,40}", value):
            raise ConfigError(f"parameter {name}: expected lowercase letters only, got {value!r}")
        return value
    if kind == "decimal":
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ConfigError(f"parameter {name}: expected a decimal, got {value!r}") from exc
        if not number.is_finite():
            raise ConfigError(f"parameter {name}: decimal must be finite")
        return str(number)
    raise ConfigError(f"parameter {name}: unknown type {kind!r}")


def render_sql(template_sql: str, parameter_types: dict[str, str], parameters: dict[str, Any]) -> str:
    """Substitute validated parameters into ``{name}`` placeholders (exact-match check)."""
    placeholders = set(PLACEHOLDER_PATTERN.findall(template_sql))
    if placeholders != set(parameter_types):
        raise ConfigError(f"placeholders {sorted(placeholders)} do not match parameter types {sorted(parameter_types)}")
    if set(parameters) != set(parameter_types):
        raise ConfigError(f"parameter set {sorted(parameters)} does not match parameter types {sorted(parameter_types)}")
    rendered = {name: render_parameter(parameters[name], parameter_types[name], name) for name in parameters}
    return PLACEHOLDER_PATTERN.sub(lambda m: rendered[m.group(1)], template_sql).strip()


def _template(raw: dict[str, Any], *, where: str, family: str, semantic_group_id: str, form: str,
              rewrite_source: str, rewrite_pair_id: str | None, shared: dict[str, Any]) -> TemplateSpec:
    parameter_types = {str(k): str(v) for k, v in (_require(shared, "parameter_types", where) or {}).items()}
    for name, kind in parameter_types.items():
        if kind not in PARAMETER_TYPES:
            raise ConfigError(f"{where}: parameter {name} has unknown type {kind}")
    parameter_sets = tuple(dict(p) for p in _require(shared, "parameter_sets", where))
    if not parameter_sets:
        raise ConfigError(f"{where}: at least one parameter set is required")
    sql = str(_require(raw, "sql", where))
    for params in parameter_sets:
        render_sql(sql, parameter_types, params)  # validates placeholders and values
    return TemplateSpec(
        template_id=str(_require(raw, "template_id", where)),
        semantic_group_id=semantic_group_id,
        family=family,
        form=form,
        rewrite_source=rewrite_source,
        rewrite_pair_id=rewrite_pair_id,
        sql=sql,
        parameter_types=parameter_types,
        parameter_sets=parameter_sets,
        snapshots=tuple(_require(shared, "snapshots", where)),
        configurations=tuple(_require(shared, "configurations", where)),
        coverage=tuple(shared.get("coverage", []) or []),
        expected_mechanism=str(_require(shared, "expected_mechanism", where)),
    )


def parse_poster_smoke_config(document: dict[str, Any], path: Path, sha256: str) -> PosterSmokeConfig:
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ConfigError("poster smoke config must be a mapping with version: 1")
    raw_protocol = _require(document, "protocol", "config")
    protocol = ProtocolConfig(
        protocol_version=str(_require(raw_protocol, "protocol_version", "protocol")),
        cache_protocol=str(_require(raw_protocol, "cache_protocol", "protocol")),
        statement_timeout_ms=int(_require(raw_protocol, "statement_timeout_ms", "protocol")),
        planned_final_timeout_ms=int(_require(raw_protocol, "planned_final_timeout_ms", "protocol")),
        lock_timeout_ms=int(_require(raw_protocol, "lock_timeout_ms", "protocol")),
        warmup_probes=int(_require(raw_protocol, "warmup_probes", "protocol")),
        measured_repetitions=int(_require(raw_protocol, "measured_repetitions", "protocol")),
        order_seed=int(_require(raw_protocol, "order_seed", "protocol")),
        watchdog_grace_ms=int(_require(raw_protocol, "watchdog_grace_ms", "protocol")),
        snapshot_check_every_n_keys=int(_require(raw_protocol, "snapshot_check_every_n_keys", "protocol")),
        max_transient_retries=int(_require(raw_protocol, "max_transient_retries", "protocol")),
        high_runtime_thresholds_ms=tuple(float(v) for v in _require(raw_protocol, "high_runtime_thresholds_ms", "protocol")),
        explain_estimate_options=str(_require(raw_protocol, "explain_estimate_options", "protocol")),
        explain_measured_options=str(_require(raw_protocol, "explain_measured_options", "protocol")),
        min_keys=int(_require(raw_protocol, "min_keys", "protocol")),
        max_keys=int(_require(raw_protocol, "max_keys", "protocol")),
    )
    if protocol.cache_protocol != "warm":
        raise ConfigError("the poster smoke collection uses the warm-cache protocol only")
    if protocol.warmup_probes != 1:
        raise ConfigError("exactly one unmeasured warm-up/probe is required")
    if protocol.measured_repetitions < 1 or protocol.measured_repetitions % 2 == 0:
        raise ConfigError("measured_repetitions must be an odd number >= 1")
    if protocol.statement_timeout_ms <= 0 or protocol.lock_timeout_ms <= 0:
        raise ConfigError("timeouts must be positive")
    for required_option in ("ANALYZE", "TIMING OFF", "SUMMARY ON", "SETTINGS", "FORMAT JSON", "BUFFERS", "WAL"):
        if required_option not in protocol.explain_measured_options.upper():
            raise ConfigError(f"explain_measured_options must include {required_option}")
    if "ANALYZE" in protocol.explain_estimate_options.upper():
        raise ConfigError("the pre-execution estimate must be a plain EXPLAIN (no ANALYZE)")

    snapshots = {}
    for name, raw in _require(document, "snapshots", "config").items():
        snapshots[name] = SnapshotSpec(name=name, database=str(raw["database"]), workload=str(raw["workload"]),
                                       scale_factor=str(raw["scale_factor"]), required=bool(raw["required"]))
    configurations = {}
    for name, raw in _require(document, "configurations", "config").items():
        settings = {str(k): str(v) for k, v in (raw.get("session_settings") or {}).items()}
        for key, value in settings.items():
            if key not in ALLOWED_SESSION_SETTINGS:
                raise ConfigError(f"configuration {name}: session setting {key} is not allowed")
            if not SETTING_VALUE_PATTERN.match(value):
                raise ConfigError(f"configuration {name}: invalid value for {key}")
        if settings.get("jit") != "off":
            raise ConfigError(f"configuration {name}: jit must be 'off'")
        configurations[name] = ConfigurationSpec(name=name, description=str(raw.get("description", "")), session_settings=settings)

    families = []
    for raw in _require(document, "families", "config"):
        where = f"family {raw.get('family')}"
        families.append(_template(raw, where=where, family=str(_require(raw, "family", where)),
                                  semantic_group_id=str(_require(raw, "semantic_group_id", where)), form="standalone",
                                  rewrite_source="NOT_A_REWRITE", rewrite_pair_id=None, shared=raw))

    pairs = []
    for raw in document.get("rewrite_pairs", []) or []:
        where = f"rewrite pair {raw.get('pair_id')}"
        role = str(_require(raw, "role", where))
        if role not in REWRITE_ROLES:
            raise ConfigError(f"{where}: role must be one of {sorted(REWRITE_ROLES)}")
        source = str(_require(raw, "rewrite_source", where))
        if source != "MANUAL_REFERENCE_REWRITE":
            raise ConfigError(f"{where}: the poster milestone only allows MANUAL_REFERENCE_REWRITE (no LLM rewrites)")
        expected = bool(_require(raw, "expected_equivalent", where))
        if (role == "negative_control") == expected:
            raise ConfigError(f"{where}: reference pairs must be expected equivalent; negative controls must not")
        pair_id = str(_require(raw, "pair_id", where))
        family = str(_require(raw, "family", where))
        group = str(_require(raw, "semantic_group_id", where))
        rewrite_form = "negative_control_rewrite" if role == "negative_control" else "reference_rewrite"
        original = _template(_require(raw, "original", where), where=where + " original", family=family,
                             semantic_group_id=group, form="original", rewrite_source="NOT_A_REWRITE",
                             rewrite_pair_id=pair_id, shared=raw)
        rewrite = _template(_require(raw, "rewrite", where), where=where + " rewrite", family=family,
                            semantic_group_id=group, form=rewrite_form, rewrite_source=source,
                            rewrite_pair_id=pair_id, shared=raw)
        preconditions = tuple((str(p["name"]), str(p["sql"])) for p in (raw.get("preconditions") or []))
        pairs.append(RewritePairSpec(
            pair_id=pair_id, family=family, semantic_group_id=group, role=role, rewrite_source=source,
            expected_equivalent=expected, ordering_required=bool(raw.get("ordering_required", False)),
            order_by_columns=tuple(int(c) for c in (raw.get("order_by_columns") or [])),
            equivalence_argument=str(_require(raw, "equivalence_argument", where)).strip(),
            preconditions=preconditions, original=original, rewrite=rewrite,
            verification_snapshots=tuple(raw.get("verification_snapshots") or original.snapshots),
        ))

    raw_verify = _require(document, "rewrite_verification", "config")
    verification = RewriteVerificationConfig(
        timing_pairs=int(raw_verify["timing_pairs"]), warmup_each=int(raw_verify["warmup_each"]),
        min_speedup_to_recommend=float(raw_verify["min_speedup_to_recommend"]),
        max_result_rows=int(raw_verify["max_result_rows"]), statement_timeout_ms=int(raw_verify["statement_timeout_ms"]),
        order_seed=int(raw_verify["order_seed"]), configuration=str(raw_verify["configuration"]),
    )
    if verification.configuration not in configurations:
        raise ConfigError("rewrite_verification.configuration is not a defined configuration")

    templates = tuple(families) + tuple(t for p in pairs for t in (p.original, p.rewrite))
    ids = [t.template_id for t in templates]
    if len(ids) != len(set(ids)):
        raise ConfigError("template_id values must be unique")
    group_family: dict[str, str] = {}
    for spec in templates:
        if group_family.setdefault(spec.semantic_group_id, spec.family) != spec.family:
            raise ConfigError(f"semantic group {spec.semantic_group_id} is used by more than one family")
        for snap in spec.snapshots:
            if snap not in snapshots:
                raise ConfigError(f"{spec.template_id}: unknown snapshot {snap}")
        for cfg in spec.configurations:
            if cfg not in configurations:
                raise ConfigError(f"{spec.template_id}: unknown configuration {cfg}")
    required_coverage = tuple(_require(document, "required_coverage", "config"))
    covered = {tag for spec in templates for tag in spec.coverage}
    missing = [tag for tag in required_coverage if tag not in covered]
    if missing:
        raise ConfigError(f"required coverage tags without any template: {missing}")
    expected_counts = {str(sf): {str(t): int(n) for t, n in counts.items()}
                       for sf, counts in (document.get("expected_row_counts") or {}).items()}
    return PosterSmokeConfig(
        path=path, sha256=sha256, protocol=protocol, snapshots=snapshots, expected_row_counts=expected_counts,
        configurations=configurations, required_coverage=required_coverage, families=tuple(families),
        rewrite_pairs=tuple(pairs), rewrite_verification=verification, templates=templates,
    )


def load_poster_smoke_config(path: Path | None = None) -> PosterSmokeConfig:
    target = path or default_poster_smoke_path()
    with target.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return parse_poster_smoke_config(document, target, sha256_file(target))


# ----------------------------------------------------------------------------------
# Database settings from the environment
# ----------------------------------------------------------------------------------
@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    bench_port: int
    evidence_port: int
    bench_admin_user: str
    bench_admin_password: str
    evidence_admin_user: str
    evidence_admin_password: str
    bench_owner_password: str
    bench_reader_password: str
    evidence_owner_password: str
    evidence_writer_password: str
    reader_temp_file_limit: str


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"environment variable {name} is not set (check .env, runbook step 3)")
    if "CHANGE_ME" in value:
        raise ConfigError(f"environment variable {name} still contains a CHANGE_ME placeholder")
    return value


def _port(name: str) -> int:
    raw = _env(name)
    if not raw.isdigit() or not 1024 <= int(raw) <= 65535:
        raise ConfigError(f"{name} must be an integer port between 1024 and 65535")
    return int(raw)


def _password(name: str) -> str:
    value = _env(name)
    if not PASSWORD_PATTERN.match(value):
        raise ConfigError(f"{name} must contain only letters and digits and be at least 16 characters")
    return value


def database_settings_from_env() -> DatabaseSettings:
    host = _env("QCP_DB_HOST")
    if host not in LOCAL_HOSTS:
        raise ConfigError(f"QCP_DB_HOST must be a local address {sorted(LOCAL_HOSTS)}; got {host!r}")
    bench_port, evidence_port = _port("QCP_BENCH_PORT"), _port("QCP_EVIDENCE_PORT")
    if bench_port == evidence_port:
        raise ConfigError("QCP_BENCH_PORT and QCP_EVIDENCE_PORT must differ")
    temp_limit = _env("QCP_READER_TEMP_FILE_LIMIT")
    if not TEMP_FILE_LIMIT_PATTERN.match(temp_limit):
        raise ConfigError("QCP_READER_TEMP_FILE_LIMIT must look like 5GB or 500MB")
    return DatabaseSettings(
        host=host,
        bench_port=bench_port,
        evidence_port=evidence_port,
        bench_admin_user=_env("QCP_BENCH_ADMIN_USER"),
        bench_admin_password=_password("QCP_BENCH_ADMIN_PASSWORD"),
        evidence_admin_user=_env("QCP_EVIDENCE_ADMIN_USER"),
        evidence_admin_password=_password("QCP_EVIDENCE_ADMIN_PASSWORD"),
        bench_owner_password=_password("QCP_BENCH_OWNER_PASSWORD"),
        bench_reader_password=_password("QCP_BENCH_READER_PASSWORD"),
        evidence_owner_password=_password("QCP_EVIDENCE_OWNER_PASSWORD"),
        evidence_writer_password=_password("QCP_EVIDENCE_WRITER_PASSWORD"),
        reader_temp_file_limit=temp_limit,
    )
