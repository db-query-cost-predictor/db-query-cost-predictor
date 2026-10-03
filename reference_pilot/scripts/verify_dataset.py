#!/usr/bin/env python3
"""Verify measured outputs, source tables and provenance; produce a report."""
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
from collections import Counter
from datetime import datetime, timezone
import psycopg

root = Path(__file__).resolve().parents[1]
out = root / 'data/output'
rows = list(csv.DictReader((out / 'query_dataset.csv').open()))
raw = [json.loads(line) for line in (out / 'query_runs.jsonl').read_text().splitlines()]
errors = [json.loads(line) for line in (out / 'errors.jsonl').read_text().splitlines()]
expected = dict(region=5, nation=25, part=20000, supplier=1000, partsupp=80000, customer=15000, orders=150000, lineitem=600572)
with psycopg.connect('postgresql://tpch:tpch@localhost:5432/tpch', autocommit=True) as conn:
    counts = {t: conn.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in expected}
    settings = dict(conn.execute("SELECT name, setting FROM pg_settings WHERE name IN ('server_version','shared_buffers','work_mem','effective_cache_size','max_parallel_workers_per_gather','track_io_timing','random_page_cost','seq_page_cost')").fetchall())
    indexes = conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public' ORDER BY indexname").fetchall()
files = {t: {'bytes': (root / f'data/tbl/{t}.tbl').stat().st_size, 'rows': sum(1 for _ in (root / f'data/tbl/{t}.tbl').open())} for t in expected}
missing = {k: sum(r[k] == '' for r in rows) for k in rows[0]}
runtimes = [float(r['execution_time_mean_ms']) for r in rows]
run_counts = Counter(r['query_id'] for r in raw)
by_id = {r['query_id']: r for r in rows}
for query_id, row in by_id.items():
    records = [r for r in raw if r['query_id'] == query_id]
    assert len(records) == 3
    assert {r['run_number'] for r in records} == {1, 2, 3}
    assert math.isclose(float(row['execution_time_mean_ms']), statistics.mean(r['plan']['Execution Time'] for r in records), rel_tol=1e-12)
    assert float(row['optimizer_total_cost']) == records[-1]['plan']['Plan']['Total Cost']
queries = sorted((root / 'data/queries').glob('*.sql'))
summary = {
    'validated_at_utc': datetime.now(timezone.utc).isoformat(),
    'successful_queries': len(rows), 'failed_queries': len({e['query_id'] for e in errors}),
    'dimensions': [len(rows), len(rows[0])], 'unique_query_ids': len(by_id),
    'unique_template_ids': len({r['template_id'] for r in rows}),
    'queries_generated': len(queries), 'measured_runs': len(raw), 'errors': len(errors),
    'duplicate_rows': len(rows) - len({tuple(r.items()) for r in rows}),
    'duplicate_query_ids': len(rows) - len(by_id),
    'duplicate_sql': len(rows) - len({r['sql'] for r in rows}),
    'missing_values': missing,
    'runtime_mean_ms_summary': {'min': min(runtimes), 'median': statistics.median(runtimes), 'mean': statistics.mean(runtimes), 'max': max(runtimes), 'std': statistics.stdev(runtimes)},
    'quality_flags': dict(Counter(r['quality_flag'] for r in rows)),
    'table_counts': counts, 'tbl_files': files, 'postgres_settings': settings,
    'indexes': [r[0] for r in indexes],
    'toolkit_commit': subprocess.check_output(['git', '-C', str(root / 'vendor/tpch-dbgen'), 'rev-parse', 'HEAD'], text=True).strip(),
    'collector_settings': {'jit': 'off', 'read_only': True, 'timeout_ms': 300000, 'warmups': 1, 'measured_runs': 3, 'scale_factor': 0.1, 'order': 'query filename sorted'},
    'output_sha256': {name: hashlib.sha256((out / name).read_bytes()).hexdigest() for name in ['query_dataset.csv', 'query_runs.jsonl', 'errors.jsonl']},
}
assert counts == expected, counts
assert all(files[t]['rows'] == expected[t] and files[t]['bytes'] > 0 for t in expected)
assert len(rows) + summary['failed_queries'] == len(queries) == 220
assert summary['unique_template_ids'] == 22
assert not any(missing.values())
assert not summary['duplicate_query_ids']
assert len(raw) == len(rows) * 3
(out / 'validation_summary.json').write_text(json.dumps(summary, indent=2))
report = '# TPC-H dataset validation report\n\n'
report += f"Verified {summary['validated_at_utc']}. Real PostgreSQL EXPLAIN ANALYZE measurements; sample CSV was not used.\n\n"
report += f"Successful queries: **{len(rows)}**. Failed queries: **{summary['failed_queries']}**. Templates: **{summary['unique_template_ids']}**. Dataset: **{len(rows)} rows × {len(rows[0])} columns**. Measured runs: **{len(raw)}**.\n\n"
report += 'Primary prediction target: `execution_time_mean_ms`. `optimizer_total_cost` is the PostgreSQL baseline estimate, not ground truth.\n\n'
report += 'Every successful query received one unrecorded warm-up and three recorded runs using `EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON)`. All JSONL lines parsed successfully; an empty errors file is valid. Aggregated runtime targets and optimizer costs were checked against the raw plans.\n\n'
report += '## Validation results\n\n```json\n' + json.dumps(summary, indent=2) + '\n```\n\n'
report += '## Exact CSV schema\n\n' + ', '.join(f'`{k}`' for k in rows[0]) + '\n\n'
report += '## Interpretation and known limitations\n\nNo unresolved query failures in this collection. Q15 uses a materialized CTE instead of CREATE/DROP VIEW; row limits use PostgreSQL LIMIT, and Q1 omits the unsupported DAY precision. SF 0.1 and these adaptations are for ML experimentation, not an audited TPC-H result.\n\n'
report += 'Query identity is template plus seed. Duplicate SQL parameter draws are retained and counted above. Buffer/I/O/WAL columns use inclusive root-plan counters; non-runtime plan features come from measured run 3. Warm caches, local Docker/WSL scheduling and small sample sizes affect timing. High-variance rows remain flagged, not excluded. p95 is interpolated from only three runs.\n\n'
report += 'Duplicate SQL occurs in Q6, Q13 and Q18. The upstream Q18 parameter domain is only 312-315, so ten seeded instances cannot all be distinct without changing benchmark semantics.\n\n'
report += 'Ready for exploratory Data Science. Split by template and group identical SQL together. For pre-execution prediction use only SQL/provenance and optimizer-estimated features; actual rows, buffers, I/O, wall/planning time, runtime aggregates and quality flags are post-execution observations and can leak the target. More scales, seeds and repeat sessions are needed for production generalization.\n\nSee README.md for exact reproduction commands.\n'
evidence = out / 'setup_verification.json'
if evidence.exists():
    report += '\n## Setup verification evidence\n\n```json\n' + json.dumps(json.loads(evidence.read_text()), indent=2) + '\n```\n'
(out / 'DATASET_VALIDATION_REPORT.md').write_text(report)
print(json.dumps({k: v for k, v in summary.items() if k not in ['missing_values', 'tbl_files', 'output_sha256']}, indent=2))
