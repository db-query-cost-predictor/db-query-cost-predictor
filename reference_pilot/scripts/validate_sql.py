#!/usr/bin/env python3
"""Check all query plans without execution before collection."""
import json
from pathlib import Path
import psycopg
from collect import normalize_sql

out = Path('data/output')
out.mkdir(parents=True, exist_ok=True)
results = []
with psycopg.connect('postgresql://tpch:tpch@localhost:5432/tpch', autocommit=True) as conn:
    conn.execute('SET default_transaction_read_only = on')
    for path in sorted(Path('data/queries').glob('*.sql')):
        try:
            conn.execute('EXPLAIN (FORMAT JSON) ' + normalize_sql(path.read_text())).fetchone()
            results.append({'query_file': path.name, 'valid': True})
        except Exception as exc:
            results.append({'query_file': path.name, 'valid': False, 'error': str(exc)})
(out / 'sql_validation.json').write_text(json.dumps(results, indent=2))
failures = [r for r in results if not r['valid']]
print(json.dumps({'queries': len(results), 'failures': failures}, indent=2))
raise SystemExit(bool(failures) or len(results) != 220)
