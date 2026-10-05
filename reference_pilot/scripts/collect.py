#!/usr/bin/env python3
"""Collect repeated PostgreSQL EXPLAIN ANALYZE measurements into JSONL and CSV."""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import statistics
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg


def walk(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [node] + [item for child in node.get("Plans", []) for item in walk(child)]


def sum_field(nodes: list[dict[str, Any]], name: str) -> float:
    return float(sum(float(node.get(name, 0) or 0) for node in nodes))


def plan_features(doc: dict[str, Any]) -> dict[str, Any]:
    root = doc["Plan"]
    nodes = walk(root)
    node_types = [str(n.get("Node Type", "")) for n in nodes]
    return {
        "optimizer_total_cost": root.get("Total Cost"),
        "optimizer_startup_cost": root.get("Startup Cost"),
        "plan_rows": root.get("Plan Rows"),
        "actual_rows": root.get("Actual Rows"),
        "planning_time_ms": doc.get("Planning Time"),
        "execution_time_ms": doc.get("Execution Time"),
        "plan_node_count": len(nodes),
        "join_count": sum("Join" in t or t == "Nested Loop" for t in node_types),
        "scan_count": sum("Scan" in t for t in node_types),
        "index_scan_count": sum("Index" in t and "Scan" in t for t in node_types),
        "sort_count": node_types.count("Sort"),
        "aggregate_count": sum("Aggregate" in t for t in node_types),
        "max_plan_depth": max_depth(root),
        "shared_hit_blocks": sum_field([root], "Shared Hit Blocks"),
        "shared_read_blocks": sum_field([root], "Shared Read Blocks"),
        "shared_dirtied_blocks": sum_field([root], "Shared Dirtied Blocks"),
        "temp_read_blocks": sum_field([root], "Temp Read Blocks"),
        "temp_written_blocks": sum_field([root], "Temp Written Blocks"),
        "io_read_time_ms": sum_field([root], "I/O Read Time"),
        "io_write_time_ms": sum_field([root], "I/O Write Time"),
        "wal_records": sum_field([root], "WAL Records"),
    }


def max_depth(node: dict[str, Any]) -> int:
    children = node.get("Plans", [])
    return 1 if not children else 1 + max(max_depth(c) for c in children)


def normalize_sql(sql: str) -> str:
    sql = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
    return sql.strip().rstrip(";")


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    pos = (len(ordered) - 1) * p
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", default="data/queries")
    ap.add_argument("--output", default="data/output")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--warmups", type=int, default=1)
    ap.add_argument("--timeout-ms", type=int, default=300_000)
    ap.add_argument("--scale-factor", type=float, default=0.1)
    ap.add_argument("--dsn", default=os.getenv("DATABASE_URL", "postgresql://tpch:tpch@localhost:5432/tpch"))
    args = ap.parse_args()
    if args.runs < 1 or args.warmups < 0:
        ap.error("--runs must be >= 1 and --warmups >= 0")

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    raw_path, csv_path, error_path = out / "query_runs.jsonl", out / "query_dataset.csv", out / "errors.jsonl"
    rows: list[dict[str, Any]] = []
    failures = 0
    existing = [p for p in (raw_path, csv_path, error_path) if p.exists()]
    if existing:
        archive = out / "archive" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive.mkdir(parents=True)
        for path in existing:
            shutil.copy2(path, archive / path.name)

    with psycopg.connect(args.dsn, autocommit=True) as conn, raw_path.open("w", encoding="utf-8") as raw, error_path.open("w", encoding="utf-8") as errors:
        conn.execute("SET jit = off")
        conn.execute("SET default_transaction_read_only = on")
        conn.execute(f"SET statement_timeout = {args.timeout_ms}")
        pg_version = conn.execute("SHOW server_version").fetchone()[0]
        for path in sorted(Path(args.queries).glob("*.sql")):
            sql = normalize_sql(path.read_text(encoding="utf-8"))
            match = re.match(r"q(\d+)_s(\d+)", path.stem)
            query_id = path.stem
            docs: list[dict[str, Any]] = []
            run_idx = 0
            try:
                if not sql:
                    raise ValueError("Empty SQL file")
                for run_idx in range(args.warmups + args.runs):
                    started = time.perf_counter()
                    result = conn.execute("EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON) " + sql).fetchone()[0]
                    wall_ms = (time.perf_counter() - started) * 1000
                    doc = result[0] if isinstance(result, list) else result
                    if run_idx >= args.warmups:
                        record = {
                            "query_id": query_id, "query_file": path.name,
                            "template_id": int(match.group(1)) if match else None,
                            "seed": int(match.group(2)) if match else None,
                            "run_number": run_idx - args.warmups + 1,
                            "sql": sql, "wall_time_ms": wall_ms, "plan": doc,
                        }
                        raw.write(json.dumps(record, separators=(",", ":")) + "\n")
                        docs.append(record)
                features = [plan_features(r["plan"]) | {"wall_time_ms": r["wall_time_ms"]} for r in docs]
                execution = [float(f["execution_time_ms"]) for f in features]
                base = features[-1]
                row = {
                    "query_id": query_id, "query_file": path.name,
                    "template_id": int(match.group(1)) if match else None,
                    "seed": int(match.group(2)) if match else None,
                    "scale_factor": args.scale_factor, "successful_runs": len(features),
                    "sql": sql,
                    "execution_time_mean_ms": statistics.fmean(execution),
                    "execution_time_median_ms": statistics.median(execution),
                    "execution_time_std_ms": statistics.stdev(execution) if len(execution) > 1 else 0.0,
                    "execution_time_p95_ms": percentile(execution, 0.95),
                    **{k: v for k, v in base.items() if k != "execution_time_ms"},
                    "postgres_version": pg_version, "host_os": platform.platform(),
                    "collected_at_utc": datetime.now(timezone.utc).isoformat(),
                    "quality_flag": "high_variance" if len(execution) > 1 and statistics.stdev(execution) / max(statistics.fmean(execution), 0.001) > 0.20 else "ok",
                }
                rows.append(row)
                print(f"OK {path.name}: mean={row['execution_time_mean_ms']:.2f} ms")
            except Exception as exc:
                failures += 1
                conn.rollback()
                errors.write(json.dumps({"query_file": path.name, "query_id": query_id, "template_id": int(match.group(1)) if match else None, "phase": "warmup" if run_idx < args.warmups else "measured", "completed_measured_runs": len(docs), "error": str(exc), "collected_at_utc": datetime.now(timezone.utc).isoformat()}) + "\n")
                print(f"ERROR {path.name}: {exc}")

    if not rows:
        raise SystemExit("No successful measurements; inspect data/output/errors.jsonl")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} query-level rows to {csv_path}")
    if failures:
        raise SystemExit(f"{failures} queries failed; inspect {error_path}")


if __name__ == "__main__":
    main()
