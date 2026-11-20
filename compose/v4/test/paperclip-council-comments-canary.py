"""Exercise native Council dependencies and natural-language handoffs on a disposable board.

The seats are scripted process fixtures with a preselected reaction: this verifies Paperclip
routing and attribution, not an LLM's interpretation of the comments. The dissent case chooses
the fixture reaction explicitly rather than parsing the comment.
"""
import json
import runpy
import secrets
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RFC = runpy.run_path(str(HERE / "paperclip-rfc-canary.py"))
STAGE = runpy.run_path(str(HERE / "paperclip-rfc-review-stage-canary.py"))
request = RFC["request"]
fixture = RFC["authenticated_fixture"]

JUDGE = r"""
set -e
pc() {
  curl -fsS --max-time 10 -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
    -H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID" "$@"
}
issue=$(pc "$PAPERCLIP_API_URL/api/heartbeat-runs/$PAPERCLIP_RUN_ID" | jq -r '.issueId')
sleep 3
comment=COMMENT_LITERAL
payload=$(jq -nc --arg comment "$comment" '{status:"done",comment:$comment}')
pc --json "$payload" -X PATCH "$PAPERCLIP_API_URL/api/issues/$issue" >/dev/null
"""

MANAGER = r"""
set -e
pc() {
  curl -fsS --max-time 10 -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
    -H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID" "$@"
}
issue=$(pc "$PAPERCLIP_API_URL/api/heartbeat-runs/$PAPERCLIP_RUN_ID" | jq -r '.issueId')
marker="/tmp/council-deps-$issue"
if [ ! -f "$marker" ]; then
  revision=$(pc "$PAPERCLIP_API_URL/api/issues/$issue/documents/rfc" | jq -r '.latestRevisionId')
  for judge in JUDGES; do
    payload=$(jq -nc --arg issue "$issue" --arg judge "$judge" --arg revision "$revision" \
      '{title:("Council fixture "+$judge),
        description:("Review RFC "+$issue+" revision "+$revision+" and explain your conclusion."),
        status:"backlog",parentId:$issue,assigneeAgentId:$judge,
        idempotencyKey:("council:"+$issue+":"+$judge)}')
    created=$(pc --json "$payload" "$PAPERCLIP_API_URL/api/companies/$PAPERCLIP_COMPANY_ID/issues")
    printf '%s\n' "$created" | jq -r '.id' >> "$marker"
  done
  blockers=$(jq -R . < "$marker" | jq -s .)
  payload=$(jq -nc --argjson blockers "$blockers" '{blockedByIssueIds:$blockers}')
  updated=$(pc --json "$payload" -X PATCH "$PAPERCLIP_API_URL/api/issues/$issue")
  printf '%s' "$updated" | jq -e '.status == "in_review" and (.blockedByIssueIds|length)==3' >/dev/null
  while IFS= read -r judge_issue; do
    pc --json '{"status":"todo"}' -X PATCH "$PAPERCLIP_API_URL/api/issues/$judge_issue" >/dev/null
  done < "$marker"
else
  while IFS= read -r judge_issue; do
    judge=$(pc "$PAPERCLIP_API_URL/api/issues/$judge_issue")
    printf '%s' "$judge" | jq -e '.status=="done"' >/dev/null
    author=$(printf '%s' "$judge" | jq -r '.assigneeAgentId')
    pc "$PAPERCLIP_API_URL/api/issues/$judge_issue/comments" | jq -e --arg author "$author" \
      'any(.[]; .authorAgentId==$author and (.body|length)>0)' >/dev/null
  done < "$marker"
  if [ DECISION_LITERAL = "revise" ]; then
    pc --json '{"status":"todo","comment":"Please address the security concern from Council."}' \
      -X PATCH "$PAPERCLIP_API_URL/api/issues/$issue" >/dev/null
  else
    doc=$(pc "$PAPERCLIP_API_URL/api/issues/$issue/documents/rfc")
    payload=$(printf '%s' "$doc" | jq -c --arg issue "$issue" --arg human DIRECTOR \
      '{kind:"request_confirmation",resolverPolicy:"human_only",addresseeUserId:$human,
        continuationPolicy:"wake_assignee_on_accept",idempotencyKey:("approval:"+.latestRevisionId),
        payload:{version:1,prompt:"Approve this fixture RFC?",
          target:{type:"issue_document",issueId:$issue,key:"rfc",documentId:.id,
            revisionId:.latestRevisionId,revisionNumber:.latestRevisionNumber}}}')
    pc --json "$payload" "$PAPERCLIP_API_URL/api/issues/$issue/interactions" >/dev/null
  fi
fi
"""


def check_council_comments(container, *, dissent=False):
    actual = subprocess.check_output(
        ["docker", "inspect", container, "--format", "{{.Config.Image}}"], text=True
    ).strip()
    assert actual == fixture.__wrapped__.__globals__["IMAGE"]

    def board(path, body=None, method="POST"):
        return request(container, "director", path, body, method)[1]

    board("auth/sign-up/email", {
        "name": "director", "email": "director@fixture.invalid", "password": secrets.token_hex(16),
    })
    board("bootstrap/claim", {})
    director = board("auth/get-session")["user"]["id"]
    company = board("companies", {"name": "Council dependency fixture"})
    root = f"companies/{company['id']}"
    opinions = (
        "The scope fits; I'm happy to proceed.",
        "Architecture looks sound. I approve the current revision.",
        ("I cannot support this yet: the authorization gap needs a fix." if dissent
         else "I found no security objection; proceed to the Director."),
    )
    judges = [
        board(f"{root}/agents", {
            "name": f"Judge {index}", "adapterType": "process",
            "adapterConfig": {
                "command": "/bin/sh", "args": ["-ec", JUDGE.replace("COMMENT_LITERAL", json.dumps(opinion))],
            },
        })
        for index, opinion in enumerate(opinions)
    ]
    script = MANAGER.replace("JUDGES", " ".join(judge["id"] for judge in judges)).replace(
        "DIRECTOR", json.dumps(director)
    ).replace("DECISION_LITERAL", json.dumps("revise" if dissent else "approve"))
    manager = board(f"{root}/agents", {
        "name": "Manager", "adapterType": "process",
        "adapterConfig": {"command": "/bin/sh", "args": ["-ec", script]},
        "runtimeConfig": {"heartbeat": {"maxConcurrentRuns": 1}},
    })
    author = board(f"{root}/agents", {
        "name": "Author", "adapterType": "process",
        "adapterConfig": {"command": "/bin/sh", "args": ["-ec", STAGE["AUTHOR_SCRIPT"]]},
    })
    for seat in [*judges, manager, author]:
        STAGE["wait_ready"](board, seat["id"])
    board(f"agents/{manager['id']}/permissions", {
        "canCreateAgents": False, "canCreateSkills": False, "canAssignTasks": True,
    }, "PATCH")
    parent = board(f"{root}/issues", {"title": "Root", "status": "backlog"})
    stage = {"type": "review", "participants": [{"type": "agent", "agentId": manager["id"]}]}
    issue = board(f"{root}/issues", {
        "title": "Fixture RFC", "status": "todo", "parentId": parent["id"],
        "assigneeAgentId": author["id"], "executionPolicy": {"stages": [stage]},
    })
    path = f"issues/{issue['id']}"
    deadline = time.monotonic() + 110
    while time.monotonic() < deadline:
        issue = board(path)
        cards = board(f"{path}/interactions")
        state = issue.get("executionState") or {}
        changed = dissent and state.get("lastDecisionOutcome") == "changes_requested"
        if cards or issue["status"] == "blocked" or changed:
            break
        time.sleep(1)
    if not cards and not (dissent and state.get("lastDecisionOutcome") == "changes_requested"):
        for run in board(f"{path}/runs")[:6]:
            detail = board(f"heartbeat-runs/{run['runId']}")
            print("Run:", detail["status"], detail.get("errorCode"), (detail.get("stdoutExcerpt") or "")[-1000:])
        raise AssertionError(f"No native confirmation; source status {issue['status']}")

    panel = [item for item in board(f"{root}/issues") if item["title"].startswith("Council fixture ")]
    assert len(panel) == 3 and all(
        item["status"] == "done" and item["createdByAgentId"] == manager["id"] for item in panel
    )
    assert all(item["parentId"] == parent["id"] and item["privacyParentIssueId"] == issue["id"] for item in panel)
    assert {item["id"] for item in issue["blockedBy"]} == {item["id"] for item in panel}
    document = board(f"{path}/documents/rfc")
    for task in panel:
        assert document["latestRevisionId"] in task["description"]
        comments = board(f"issues/{task['id']}/comments")
        assert any(comment.get("authorAgentId") == task["assigneeAgentId"] and comment["body"] in opinions
                   for comment in comments)
        assert board(f"issues/{task['id']}/documents") == []
    if dissent:
        assert not cards and issue["status"] == "in_progress" and issue["assigneeAgentId"] == author["id"]
        assert any(comment.get("authorAgentId") == manager["id"] and "security concern" in comment["body"]
                   for comment in board(f"{path}/comments"))
    else:
        assert issue["status"] == "in_review" and issue["assigneeAgentId"] == manager["id"]
        assert len(cards) == 1 and cards[0]["createdByAgentId"] == manager["id"] and cards[0]["status"] == "pending"
        assert cards[0]["resolverPolicy"] == "human_only" and cards[0]["addresseeUserId"] == director
        assert cards[0]["payload"]["target"]["revisionId"] == document["latestRevisionId"]
    manager_runs = [
        board(f"heartbeat-runs/{run['runId']}")
        for run in board(f"{path}/runs") if run["agentId"] == manager["id"]
    ]
    reasons = [(run.get("contextSnapshot") or {}).get("wakeReason") for run in manager_runs]
    assert "issue_blockers_resolved" in reasons, reasons
    print("PASS: attributed natural-language comments release the manager's native review dependency;",
          "dissent returns to the Author." if dissent else "support reaches a Director confirmation.")


if __name__ == "__main__":
    for dissent in (False, True):
        with fixture("council-comments") as container:
            check_council_comments(container, dissent=dissent)
