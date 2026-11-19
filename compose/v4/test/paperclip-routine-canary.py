"""Probe manual Routine deduplication and exact-head audit dossiers offline."""

import json
import os
import runpy
import secrets
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
FIXTURE = runpy.run_path(str(HERE / "paperclip-rfc-canary.py"))
IMAGE = ("ghcr.io/paperclipai/paperclip:2026.1117.0"
         "@sha256:bdf38d10544dc6c831e16dc027f23ad586353ef3be1a1b2c6979e63b956aceb1")
WORKER = """
import { spawnSync } from "node:child_process";
import { auditPr } from "./coding-harness/audit-pr.js";
import { runRoutineAudit } from "./compose/v4/routine-audit-worker.mjs";
const { container, origin, ...input } = JSON.parse(process.argv[1]);
function call(command, args, options) {
  const result = spawnSync(command, args, { encoding: "utf8", timeout: 15000, ...options });
  if (result.status !== 0) throw new Error(`${command} fixture command failed (${result.status})`);
  return result.stdout;
}
function board(path, method = "GET", body) {
  const args = body === undefined ? [] : ["--json", "@-"];
  return JSON.parse(call("docker", ["exec", "-i", container, "curl", "-fsS", "--max-time", "10",
    "-b", "/tmp/rfc-director.cookies", "-H", `Origin: ${origin}`, "-X", method,
    ...args, `${origin}/api/${path}`], { input: body === undefined ? "" : JSON.stringify(body) }));
}
let report;
try {
  const status = await runRoutineAudit({
    ...input, board,
    currentHead(pr) {
      return call("git", ["ls-remote", "origin", `refs/pull/${pr}/head`], { cwd: input.repoPath }).split(/\\s+/)[0];
    },
    async audit(request) { return report = await auditPr(request); },
  });
  console.log(JSON.stringify({ status, report }));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
"""


def check_routine(container, repo):
    def board(path, body=None, method="POST"):
        return FIXTURE["request"](container, "director", path, body, method)[1]

    board("auth/sign-up/email", {
        "name": "Director", "email": "director@fixture.invalid", "password": secrets.token_hex(16),
    })
    board("bootstrap/claim", {})
    company = board("companies", {"name": "Audit canary"})
    company_path = f"companies/{company['id']}"
    worker = board(f"{company_path}/agents", {
        "name": "Disposable auditor", "adapterType": "process", "adapterConfig": {"command": "/bin/true"},
    })
    routine = board(f"{company_path}/routines", {
        "title": "Read-only PR dossier", "assigneeAgentId": worker["id"],
        "concurrencyPolicy": "coalesce_if_active",
    })
    route = f"routines/{routine['id']}"

    env = {key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
           if key in os.environ}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0")
    env["RALPH_AUDIT_REPOS"] = json.dumps({str(repo): {
        "image": IMAGE,
        "test": 'test "$(id -u)" = 65534 && test "$(ls /sys/class/net)" = lo && '
                'test ! -e .git && test ! -e .env && test -z "${GH_TOKEN:-}${GITHUB_TOKEN:-}"',
        "lint": "grep -qx 'fixture' fixture.txt",
    }})

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, env=env, text=True, timeout=10).strip()

    git("init", "--template=", "-b", "main")
    git("config", "user.name", "Audit Fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("commit", "--allow-empty", "-m", "Base")
    base = git("rev-parse", "HEAD")
    git("remote", "add", "origin", str(repo))
    (repo / ".env").write_text("SYNTHETIC_FIXTURE=1\n")

    def new_head():
        (repo / "fixture.txt").write_text("fixture\n")
        git("add", "fixture.txt")
        git("commit", "--allow-empty", "-m", "PR revision")
        head = git("rev-parse", "HEAD")
        merge = git("commit-tree", "HEAD^{tree}", "-p", base, "-p", head, "-m", "Synthetic merge")
        git("update-ref", "refs/pull/1/head", head)
        git("update-ref", "refs/pull/1/merge", merge)
        return head

    def trigger(head):
        return board(f"{route}/run", {
            "source": "manual", "idempotencyKey": f"pr-1-{head}",
            "payload": {"repo": "fixture/canary", "pr_number": 1, "head_sha": head},
        })

    def audit(run):
        result = subprocess.run(
            ["node", "--input-type=module", "-e", WORKER, json.dumps({
                "container": container, "origin": FIXTURE["ORIGIN"], "companyId": company["id"],
                "routineId": routine["id"], "runId": run["id"], "repoPath": str(repo), "repoName": "fixture/canary",
            })], cwd=HERE.parents[2], env=env, text=True, capture_output=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    first_head = new_head()
    first = trigger(first_head)
    duplicate = trigger(first_head)
    assert first["linkedIssueId"] and duplicate["id"] == first["id"], (first, duplicate)
    assert duplicate["linkedIssueId"] == first["linkedIssueId"]
    assert [row["id"] for row in board(f"{route}/runs")] == [first["id"]]
    print("PASS: duplicate manual trigger produced one Routine run and one linked issue")

    second_head = new_head()
    assert audit(first) == {"status": "stale"}, "Stale audit was published after head changed"
    assert not board(f"issues/{first['linkedIssueId']}/documents")
    board(f"issues/{first['linkedIssueId']}", {"status": "done"}, "PATCH")
    assert trigger(first_head)["id"] == first["id"], "Completed run was duplicated"

    second = trigger(second_head)
    assert second["id"] != first["id"] and second["linkedIssueId"] != first["linkedIssueId"]
    second_result = audit(second)
    assert second_result["status"] == "published"
    report = second_result["report"]
    assert report["tests_passed"] and not report["lint_issues"] and report["changed_files"] == ["fixture.txt"]
    stored = board(f"issues/{second['linkedIssueId']}/documents/audit")
    assert stored["body"].startswith(f"Audit for `fixture/canary#1` at `{second_head}`\n")
    assert first_head not in stored["body"]
    assert audit(second) == {"status": "already_published"}
    assert len(board(f"{route}/runs")) == 2
    third_head = new_head()
    assert audit(second) == {"status": "superseded"}
    status_path = f"issues/{second['linkedIssueId']}/documents/audit-status"
    status = board(status_path)
    assert third_head in status["body"]
    assert audit(second) == {"status": "superseded"}
    assert board(status_path)["latestRevisionId"] == status["latestRevisionId"]
    assert board(f"issues/{second['linkedIssueId']}/documents/audit")["body"] == stored["body"]
    print("PASS: current head audited in isolation; stale dossier preserved and marked once in Paperclip")


if __name__ == "__main__":
    with TemporaryDirectory(prefix="ralph-pr-fixture-") as directory:
        with FIXTURE["authenticated_fixture"]("routine") as container:
            check_routine(container, Path(directory).resolve())
