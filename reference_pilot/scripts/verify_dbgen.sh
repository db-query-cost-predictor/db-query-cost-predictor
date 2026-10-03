#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
project_dir="$PWD"
stage="$(mktemp -d /tmp/tpch-dbgen-check.XXXXXX)"
(cd vendor/tpch-dbgen && DSS_CONFIG="$PWD" DSS_PATH="$stage" ./dbgen -s 0.1)
for table in region nation part supplier partsupp customer orders lineitem; do
  test -s "$stage/$table.tbl"
  cmp <(sed 's/|$//' "$stage/$table.tbl") <(sed 's/|$//' "$project_dir/data/tbl/$table.tbl")
  echo "$table: regenerated SF 0.1 matches loaded source"
done
printf 'Fresh generated files retained at %s\n' "$stage"
