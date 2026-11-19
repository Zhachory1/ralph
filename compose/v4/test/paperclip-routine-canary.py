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
AUDIT = """
import { auditPr } from "./coding-harness/audit-pr.js";
try {
  console.log(JSON.stringify(await auditPr(JSON.parse(process.argv[1]))));
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
        return board(f"{route}/run", {"idempotencyKey": f"pr-1-{head}", "payload": {"head_sha": head}})

    def audit(head):
        return subprocess.run(
            ["node", "--input-type=module", "-e", AUDIT,
             json.dumps({"repo_path": str(repo), "pr_number": 1, "head_sha": head})],
            cwd=HERE.parents[2], env=env, text=True, capture_output=True, timeout=120,
        )

    def publish(run, head, result):
        assert run["triggerPayload"]["head_sha"] == head
        if git("ls-remote", "origin", "refs/pull/1/head").split()[0] != head:
            return False
        board(f"issues/{run['linkedIssueId']}/documents/audit", {
            "format": "markdown", "body": f"Audit for `{head}`\n\n```json\n{json.dumps(result)}\n```\n",
        }, "PUT")
        return True

    first_head = new_head()
    first = trigger(first_head)
    duplicate = trigger(first_head)
    assert first["linkedIssueId"] and duplicate["id"] == first["id"], (first, duplicate)
    assert duplicate["linkedIssueId"] == first["linkedIssueId"]
    assert [row["id"] for row in board(f"{route}/runs")] == [first["id"]]
    print("PASS: duplicate manual trigger produced one Routine run and one linked issue")

    first_result = audit(first["triggerPayload"]["head_sha"])
    assert first_result.returncode == 0, first_result.stderr
    report = json.loads(first_result.stdout)
    assert report["tests_passed"] and not report["lint_issues"] and report["changed_files"] == ["fixture.txt"]
    second_head = new_head()
    assert not publish(first, first_head, report), "Stale audit was published after head changed"
    assert not board(f"issues/{first['linkedIssueId']}/documents")
    rejected = audit(first_head)
    assert rejected.returncode != 0 and "PR head SHA mismatch" in rejected.stderr
    board(f"issues/{first['linkedIssueId']}", {"status": "done"}, "PATCH")
    assert trigger(first_head)["id"] == first["id"], "Completed run was duplicated"

    second = trigger(second_head)
    assert second["id"] != first["id"] and second["linkedIssueId"] != first["linkedIssueId"]
    second_result = audit(second["triggerPayload"]["head_sha"])
    assert second_result.returncode == 0, second_result.stderr
    report = json.loads(second_result.stdout)
    assert report["tests_passed"] and not report["lint_issues"]
    assert publish(second, second_head, report)
    stored = board(f"issues/{second['linkedIssueId']}/documents/audit")
    assert f"Audit for `{second_head}`" in stored["body"] and first_head not in stored["body"]
    assert len(board(f"{route}/runs")) == 2
    print("PASS: old head rejected, new head audited in isolation and stored only in Paperclip")


if __name__ == "__main__":
    with TemporaryDirectory(prefix="ralph-pr-fixture-") as directory:
        with FIXTURE["authenticated_fixture"]("routine") as container:
            check_routine(container, Path(directory).resolve())
