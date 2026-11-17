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

VIKUNJA_URL="${VIKUNJA_URL:-http://127.0.0.1:3456}"
VIKUNJA_USERNAME="${VIKUNJA_USERNAME:?set VIKUNJA_USERNAME}"
VIKUNJA_PASSWORD="${VIKUNJA_PASSWORD:?set VIKUNJA_PASSWORD}"
PROJECT_TITLE="${VIKUNJA_PROJECT_TITLE:-ralph}"

# Columns in spec Sec 5's exact order. The kanban view's own default bucket
# (always created as "Backlog" on project creation) is deleted once our own
# named buckets exist, so there is exactly one "Backlog".
COLUMNS=("Triage" "RFC Author" "Council Review" "Backlog" "Ready" "Peer Review" "Director PR Merge" "Done")

TOKEN=$(curl -sf -X POST "$VIKUNJA_URL/api/v1/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$VIKUNJA_USERNAME\",\"password\":\"$VIKUNJA_PASSWORD\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

auth() { curl -sf -H "Authorization: Bearer $TOKEN" "$@"; }

PROJECT_ID=$(auth "$VIKUNJA_URL/api/v1/projects" \
  | python3 -c "import sys,json; print(next((p['id'] for p in json.load(sys.stdin) if p['title']=='$PROJECT_TITLE'), ''))")

if [ -z "$PROJECT_ID" ]; then
  PROJECT_ID=$(auth -X PUT "$VIKUNJA_URL/api/v1/projects" -H "Content-Type: application/json" \
    -d "{\"title\":\"$PROJECT_TITLE\",\"description\":\"v4 PR-review pipeline (spec Sec 5)\"}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
  echo "created project $PROJECT_TITLE (id $PROJECT_ID)"
else
  echo "project $PROJECT_TITLE already exists (id $PROJECT_ID)"
fi

VIEW_ID=$(auth "$VIKUNJA_URL/api/v1/projects/$PROJECT_ID/views" \
  | python3 -c "import sys,json; print(next(v['id'] for v in json.load(sys.stdin) if v['view_kind']=='kanban'))")

EXISTING=$(auth "$VIKUNJA_URL/api/v1/projects/$PROJECT_ID/views/$VIEW_ID/buckets" \
  | python3 -c "import sys,json; [print(b['title']) for b in json.load(sys.stdin)]")

for col in "${COLUMNS[@]}"; do
  if echo "$EXISTING" | grep -qxF "$col"; then
    echo "bucket '$col' already exists, skipping"
    continue
  fi
  auth -X PUT "$VIKUNJA_URL/api/v1/projects/$PROJECT_ID/views/$VIEW_ID/buckets" \
    -H "Content-Type: application/json" -d "{\"title\":\"$col\"}" > /dev/null
  echo "created bucket '$col'"
done

# The auto-created default bucket is untitled "Backlog" too, created before
# this script's own named one -- if both exist, the lowest-id "Backlog" with
# no tasks in it and not the one this script just created is the stray one.
BUCKETS_JSON=$(auth "$VIKUNJA_URL/api/v1/projects/$PROJECT_ID/views/$VIEW_ID/buckets")
BACKLOG_COUNT=$(echo "$BUCKETS_JSON" | python3 -c "import sys,json; print(sum(1 for b in json.load(sys.stdin) if b['title']=='Backlog'))")
if [ "$BACKLOG_COUNT" -gt 1 ]; then
  STRAY_ID=$(echo "$BUCKETS_JSON" | python3 -c "
import sys, json
buckets = [b for b in json.load(sys.stdin) if b['title'] == 'Backlog']
print(min(b['id'] for b in buckets))
")
  auth -X DELETE "$VIKUNJA_URL/api/v1/projects/$PROJECT_ID/views/$VIEW_ID/buckets/$STRAY_ID" > /dev/null
  echo "removed duplicate default 'Backlog' bucket (id $STRAY_ID)"
fi

echo "done. project_id=$PROJECT_ID view_id=$VIEW_ID"
