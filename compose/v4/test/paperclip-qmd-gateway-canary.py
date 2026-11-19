"""Verify a real Paperclip hermes_local seat's own Hermes environment reaches
the pinned, approved-only qmd index through Hermes' own native MCP connector
(#265 first slice), against the actual Paperclip Compose target.

Starts the same disposable, network-disabled canary container
`paperclip-canary.py` uses. Copies in a qmd 2.8.3 binary built from the
existing `compose/Dockerfile.knowledge` image (that container has no
network, so qmd cannot be installed from the registry inside it) and builds
its own approved/draft/transient Git fixture and qmd index entirely inside
that one container -- nothing bind-mounted or shared with another
container. Runs `compose/v4/bootstrap-qmd-gateway.sh` against it exactly as
an operator would, confirms Paperclip's own
`adapters/hermes_local/test-environment` check passes for an agent whose
`adapterConfig.env.HERMES_HOME` is that exact directory, creates that
`hermes_local` agent, then speaks MCP directly to the exact command/env
Hermes stores to confirm the result is the approved text and excludes a
draft revision and a transient log, before and after a refresh to a second
approved revision.

Known gap: this does not dispatch a model-driven Paperclip run. Hermes
defers MCP tools behind a tool_search/tool_describe/tool_call catalog that,
in this offline probe, never surfaced qmd as an available source for any
query tried -- plausibly because that catalog's own matching needs a local
embedding index this canary deliberately does not install (no embedding
downloads). This canary instead proves the seat's exact configured
environment and its MCP connector directly; simulating the model's tool
discovery is unverified and left for a follow-up.

No live Paperclip target, Hindsight, or push.

    python3 compose/v4/test/paperclip-qmd-gateway-canary.py
"""

import importlib.machinery
import importlib.util
import json
import os
import secrets
import subprocess
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DOCKERFILE = ROOT.parent / "Dockerfile.knowledge"
QMD_IMAGE = "ralph-knowledge-qmd:2.8.3"  # Pinned; matches compose/Dockerfile.knowledge's default.
BOOTSTRAP_SCRIPT = ROOT / "bootstrap-qmd-gateway.sh"
HERMES_HOME = "/paperclip/.hermes"
QMD_NPM_GLOBAL = "/paperclip/npm-global"
QMD_STATE_DIR = "/paperclip/qmd-canary/state"
SOURCE_DIR = "/paperclip/qmd-canary/source"
APPROVED_DIR = "/paperclip/qmd-canary/approved"
APPROVED = "coppermeadow"
APPROVED_NEXT = "silverfen"
DRAFT = "violetdraft"
LOG = "embertranscript"


def load_paperclip_canary():
    path = Path(__file__).with_name("paperclip-canary.py")
    loader = importlib.machinery.SourceFileLoader("paperclip_canary", str(path))
    spec = importlib.util.spec_from_loader("paperclip_canary", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


pc = load_paperclip_canary()


def run_checked(*args, input_text=None, timeout=120, label=""):
    result = subprocess.run(args, input=input_text, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"{label or args[0]} failed: {result.stdout[-2000:]}\n{result.stderr[-2000:]}")
    return result.stdout


def build_qmd_binary(scratch):
    """Builds the existing, already-pinned compose/Dockerfile.knowledge image
    (reuses the project's own qmd setup) and extracts its installed binary
    to a host directory for copying into the network-disabled canary."""
    run_checked("docker", "build", "-q", "-t", QMD_IMAGE, "-f", str(KNOWLEDGE_DOCKERFILE), str(ROOT.parent.parent),
                label="build knowledge image")
    builder = run_checked("docker", "create", QMD_IMAGE, label="create qmd builder").strip()
    try:
        run_checked("docker", "cp", f"{builder}:/usr/local/lib/node_modules", str(scratch / "node_modules"),
                    label="copy qmd node_modules")
    finally:
        subprocess.run(["docker", "rm", builder], capture_output=True, timeout=30, check=False)


def install_qmd_into_container(container, scratch):
    pc.docker("exec", "-u", "node", container, "mkdir", "-p",
              f"{QMD_NPM_GLOBAL}/lib/node_modules", f"{QMD_NPM_GLOBAL}/bin")
    run_checked("docker", "cp", str(scratch / "node_modules" / "@tobilu"),
                f"{container}:{QMD_NPM_GLOBAL}/lib/node_modules/@tobilu", label="copy qmd into canary")
    pc.docker("exec", "-u", "node", container, "ln", "-sf",
              "../lib/node_modules/@tobilu/qmd/bin/qmd", f"{QMD_NPM_GLOBAL}/bin/qmd")
    version = pc.docker("exec", "-u", "node", container, f"{QMD_NPM_GLOBAL}/bin/qmd", "--version")
    assert version.split()[:2] == ["qmd", "2.8.3"], version


def build_approved_fixture(container):
    """Builds the approved/draft/transient Git fixture and its qmd index
    entirely inside this one disposable container -- the existing refresh
    runbook's own commands, run with `docker exec` since there are no host
    bind mounts for this target."""
    script = f"""
set -eu
mkdir -p {QMD_STATE_DIR}/config {QMD_STATE_DIR}/cache
mkdir -p {SOURCE_DIR}/docs/1-projects/fixture
cd {SOURCE_DIR}
git init --template= -b main -q
git config user.name "Gateway Canary"
git config user.email "canary@fixture.invalid"
printf "# Approved RFC\\n\\n{APPROVED}\\n" > docs/1-projects/fixture/rfc.md
git add -A
git commit -q -m "Approve RFC"
APPROVED_SHA=$(git rev-parse HEAD)
git switch -q -c draft
printf "# Draft RFC\\n\\n{DRAFT}\\n" > docs/1-projects/fixture/rfc.md
git commit -q -am "Unapproved revision"
printf "# Transient run log\\n\\n{LOG}\\n" > docs/1-projects/fixture/transient.md

git worktree add --detach -q {APPROVED_DIR} "$APPROVED_SHA"

export QMD_CONFIG_DIR={QMD_STATE_DIR}/config XDG_CACHE_HOME={QMD_STATE_DIR}/cache INDEX_PATH={QMD_STATE_DIR}/cache/index.sqlite
QMD={QMD_NPM_GLOBAL}/bin/qmd
$QMD collection add {APPROVED_DIR}/docs --name approved --mask '**/*.md' >/dev/null
$QMD update >/dev/null
"""
    pc.docker("exec", "-u", "node", container, "sh", "-c", script)


def refresh_to_next_revision(container):
    script = f"""
set -eu
cd {SOURCE_DIR}
git switch -q main
printf "# Approved RFC, revision two\\n\\n{APPROVED_NEXT}\\n" > docs/1-projects/fixture/rfc.md
git commit -q -am "Approve second revision"
NEXT_SHA=$(git rev-parse HEAD)

cd {APPROVED_DIR}
test -z "$(git status --porcelain --ignored)"
git switch -q --detach "$NEXT_SHA"
test "$(git rev-parse HEAD)" = "$NEXT_SHA"

export QMD_CONFIG_DIR={QMD_STATE_DIR}/config XDG_CACHE_HOME={QMD_STATE_DIR}/cache INDEX_PATH={QMD_STATE_DIR}/cache/index.sqlite
{QMD_NPM_GLOBAL}/bin/qmd update >/dev/null
"""
    pc.docker("exec", "-u", "node", container, "sh", "-c", script)


def run_gateway_bootstrap(container):
    env = {
        **os.environ, "PAPERCLIP_CONTAINER": container, "HERMES_HOME": HERMES_HOME,
        "QMD_STATE_DIR": QMD_STATE_DIR,
    }
    result = subprocess.run(["bash", str(BOOTSTRAP_SCRIPT)], env=env, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f"bootstrap-qmd-gateway.sh failed: {result.stdout[-2000:]}\n{result.stderr[-2000:]}")
    return result.stdout


def assert_connector_discovered(output):
    assert "Connected! Found 4 tool(s) from 'qmd'" in output, output
    for tool in ("query", "get", "multi_get", "status"):
        assert tool in output, f"missing tool {tool} in discovery output"
    assert "Tools discovered: 4" in output, "hermes mcp test did not confirm the connector"


def query_through_configured_entrypoint(container, term):
    """Speaks MCP over stdio to the literal command/env Hermes stores for
    this connector, inside this seat's actual container, so the result
    reflects what the seat itself would see."""
    proc = subprocess.Popen(
        ["docker", "exec", "-u", "node", "-i",
         "-e", f"QMD_CONFIG_DIR={QMD_STATE_DIR}/config", "-e", f"XDG_CACHE_HOME={QMD_STATE_DIR}/cache",
         "-e", f"INDEX_PATH={QMD_STATE_DIR}/cache/index.sqlite",
         container, f"{QMD_NPM_GLOBAL}/bin/qmd", "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        def rpc(message):
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
            return json.loads(proc.stdout.readline())

        init = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "canary", "version": "0"}}})
        assert "result" in init, init
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        response = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "query", "arguments": {
                "searches": [{"type": "lex", "query": term}], "collections": ["approved"], "rerank": False}}})
        return response["result"]["structuredContent"]["results"]
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=10)


def run_canary():
    project = f"ralph-qmd-gateway-canary-{uuid.uuid4().hex[:10]}"
    jwt_secret = secrets.token_hex(32)
    env = {**os.environ, "CANARY_AGENT_JWT_SECRET": jwt_secret}
    compose = ("compose", "--env-file", "/dev/null", "-f", str(pc.COMPOSE), "-p", project)
    with tempfile.TemporaryDirectory(prefix="ralph-qmd-gateway-qmd-") as scratch_dir:
        scratch = Path(scratch_dir)
        build_qmd_binary(scratch)
        try:
            pc.docker(*compose, "up", "-d", "--no-build", "--pull", "never", env=env)
            container = pc.docker(*compose, "ps", "-q", pc.SERVICE, env=env)
            pc.wait_for_health(container, f"{pc.API}/health", pc.SERVICE, paperclip=True)

            install_qmd_into_container(container, scratch)
            build_approved_fixture(container)

            output = run_gateway_bootstrap(container)
            assert_connector_discovered(output)
            print("PASS: hermes mcp add discovered and enabled the native qmd connector"
                  " inside the real Paperclip container (no Ralph custom MCP)")

            approved_hits = query_through_configured_entrypoint(container, APPROVED)
            draft_hits = query_through_configured_entrypoint(container, DRAFT)
            log_hits = query_through_configured_entrypoint(container, LOG)
            assert len(approved_hits) == 1 and approved_hits[0]["file"] == "approved/1-projects/fixture/rfc.md", approved_hits
            assert not draft_hits, "unapproved draft was searchable through the seat's connector"
            assert not log_hits, "transient log was searchable through the seat's connector"
            print("PASS: the seat's own query tool returns the approved RFC and excludes the draft and transient log")

            # HERMES_HOME already has mcp_servers.qmd from the bootstrap step above;
            # `config set` merges into the existing config.yaml instead of
            # overwriting it, unlike a raw file write.
            for key, value in (
                ("model.provider", "custom"), ("model.default", "fixture-hermes"),
                ("model.base_url", f"{pc.MODEL_API}/v1"), ("model.api_key", pc.MODEL_PLACEHOLDER),
                ("terminal.backend", "local"), ("terminal.cwd", "/paperclip"),
            ):
                pc.docker("exec", "-u", "node", "-e", f"HERMES_HOME={HERMES_HOME}", container,
                          "/opt/hermes/bin/hermes", "config", "set", key, value)
            model = subprocess.Popen(
                ["docker", "exec", "-i", "-u", "node", container, "sh", "-c", "python3 - 2>/tmp/canary-model.log"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                with model.stdin as source:
                    source.write(Path(__file__).with_name("paperclip-model.py").read_bytes())
                pc.wait_for_health(container, pc.MODEL_API, "synthetic provider")
                company = pc.api(container, "companies", {"name": "Disposable qmd gateway canary"})
                hermes_adapter_config = {
                    "hermesCommand": "/opt/hermes/bin/hermes",
                    "model": "fixture-hermes", "provider": "custom",
                    "toolsets": "terminal", "cwd": "/paperclip", "timeoutSec": 20,
                    "env": {"HERMES_HOME": HERMES_HOME, "PAPERCLIP_AGENT_JWT_SECRET": "",
                            "OPENAI_API_KEY": pc.MODEL_PLACEHOLDER},
                }
                check = pc.api(container, f"companies/{company['id']}/adapters/hermes_local/test-environment", {
                    "adapterConfig": hermes_adapter_config,
                })
                assert check["status"] == "pass", check
                agent = pc.api(container, f"companies/{company['id']}/agents", {
                    "name": "Qmd Gateway Canary Seat",
                    "adapterType": "hermes_local",
                    "adapterConfig": hermes_adapter_config,
                })
                assert agent["adapterType"] == "hermes_local"
                print("PASS: a real hermes_local seat's test-environment check passes with the qmd connector wired"
                      " into its exact HERMES_HOME, and the seat was created")
            finally:
                model.terminate()
                model.wait(timeout=10)

            refresh_to_next_revision(container)
            next_hits = query_through_configured_entrypoint(container, APPROVED_NEXT)
            old_hits = query_through_configured_entrypoint(container, APPROVED)
            assert len(next_hits) == 1 and next_hits[0]["file"] == "approved/1-projects/fixture/rfc.md", next_hits
            assert not old_hits, "superseded approved text was still searchable after refresh"
            print("PASS: a refresh to the next approved revision reaches the seat without reconfiguring the connector")
        finally:
            pc.docker(*compose, "down", "--volumes", "--timeout", "5", env=env)


if __name__ == "__main__":
    run_canary()
