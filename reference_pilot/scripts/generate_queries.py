#!/usr/bin/env python3
"""Generate PostgreSQL-compatible SQL from the existing Oracle-configured qgen."""
import argparse
import os
from pathlib import Path
import re
import subprocess

p = argparse.ArgumentParser()
p.add_argument('variations', type=int)
p.add_argument('scale_factor')
a = p.parse_args()
root = Path(__file__).resolve().parents[1]
toolkit = root / 'vendor/tpch-dbgen'
out = root / 'data/queries'
out.mkdir(parents=True, exist_ok=True)
for seed in range(1, a.variations + 1):
    for template in range(1, 23):
        sql = subprocess.check_output([str(toolkit / 'qgen'), '-c', '-s', a.scale_factor, '-r', str(seed), str(template)], cwd=toolkit, env=os.environ | {'DSS_CONFIG': str(toolkit), 'DSS_QUERY': str(toolkit / 'queries')}, text=True)
        sql = re.sub(r'where rownum <= -1;', '', sql, flags=re.I)
        sql = re.sub(r';\s*where rownum <= (\d+);', r'\nLIMIT \1;', sql, flags=re.I)
        sql = re.sub(r'\bday\s*\(3\)', 'day', sql, flags=re.I)
        if template == 15:
            match = re.search(r'create view (\w+)\s*(\([^;]+?)\s+as\s+(.*?);\s*(select\s+.*?);\s*drop view \1;', sql, re.I | re.S)
            if not match:
                raise ValueError('Unexpected Q15 shape')
            sql = f'WITH {match[1]} {match[2]} AS MATERIALIZED (\n{match[3]}\n)\n{match[4]};\n'
        (out / f'q{template:02d}_s{seed:04d}.sql').write_text(sql)
print(f'Generated {a.variations * 22} SQL files')
