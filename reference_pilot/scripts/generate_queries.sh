#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$project_dir/scripts/generate_queries.py" "${1:-10}" "${2:-0.1}"
