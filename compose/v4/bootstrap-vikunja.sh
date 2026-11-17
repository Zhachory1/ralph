#!/usr/bin/env bash
# Creates the "ralph" Vikunja project and its 8 kanban columns (spec Sec 5),
# idempotently, against a running compose/v4 stack.
#
#   VIKUNJA_URL=http://127.0.0.1:3456 VIKUNJA_USERNAME=ralph \
#     VIKUNJA_PASSWORD=... ./bootstrap-vikunja.sh
#
# Safe to re-run: a project or bucket with the same title already present is
# left alone, never duplicated.
set -euo pipefail

export VIKUNJA_URL="${VIKUNJA_URL:-http://127.0.0.1:3456}"
export VIKUNJA_USERNAME="${VIKUNJA_USERNAME:?set VIKUNJA_USERNAME}"
export VIKUNJA_PASSWORD="${VIKUNJA_PASSWORD:?set VIKUNJA_PASSWORD}"
export PROJECT_TITLE="${VIKUNJA_PROJECT_TITLE:-ralph}"

# Columns in spec Sec 5's exact order.
COLUMNS=("Triage" "RFC Author" "Council Review" "Backlog" "Ready" "Peer Review" "Director PR Merge" "Done")

# Every value an operator supplies (title, credentials) crosses into a Python
# string via an environment variable, never string-interpolated into source:
# a quote in a title or password must not corrupt the generated code or JSON.
TOKEN=$(curl -sf -X POST "$VIKUNJA_URL/api/v1/login" \
  -H "Content-Type: application/json" \
  -d "$(python3 -c 'import os,json; print(json.dumps({"username": os.environ["VIKUNJA_USERNAME"], "password": os.environ["VIKUNJA_PASSWORD"]}))')" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
export TOKEN

auth() { curl -sf -H "Authorization: Bearer $TOKEN" "$@"; }

PROJECT_ID=$(auth "$VIKUNJA_URL/api/v1/projects" \
  | python3 -c "import os,sys,json; print(next((p['id'] for p in json.load(sys.stdin) if p['title']==os.environ['PROJECT_TITLE']), ''))")

NEW_PROJECT=0
if [ -z "$PROJECT_ID" ]; then
  NEW_PROJECT=1
  CREATE_RESPONSE=$(auth -X PUT "$VIKUNJA_URL/api/v1/projects" -H "Content-Type: application/json" \
    -d "$(python3 -c 'import os,json; print(json.dumps({"title": os.environ["PROJECT_TITLE"], "description": "v4 PR-review pipeline (spec Sec 5)"}))')")
  PROJECT_ID=$(echo "$CREATE_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
  echo "created project $PROJECT_TITLE (id $PROJECT_ID)"
else
  echo "project $PROJECT_TITLE already exists (id $PROJECT_ID)"
fi

VIEW_ID=$(auth "$VIKUNJA_URL/api/v1/projects/$PROJECT_ID/views" \
  | python3 -c "
import sys, json
view = next((v for v in json.load(sys.stdin) if v['view_kind'] == 'kanban'), None)
if view is None:
    sys.exit('error: project has no kanban view')
print(view['id'])
")

# On a fresh project, Vikunja's own default bucket is the only bucket that
# exists yet, and the API refuses to delete a view's last bucket -- so it is
# recorded here and removed only after our 8 named buckets exist below, not
# before. On an already-provisioned project this is simply empty.
DEFAULT_BUCKET_ID=""
if [ "$NEW_PROJECT" -eq 1 ]; then
  DEFAULT_BUCKET_ID=$(auth "$VIKUNJA_URL/api/v1/projects/$PROJECT_ID/views/$VIEW_ID/buckets" \
    | python3 -c "import sys,json; b=json.load(sys.stdin); print(b[0]['id'] if b else '')")
fi

# Excludes the default bucket (if this is a fresh project): it is deleted
# below, so a title match against it must not count as "already exists" --
# otherwise a default bucket titled "Backlog" would make the loop skip
# creating our own "Backlog" column, then delete the only one that existed.
EXISTING=$(auth "$VIKUNJA_URL/api/v1/projects/$PROJECT_ID/views/$VIEW_ID/buckets" \
  | DEFAULT_ID="$DEFAULT_BUCKET_ID" python3 -c "
import os, sys, json
default_id = os.environ.get('DEFAULT_ID') or None
for b in json.load(sys.stdin):
    if default_id and str(b['id']) == default_id:
        continue
    print(b['title'])
")

for col in "${COLUMNS[@]}"; do
  if echo "$EXISTING" | grep -qxF "$col"; then
    echo "bucket '$col' already exists, skipping"
    continue
  fi
  auth -X PUT "$VIKUNJA_URL/api/v1/projects/$PROJECT_ID/views/$VIEW_ID/buckets" \
    -H "Content-Type: application/json" \
    -d "$(COL="$col" python3 -c 'import os,json; print(json.dumps({"title": os.environ["COL"]}))')" > /dev/null
  echo "created bucket '$col'"
done

if [ -n "$DEFAULT_BUCKET_ID" ]; then
  auth -X DELETE "$VIKUNJA_URL/api/v1/projects/$PROJECT_ID/views/$VIEW_ID/buckets/$DEFAULT_BUCKET_ID" > /dev/null
  echo "removed the project's auto-created default bucket (id $DEFAULT_BUCKET_ID)"
fi

echo "done. project_id=$PROJECT_ID view_id=$VIEW_ID"
