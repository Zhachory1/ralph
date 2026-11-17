#!/usr/bin/env bash
# Wires a running Hermes container to the coding-harness MCP server, and
# installs the mewrite adapter it shells out to. Automates four gotchas
# found running this by hand (docs/1-projects/v4-architecture/
# hermes-harness-integration.md); each step below exists because of one.
#
#   HERMES_HOME_VOLUME=/path/to/host/dir \
#   MEWRITE_AUTH_SOURCE=~/.mewrite/agent/auth.json \
#     ./bootstrap-hermes.sh
#
# Idempotent: re-running reinstalls mewrite (cheap, catches a version bump)
# and re-applies config with `hermes config set`, which replaces rather than
# duplicates a key.
set -euo pipefail

HERMES_IMAGE="${HERMES_IMAGE:?set HERMES_IMAGE, e.g. nousresearch/hermes-agent:v0.22.2}"
HERMES_HOME_VOLUME="${HERMES_HOME_VOLUME:?set HERMES_HOME_VOLUME to a host directory Hermes /opt/data mounts to}"
MEWRITE_AUTH_SOURCE="${MEWRITE_AUTH_SOURCE:?set MEWRITE_AUTH_SOURCE to the mewrite OAuth auth.json to copy in (read once, never retained by this script)}"
MEWRITE_VERSION="${MEWRITE_VERSION:-1.6.5}"
HARNESS_DIR="${HARNESS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../coding-harness" && pwd)}"

run() {
  docker run --rm -v "$HERMES_HOME_VOLUME:/opt/data" "$HERMES_IMAGE" "$@"
}

echo "== mewrite: install into the Hermes home volume =="
# Gotcha 1: the image's global npm prefix is root-owned; the container runs
# as a non-root user (uid 10000), so a plain `npm install -g` fails EACCES.
# Installing into the Hermes home volume (writable, and already mounted
# every run) instead of the image's own npm prefix is what makes this
# survive a container restart at all.
run sh -c "mkdir -p /opt/data/npm-global && npm install -g --prefix /opt/data/npm-global \
  --allow-scripts='@zhachory1/mewrite-code,better-sqlite3,node-pty,onnxruntime-node,@google/genai,protobufjs,koffi' \
  @zhachory1/mewrite-code@${MEWRITE_VERSION}"
# Gotcha 2: mewrite's own native deps (better-sqlite3, node-pty, ...) have
# install scripts npm blocks by default on a global install; without
# --allow-scripts the binary installs but every real command fails at
# runtime, not at install time, which makes the failure far harder to place.

echo "== mewrite: copy its OAuth credential into a writable HOME =="
# Gotcha 3: mewrite needs a writable $HOME to create its session/state
# files. The image's own $HOME (root's) is not writable by uid 10000;
# /opt/data/mewrite-home is, and survives restarts since it's on the same
# volume. The source auth.json is bind-mounted read-only for this one copy
# and never printed, logged, or committed -- this script's only privileged
# action.
docker run --rm -v "$HERMES_HOME_VOLUME:/opt/data" -v "$MEWRITE_AUTH_SOURCE:/tmp/auth.json:ro" "$HERMES_IMAGE" \
  sh -c "mkdir -p /opt/data/mewrite-home/.mewrite/agent && cp /tmp/auth.json /opt/data/mewrite-home/.mewrite/agent/auth.json"

echo "== git: trust worktrees this container creates =="
# Gotcha 4: a worktree the harness creates is owned by uid 10000 (the
# container's user); git refuses to operate on a repository whose owner
# doesn't match the running user ("detected dubious ownership") unless that
# path is explicitly marked safe. Any repo_path the harness is ever pointed
# at needs this -- a wildcard is the practical choice here, since the harness
# only ever operates on repos the operator already chose to point it at.
run sh -c 'export HOME=/opt/data/mewrite-home; git config --global --get-all safe.directory | grep -qxF "*" || git config --global --add safe.directory "*"'

echo "== hermes: configure the coding-harness MCP server =="
run hermes config set mcp_servers.coding-harness.command "node"
run hermes config set mcp_servers.coding-harness.args "[\"$HARNESS_DIR/server.js\"]"
run hermes config set mcp_servers.coding-harness.enabled true
run hermes config set mcp_servers.coding-harness.env.PATH "/opt/data/npm-global/bin:/usr/local/bin:/usr/bin:/bin"
run hermes config set mcp_servers.coding-harness.env.HOME "/opt/data/mewrite-home"

echo "done. Mount $HARNESS_DIR into the hermes container at $HARNESS_DIR (read-only) when running it for real."
