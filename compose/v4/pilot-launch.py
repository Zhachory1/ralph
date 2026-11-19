"""Arm the model relay for one prepared dashboard task in the local Ralph preview."""

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PREVIEW = "ralph-paperclip-preview"
PREVIEW_IMAGE = "ralph-paperclip-canary:2026.1117.0"
RELAY = "ralph-preview-relay-live"
IMAGE = ("ghcr.io/paperclipai/paperclip:2026.1117.0"
         "@sha256:bdf38d10544dc6c831e16dc027f23ad586353ef3be1a1b2c6979e63b956aceb1")
MODEL = "openai/gpt-4.1-mini"
REPO = "/paperclip/pilot-repo"
ORIGIN = "https://github.com/Zhachory1/ralph-canary.git"
WINDOW = 240
DASHBOARD = "http://127.0.0.1:4310"
DIGEST_TEST = "test_pilot_digest_order.py"


def cmd(*args, env=None):
    return subprocess.check_output(["docker", *args], env=env, text=True, timeout=10).strip()


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def preflight(env, *, finishing=False):
    state = json.loads(cmd("inspect", PREVIEW, env=env))[0]
    require(state["State"]["Running"] and state["Config"]["Image"] == PREVIEW_IMAGE,
            "Expected the running pinned Paperclip preview")
    network_settings = state["NetworkSettings"]
    require(set(network_settings["Networks"]) == {PREVIEW} and not state["Mounts"],
            "Expected only the preview network and no mounts")
    published = {port: binds for port, binds in network_settings["Ports"].items() if binds}
    require(published == {"4310/tcp": [{"HostIp": "127.0.0.1", "HostPort": "4310"}]},
            "Expected only the localhost dashboard port")
    network = json.loads(cmd("network", "inspect", PREVIEW, env=env))[0]
    require(network["Driver"] == "bridge" and set(network["Containers"]) == {state["Id"]},
            "Expected a dedicated preview bridge with no other members")
    env_names = {item.split("=", 1)[0] for item in state["Config"]["Env"]}
    require(not env_names.intersection({"OPENROUTER_API_KEY", "GH_TOKEN", "GITHUB_TOKEN"}),
            "Unexpected provider or GitHub credential in preview environment")

    def local(*args):
        return cmd("exec", "-u", "node", PREVIEW, *args, env=env)

    require(local("git", "-C", REPO, "remote", "get-url", "origin") == ORIGIN,
            "Expected the public ralph-canary checkout")
    require(local("git", "-C", REPO, "remote", "get-url", "--push", "--all", "origin") == "disabled://read-only",
            "Expected the ralph-canary origin push URL to be disabled")
    require(not local("git", "-C", REPO, "status", "--porcelain"), "Expected a clean ralph-canary checkout")
    task = local("sh", "-ec", 'if test -f "$1"; then cat "$1"; fi', "probe", "/paperclip/pilot-next-task")
    require(re.fullmatch(r"[A-Z][A-Z0-9]*-[1-9][0-9]*", task), "No valid prepared Paperclip task found")
    marker = f"/paperclip/pilot-finished-{task}"
    require(finishing or not local("sh", "-ec", 'if test -e "$1"; then echo finished; fi', "probe", marker),
            "This prepared task already reported completion")
    return task, marker


def finish_issue(board, company_id, parent_id, developer_id, issue, verify):
    path = f"issues/{issue['id']}"

    def check_scope(row):
        require((row["companyId"], row["parentId"], row["assigneeAgentId"]) ==
                (company_id, parent_id, developer_id), "Child is outside the expected sandbox assignment")
        require(row["status"] in {"in_progress", "done"}, "Child is not in a finishable state")

    check_scope(issue)
    runs = board(f"{path}/runs")
    assignments = [run for run in runs if run["agentId"] == developer_id
                   and run["invocationSource"] == "assignment"]
    require(len(assignments) == 1 and assignments[0]["status"] == "succeeded",
            "Expected exactly one successful OpenCode assignment")
    require(not any(run["status"] in {"running", "queued"} for run in runs), "Issue still has active runs")
    verify(issue["identifier"])
    current = board(path)
    check_scope(current)
    if current["status"] != "done":
        updated = board(path, {"status": "done", "comment": f"Launcher verified: {DIGEST_TEST} passed."}, "PATCH")
        require(updated["status"] == "done", "Paperclip did not record completion")


def verify_digest_worktree(identifier, env):
    require(re.fullmatch(r"[A-Z][A-Z0-9]*-[1-9][0-9]*", identifier), "Unexpected child issue identifier")

    def local(*args):
        return cmd("exec", "-u", "node", PREVIEW, *args, env=env)

    trees = local("git", "-C", REPO, "worktree", "list", "--porcelain").splitlines()
    matches = [line[9:] for line in trees if line.startswith(f"worktree /paperclip/pilot-worktrees/{identifier}-")]
    require(len(matches) == 1, "Expected one issue worktree")
    cwd = matches[0]
    base = local("git", "-C", REPO, "rev-parse", "refs/heads/main")
    require(local("git", "-C", cwd, "rev-parse", "HEAD") == base, "Issue worktree is not at the sandbox base")
    require(not local("git", "-C", cwd, "diff", "--name-only", "HEAD"), "Unexpected tracked change in worktree")
    require(local("git", "-C", cwd, "ls-files", "--others", "--exclude-standard", "--", DIGEST_TEST) == DIGEST_TEST,
            "Expected a new digest regression test")
    test = local("sh", "-ec", 'test -f "$1" && test ! -L "$1" && head -c 16385 "$1"',
                 "verify", f"{cwd}/{DIGEST_TEST}")
    require(bool(test) and len(test.encode()) <= 16384, "Digest test file is empty or too large")
    files = json.dumps({DIGEST_TEST: test, "src/digest.py": local("git", "-C", REPO, "show", f"{base}:src/digest.py")})
    script = ("import json, pathlib, sys, unittest\n"
              "pathlib.Path('src').mkdir()\n"
              "for name, text in json.load(sys.stdin).items(): pathlib.Path(name).write_text(text)\n"
              "from src.digest import render\n"
              "if render([{'title':'Zulu'}, {'title':'Alpha'}]) != '2 waiting on you\\n- Zulu\\n- Alpha': sys.exit(1)\n"
              "suite = unittest.defaultTestLoader.loadTestsFromName('test_pilot_digest_order')\n"
              "sys.exit(not (suite.countTestCases() and unittest.TextTestRunner().run(suite).wasSuccessful()))")
    subprocess.run(["docker", "run", "--rm", "--pull", "never", "-i", "--network", "none", "--read-only",
                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "node",
                    "--tmpfs", "/verify:rw,noexec,nosuid,nodev,mode=1777,size=1m", "-w", "/verify",
                    "--entrypoint", "/usr/bin/timeout", IMAGE, "--kill-after=1s", "15",
                    "/usr/bin/python3", "-B", "-c", script],
                   input=files, text=True, env=env, timeout=20, check=True)


def finish_digest_pilot(task, marker, company_id, env):
    def api(path, body=None, method=None, token=None):
        headers = {"Content-Type": "application/json", "Origin": DASHBOARD}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(f"{DASHBOARD}/api/{path}", method=method, headers=headers,
                          data=None if body is None else json.dumps(body).encode())
        with urlopen(request, timeout=10) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    challenge = api("cli-auth/challenges", {"command": f"Verify and record {task} sandbox completion",
        "clientName": "Ralph sandbox verified completion", "requestedAccess": "board", "requestedCompanyId": company_id})
    approval = challenge["approvalPath"]
    require(approval.startswith("/cli-auth/"), "Unexpected dashboard approval path")
    subprocess.run(["open", DASHBOARD + approval], env=env, check=True)
    poll = f"cli-auth/challenges/{challenge['id']}?" + urlencode({"token": challenge["token"]})
    print("Approve Ralph sandbox verified completion in Paperclip (no model calls).", flush=True)
    for _ in range(90):
        status = api(poll)["status"]
        if status == "approved":
            break
        require(status == "pending", f"Paperclip authorization: {status}")
        time.sleep(2)
    else:
        raise SystemExit("Paperclip authorization was not approved within three minutes")

    token = challenge["boardApiToken"]

    def board(path, body=None, method=None):
        return api(path, body, method, token)

    try:
        company = board(f"companies/{company_id}")
        require(company["name"] == "Ralph Sandbox Pilot", "Not the Ralph sandbox company")
        parent = board(f"issues/{task}")
        require(parent["companyId"] == company_id and parent["status"] == "done",
                "Expected the completed sandbox intake")
        agents = board(f"companies/{company_id}/agents")
        require(all(agent["status"] == "paused" for agent in agents), "Pause the sandbox agents before finishing")
        developer, = [agent for agent in agents if agent["adapterType"] == "opencode_local"]
        children = [issue for issue in board(f"companies/{company_id}/issues") if issue["parentId"] == parent["id"]]
        require(len(children) == 1 and children[0]["title"] == "Add digest title order regression test",
                "Expected one digest regression task")
        finish_issue(board, company_id, parent["id"], developer["id"], children[0],
                     lambda identifier: verify_digest_worktree(identifier, env))
        cmd("exec", "-u", "node", PREVIEW, "touch", marker, env=env)
        print(f"{children[0]['identifier']}: test verified and Done recorded in Paperclip", flush=True)
    finally:
        board("cli-auth/revoke-current", {})
        print("Temporary board authorization revoked.", flush=True)


def wait_for_finish(task, marker, process, env):
    endpoint = f"http://{RELAY}:8765/v1/models"
    models = cmd("exec", "-u", "node", PREVIEW, "curl", "--retry", "3", "--retry-connrefused",
                 "--retry-delay", "1", "--max-time", "2", "-fsS", endpoint, env=env)
    require([model["id"] for model in json.loads(models)["data"]] == ["ralph-pilot"],
            "The relay did not advertise the expected model alias")
    print(f"Relay ready. Launch {task} in Paperclip now; the window is at most {WINDOW} seconds.", flush=True)
    while process.poll() is None:
        if cmd("exec", "-u", "node", PREVIEW, "sh", "-ec",
               'if test -e "$1"; then echo finished; fi', "probe", marker, env=env):
            print(f"{task} reported completion. Check the result in Paperclip.", flush=True)
            return
        time.sleep(2)
    raise SystemExit("Relay exited before completion; check the task in Paperclip before retrying")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="Check the prepared task without using a provider key.")
    modes.add_argument("--finish-digest-pilot", metavar="SANDBOX_COMPANY_ID",
                       help="Verify a settled digest order task and record Done without using a provider key.")
    args = parser.parse_args()
    env = {name: os.environ[name] for name in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
           if name in os.environ}
    task, marker = preflight(env, finishing=bool(args.finish_digest_pilot))
    if args.check:
        print(f"Prepared task: {task}")
        return
    if args.finish_digest_pilot:
        require(re.fullmatch(r"[0-9a-f-]{36}", args.finish_digest_pilot), "Expected the sandbox company UUID")
        finish_digest_pilot(task, marker, args.finish_digest_pilot, env)
        return
    key = os.environ.pop("OPENROUTER_API_KEY", "")
    if not key or "\n" in key or "\r" in key:
        parser.error("Set OPENROUTER_API_KEY in the launcher environment")
    relay_script = Path(__file__).with_name("pilot-model-relay.py").read_text()
    cid = None
    try:
        # Own the exact ID before starting, so a conflicting name is never stopped.
        cid = cmd("create", "--pull", "never", "-i", "--name", RELAY, "--network", PREVIEW,
                  "--user", "node", "--read-only", "--cap-drop", "ALL",
                  "--security-opt", "no-new-privileges", "-e", f"RALPH_PILOT_MODEL={MODEL}",
                  "--entrypoint", "/usr/bin/timeout", IMAGE, "--kill-after=2s", str(WINDOW),
                  "/usr/bin/python3", "-c", relay_script, env=env)
        with subprocess.Popen(["docker", "start", "-ai", cid], env=env,
                              stdin=subprocess.PIPE, text=True) as process:
            try:
                process.stdin.write(key + "\n")
                process.stdin.close()
                del key
                wait_for_finish(task, marker, process, env)
            finally:
                cmd("stop", "-t", "2", cid, env=env)
    finally:
        if cid is not None:
            cmd("rm", cid, env=env)


if __name__ == "__main__":
    main()
