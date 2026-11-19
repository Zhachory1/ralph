"""Exercise Ralph roster provisioning against a disposable authenticated board."""

import runpy
import secrets
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
    recipient = board(f"{root}/agents", {"name": "Fixture assignee", "adapterType": "process",
                                         "adapterConfig": {"command": "/bin/true"}})
    key = board(f"agents/{manager}/keys", {"name": "Disposable manager"})["token"]
    status, delegated = bearer(container, key, f"{root}/issues", body={
        "title": "Delegate fixture task", "status": "backlog", "parentId": issue["id"], "assigneeAgentId": recipient["id"],
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
