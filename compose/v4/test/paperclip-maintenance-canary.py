"""Exercise the outbound-maintenance worker's real Paperclip board path, offline.

Disposable, authenticated, network-isolated container only (see authenticated_fixture);
no production company, no live GitHub write, no scheduling. Proves: a missing
outbound-pr/maintenance document reads as a real 404 (not swallowed as any other error), a
wake posts exactly one comment and commits the maintenance document via baseRevisionId, a
redelivered event is deduped without a second comment, the PDF's cap of two wake attempts is
enforced before escalating, and an escalation creates a human_only interaction addressed to
the Director -- never a second effect once escalated.
"""

import json
import runpy
import secrets
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
FIXTURE = runpy.run_path(str(HERE / "paperclip-rfc-canary.py"))
request = FIXTURE["request"]
authenticated_fixture = FIXTURE["authenticated_fixture"]

WORKER = """
import { spawnSync } from "node:child_process";
import { createPaperclipDeps, runOutboundMaintenance } from "./compose/v4/routine-maintenance-worker.mjs";
const { container, origin, cookieJar, botLogin, directorUserId, event } = JSON.parse(process.argv[1]);
function board(path, method = "GET", body) {
  const args = ["exec", "-i", container, "curl", "-sS", "--max-time", "10",
    "-b", cookieJar, "-c", cookieJar, "-H", `Origin: ${origin}`, "-w", "\\n%{http_code}",
    "-X", method, ...(body === undefined ? [] : ["--json", "@-"]), `${origin}/api/${path}`];
  const result = spawnSync("docker", args, { encoding: "utf8", timeout: 15000,
    input: body === undefined ? "" : JSON.stringify(body) });
  if (result.status !== 0) throw new Error(`docker exec failed: ${result.stderr}`);
  const at = result.stdout.lastIndexOf("\\n");
  const status = Number(result.stdout.slice(at + 1));
  const text = result.stdout.slice(0, at);
  if (status < 200 || status >= 300) {
    throw Object.assign(new Error(`Paperclip ${method} ${path} failed (${status})`), { status });
  }
  return text ? JSON.parse(text) : null;
}
const deps = createPaperclipDeps(board, { botLogin, directorUserId });
console.log(JSON.stringify(await runOutboundMaintenance(event, deps)));
"""


def run_worker(container, origin, bot_login, director_id, event):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", WORKER, json.dumps({
            "container": container, "origin": origin, "cookieJar": "/tmp/rfc-director.cookies",
            "botLogin": bot_login, "directorUserId": director_id, "event": event,
        })],
        cwd=REPO_ROOT, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def check_maintenance(container):
    origin = FIXTURE["ORIGIN"]
    bot_login = "ralph-bot"

    def director(path, body=None, method="POST"):
        return request(container, "director", path, body, method)[1]

    request(container, "director", "auth/sign-up/email", {
        "name": "director", "email": "director@fixture.invalid", "password": secrets.token_hex(16),
    })
    director("bootstrap/claim", {})
    company = director("companies", {"name": "Outbound maintenance canary"})
    company_path = f"companies/{company['id']}"
    director_id = director("auth/get-session")["user"]["id"]

    def make_tracked_issue(repo, pr_number, title):
        issue = director(f"{company_path}/issues", {"title": title, "status": "backlog"})
        director(f"issues/{issue['id']}/documents/outbound-pr", {
            "format": "markdown", "body": json.dumps({"repo": repo, "pr_number": pr_number}),
        }, "PUT")
        return issue["id"]

    head = "a" * 40
    repo, pr_number = "fixture/outbound", 3
    issue_id = make_tracked_issue(repo, pr_number, "Outbound PR fixture/outbound#3")

    def event(event_type, event_id, author="a-human"):
        return {"issueId": issue_id, "repo": repo, "pr_number": pr_number, "head_sha": head,
                "event_type": event_type, "event_id": event_id, "author_login": author}

    untracked_id = director(f"{company_path}/issues", {"title": "Not yet linked to a PR", "status": "backlog"})["id"]
    out_of_scope = run_worker(container, origin, bot_login, director_id,
                               {**event("ci_failure", "check_suite:0"), "issueId": untracked_id})
    assert out_of_scope == {"status": "out_of_scope"}, out_of_scope
    print("PASS: a missing outbound-pr document (a real 404) reads as out_of_scope, not a reset")

    first = run_worker(container, origin, bot_login, director_id, event("ci_failure", "check_suite:1"))
    assert first == {"status": "woke", "wakeCount": 1}, first
    comments = director(f"issues/{issue_id}/comments", None, "GET")
    assert len(comments) == 1 and "check_suite:1" in comments[0]["body"], comments
    state_doc = director(f"issues/{issue_id}/documents/maintenance", None, "GET")
    state = json.loads(state_doc["body"])
    assert state == {"processedEventIds": ["check_suite:1"], "wakeCount": 1, "escalated": False}, state
    print("PASS: a wake posts exactly one comment and commits the maintenance document")

    replay = run_worker(container, origin, bot_login, director_id, event("ci_failure", "check_suite:1"))
    assert replay == {"status": "duplicate"}, replay
    assert director(f"issues/{issue_id}/comments", None, "GET") == comments, "duplicate wake posted a second comment"
    assert director(f"issues/{issue_id}/documents/maintenance", None, "GET")["latestRevisionId"] == state_doc["latestRevisionId"]
    print("PASS: a redelivered event is deduped -- no second comment, no document write")

    second = run_worker(container, origin, bot_login, director_id, event("ci_failure", "check_suite:2"))
    assert second == {"status": "woke", "wakeCount": 2}, second
    third = run_worker(container, origin, bot_login, director_id, event("ci_failure", "check_suite:3"))
    assert third == {"status": "escalated", "reason": "wake_attempts_exhausted"}, third
    assert len(director(f"issues/{issue_id}/comments", None, "GET")) == 2, "escalation must not also wake"
    interactions = director(f"issues/{issue_id}/interactions", None, "GET")
    (escalation,) = [row for row in interactions if row["idempotencyKey"].endswith("check_suite:3")]
    assert escalation["kind"] == "request_confirmation"
    assert escalation["resolverPolicy"] == "human_only"
    assert escalation["addresseeUserId"] == director_id
    print("PASS: the PDF's cap of two wake attempts is enforced, then the Director is notified")

    again = run_worker(container, origin, bot_login, director_id, event("ci_failure", "check_suite:4"))
    assert again == {"status": "already_escalated"}, again
    assert len(director(f"issues/{issue_id}/comments", None, "GET")) == 2
    assert len(director(f"issues/{issue_id}/interactions", None, "GET")) == 1
    print("PASS: no further wake or escalation once already waiting on the Director")

    drift_repo, drift_pr = "fixture/outbound", 5
    drift_issue = make_tracked_issue(drift_repo, drift_pr, "Outbound PR fixture/outbound#5")
    drift_event = {"issueId": drift_issue, "repo": drift_repo, "pr_number": drift_pr, "head_sha": head,
                   "event_type": "base_drift", "event_id": "base_drift:1", "author_login": "a-human"}
    drift = run_worker(container, origin, bot_login, director_id, drift_event)
    assert drift == {"status": "escalated", "reason": "base_drift"}, drift
    assert director(f"issues/{drift_issue}/comments", None, "GET") == []
    print("PASS: base drift escalates to the Director without ever waking")

    own_reply = run_worker(container, origin, bot_login, director_id, event("ci_failure", "check_suite:5", author=bot_login))
    assert own_reply == {"status": "own_reply"}, own_reply
    assert len(director(f"issues/{issue_id}/comments", None, "GET")) == 2, "an own reply must never be acted on"
    print("PASS: the worker's own reply is never acted on")


if __name__ == "__main__":
    with authenticated_fixture("maintenance") as container:
        check_maintenance(container)
