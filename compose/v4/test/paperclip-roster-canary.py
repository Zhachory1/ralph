"""Exercise Ralph roster provisioning against a disposable authenticated board."""

import runpy
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

TESTS = Path(__file__).resolve().parent
fixture = runpy.run_path(str(TESTS / "paperclip-rfc-canary.py"))
provision = runpy.run_path(str(TESTS.parent / "provision-paperclip.py"))["provision"]


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
        coding = name in {"SWE", "SRE"}
        assert seat["id"] == roster[name]
        assert seat["adapterType"] == ("opencode_local" if coding else "hermes_local")
        if coding:
            assert seat["adapterConfig"]["model"] == config["model"]
        else:
            assert seat["adapterConfig"]["hermesCommand"] == "/opt/hermes/bin/hermes"
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

    models = {"Council Architecture": "anthropic/fixture", "Council Security": "google/fixture",
              "Council Scope": "openai/fixture"}
    def reconcile(client, adapter=config):
        return provision(client, company, manifest, adapter, 500, 100, reconcile=True, judge_models=models)

    board(f"agents/{roster['Author']}", {"name": "RFC Author"}, "PATCH")
    issue = board(f"{root}/issues", {"title": "Existing work", "status": "backlog"})
    full = reconcile(board)
    assert len(full) == 12 and all(full[k] == v for k, v in roster.items())
    fixture["docker"]("restart", container)
    fixture["fixture"]["wait_for_health"](container, fixture["ORIGIN"] + "/api/health", "Paperclip", paperclip=True)
    assert reconcile(readonly) == full
    assert board(f"issues/{issue['id']}")["status"] == "backlog"
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
        reconcile(readonly, {"model": "fixture/different"})
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
        for row in board(f"{issue_path}/runs")[:4]:
            detail = board(f"heartbeat-runs/{row['runId']}")
            print(detail["status"], detail.get("error"), (detail.get("stderrExcerpt") or "")[-500:])
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
