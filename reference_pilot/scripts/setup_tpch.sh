#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
scale_factor="${1:-0.1}"
toolkit_dir="$project_dir/vendor/tpch-dbgen"
data_dir="$project_dir/data/tbl"
docker compose up -d
if [[ ! -d "$toolkit_dir" ]]; then
  git clone --depth 1 https://github.com/electrum/tpch-dbgen.git "$toolkit_dir"
fi
make -C "$toolkit_dir"
mkdir -p "$data_dir"
tables=(region nation part supplier partsupp customer orders lineitem)
if [[ -f "$data_dir/scale_factor" && "$(cat "$data_dir/scale_factor")" != "$scale_factor" ]]; then
  echo 'Existing data has a different scale factor; refusing to overwrite.' >&2; exit 1
fi
complete=true
for table in "${tables[@]}"; do [[ -s "$data_dir/$table.tbl" ]] || complete=false; done
if [[ "$complete" == false ]]; then
  stage="$(mktemp -d "$project_dir/data/tbl-stage.XXXXXX")"
  (cd "$toolkit_dir" && DSS_CONFIG="$toolkit_dir" DSS_PATH="$stage" ./dbgen -s "$scale_factor")
  for table in "${tables[@]}"; do
    test -s "$stage/$table.tbl"
    sed 's/|$//' "$stage/$table.tbl" > "$data_dir/$table.tbl"
  done
fi
printf '%s\n' "$scale_factor" > "$data_dir/scale_factor"
ready=false
for attempt in $(seq 1 60); do
  if docker compose exec -T postgres pg_isready -U tpch -d tpch >/dev/null; then ready=true; break; fi
  sleep 1
 done
[[ "$ready" == true ]]
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U tpch -d tpch < sql/schema.sql
for table in "${tables[@]}"; do
  expected="$(wc -l < "$data_dir/$table.tbl")"
  actual="$(docker compose exec -T postgres psql -At -U tpch -d tpch -c "SELECT count(*) FROM $table")"
  if [[ "$actual" == 0 ]]; then
    sed 's/|$//' "$data_dir/$table.tbl" | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U tpch -d tpch -c "COPY $table FROM STDIN WITH (FORMAT csv, DELIMITER '|')"
  elif [[ "$actual" != "$expected" ]]; then
    echo "$table has $actual rows, expected $expected; refusing to replace existing data." >&2; exit 1
  fi
  echo "$table: $expected rows"
done
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U tpch -d tpch -c 'ANALYZE; CREATE EXTENSION IF NOT EXISTS pg_stat_statements;'
echo "TPC-H SF=$scale_factor ready."
