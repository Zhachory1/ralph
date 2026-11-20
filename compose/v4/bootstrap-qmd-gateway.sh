#!/usr/bin/env bash
# Grants a running Paperclip container's hermes_local seat the pinned,
# approved-only qmd index through Hermes' own native MCP connector (#265
# first slice). Paperclip runs the Hermes CLI (/opt/hermes/bin/hermes)
# inside the same combined container a seat's run uses -- there is no
# separate Hermes container or named profile (-p) to target, and Paperclip
# has no agent-level MCP registration of its own (its "Gateways"/remote-MCP
# UI exposes Paperclip's own data to external clients, the opposite
# direction). This runs everything with `docker exec` against HERMES_HOME,
# the same directory a seat's adapterConfig.env.HERMES_HOME names.
# No Ralph "knowledge" container, HTTP wrapper, or custom indexer: this
# installs qmd into the container and registers it with `hermes mcp add`,
# the same discovery-first command an operator would run by hand. Does not
# build or refresh the index itself -- run the existing approved-snapshot
# runbook (docs/operations.md, "Refresh a separate qmd index from approved
# PARA snapshots") against QMD_STATE_DIR first, inside this same container
# (there are no host bind mounts).
#
#   PAPERCLIP_CONTAINER=my-paperclip-container \
#   HERMES_HOME=/paperclip/.hermes \
#   QMD_STATE_DIR=/paperclip/qmd-state \
#     ./bootstrap-qmd-gateway.sh
#
# Idempotent: the npm install tolerates reruns; `hermes mcp add` prompts to
# overwrite an existing server and to confirm the discovered toolset, so
# every invocation pipes "y" answers to both.
set -euo pipefail

PAPERCLIP_CONTAINER="${PAPERCLIP_CONTAINER:?set PAPERCLIP_CONTAINER to the running combined Paperclip/Hermes container}"
HERMES_HOME="${HERMES_HOME:-/paperclip/.hermes}"
QMD_STATE_DIR="${QMD_STATE_DIR:?set QMD_STATE_DIR to the approved qmd state directory inside the container, with config/ and cache/ built by the existing refresh runbook}"
QMD_NPM_GLOBAL="${QMD_NPM_GLOBAL:-/paperclip/npm-global}"

run() {
  docker exec -u node "$PAPERCLIP_CONTAINER" "$@"
}

run_with_home() {
  docker exec -u node -e "HERMES_HOME=$HERMES_HOME" "$PAPERCLIP_CONTAINER" "$@"
}

run_interactive_with_home() {
  docker exec -u node -i -e "HERMES_HOME=$HERMES_HOME" "$PAPERCLIP_CONTAINER" "$@"
}

echo "== qmd: install 2.8.3 into the container =="
# Pinned literally, matching compose/Dockerfile.knowledge's default; not a
# shell-interpolated version, so this command cannot be redirected by an
# unexpected environment value. Skipped if already present: a network-
# disabled target (or the offline canary) places the binary here itself.
if run test -x "$QMD_NPM_GLOBAL/bin/qmd"; then
  echo "qmd already present at $QMD_NPM_GLOBAL/bin/qmd; skipping install"
else
  run mkdir -p "$QMD_NPM_GLOBAL"
  run npm install -g --prefix "$QMD_NPM_GLOBAL" @tobilu/qmd@2.8.3
fi

echo "== hermes: ensure HERMES_HOME exists =="
run mkdir -p "$HERMES_HOME"

echo "== hermes: grant this seat's Hermes environment the native qmd MCP connector (no Ralph custom MCP) =="
# `mcp add` is discovery-first: it spawns the command immediately to list
# tools, then prompts to confirm enabling them. Piping two "y" answers
# covers both that prompt and the "already exists, overwrite?" prompt a
# rerun hits; qmd mcp exposes only its four read tools (query, get,
# multi_get, status), so there is no admin/update tool to exclude.
printf 'y\ny\n' | run_interactive_with_home /opt/hermes/bin/hermes mcp add qmd \
  --command "$QMD_NPM_GLOBAL/bin/qmd" \
  --env "QMD_CONFIG_DIR=${QMD_STATE_DIR}/config" "XDG_CACHE_HOME=${QMD_STATE_DIR}/cache" "INDEX_PATH=${QMD_STATE_DIR}/cache/index.sqlite" \
  --args mcp

echo "== hermes: verify the connector =="
run_with_home /opt/hermes/bin/hermes mcp test qmd

echo "done. Point this seat's adapterConfig.env.HERMES_HOME at $HERMES_HOME, and keep QMD_STATE_DIR ($QMD_STATE_DIR) refreshed inside this same container."
echo "If adapterConfig.toolsets is set, include the server name qmd (for example, terminal,qmd)."
