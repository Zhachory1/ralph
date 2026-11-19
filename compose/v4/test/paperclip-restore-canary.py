"""Rehearse a cold Paperclip backup, restore, and rollback with synthetic data."""

import json
import os
import runpy
import secrets
import subprocess
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryFile

FIXTURE = runpy.run_path(str(Path(__file__).with_name("paperclip-rfc-canary.py")))
DOCKER = FIXTURE["docker"]
ORIGIN = FIXTURE["ORIGIN"]
EMAIL = "restore@fixture.invalid"


def create(stack, name, env):
    volume = DOCKER("volume", "create", f"{name}-state")
    stack.callback(DOCKER, "volume", "rm", volume)
    container = DOCKER(
        "create", "--pull", "never", "--network", "none", "--name", name,
        "-e", "HOST=127.0.0.1", "-e", "PAPERCLIP_DEPLOYMENT_MODE=authenticated",
        "-e", "PAPERCLIP_DEPLOYMENT_EXPOSURE=private", "-e", "PAPERCLIP_TELEMETRY_DISABLED=1",
        "-e", "PAPERCLIP_AUTH_BASE_URL_MODE=explicit", "-e", f"PAPERCLIP_AUTH_PUBLIC_BASE_URL={ORIGIN}",
        "-e", "PAPERCLIP_AUTH_DISABLE_SIGN_UP=false", "-e", "PAPERCLIP_AGENT_JWT_SECRET",
        "--mount", f"type=volume,src={volume},dst=/paperclip", FIXTURE["IMAGE"], env=env,
    )
    stack.callback(DOCKER, "rm", "-fv", container)
    state = json.loads(DOCKER("inspect", container))[0]
    assert state["HostConfig"]["NetworkMode"] == "none"
    assert not state["HostConfig"]["PortBindings"]
    mount, = state["Mounts"]
    assert (mount["Type"], mount["Name"], mount["Destination"]) == ("volume", volume, "/paperclip")
    return container


def start(container):
    DOCKER("start", container)
    FIXTURE["fixture"]["wait_for_health"](container, f"{ORIGIN}/api/health", "Paperclip", paperclip=True)


def stop(container):
    DOCKER("stop", "-t", "15", container)
    assert DOCKER("inspect", "-f", "{{.State.ExitCode}}", container) == "0", "Cold copy requires clean shutdown"


def board(container, path, body=None, method="POST", *, actor="restore"):
    return FIXTURE["request"](container, actor, path, body, method)[1]


def record_run(container, issue, agent):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        for run in board(container, f"issues/{issue}/runs"):
            if run["agentId"] == agent and run["invocationSource"] == "assignment":
                assert run["status"] not in {"failed", "cancelled", "timed_out"}, run["status"]
                if run["status"] == "succeeded":
                    return run["runId"]
        time.sleep(1)
    raise AssertionError("Expected a successful fixture assignment run")


def drill():
    suffix = uuid.uuid4().hex[:10]
    env = {**os.environ, "PAPERCLIP_AGENT_JWT_SECRET": secrets.token_hex(32)}
    credentials = {"email": EMAIL, "password": secrets.token_hex(16)}
    with ExitStack() as stack:
        source = create(stack, f"ralph-paperclip-restore-source-{suffix}", env)
        restored = create(stack, f"ralph-paperclip-restore-target-{suffix}", env)
        start(source)
        board(source, "auth/sign-up/email", {**credentials, "name": "Restore fixture"})
        board(source, "bootstrap/claim", {})
        user = board(source, "auth/get-session")["user"]["id"]
        company = board(source, "companies", {"name": "Restore fixture"})
        company_path = f"companies/{company['id']}"
        agent = board(source, f"{company_path}/agents", {
            "name": "Fixture worker", "adapterType": "process", "adapterConfig": {"command": "/bin/true"},
        })
        issue = board(source, f"{company_path}/issues", {
            "title": "Restore checkpoint", "status": "todo", "assigneeAgentId": agent["id"],
        })
        run_id = record_run(source, issue["id"], agent["id"])
        issue_path = f"issues/{issue['id']}"
        board(source, issue_path, {"status": "done"}, "PATCH")
        before = board(source, f"heartbeat-runs/{run_id}")
        stop(source)

        # Only the generated fixture is archived. Preserve ownership for embedded PostgreSQL.
        with TemporaryFile() as archive:
            subprocess.run(["docker", "cp", "-a", f"{source}:/paperclip/.", "-"],
                           stdout=archive, timeout=30, check=True)
            archive.seek(0)
            subprocess.run(["docker", "cp", "-a", "-", f"{restored}:/paperclip/"],
                           stdin=archive, timeout=30, check=True)
        start(restored)

        def verify(container, actor):
            board(container, "auth/sign-in/email", credentials, actor=actor)
            assert board(container, "auth/get-session", actor=actor)["user"]["id"] == user
            saved = board(container, issue_path, actor=actor)
            assert (saved["id"], saved["title"], saved["status"]) == (issue["id"], "Restore checkpoint", "done")
            run = board(container, f"heartbeat-runs/{run_id}", actor=actor)
            for key in ("id", "agentId", "status", "finishedAt"):
                assert run[key] == before[key], key

        verify(restored, "target")
        print("PASS: backup restored the account, issue, and completed run", flush=True)
        marker = "Written only on the restored instance"
        board(restored, f"{issue_path}/comments", {"body": marker}, actor="target")
        assert any(c["body"] == marker for c in board(restored, f"{issue_path}/comments", actor="target"))
        stop(restored)
        start(source)
        verify(source, "rollback")
        assert all(c["body"] != marker for c in board(source, f"{issue_path}/comments", actor="rollback"))
        print("PASS: rollback restarted the original checkpoint without restored-instance changes", flush=True)


if __name__ == "__main__":
    drill()
