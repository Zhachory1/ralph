"""Exercise native wake_assignee_on_accept against a disposable authenticated board.

Validates the #290 contract (docs/workflows.md "Librarian", compose/v4/provision-paperclip.py
Feature Workflow Manager seat): Paperclip wakes the *current assignee* when the Director
accepts the exact RFC revision confirmation, that wake runs under the assignee's own
run-scoped credentials (not the Director's or an operator's), and the resulting Librarian
assignment is created with idempotencyKey publish-rfc:<confirmation-id> so a replay does
not create a second assignment. Both the manager and the Librarian are scripted `process`
fixtures: this probes the native trigger and idempotency, not an LLM's judgment.
The Librarian fixture reads the Manager's handoff without accessing the approval endpoint;
it does not publish a PR.
"""

import runpy
import secrets
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = runpy.run_path(str(HERE / "paperclip-rfc-canary.py"))
BOARD_KEY = runpy.run_path(str(HERE / "paperclip-board-key-canary.py"))
request = FIXTURE["request"]
authenticated_fixture = FIXTURE["authenticated_fixture"]
bearer = BOARD_KEY["bearer"]

MANAGER_SCRIPT = r"""
set -e
pc() { curl -fsS --max-time 5 -H "Authorization: Bearer $PAPERCLIP_API_KEY" -H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID" "$@"; }
run=$(pc "$PAPERCLIP_API_URL/api/heartbeat-runs/$PAPERCLIP_RUN_ID")
kind=$(printf '%s' "$run" | jq -r '.contextSnapshot.interactionKind // empty')
status=$(printf '%s' "$run" | jq -r '.contextSnapshot.interactionStatus // empty')
confirmation=$(printf '%s' "$run" | jq -r '.contextSnapshot.interactionId // empty')
issue=$(printf '%s' "$run" | jq -r '.contextSnapshot.issueId // empty')
if [ "$kind" = "request_confirmation" ] && [ "$status" = "accepted" ] && [ -n "$confirmation" ]; then
  librarian=$(pc "$PAPERCLIP_API_URL/api/companies/$PAPERCLIP_COMPANY_ID/agents" | jq -r '.[] | select(.name=="Librarian") | .id')
  body=$(jq -nc --arg parent "$issue" --arg assignee "$librarian" --arg key "publish-rfc:$confirmation" \
    '{title: "Publish the RFC", description: "Please publish the RFC from the parent task in our docs repository.", status: "todo", parentId: $parent, assigneeAgentId: $assignee, idempotencyKey: $key}')
  pc --json "$body" "$PAPERCLIP_API_URL/api/companies/$PAPERCLIP_COMPANY_ID/issues" > /tmp/wakeup-assignment.json
fi
"""

LIBRARIAN_SCRIPT = r"""
set -e
pc() {
  case "$*" in *"/interactions"*) return 1 ;; esac
  curl -fsS --max-time 5 -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
    -H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID" "$@"
}
issue=$(pc "$PAPERCLIP_API_URL/api/heartbeat-runs/$PAPERCLIP_RUN_ID" | jq -r '.issueId')
handoff=$(pc "$PAPERCLIP_API_URL/api/issues/$issue" | jq -r '.description')
body=$(jq -nc --arg handoff "$handoff" '{status:"done",comment:$handoff}')
pc --json "$body" -X PATCH "$PAPERCLIP_API_URL/api/issues/$issue" >/dev/null
"""


def wait_ready(director, agent_id):
    deadline = time.monotonic() + 20
    while director(f"agents/{agent_id}", method="GET").get("lifecycleState") != "ready":
        assert time.monotonic() < deadline, "Fixture agent never became ready"
        time.sleep(0.5)


def wait_for_wake_run(director, path, before, interaction_id):
    """Find the settled run whose wake carried this exact confirmation, ignoring
    any other heartbeat activity the process-adapter fixture also produces."""
    deadline = time.monotonic() + 40
    checked = set()
    while time.monotonic() < deadline:
        for run in director(f"{path}/runs", method="GET"):
            if run["runId"] in before or run["runId"] in checked or run["status"] not in ("succeeded", "failed"):
                continue
            checked.add(run["runId"])
            detail = director(f"heartbeat-runs/{run['runId']}", method="GET")
            if (detail.get("contextSnapshot") or {}).get("interactionId") == interaction_id:
                return run, detail
        time.sleep(1)
    raise RuntimeError("Expected the acceptance wake to settle a run carrying this confirmation")


def check_wakeup(container):
    def director(path, body=None, method="POST"):
        return request(container, "director", path, body, method)[1]

    request(container, "director", "auth/sign-up/email", {
        "name": "director", "email": "director@fixture.invalid", "password": secrets.token_hex(16),
    })
    director("bootstrap/claim", {})
    director_id = director("auth/get-session")["user"]["id"]
    company = director("companies", {"name": "Wakeup canary"})
    root = f"companies/{company['id']}"

    manager = director(f"{root}/agents", {"name": "Manager", "adapterType": "process",
                                           "adapterConfig": {"command": "/bin/sh", "args": ["-ec", MANAGER_SCRIPT]}})
    wait_ready(director, manager["id"])

    coordination = director(f"{root}/issues", {
        "title": "RFC publication wakeup fixture", "status": "backlog", "assigneeAgentId": manager["id"],
    })
    path = f"issues/{coordination['id']}"

    quote = "Keep this sentence."
    document = director(f"{path}/documents/rfc", {
        "format": "markdown", "body": f"{quote}\n\nApproved revision.\n",
    }, "PUT")
    pending = director(f"{path}/interactions", {
        "kind": "request_confirmation", "idempotencyKey": document["latestRevisionId"],
        "continuationPolicy": "wake_assignee_on_accept",
        "resolverPolicy": "human_only", "addresseeUserId": director_id,
        "payload": {
            "version": 1, "prompt": "Approve this exact RFC revision?",
            "target": {
                "type": "issue_document", "issueId": coordination["id"], "key": "rfc",
                "documentId": document["id"], "revisionId": document["latestRevisionId"],
                "revisionNumber": document["latestRevisionNumber"],
            },
        },
    })
    librarian = director(f"{root}/agents", {"name": "Librarian", "adapterType": "process",
        "adapterConfig": {"command": "/bin/sh", "args": ["-ec", LIBRARIAN_SCRIPT]}})
    wait_ready(director, librarian["id"])
    director(path, {"status": "in_review", "reviewInteractionId": pending["id"]}, "PATCH")
    time.sleep(3)

    before_wake = {run["runId"] for run in director(f"{path}/runs", method="GET")}
    status, decision = request(container, "director", f"{path}/interactions/{pending['id']}/accept", {})
    assert status == 200 and decision["status"] == "accepted", decision
    assert decision["effectiveResolverPolicy"] == "human_only"
    assert decision["resolvedByUserId"] == director_id
    assert decision["payload"]["target"] == pending["payload"]["target"]
    print("PASS: the Director's human-only accept of the exact RFC revision was recorded")

    woken_run, detail = wait_for_wake_run(director, path, before_wake, pending["id"])
    assert woken_run["agentId"] == manager["id"], "The wake invoked someone other than the current assignee"
    ctx = detail.get("contextSnapshot") or {}
    assert ctx.get("interactionKind") == "request_confirmation"
    assert ctx.get("interactionStatus") == "accepted"
    print("PASS: native wake_assignee_on_accept ran the Manager under its own run, tied to this exact confirmation")

    children = [issue for issue in director(f"{root}/issues", method="GET") if issue["parentId"] == coordination["id"]]
    assert len(children) == 1, f"Expected exactly one Librarian assignment, found {len(children)}"
    (assignment,) = children
    assert assignment["assigneeAgentId"] == librarian["id"]
    assert assignment["originRunId"] == woken_run["runId"]
    assert assignment["description"]
    assert assignment["createdByAgentId"] == manager["id"], "Assignment was not created under the Manager's own credentials"
    print("PASS: the Manager's own run credentials created the Librarian assignment, keyed by confirmation ID")

    deadline = time.monotonic() + 20
    receipt = None
    while time.monotonic() < deadline:
        comments = director(f"issues/{assignment['id']}/comments")
        receipt = next((comment for comment in comments
                        if comment.get("authorAgentId") == librarian["id"]
                        and comment["body"] == assignment["description"]), None)
        if receipt:
            break
        time.sleep(0.5)
    assert receipt, "The Librarian did not receive the Manager's handoff"
    assert any(run["runId"] == receipt.get("createdByRunId") and run["agentId"] == librarian["id"]
               for run in director(f"issues/{assignment['id']}/runs"))
    print("PASS: the Librarian's own run received the Manager's handoff without reading approval")

    key = director(f"agents/{manager['id']}/keys", {"name": "Disposable manager"})["token"]
    replay_status, replay = bearer(container, key, f"{root}/issues", body={
        "title": f"Publish accepted RFC {pending['id']}", "status": "backlog",
        "parentId": coordination["id"], "assigneeAgentId": librarian["id"],
        "idempotencyKey": f"publish-rfc:{pending['id']}",
    })
    assert replay_status in (200, 201) and replay["id"] == assignment["id"], \
        "A replay with the same confirmation-keyed idempotencyKey created a second assignment"
    children_after_replay = [issue for issue in director(f"{root}/issues", method="GET")
                              if issue["parentId"] == coordination["id"]]
    assert len(children_after_replay) == 1
    print("PASS: replaying the keyed assignment under the Manager's own credentials stayed idempotent")

    replay_accept_status, _ = request(
        container, "director", f"{path}/interactions/{pending['id']}/accept", {},
        expected=(403, 404, 409, 422),
    )
    assert replay_accept_status != 200
    print("PASS: replaying acceptance of the same confirmation could not trigger a second wake")


if __name__ == "__main__":
    with authenticated_fixture("wakeup") as container:
        check_wakeup(container)
