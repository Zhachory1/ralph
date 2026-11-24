"""Exercise Ralph roster provisioning against a disposable authenticated board."""

import json
import runpy
import secrets
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

TESTS = Path(__file__).resolve().parent
fixture = runpy.run_path(str(TESTS / "paperclip-rfc-canary.py"))
provisioning = runpy.run_path(str(TESTS.parent / "provision-paperclip.py"))
provision = provisioning["provision"]

WAKE_STATUSES = {"queued", "claimed", "coalesced", "skipped", "completed", "failed", "cancelled",
                 "deferred_issue_execution"}
RUN_STATUSES = {"queued", "running", "succeeded", "failed", "cancelled", "timed_out", "interrupted"}


def print_review_handoff_evidence(board, issue_path, state, developer_id, reviewer_id):
    def read(suffix, expected_type):
        try:
            data = board(f"{issue_path}/{suffix}")
            return data if isinstance(data, expected_type) else None
        except (AssertionError, ValueError, OSError, subprocess.SubprocessError):
            return None

    def status(row, allowed):
        value = row.get("status")
        return value if isinstance(value, str) and value in allowed else "other"

    def reassigned(row):
        if row.get("action") != "issue.updated":
            return False
        assignee = ((row.get("details") or {}).get("changes") or {}).get("assigneeAgentId") or {}
        return assignee.get("from") == developer_id and assignee.get("to") == reviewer_id

    activity = read("activity?view=recent", list)
    reassignment = next((row for row in activity or [] if reassigned(row)), None)
    reassigned_at = (reassignment or {}).get("createdAt")
    wakes = read("diagnostics/wakes", dict)
    wake_events = (wakes or {}).get("events")
    wake_history_truncated = (wakes or {}).get("truncated")
    runs = read("runs?view=recent", list)
    reviewer_runs = [row for row in runs or [] if row.get("agentId") == reviewer_id]
    reviewer_run_starts = {row["runId"]: bool(row.get("startedAt")) for row in reviewer_runs if row.get("runId")}

    def wake_evidence(row):
        requested_at = row.get("requestedAt")
        # Both endpoints serialize timestamps as UTC ISO strings. Timing alone does not prove causation.
        return {
            "status": status(row, WAKE_STATUSES),
            "atOrAfterReassignment": (
                requested_at >= reassigned_at if isinstance(requested_at, str) and isinstance(reassigned_at, str)
                else None
            ),
            "linked": bool(row.get("runId")),
            "claimed": bool(row.get("claimedAt")),
            "linkedRecentRunStarted": reviewer_run_starts.get(row.get("runId")),
        }

    assignee = state.get("assigneeAgentId")
    execution = state.get("executionState") or {}
    evidence = {
        "issueStatus": status(state, {"backlog", "todo", "in_progress", "in_review", "done", "blocked", "cancelled"}),
        "assignee": ("reviewer" if assignee == reviewer_id else "developer" if assignee == developer_id else "other"),
        "inReviewStage": execution.get("currentStageType") == "review",
        "reviewerIsCurrentParticipant": (execution.get("currentParticipant") or {}).get("agentId") == reviewer_id,
        "reviewerReassignmentInRecentActivity": reassignment is not None if activity is not None else None,
        "wakeHistoryTruncated": wake_history_truncated if isinstance(wake_history_truncated, bool) else None,
        "reviewerAssignmentWakes": [
            wake_evidence(row) for row in wake_events
            if row.get("kind") == "wake_request" and row.get("agentId") == reviewer_id
            and row.get("source") == "assignment"
        ][:4] if isinstance(wake_events, list) else None,
        "recentReviewerRuns": [
            {"status": status(row, RUN_STATUSES), "started": bool(row.get("startedAt"))}
            for row in reviewer_runs
        ] if runs is not None else None,
        "developerCancellationInRecentRuns": any(
            row.get("agentId") == developer_id and row.get("status") == "cancelled"
            and row.get("errorCode") == "issue_reassigned" for row in runs
        ) if runs is not None else None,
    }
    print("Roster review evidence:", json.dumps(evidence, sort_keys=True))


def assert_orchestrator_handoffs(instructions):
    for role in ("Feature Workflow Manager", "Review Workflow Manager", "Author", "Council"):
        assert role in instructions, f"Orchestrator instructions omit {role}"
    assert "Do not draft the feature RFC or request its approval yourself" in instructions


def check_roster(container):
    def board(path, body=None, method="POST"):
        response = fixture["request"](container, "director", path, body, method)[1]
        if path.endswith("/budgets/policies"):
            assert response["warnPercent"] == 80
            assert response["hardStopEnabled"] is True and response["notifyEnabled"] is True
            assert response["windowKind"] == "calendar_month_utc"
        return response

    board("auth/sign-up/email", {
        "name": "Director", "email": "director@fixture.invalid", "password": secrets.token_hex(16),
    })
    board("bootstrap/claim", {})
    company = board("companies", {"name": "Roster fixture"})["id"]
    root = f"companies/{company}"
    manifest = "Never merge, deploy, or send external messages without the Director."
    config = {"model": "fixture/small"}
    roster = provision(board, company, manifest, config, 500, 100)
    seats = {seat["name"]: seat for seat in board(f"{root}/agents")}
    assert set(seats) == {"Orchestrator", "Author", "Reviewer", "SWE", "SRE"}
    for name, seat in seats.items():
        assert seat["id"] == roster[name]
        assert seat["adapterType"] == "opencode_local"
        assert seat["adapterConfig"]["model"] == config["model"]
        assert seat["reportsTo"] == (None if name == "Orchestrator" else roster["Orchestrator"])
        assert seat["runtimeConfig"]["heartbeat"]["enabled"] is False
        assert seat["permissions"]["canCreateAgents"] is False, name
    try:
        provision(board, company, manifest, config, 500, 100)
    except ValueError as error:
        assert "empty company" in str(error)
    else:
        raise AssertionError("Provisioning accepted a company that already had agents")
    assert len(board(f"{root}/agents")) == 5
    print("PASS: five seats persisted with native budget policies; a second invocation did not modify them")

    def readonly(path, body=None, method="GET"):
        assert body is None, f"Unexpected reconciliation write: {method} {path}"
        return board(path)

    models = {"Council Architecture": "anthropic/claude-fixture", "Council Security": "google/gemini-fixture",
              "Council Scope": "openai/gpt-fixture"}
    def reconcile(client, adapter=config, seat_models=None):
        return provision(client, company, manifest, adapter, 500, 100, reconcile=True,
                         judge_models=models, seat_models=seat_models)

    board(f"agents/{roster['Author']}", {"name": "RFC Author"}, "PATCH")
    assert board(f"agents/{roster['Author']}/pause", {})["status"] == "paused"
    issue = board(f"{root}/issues", {"title": "Existing work", "status": "backlog"})
    full = reconcile(board)
    assert len(full) == 17 and all(full[k] == v for k, v in roster.items())
    assert {"Review Workflow Manager", "Review Lens Generalist", "Review Lens Reliability", "Review Lens MVP",
            "Review Lens Security", "Review Synthesis"} <= set(full)
    fixture["docker"]("restart", container)
    fixture["fixture"]["wait_for_health"](container, fixture["ORIGIN"] + "/api/health", "Paperclip", paperclip=True)
    assert reconcile(readonly) == full
    assert board(f"agents/{roster['Author']}")["status"] == "paused"
    assert board(f"issues/{issue['id']}")["status"] == "backlog"
    for agent_id in full.values():
        instructions = board(f"agents/{agent_id}/instructions-bundle/file?path=AGENTS.md")["content"]
        assert provisioning["HANDOFF_GUIDANCE"] in instructions
        if agent_id == full["Orchestrator"]:
            assert_orchestrator_handoffs(instructions)
    manager = full["Feature Workflow Manager"]
    def manager_grant(enabled):
        board(f"agents/{manager}/permissions", {
            "canCreateAgents": False, "canCreateSkills": False, "canAssignTasks": enabled,
        }, "PATCH")
    manager_grant(False)
    try:
        reconcile(readonly)
    except ValueError as error:
        assert "assignment grant" in str(error)
    else:
        raise AssertionError("Missing explicit manager assignment grant was silently accepted")
    manager_grant(True)
    try:
        reconcile(readonly, seat_models={"SWE": "fixture/different"})
    except ValueError as error:
        assert "model" in str(error)
    else:
        raise AssertionError("Reconciliation accepted conflicting OpenCode model")
    print("PASS: reconciliation preserves seat IDs and rejects drift without writes")

    bearer = runpy.run_path(str(TESTS / "paperclip-board-key-canary.py"))["bearer"]
    # A backlog assignment exercises native authorization without starting a model run.
    def review_actor(name, expected):
        script = (
            'curl -sS -X PATCH -H "Authorization: Bearer $PAPERCLIP_API_KEY" '
            '-H "X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID" '
            """--json '{"status":"done","comment":"Technical disposition for fixture commit aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}' """
            f'"{fixture["ORIGIN"]}/api/issues/$(cat /tmp/technical-review-issue)" '
            f"""| python3 -c 'import sys,json; d=json.load(sys.stdin); assert d.get("status") == "{expected}", d.get("error", d.get("status"))'"""
        )
        agent = board(f"{root}/agents", {"name": name, "adapterType": "process",
                                         "adapterConfig": {"command": "/bin/sh", "args": ["-ec", script]}})
        deadline = time.monotonic() + 20
        while board(f"agents/{agent['id']}").get("lifecycleState") != "ready":
            assert time.monotonic() < deadline, "Fixture actor not ready"
            time.sleep(0.5)
        return agent

    recipient = review_actor("Fixture assignee", "in_review")
    reviewer = review_actor("Fixture reviewer", "done")
    stage = {"type": "review", "participants": [{"type": "agent", "agentId": reviewer["id"]}]}
    key = board(f"agents/{manager}/keys", {"name": "Disposable manager"})["token"]
    ceo_key = board(f"agents/{roster['Orchestrator']}/keys", {"name": "Disposable CEO"})["token"]
    for name in ("Review Workflow Manager", "Review Lens Generalist", "Review Lens Reliability",
                 "Review Lens MVP", "Review Lens Security", "Review Synthesis"):
        seat_path = f"agents/{full[name]}"
        seat = board(seat_path)
        assert seat["status"] == "paused", (name, seat["status"])
        for replacement in ({"protectedAgent": {"blockAssignment": False}}, {}):
            status, denied = bearer(container, ceo_key, f"{seat_path}/permissions", method="PATCH", body={
                "canCreateAgents": False, "canAssignTasks": False, "authorizationPolicy": replacement,
            })
            assert status == 403, (name, status, denied)
        assert board(seat_path)["permissions"]["authorizationPolicy"]["protectedAgent"]["blockAssignment"] is True
        status, denied = bearer(container, key, f"{root}/issues", body={
            "title": f"Paused assignment to {name}", "status": "backlog", "assigneeAgentId": full[name],
        })
        assert status == 409 and denied.get("details", {}).get("assigneeStatus") == "paused", (name, status, denied)

    # A ready process fixture lets the request reach the policy check without resuming an unverified seat.
    protected = review_actor("Fixture protected review seat", "done")["id"]
    for blocked in (True, False, True):
        board(f"agents/{protected}/permissions", {
            "canCreateAgents": False, "canAssignTasks": False,
            "authorizationPolicy": {"protectedAgent": {"blockAssignment": blocked}},
        }, "PATCH")
        status, result = bearer(container, key, f"{root}/issues", body={
            "title": "Protected assignment fixture", "status": "backlog", "assigneeAgentId": protected,
        })
        if blocked:
            assert status == 403 and "protected-agent policy" in result["error"], (status, result)
        else:
            assert status == 201 and result["assigneeAgentId"] == protected, (status, result)
    print("PASS: review seats stay paused and protected; the board can release a ready fixture", flush=True)

    uncapped_company = board("companies", {"name": "Advisory fixture"})["id"]
    advisory_models = {name: "openai/fixture" for name in models}
    advisory = provision(board, uncapped_company, manifest, config, None, None, reconcile=True,
                         judge_models=advisory_models, advisory_council=True)
    policies = board(f"companies/{uncapped_company}/budgets/overview")["policies"]
    assert not any(policy["isActive"] and policy["hardStopEnabled"] for policy in policies)
    instructions = board(f"agents/{advisory['Feature Workflow Manager']}/instructions-bundle/file?path=AGENTS.md")["content"]
    assert "Council findings are advisory" in instructions
    assert "all three judges supported" not in instructions
    print("PASS: same-family Council is advisory and creates no spend caps", flush=True)

    status, delegated = bearer(container, key, f"{root}/issues", body={
        "title": "Delegate fixture task", "status": "backlog", "parentId": issue["id"], "assigneeAgentId": recipient["id"],
        "executionPolicy": {"stages": [stage]},
    })
    assert status == 201 and delegated["createdByAgentId"] == manager, (status, delegated.get("error"))
    pending = board(f"issues/{issue['id']}/interactions", {
        "kind": "request_confirmation", "resolverPolicy": "human_only",
        "addresseeUserId": board("auth/get-session")["user"]["id"],
        "payload": {"version": 1, "prompt": "Approve this fixture?"},
    })
    endpoint = f"issues/{issue['id']}/interactions/{pending['id']}/accept"
    status, _ = bearer(container, key, endpoint, body={})
    assert status in (403, 404, 422), status
    assert board(f"issues/{issue['id']}/interactions")[0]["status"] == "pending"
    assert board(endpoint, {})["status"] == "accepted"
    print("PASS: manager delegated a backlog task but could not resolve Director confirmation")

    issue_path = f"issues/{delegated['id']}"
    fixture["docker"]("exec", container, "sh", "-ec", 'echo "$1" > /tmp/technical-review-issue', "fixture", delegated["id"])
    assert delegated["executionPolicy"]["stages"][0]["participants"][0]["agentId"] == reviewer["id"]
    # A Developer attempting "done" must enter review, not approve its own work.
    board(issue_path, {"status": "todo"}, "PATCH")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = board(issue_path)
        if state["status"] == "done":
            break
        time.sleep(0.5)
    if (state.get("executionState") or {}).get("lastDecisionOutcome") != "approved":
        print_review_handoff_evidence(board, issue_path, state, recipient["id"], reviewer["id"])
        raise AssertionError(state["status"])
    comments = board(f"{issue_path}/comments")
    actors = {c["authorAgentId"] for c in comments if c["body"].startswith("Technical disposition")}
    assert actors == {recipient["id"], reviewer["id"]}, actors
    assert state["executionState"]["returnAssignee"]["agentId"] == recipient["id"]
    print("PASS: Developer cannot decide its pending technical review; assigned independent Reviewer can")

    swe_id = roster["SWE"]
    for cents, expected in ((80, "warning"), (20, "hard_stop")):
        board(f"{root}/cost-events", {
            "agentId": swe_id, "provider": "fixture", "model": "small",
            "billingType": "metered_api", "costCents": cents,
            "occurredAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })
        policy, = [row for row in board(f"{root}/budgets/overview")["policies"]
                   if row["scopeId"] == swe_id]
        assert policy["status"] == expected, policy["status"]
    print("PASS: synthetic ledger entries reached warning at 80% and hard stop at 100%")


if __name__ == "__main__":
    with fixture["authenticated_fixture"]("roster") as container:
        check_roster(container)
