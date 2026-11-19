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
