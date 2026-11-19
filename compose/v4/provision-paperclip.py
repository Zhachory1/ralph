"""Provision the Ralph roster and native spend policies in an empty Paperclip company."""

import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

SEATS = (
    ("Orchestrator", "ceo", "hermes_local", "Route requests and assign approved work."),
    ("Author", "general", "hermes_local", "Draft and revise initiative RFCs."),
    ("Reviewer", "general", "hermes_local", "Review exact-head evidence and report to the Director."),
    ("SWE", "engineer", "opencode_local", "Implement assigned work in its issue worktree."),
    ("SRE", "engineer", "opencode_local", "Triage CI and maintain assigned repositories."),
)


def provision(board, company_id, manifest, opencode, company_cents, agent_cents):
    company = f"companies/{UUID(company_id)}"
    if type(company_cents) is not int or type(agent_cents) is not int or not 0 < agent_cents <= company_cents:
        raise ValueError("Set positive integer budgets in cents; company budget must cover one seat")
    if not manifest.strip() or not re.fullmatch(r"[^/\s]+/[^/\s]+(?:/[^/\s]+)*", opencode.get("model", "")):
        raise ValueError("Provide a nonempty governance manifest and an explicit OpenCode provider/model")
    if board(f"{company}/agents"):
        raise ValueError("Use an empty company; this command never reconciles or overwrites existing seats")

    def budget(scope, scope_id, amount):
        board(f"{company}/budgets/policies", {
            "scopeType": scope, "scopeId": scope_id, "amount": amount,
            "warnPercent": 80, "hardStopEnabled": True, "notifyEnabled": True,
            "unpricedUsagePolicy": "block", "isActive": True,
        })

    budget("company", company_id, company_cents)
    roster = {}
    for name, role, adapter, duty in SEATS:
        seat = board(f"{company}/agents", {
            "name": name, "role": role, "adapterType": adapter, "runner": "legacy",
            "reportsTo": roster.get("Orchestrator"),
            "adapterConfig": {"command": "/opt/hermes/bin/hermes"} if adapter == "hermes_local" else opencode,
            "runtimeConfig": {"heartbeat": {"enabled": False}},
            "permissions": {"canCreateAgents": False, "canCreateSkills": False},
            "instructionsBundle": {"entryFile": "AGENTS.md", "files": {
                "AGENTS.md": f"# {name}\n\n{duty}\n\n{manifest}",
            }},
        })
        roster[name] = seat["id"]
        if role == "ceo":
            board(f"agents/{seat['id']}/permissions", {
                "canCreateAgents": False, "canCreateSkills": False, "canAssignTasks": True,
            }, "PATCH")
        budget("agent", seat["id"], agent_cents)
    return roster


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin")
    parser.add_argument("company_id")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("opencode_config", type=Path, help="JSON adapter config, not provider credentials")
    parser.add_argument("--company-cents", type=int, required=True)
    parser.add_argument("--agent-cents", type=int, required=True)
    args = parser.parse_args()
    origin = urlsplit(args.origin)
    if (not origin.hostname or origin.username or origin.password or origin.path not in ("", "/")
            or origin.query or origin.fragment
            or (origin.scheme != "https" and not (origin.scheme == "http" and origin.hostname == "127.0.0.1"))):
        parser.error("Use an HTTPS origin or http://127.0.0.1")
    token = os.environ.pop("PAPERCLIP_API_KEY", "")
    if not token:
        parser.error("Set PAPERCLIP_API_KEY from the target board")

    def board(path, body=None, method=None):
        request = Request(
            f"{args.origin.rstrip('/')}/api/{path}",
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Origin": args.origin.rstrip("/")},
            method=method,
        )
        request.add_unredirected_header("Authorization", f"Bearer {token}")
        with urlopen(request, timeout=15) as result:
            return json.load(result)

    roster = provision(board, args.company_id, args.manifest.read_text(),
                       json.loads(args.opencode_config.read_text()), args.company_cents, args.agent_cents)
    print(json.dumps(roster, indent=2))


if __name__ == "__main__":
    main()
