#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 0 ]; then set -- psql; fi
client=("$@")
psql_query() { "${client[@]}" -XAtq -v ON_ERROR_STOP=1 "$@"; }

schema=$(psql_query -c "SELECT 'ready_fixture_' || replace(gen_random_uuid()::text, '-', '')")
[[ "$schema" =~ ^ready_fixture_[a-f0-9]{32}$ ]]
output=$(mktemp -d)
cleanup() {
  psql_query -c "DROP SCHEMA IF EXISTS $schema CASCADE" >/dev/null
  rm -f "$output"/claim-*
  rmdir "$output"
}
trap cleanup EXIT
psql_query -c "CREATE SCHEMA $schema"
{
  printf 'SET search_path TO %s, public;\n' "$schema"
  cat "$(dirname "$0")/../init-db/02-ready-dispatch.sql"
} | psql_query

workers=()
for i in {1..8}; do
  psql_query -c "SET search_path TO $schema, public; SELECT claim_ready_dispatch(101);" >"$output/claim-$i" &
  workers+=("$!")
done
for worker in "${workers[@]}"; do wait "$worker"; done

winners=$(awk 'NF { count++ } END { print count+0 }' "$output"/claim-*)
if [ "$winners" -ne 1 ]; then
  echo "Expected one winner across eight Postgres connections; got $winners" >&2
  exit 1
fi
echo 'Ready dispatch concurrency: exactly one claim'
