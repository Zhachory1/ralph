"""Exercise the disposable Paperclip canary using the existing Docker daemon."""

import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[1] / "paperclip-canary.yml"
SERVICE = "paperclip"
STARTUP_SECONDS = 90
RUN_SECONDS = 25
API = "http://127.0.0.1:3100/api"
MODEL_API = "http://127.0.0.1:8765"
MODEL_PLACEHOLDER = "fixture-not-a-credential"
REPO = "/paperclip/canary-repo"
FORBIDDEN_ENV = {"GH_TOKEN", "GITHUB_TOKEN", "OPENROUTER_API_KEY", "OPENAI_API_KEY"}


def docker(*args, env=None):
    return subprocess.check_output(["docker", *args], text=True, timeout=120, env=env).strip()


def api(container, path, payload=None, method="POST"):
    command = ["docker", "exec", "-i", container, "curl", "--fail-with-body", "-sS", "--max-time", "20"]
    args = ["-X", method, "--json", "@-"] if payload is not None else []
    response = subprocess.run(
        [*command, *args, f"{API}/{path}"],
        input=json.dumps(payload) if payload is not None else "", text=True,
        stdout=subprocess.PIPE, timeout=25, check=False,
    )
    if response.returncode:
        raise RuntimeError(f"Paperclip API {path}: {response.stdout}")
    return json.loads(response.stdout)


def wait_for_health(container, url, service, *, paperclip=False):
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        response = subprocess.run(
            ["docker", "exec", container, "curl", "-fsS", "--max-time", "2", url],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if response.returncode == 0 and (
            not paperclip or json.loads(response.stdout).get("status") == "ok"
        ):
            return
        if docker("inspect", "-f", "{{.State.Running}}", container) != "true":
            raise RuntimeError(f"Paperclip exited before {service} became healthy")
        time.sleep(1)
    raise RuntimeError(f"{service} did not become healthy")


def wait_for_run(container, issue_id, agent_id, *, comment_id=None):
    deadline = time.monotonic() + RUN_SECONDS
    while time.monotonic() < deadline:
        runs = api(container, f"issues/{issue_id}/runs")
        for run in runs:
            # A delegated issue also lists its originating Hermes run.
            if run["agentId"] != agent_id:
                continue
            matches = (
                run["wakeCommentId"] == comment_id if comment_id else
                run["invocationSource"] == "assignment"
            )
            if not matches:
                continue
            if run["status"] == "failed":
                detail = api(container, f"heartbeat-runs/{run['runId']}")
                raise AssertionError(f"Run failed: {detail.get('error')}; {detail.get('stderrExcerpt')}")
            if run["status"] == "succeeded":
                return api(container, f"heartbeat-runs/{run['runId']}")
        time.sleep(1)
    summary = [(run["invocationSource"], run["status"], run["errorCode"]) for run in runs]
    raise RuntimeError(f"Expected a settled run on {issue_id}; got {summary}")


def delegate(container, company_id, project_id, orchestrator_id, developer_id):
    issues = f"companies/{company_id}/issues"
    parent = api(container, issues, {
        "title": "CANARY_DELEGATE", "description": "Delegate the fixture check to Developer.",
        "status": "backlog",
    })
    task = {
        "title": "CANARY_FIRST", "description": "Create result.txt and run the local check. Report the result.",
        "parentId": parent["id"], "projectId": project_id, "assigneeAgentId": developer_id,
        "status": "todo", "idempotencyKey": "canary-delegation",
    }
    script = f"""import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

def submit(path, body, method="POST"):
    headers = {{
        "Authorization": "Bearer " + os.environ["PAPERCLIP_API_KEY"],
        "X-Paperclip-Run-Id": os.environ["PAPERCLIP_RUN_ID"],
        "Content-Type": "application/json",
    }}
    request = Request({API!r} + path, data=json.dumps(body).encode(), headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return json.load(response)
    except HTTPError as error:
        raise SystemExit(error.read().decode())

assert os.environ["PAPERCLIP_TASK_ID"] == {parent["id"]!r}
assert not os.environ.get("PAPERCLIP_AGENT_JWT_SECRET"), "Signing secret must not be forwarded"
parent_path = "/issues/" + os.environ["PAPERCLIP_TASK_ID"]
submit(parent_path + "/checkout", {{"agentId": os.environ["PAPERCLIP_AGENT_ID"],
                                   "expectedStatuses": ["todo", "in_progress"]}})
child = submit({f"/{issues}"!r}, {task!r})
submit(parent_path, {{"status": "done", "comment": "Delegated to " + child["identifier"]}}, "PATCH")
print(child["identifier"])
"""
    subprocess.run(
        ["docker", "exec", "-i", "-u", "node", container, "sh", "-c",
         "umask 077; cat > /paperclip/delegate.py"],
        input=script, text=True, timeout=10, check=True,
    )
    api(container, f"issues/{parent['id']}", {"assigneeAgentId": orchestrator_id, "status": "todo"}, "PATCH")
    run = wait_for_run(container, parent["id"], orchestrator_id)
    assert run["agentId"] == orchestrator_id
    children = [issue for issue in api(container, issues) if issue["parentId"] == parent["id"]]
    assert len(children) == 1, run.get("stdoutExcerpt")
    (child,) = children
    assert child["assigneeAgentId"] == developer_id
    assert child["createdByAgentId"] == orchestrator_id
    assert child["originRunId"] == run["id"]
    assert child["projectId"] == project_id
    return child


def test_worktree(container, company_id, orchestrator_id, developer_id):
    def local(*args):
        return docker("exec", "-u", "node", container, *args)

    local("git", "init", "-b", "main", REPO)
    local("sh", "-c", """printf 'test "$(cat result.txt)" = "$1"\\n' > "$1/check.sh" """, "seed", REPO)
    local("git", "-C", REPO, "add", "check.sh")
    local("git", "-C", REPO, "-c", "user.name=Canary", "-c", "user.email=canary@example.invalid",
          "commit", "-m", "Seed canary")
    base = local("git", "-C", REPO, "rev-parse", "HEAD")
    api(container, "instance/settings/experimental", {"enableIsolatedWorkspaces": True}, "PATCH")
    project = api(container, f"companies/{company_id}/projects", {
        "name": "Disposable repository",
        "workspace": {"cwd": REPO, "defaultRef": "main", "isPrimary": True},
        "executionWorkspacePolicy": {
            "enabled": True, "defaultMode": "isolated_workspace",
            "workspaceStrategy": {
                "type": "git_worktree", "baseRef": "main", "worktreeParentDir": "/paperclip/canary-worktrees",
            },
        },
    })
    issue = delegate(container, company_id, project["id"], orchestrator_id, developer_id)
    initial = wait_for_run(container, issue["id"], developer_id)
    paths = [
        line.removeprefix("worktree ") for line in local("git", "-C", REPO, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ") and line != f"worktree {REPO}"
    ]
    (workspace,) = paths
    assert local("cat", f"{workspace}/result.txt") == "first"
    local("sh", "-c", 'cd "$1" && sh check.sh first', "verify", workspace)
    assert api(container, f"issues/{issue['id']}")["status"] == "in_review", initial["stdoutExcerpt"]
    comment = api(container, f"issues/{issue['id']}/comments", {"body": "CANARY_FOLLOWUP: update and recheck."})
    followup = wait_for_run(container, issue["id"], developer_id, comment_id=comment["id"])
    session = initial["sessionIdAfter"]
    resumed = followup["sessionIdBefore"], followup["sessionIdAfter"]
    assert session and resumed == (session, session), (session, resumed)
    assert "CANARY_RESULT" in followup["stdoutExcerpt"]
    assert api(container, f"issues/{issue['id']}")["status"] == "done"
    assert local("cat", f"{workspace}/result.txt") == "second"
    local("sh", "-c", 'cd "$1" && sh check.sh second', "verify", workspace)
    assert local("git", "-C", REPO, "rev-parse", "refs/heads/main") == base
    base_status = local("git", "-C", REPO, "status", "--porcelain")
    assert not base_status, base_status


def run_canary():
    project = f"ralph-paperclip-canary-{uuid.uuid4().hex[:10]}"
    jwt_secret = secrets.token_hex(32)
    env = {**os.environ, "CANARY_AGENT_JWT_SECRET": jwt_secret}
    compose = (
        "compose", "--env-file", "/dev/null", "-f", str(COMPOSE), "-p", project,
    )
    config = json.loads(docker(*compose, "config", "--format", "json", env=env))
    assert set(config["services"]) == {SERVICE}, "canary must not start legacy services"
    model = None
    try:
        docker(*compose, "up", "-d", "--no-build", "--pull", "never", env=env)
        container_id = docker(*compose, "ps", "-q", SERVICE, env=env)
        state = json.loads(docker("inspect", container_id))[0]
        assert state["HostConfig"]["NetworkMode"] == "none"
        assert not any(mount["Type"] == "bind" for mount in state["Mounts"])
        assert not state["HostConfig"]["PortBindings"]
        environment = {entry.split("=", 1)[0] for entry in state["Config"]["Env"]}
        assert not FORBIDDEN_ENV.intersection(environment)
        wait_for_health(container_id, f"{API}/health", SERVICE, paperclip=True)
        model = subprocess.Popen(
            ["docker", "exec", "-i", "-u", "node", container_id, "sh", "-c",
             "python3 - 2>/tmp/canary-model.log"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        with model.stdin as source:
            source.write(Path(__file__).with_name("paperclip-model.py").read_bytes())
        wait_for_health(container_id, MODEL_API, "synthetic provider")
        company = api(container_id, "companies", {"name": "Disposable canary"})
        agents = f"companies/{company['id']}/agents"
        hermes_home = "/paperclip/.hermes"
        docker("exec", "-u", "node", container_id, "mkdir", "-p", hermes_home)
        hermes_config = json.dumps({
            "model": {
                "provider": "custom", "default": "fixture-hermes",
                "base_url": f"{MODEL_API}/v1", "api_key": MODEL_PLACEHOLDER,
            },
            "terminal": {"backend": "local", "cwd": "/paperclip"},
        })
        docker("exec", "-u", "node", container_id, "sh", "-c",
               'printf "%s\\n" "$1" > "$2/config.yaml"', "configure", hermes_config, hermes_home)
        hermes_adapter_config = {
            "hermesCommand": "/opt/hermes/bin/hermes",
            "model": "fixture-hermes", "provider": "custom",
            "toolsets": "terminal", "cwd": "/paperclip", "timeoutSec": 20,
            "env": {
                "HERMES_HOME": hermes_home, "PAPERCLIP_AGENT_JWT_SECRET": "",
                # The legacy adapter's environment check does not recognize this custom provider.
                "OPENAI_API_KEY": MODEL_PLACEHOLDER,
            },
        }
        check = api(container_id, f"companies/{company['id']}/adapters/hermes_local/test-environment", {
            "adapterConfig": hermes_adapter_config,
        })
        assert check["status"] == "pass", check
        orchestrator = api(container_id, agents, {
            "name": "Orchestrator",
            "adapterType": "hermes_local",
            "adapterConfig": hermes_adapter_config,
        })
        opencode_config = {
            "cwd": "/paperclip",
            "model": "fixture/small",
            "helloProbeTimeoutSec": 8,
            "dangerouslySkipPermissions": False,
            "env": {
                "OPENCODE_DISABLE_MODELS_FETCH": "1",
                "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "OPENCODE_CONFIG_CONTENT": json.dumps({
                    "enabled_providers": ["fixture"], "permission": {"bash": "allow"},
                    "provider": {"fixture": {
                        "npm": "@ai-sdk/openai-compatible",
                        "options": {"baseURL": f"{MODEL_API}/v1"},
                        "models": {"small": {"name": "Fixture"}},
                    }},
                }),
            },
        }
        developer = api(container_id, agents, {
            "name": "Developer",
            "reportsTo": orchestrator["id"],
            "adapterType": "opencode_local",
            "runner": "legacy",
            "adapterConfig": opencode_config,
        })
        assert orchestrator["adapterType"] == "hermes_local"
        assert developer["adapterType"] == "opencode_local", "Paperclip substituted its default runner"
        assert developer["reportsTo"] == orchestrator["id"]
        test_worktree(container_id, company["id"], orchestrator["id"], developer["id"])
    except Exception:
        logs = docker(*compose, "logs", "--tail", "25", env=env)
        logs = logs.replace(jwt_secret, "[redacted]")
        print("\n".join(line for line in logs.splitlines()
                        if '"method":"GET"' not in line))
        if model is not None:
            subprocess.run(["docker", "exec", container_id, "tail", "-n", "15", "/tmp/canary-model.log"],
                           timeout=5, check=False)
        raise
    finally:
        docker(*compose, "down", "--volumes", "--timeout", "5", env=env)
        if model is not None:
            model.wait(timeout=10)
    assert not docker(*compose, "ps", "-aq", env=env), "canary container survived cleanup"
    label = f"label=com.docker.compose.project={project}"
    assert not docker("volume", "ls", "-q", "--filter", label), "canary volume survived cleanup"


if __name__ == "__main__":
    run_canary()
    print("PASS: Hermes delegation, OpenCode worktree and session resume, and cleanup")
