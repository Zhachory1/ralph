#!/usr/bin/env bash
set -euo pipefail

# Run inside the Hermes runtime with its intended HERMES_HOME mounted.
profile=ralph-v4-reviewer
if [ ! -d "${HERMES_HOME:?set HERMES_HOME}/profiles/$profile" ]; then
  hermes profile create "$profile" --no-alias --no-skills --description "v4 sandboxed PR auditor"
fi

grant="mcp_servers.coding-harness-audit"
hermes -p "$profile" config set "$grant.url" 'http://host.docker.internal:8765/mcp'
hermes -p "$profile" config set "$grant.headers.Authorization" 'Bearer ${RALPH_REVIEWER_HARNESS_TOKEN}'
hermes -p "$profile" config set "$grant.strict_redirect_headers" true
hermes -p "$profile" config set "$grant.enabled" true
hermes -p "$profile" config set "$grant.tools.include" '[audit_pr]'
