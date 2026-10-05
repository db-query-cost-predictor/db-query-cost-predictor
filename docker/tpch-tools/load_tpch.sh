#!/usr/bin/env bash
# Generate TPC-H data with the pinned dbgen and load it into bench-db.
#
# Usage (inside the tpch-tools container):  load_tpch.sh <scale factor: 0.1 | 1>
#
# Safety rules:
#  * refuses to touch a database that already has TPC-H tables unless a previous
#    load of this script completed (then it only re-verifies counts);
#  * never drops or truncates anything;
#  * records per-table row counts and SHA-256 of the loaded (delimiter-normalized)
#    files in qcp_meta.load_manifest for the snapshot identity.
set -euo pipefail

sf="${1:?usage: load_tpch.sh <0.1|1>}"
case "$sf" in
  0.1) db="tpch_sf0_1" ;;
  1)   db="tpch_sf1" ;;
  *) echo "TPCH-LOADER: FAIL unsupported scale factor '$sf' (use 0.1 or 1)" >&2; exit 2 ;;
esac

tables=(region nation part supplier partsupp customer orders lineitem)
sql_dir="${QCP_SQL_DIR:?QCP_SQL_DIR not set}"
dbgen_dir="${QCP_DBGEN_DIR:?QCP_DBGEN_DIR not set}"
out_dir="/data/tbl/sf_${sf}"
stage_dir="/data/tbl/sf_${sf}.staging"
psql_db=(psql -X -q -v ON_ERROR_STOP=1 -d "$db")

echo "[load_tpch] database=$db host=$PGHOST user=$PGUSER scale_factor=$sf"
"${psql_db[@]}" -At -c "SELECT 'connected as ' || current_user" >/dev/null

has_meta="$("${psql_db[@]}" -At -c "SELECT to_regclass('qcp_meta.load_state') IS NOT NULL")"
state="none"
if [[ "$has_meta" == "t" ]]; then
  state="$("${psql_db[@]}" -At -c "SELECT coalesce(max(state), 'none') FROM qcp_meta.load_state WHERE scale_factor = '$sf'")"
fi

verify_counts() {
  local failures=0
  while IFS=$'\t' read -r table rows; do
    actual="$("${psql_db[@]}" -At -c "SELECT count(*) FROM public.$table")"
    echo "  $table: manifest=$rows loaded=$actual"
    if [[ "$rows" != "$actual" ]]; then failures=$((failures + 1)); fi
  done < <("${psql_db[@]}" -At -F $'\t' -c "SELECT table_name, row_count FROM qcp_meta.load_manifest ORDER BY table_name")
  return "$failures"
}

if [[ "$state" == "complete" ]]; then
  echo "[load_tpch] SF $sf already loaded by this script; verifying counts only."
  if verify_counts; then
    echo "TPCH-LOADER: PASS (already loaded)"
    exit 0
  fi
  echo "TPCH-LOADER: FAIL row counts differ from the recorded load manifest" >&2
  exit 5
fi
if [[ "$state" != "none" ]]; then
  echo "TPCH-LOADER: FAIL $db has load state '$state' (partial load). Refusing to overwrite; inspect and drop the database yourself if you decide to reload." >&2
  exit 3
fi
existing="$("${psql_db[@]}" -At -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('region','nation','part','supplier','partsupp','customer','orders','lineitem')")"
if [[ "$existing" != "0" ]]; then
  echo "TPCH-LOADER: FAIL $db already contains TPC-H tables without a completed load record. Refusing to overwrite." >&2
  exit 3
fi

echo "[load_tpch] generating SF $sf with dbgen $(git -C "$dbgen_dir" rev-parse HEAD)"
rm -rf "$stage_dir" "$out_dir"
mkdir -p "$stage_dir" "$out_dir"
(cd "$dbgen_dir" && DSS_CONFIG="$dbgen_dir" DSS_PATH="$stage_dir" ./dbgen -f -s "$sf")

: > "$out_dir/load_manifest.tsv"
for table in "${tables[@]}"; do
  if [[ ! -s "$stage_dir/$table.tbl" ]]; then
    echo "TPCH-LOADER: FAIL dbgen did not produce $table.tbl" >&2
    exit 4
  fi
  sed 's/|$//' "$stage_dir/$table.tbl" > "$out_dir/$table.tbl"
  rows="$(wc -l < "$out_dir/$table.tbl" | tr -d ' ')"
  sha="$(sha256sum "$out_dir/$table.tbl" | cut -d' ' -f1)"
  printf '%s\t%s\t%s\n' "$table" "$rows" "$sha" >> "$out_dir/load_manifest.tsv"
done
rm -rf "$stage_dir"

echo "[load_tpch] creating tables (no constraints yet)"
"${psql_db[@]}" -f "$sql_dir/01_tables.sql"
"${psql_db[@]}" -c "INSERT INTO qcp_meta.load_state (scale_factor, state) VALUES ('$sf', 'loading')"

for table in "${tables[@]}"; do
  echo "[load_tpch] COPY $table"
  "${psql_db[@]}" -c "\\copy public.$table FROM '$out_dir/$table.tbl' WITH (FORMAT csv, DELIMITER '|')"
done

echo "[load_tpch] adding keys and the baseline index manifest"
"${psql_db[@]}" -f "$sql_dir/02_constraints_indexes.sql"
echo "[load_tpch] VACUUM (FREEZE, ANALYZE), disabling autovacuum, granting SELECT"
"${psql_db[@]}" -f "$sql_dir/03_post_load.sql"

while IFS=$'\t' read -r table rows sha; do
  "${psql_db[@]}" -c "INSERT INTO qcp_meta.load_manifest (table_name, row_count, sha256, scale_factor, dbgen_repo, dbgen_commit) VALUES ('$table', $rows, '$sha', '$sf', '${QCP_TPCH_DBGEN_REPO:-unknown}', '$(git -C "$dbgen_dir" rev-parse HEAD)')"
done < "$out_dir/load_manifest.tsv"

echo "[load_tpch] verifying row counts"
if ! verify_counts; then
  echo "TPCH-LOADER: FAIL loaded row counts differ from the generated files" >&2
  exit 5
fi
"${psql_db[@]}" -c "UPDATE qcp_meta.load_state SET state = 'complete', updated_at = now() WHERE scale_factor = '$sf'"
echo "TPCH-LOADER: PASS (loaded SF $sf into $db)"
