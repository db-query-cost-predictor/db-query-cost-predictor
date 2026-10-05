#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/validate_sql.py
.venv/bin/python -u scripts/collect.py --scale-factor 0.1 --warmups 1 --runs 3
