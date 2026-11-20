"""Exercise the native RFC review stage against a disposable authenticated board.

Validates the Feature Workflow Manager and Author seat instructions
(compose/v4/provision-paperclip.py): the Manager creates the Author's RFC issue under its
own credentials with a native review stage naming itself as participant, and verifies the
returned policy before dispatch. The Author is a scripted `process` fixture, standing in for
the real seat: it saves the RFC document and requests in_review using only its own run-scoped
credentials, never a confirmation addressed to a human. Paperclip's native engine then reassigns
the issue to the Manager and wakes the Manager's own run -- this probes the native stage
trigger, not an LLM's judgment.
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

RFC_BODY = "# Fixture RFC\n\nDraft body.\n"

AUTHOR_SCRIPT = r"""
set -e
pc() { curl -fsS --max-time 5 -H "Authorization: Bearer $PAPERCLIP_API_KEY" -H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID" "$@"; }
issue=$(pc "$PAPERCLIP_API_URL/api/heartbeat-runs/$PAPERCLIP_RUN_ID" | jq -r '.issueId')
pc --json '{"format":"markdown","body":"# Fixture RFC\n\nDraft body.\n"}' -X PUT \
  "$PAPERCLIP_API_URL/api/issues/$issue/documents/rfc" > /dev/null
pc --json '{"status":"in_review"}' -X PATCH "$PAPERCLIP_API_URL/api/issues/$issue" > /dev/null
"""


def wait_ready(director, agent_id):
    deadline = time.monotonic() + 20
    while director(f"agents/{agent_id}", method="GET").get("lifecycleState") != "ready":
        assert time.monotonic() < deadline, "Fixture agent never became ready"
        time.sleep(0.5)


def wait_for_agent_run(director, path, agent_id, *, invocation_source=None):
    """Find the settled run belonging to this exact agent, ignoring any other
    heartbeat activity the process-adapter fixture also produces (for example the
    independent nudge to post a stage comment)."""
    deadline = time.monotonic() + 30
    checked = set()
    while time.monotonic() < deadline:
        for run in director(f"{path}/runs", method="GET"):
            if run["agentId"] != agent_id or run["runId"] in checked:
                continue
            if invocation_source is not None and run["invocationSource"] != invocation_source:
                continue
            if run["status"] == "failed":
                detail = director(f"heartbeat-runs/{run['runId']}", method="GET")
                raise AssertionError(f"Run failed: {detail.get('error')}; {detail.get('stderrExcerpt')}")
            if run["status"] not in ("succeeded", "cancelled"):
                continue
            checked.add(run["runId"])
            return run, director(f"heartbeat-runs/{run['runId']}", method="GET")
        time.sleep(1)
    raise RuntimeError(f"Expected a settled run for agent {agent_id}")


def wait_for_status(director, path, status):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        issue = director(path, method="GET")
        if issue["status"] == status:
            return issue
        time.sleep(1)
    raise RuntimeError(f"Expected issue {path} to reach {status}, last saw {issue['status']}")


def check_review_stage(container):
    def director(path, body=None, method="POST"):
        return request(container, "director", path, body, method)[1]

    request(container, "director", "auth/sign-up/email", {
        "name": "director", "email": "director@fixture.invalid", "password": secrets.token_hex(16),
    })
    director("bootstrap/claim", {})
    company = director("companies", {"name": "RFC review stage canary"})
    root = f"companies/{company['id']}"

    manager = director(f"{root}/agents", {"name": "Manager", "adapterType": "process",
                                           "adapterConfig": {"command": "/bin/sh", "args": ["-ec", "true"]}})
    author = director(f"{root}/agents", {"name": "Author", "adapterType": "process",
                                          "adapterConfig": {"command": "/bin/sh", "args": ["-ec", AUTHOR_SCRIPT]}})
    wait_ready(director, manager["id"])
    wait_ready(director, author["id"])

    director(f"agents/{manager['id']}/permissions", {
        "canCreateAgents": False, "canCreateSkills": False, "canAssignTasks": True,
    }, "PATCH")

    stage = {"type": "review", "participants": [{"type": "agent", "agentId": manager["id"]}]}
    key = director(f"agents/{manager['id']}/keys", {"name": "Disposable manager"})["token"]
    status, issue = bearer(container, key, f"{root}/issues", body={
        "title": "Author RFC", "status": "todo", "assigneeAgentId": author["id"],
        "executionPolicy": {"stages": [stage]},
    })
    assert status == 201 and issue["createdByAgentId"] == manager["id"], (status, issue.get("error"))
    participant = issue["executionPolicy"]["stages"][0]["participants"][0]["agentId"]
    assert participant == manager["id"] and participant != author["id"], \
        "The Manager must verify the returned review stage names itself, not the Author, before dispatch"
    print("PASS: the Manager created the Author's RFC issue under its own credentials, "
          "naming itself the native review participant")

    path = f"issues/{issue['id']}"
    author_run, author_detail = wait_for_agent_run(director, path, author["id"])
    assert author_run["invocationSource"] == "assignment"
    # Paperclip may label this run "cancelled" because the run's own in_review PATCH
    # reassigned the issue away from the Author mid-flight; any other reason is a real failure.
    if author_run["status"] == "cancelled":
        assert author_detail.get("error") == "Cancelled before issue reassignment", author_detail.get("error")
    reviewed = wait_for_status(director, path, "in_review")
    document = director(f"{path}/documents/rfc")
    assert document["body"] == RFC_BODY and document["createdByAgentId"] == author["id"]
    print("PASS: the Author's own native run saved the RFC document and requested in_review")

    assert director(f"{path}/interactions", method="GET") == [], \
        "A confirmation exists; the native review stage, not a human, must gate this in_review"
    print("PASS: Author saved the exact RFC body and reached in_review without a human confirmation")

    assert reviewed["assigneeAgentId"] == manager["id"], \
        "Native review stage did not reassign the issue to the Manager"
    state = reviewed.get("executionState") or {}
    assert state.get("currentParticipant", {}).get("agentId") == manager["id"], \
        "The current participant is not the Manager"
    assert state.get("returnAssignee", {}).get("agentId") == author["id"]
    print("PASS: Paperclip's native review stage made the Manager -- not the Author or a human -- "
          "the current participant")

    manager_run, manager_detail = wait_for_agent_run(director, path, manager["id"], invocation_source="assignment")
    context = manager_detail.get("contextSnapshot") or {}
    trigger = ((context.get("executionContinuation") or {}).get("trigger")) or {}
    assert trigger.get("reason") == "execution_review_requested"
    assert trigger.get("interactionId") is None
    print("PASS: the native review stage woke the Manager's own run on Author's in_review request, "
          "not a human")


if __name__ == "__main__":
    with authenticated_fixture("rfc-review-stage") as container:
        check_review_stage(container)
