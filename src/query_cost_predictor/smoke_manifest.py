"""Build and register the poster-smoke manifest (no query is executed here).

Pure functions (unit-tested): :func:`expand_entries`, :func:`assign_execution_order`,
:func:`check_entries`, :func:`manifest_document`, :func:`manifest_id_for`.
Database work: capture effective settings per configuration, register the
query registry, configurations, modeling keys and the immutable manifest.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from query_cost_predictor.config import PosterSmokeConfig, load_poster_smoke_config, render_sql
from query_cost_predictor.db import connect_bench_reader, connect_evidence_writer, server_identity
from query_cost_predictor.executor import capture_effective_settings
from query_cost_predictor.hashing import (
    config_id,
    modeling_key,
    query_instance_id,
    sha256_file,
    sha256_json,
    sha256_text,
)
from query_cost_predictor.paths import data_root, ensure_dir, environment_reports_dir
from query_cost_predictor.safety import validate_read_only_sql
from query_cost_predictor.snapshot import latest_snapshot, statistics_sha256
from query_cost_predictor.sqltext import literal_insensitive_fingerprint, normalized_sql_hash

SKIPPED_SNAPSHOT = "SKIPPED_SNAPSHOT_UNAVAILABLE"


class ManifestError(RuntimeError):
    """The manifest cannot be built safely."""


def manifest_dir() -> Path:
    return data_root() / "manifests" / "poster_smoke"


def expand_entries(cfg: PosterSmokeConfig, snapshot_ids: dict[str, str],
                   config_ids: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Expand templates × parameter sets × snapshots × configurations into manifest entries.

    ``snapshot_ids`` maps snapshot names to registered snapshot ids; a template
    snapshot missing from it is reported as skipped (never silently dropped).
    """
    entries: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for tpl in cfg.templates:
        for params in tpl.parameter_sets:
            sql_text = render_sql(tpl.sql, tpl.parameter_types, params)
            safety = validate_read_only_sql(sql_text)
            qi_id = query_instance_id(tpl.template_id, params, safety.sql)
            for snap_name in tpl.snapshots:
                spec = cfg.snapshots[snap_name]
                if snap_name not in snapshot_ids:
                    skipped.append({"template_id": tpl.template_id, "parameters": params, "snapshot": snap_name,
                                    "configurations": list(tpl.configurations), "reason": SKIPPED_SNAPSHOT})
                    continue
                for cfg_name in tpl.configurations:
                    key = modeling_key(qi_id, snapshot_ids[snap_name], config_ids[cfg_name],
                                       cfg.protocol.cache_protocol, cfg.protocol.protocol_version)
                    entries.append({
                        "modeling_key": key,
                        "query_instance_id": qi_id,
                        "template_id": tpl.template_id,
                        "semantic_group_id": tpl.semantic_group_id,
                        "family": tpl.family,
                        "form": tpl.form,
                        "rewrite_source": tpl.rewrite_source,
                        "rewrite_pair_id": tpl.rewrite_pair_id,
                        "workload": spec.workload,
                        "snapshot_name": snap_name,
                        "database": spec.database,
                        "scale_factor": spec.scale_factor,
                        "snapshot_id": snapshot_ids[snap_name],
                        "configuration_name": cfg_name,
                        "config_id": config_ids[cfg_name],
                        "session_settings": dict(cfg.configurations[cfg_name].session_settings),
                        "parameters": dict(params),
                        "sql_text": safety.sql,
                        "sql_sha256": sha256_text(safety.sql),
                        "normalized_sql_sha256": normalized_sql_hash(safety.sql),
                        "literal_fingerprint": literal_insensitive_fingerprint(safety.sql),
                        "coverage": list(tpl.coverage),
                        "expected_mechanism": tpl.expected_mechanism,
                    })
    return entries, skipped


def assign_execution_order(entries: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    """Deterministic randomized order: sort by modeling_key, then shuffle with ``seed``."""
    ordered = sorted(entries, key=lambda e: e["modeling_key"])
    random.Random(seed).shuffle(ordered)
    return [dict(entry, execution_order=position) for position, entry in enumerate(ordered, start=1)]


def check_entries(cfg: PosterSmokeConfig, entries: list[dict[str, Any]]) -> list[str]:
    """Return problems (empty list means the manifest may be registered)."""
    problems: list[str] = []
    keys = Counter(e["modeling_key"] for e in entries)
    duplicates = [k for k, n in keys.items() if n > 1]
    if duplicates:
        problems.append(f"duplicate modeling keys: {duplicates[:3]}")
    orders = Counter(e.get("execution_order") for e in entries)
    if any(n > 1 for n in orders.values()):
        problems.append("duplicate execution_order values")
    if not cfg.protocol.min_keys <= len(entries) <= cfg.protocol.max_keys:
        problems.append(f"{len(entries)} keys outside the allowed range {cfg.protocol.min_keys}-{cfg.protocol.max_keys}")
    covered = {tag for e in entries for tag in e["coverage"]}
    missing = [tag for tag in cfg.required_coverage if tag not in covered]
    if missing:
        problems.append(f"required coverage without any included key: {missing}")
    equivalent_pairs = {e["rewrite_pair_id"] for e in entries if e["form"] == "reference_rewrite"}
    if len(equivalent_pairs) < 2:
        problems.append("fewer than two reference rewrite pairs are included")
    return problems


def manifest_document(cfg: PosterSmokeConfig, entries: list[dict[str, Any]], skipped: list[dict[str, Any]],
                      snapshots: dict[str, Any], configurations: dict[str, Any]) -> dict[str, Any]:
    protocol = cfg.protocol
    return {
        "manifest_name": "poster-smoke",
        "manifest_kind": "poster_smoke",
        "note": "Poster smoke manifest. Not the final dataset. Expected mechanisms are hypotheses.",
        "protocol": {
            "protocol_version": protocol.protocol_version,
            "cache_protocol": protocol.cache_protocol,
            "statement_timeout_ms": protocol.statement_timeout_ms,
            "planned_final_timeout_ms": protocol.planned_final_timeout_ms,
            "lock_timeout_ms": protocol.lock_timeout_ms,
            "warmup_probes": protocol.warmup_probes,
            "measured_repetitions": protocol.measured_repetitions,
            "order_seed": protocol.order_seed,
            "watchdog_grace_ms": protocol.watchdog_grace_ms,
            "max_transient_retries": protocol.max_transient_retries,
            "snapshot_check_every_n_keys": protocol.snapshot_check_every_n_keys,
            "explain_estimate_options": protocol.explain_estimate_options,
            "explain_measured_options": protocol.explain_measured_options,
            "high_runtime_thresholds_ms": list(protocol.high_runtime_thresholds_ms),
            "min_keys": protocol.min_keys,
            "max_keys": protocol.max_keys,
        },
        "required_coverage": list(cfg.required_coverage),
        "config_file": {"path": "config/poster_smoke.yaml", "sha256": cfg.sha256},
        "snapshots": snapshots,
        "configurations": configurations,
        "counts": {
            "entries": len(entries),
            "semantic_groups": len({e["semantic_group_id"] for e in entries}),
            "templates": len({e["template_id"] for e in entries}),
            "query_instances": len({e["query_instance_id"] for e in entries}),
            "skipped_entries": len(skipped),
        },
        "entries": sorted(entries, key=lambda e: e["execution_order"]),
        "skipped_entries": skipped,
    }


def manifest_id_for(document: dict[str, Any]) -> str:
    return "mf_" + sha256_json(document)


def _read_image_record() -> tuple[str | None, str | None, str]:
    path = environment_reports_dir() / "postgres_image.json"
    if not path.is_file():
        return None, None, "NOT_CAPTURED"
    record = json.loads(path.read_text(encoding="utf-8-sig"))
    digests = record.get("repo_digests") or []
    return record.get("configured_image"), (digests[0] if digests else None), ("CAPTURED" if digests else "NOT_CAPTURED")


def _register(ev: Any, cfg: PosterSmokeConfig, entries: list[dict[str, Any]], document: dict[str, Any],
              manifest_id: str, file_sha: str) -> None:
    groups = {}
    for tpl in cfg.templates:
        groups.setdefault(tpl.semantic_group_id, tpl)
    for group_id, tpl in groups.items():
        ev.execute(
            "INSERT INTO qcp.semantic_group (semantic_group_id, workload, family, description) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (semantic_group_id) DO NOTHING",
            (group_id, "tpch", tpl.family, tpl.expected_mechanism),
        )
    for tpl in cfg.templates:
        ev.execute(
            "INSERT INTO qcp.query_definition (template_id, semantic_group_id, workload, family, form, rewrite_source, "
            "rewrite_pair_id, sql_template, template_sha256, parameter_types, expected_mechanism, coverage_tags, "
            "source_config) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (template_id) DO NOTHING",
            (tpl.template_id, tpl.semantic_group_id, "tpch", tpl.family, tpl.form, tpl.rewrite_source, tpl.rewrite_pair_id,
             tpl.sql, sha256_text(tpl.sql), Jsonb(tpl.parameter_types), tpl.expected_mechanism, Jsonb(list(tpl.coverage)),
             "config/poster_smoke.yaml"),
        )
    for e in entries:
        ev.execute(
            "INSERT INTO qcp.query_instance (query_instance_id, template_id, semantic_group_id, parameter_values, sql_text, "
            "sql_sha256, normalized_sql_sha256, literal_fingerprint, safety_status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'PASSED_READ_ONLY_SINGLE_STATEMENT') "
            "ON CONFLICT (query_instance_id) DO NOTHING",
            (e["query_instance_id"], e["template_id"], e["semantic_group_id"], Jsonb(e["parameters"]), e["sql_text"],
             e["sql_sha256"], e["normalized_sql_sha256"], e["literal_fingerprint"]),
        )
    protocol = cfg.protocol
    ev.execute(
        "INSERT INTO qcp.collection_manifest (manifest_id, manifest_name, protocol_version, cache_protocol, "
        "statement_timeout_ms, planned_final_timeout_ms, lock_timeout_ms, warmup_probes, measured_repetitions, "
        "order_seed, config_file_sha256, manifest_file_sha256, manifest_json) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (manifest_id) DO NOTHING",
        (manifest_id, "poster-smoke", protocol.protocol_version, protocol.cache_protocol, protocol.statement_timeout_ms,
         protocol.planned_final_timeout_ms, protocol.lock_timeout_ms, protocol.warmup_probes,
         protocol.measured_repetitions, protocol.order_seed, cfg.sha256, file_sha, Jsonb(document)),
    )
    for e in entries:
        ev.execute(
            "INSERT INTO qcp.modeling_key (modeling_key, query_instance_id, snapshot_id, config_id, cache_protocol, "
            "protocol_version, template_id, semantic_group_id, workload, scale_factor, configuration_name) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (modeling_key) DO NOTHING",
            (e["modeling_key"], e["query_instance_id"], e["snapshot_id"], e["config_id"], protocol.cache_protocol,
             protocol.protocol_version, e["template_id"], e["semantic_group_id"], e["workload"], e["scale_factor"],
             e["configuration_name"]),
        )
        ev.execute(
            "INSERT INTO qcp.manifest_entry (manifest_id, modeling_key, execution_order) VALUES (%s, %s, %s) "
            "ON CONFLICT (manifest_id, modeling_key) DO NOTHING",
            (manifest_id, e["modeling_key"], e["execution_order"]),
        )


def build_and_register_manifest(config_path: Path | None = None) -> dict[str, Any]:
    cfg = load_poster_smoke_config(config_path)
    image, digest, image_status = _read_image_record()
    snapshot_ids: dict[str, str] = {}
    snapshots_info: dict[str, Any] = {}
    unavailable: dict[str, str] = {}
    with connect_evidence_writer("qcp:smoke-manifest") as ev:
        for name, spec in cfg.snapshots.items():
            snap = latest_snapshot(ev, spec.database)
            if snap is None:
                if spec.required:
                    raise ManifestError(f"required snapshot {name} is not registered; run 03b_load_tpch.ps1 -ScaleFactor {spec.scale_factor}")
                unavailable[name] = "not registered"
                continue
            with connect_bench_reader(spec.database, "qcp:smoke-manifest") as conn:
                live = statistics_sha256(conn)
            if live != snap["statistics_sha256"]:
                raise ManifestError(f"snapshot drift in {spec.database}: planner statistics changed since registration; "
                                    "re-register the snapshot before building a manifest")
            snapshot_ids[name] = snap["snapshot_id"]
            snapshots_info[name] = {**snap, "scale_factor": spec.scale_factor, "database": spec.database}

        base_db = next(s.database for s in cfg.snapshots.values() if s.required)
        config_ids: dict[str, str] = {}
        configurations_info: dict[str, Any] = {}
        with connect_bench_reader(base_db, "qcp:smoke-manifest") as conn:
            server = server_identity(conn)
            for name, spec in cfg.configurations.items():
                effective = capture_effective_settings(conn, spec.session_settings, cfg.protocol.lock_timeout_ms)
                effective_sha = sha256_json(effective)
                cid = config_id(name, effective_sha, server["server_version_num"])
                ev.execute(
                    "INSERT INTO qcp.database_configuration (config_id, configuration_name, session_settings, "
                    "effective_settings, effective_settings_sha256, server_version, server_version_num, postgres_image, "
                    "postgres_image_digest, image_capture_status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (config_id) DO NOTHING",
                    (cid, name, Jsonb(spec.session_settings), Jsonb(effective), effective_sha, server["server_version"],
                     server["server_version_num"], image, digest, image_status),
                )
                config_ids[name] = cid
                configurations_info[name] = {"config_id": cid, "session_settings": spec.session_settings,
                                             "effective_settings_sha256": effective_sha,
                                             "captured_on_database": base_db,
                                             "work_mem": effective.get("work_mem"), "image_capture_status": image_status}

        entries, skipped = expand_entries(cfg, snapshot_ids, config_ids)
        entries = assign_execution_order(entries, cfg.protocol.order_seed)
        problems = check_entries(cfg, entries)
        if problems:
            raise ManifestError("; ".join(problems))
        document = manifest_document(cfg, entries, skipped, snapshots_info, configurations_info)
        mid = manifest_id_for(document)

        folder = ensure_dir(manifest_dir())
        path = folder / f"manifest_{mid[3:19]}.json"
        created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("manifest_id") != mid:
                raise ManifestError(f"{path} exists with a different manifest id; refusing to overwrite")
            status_note = "manifest file already existed with identical content"
        else:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps({"manifest_id": mid, "created_at_utc": created, **document}, indent=2, sort_keys=True))
            status_note = "manifest file written"
        file_sha = sha256_file(path)
        _register(ev, cfg, entries, document, mid, file_sha)
        ev.commit()
    (folder / "CURRENT_MANIFEST.txt").write_text(mid + "\n", encoding="utf-8")

    by_family = Counter(e["family"] for e in entries)
    return {
        "status": "PASS",
        "manifest_id": mid,
        "manifest_path": str(path),
        "summary": {
            "manifest_id": mid,
            "manifest_file": str(path),
            "note": status_note,
            "keys": len(entries),
            "semantic_groups": len({e["semantic_group_id"] for e in entries}),
            "templates": len({e["template_id"] for e in entries}),
            "by_family": dict(sorted(by_family.items())),
            "by_configuration": dict(Counter(e["configuration_name"] for e in entries)),
            "by_snapshot": dict(Counter(e["snapshot_name"] for e in entries)),
            "skipped_entries": len(skipped),
            "unavailable_snapshots": unavailable,
            "image_capture_status": image_status,
            "next_step": "Inspect every sql_text in the manifest file, then run 05_run_poster_smoke.ps1 -Step Collect",
        },
    }


def current_manifest_id(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    pointer = manifest_dir() / "CURRENT_MANIFEST.txt"
    if not pointer.is_file():
        raise ManifestError("no manifest yet; run 05_run_poster_smoke.ps1 -Step Manifest")
    return pointer.read_text(encoding="utf-8").strip()


def load_manifest(manifest_id: str) -> tuple[dict[str, Any], Path]:
    path = manifest_dir() / f"manifest_{manifest_id[3:19]}.json"
    if not path.is_file():
        raise ManifestError(f"manifest file not found: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    body = {k: v for k, v in document.items() if k not in ("manifest_id", "created_at_utc")}
    if document.get("manifest_id") != manifest_id or manifest_id_for(body) != manifest_id:
        raise ManifestError(f"manifest file {path} does not hash to {manifest_id} (edited?)")
    return document, path
