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


WORKFLOW_SEATS = (
    ("Feature Workflow Manager", "general", "hermes_local", "Own the feature outcome; assign the Author and specialists."),
    ("Review Workflow Manager", "general", "hermes_local", "Own review outcomes and delegate checks to reviewers."),
    ("Librarian", "general", "hermes_local", "Propose housekeeping edits to shared approved knowledge."),
    ("Comms", "general", "hermes_local", "Triage communications; external sends require Director approval."),
    ("Council Architecture", "general", "hermes_local", "Return an independent architecture_scale verdict."),
    ("Council Security", "general", "hermes_local", "Return an independent security_blast_radius verdict."),
    ("Council Scope", "general", "hermes_local", "Return an independent scope_pragmatism verdict."),
)
ALIASES = {"Orchestrator": "Ralph Orchestrator", "Author": "RFC Author", "SWE": "OpenCode Developer", "SRE": "SRE Ops"}
MANAGERS = {"Feature Workflow Manager", "Review Workflow Manager"}


def provision(board, company_id, manifest, opencode, company_cents, agent_cents, *, reconcile=False, judge_models=None):
    company = f"companies/{UUID(company_id)}"
    if type(company_cents) is not int or type(agent_cents) is not int or not 0 < agent_cents <= company_cents:
        raise ValueError("Set positive integer budgets in cents; company budget must cover one seat")
    if not manifest.strip() or not re.fullmatch(r"[^/\s]+/[^/\s]+(?:/[^/\s]+)*", opencode.get("model", "")):
        raise ValueError("Provide a nonempty governance manifest and an explicit OpenCode provider/model")
    existing = board(f"{company}/agents")
    if existing and not reconcile:
        raise ValueError("Use an empty company; this command never reconciles or overwrites existing seats")

    seats = SEATS + WORKFLOW_SEATS if reconcile else SEATS
    if reconcile:
        judges = {name for name, *_ in WORKFLOW_SEATS if name.startswith("Council ")}
        if not isinstance(judge_models, dict) or set(judge_models) != judges or not all(
                isinstance(v, str) and re.fullmatch(r"[\w.-]+/[\w.-]+", v) for v in judge_models.values()
        ) or len({v.split("/")[0] for v in judge_models.values()}) != 3:
            raise ValueError("Configure three explicit Council models from distinct provider families")
    policies = board(f"{company}/budgets/overview")["policies"] if reconcile else []

    def budget(scope, scope_id, amount, *, apply):
        desired = {
            "scopeType": scope, "scopeId": scope_id, "amount": amount, "windowKind": "calendar_month_utc",
            "warnPercent": 80, "hardStopEnabled": True, "notifyEnabled": True,
            "unpricedUsagePolicy": "block", "isActive": True,
        }
        matches = [p for p in policies if p["scopeId"] == scope_id and p["scopeType"] == scope
                   and p["windowKind"] == desired["windowKind"] and p["metric"] == "billed_cents"]
        if matches:
            if len(matches) != 1 or any(matches[0].get(k) != v for k, v in desired.items()):
                raise ValueError(f"Budget conflict for {scope} {scope_id}; existing policy preserved")
        elif apply:
            board(f"{company}/budgets/policies", desired)

    roster = {}
    budget("company", company_id, company_cents, apply=False)
    for name, role, adapter, duty in seats:
        matches = [a for a in existing if a["name"] in (name, ALIASES.get(name))]
        if not matches:
            continue
        seat = matches[0]
        root = name in {"Orchestrator", "Comms"}
        if (len(matches) != 1 or seat["role"] != role or seat["adapterType"] != adapter
                or (not root and "Orchestrator" not in roster)
                or seat["reportsTo"] != (None if root else roster["Orchestrator"])):
            raise ValueError(f"Conflicting seat identity, adapter or reporting line: {name}")
        model = (judge_models or {}).get(name) or (opencode["model"] if adapter == "opencode_local" else None)
        if model and seat["adapterConfig"].get("model") != model:
            raise ValueError(f"Conflicting model for {name}; existing configuration preserved")
        if seat["permissions"].get("canCreateAgents") or seat["runtimeConfig"].get("heartbeat", {}).get("enabled"):
            raise ValueError(f"Conflicting hiring or heartbeat policy for {name}")
        roster[name] = seat["id"]
        budget("agent", seat["id"], agent_cents, apply=False)

    budget("company", company_id, company_cents, apply=True)
    for name, role, adapter, duty in seats:
        if name in roster:
            budget("agent", roster[name], agent_cents, apply=True)
            continue
        config = {"hermesCommand": "/opt/hermes/bin/hermes"} if adapter == "hermes_local" else opencode
        if name in (judge_models or {}):
            config["model"] = judge_models[name]
        seat = board(f"{company}/agents", {
            "name": name, "role": role, "adapterType": adapter, "runner": "legacy",
            "reportsTo": None if name == "Comms" else roster.get("Orchestrator"),
            "adapterConfig": config,
            "runtimeConfig": {"heartbeat": {"enabled": False}},
            "permissions": {"canCreateAgents": False, "canCreateSkills": False},
            "instructionsBundle": {"entryFile": "AGENTS.md", "files": {
                "AGENTS.md": f"# {name}\n\n{duty}\n\n{manifest}",
            }},
        })
        roster[name] = seat["id"]
        if role == "ceo" or name in MANAGERS:
            # Paperclip requires a separate hiring denial for the CEO, whose assignment rights are native.
            board(f"agents/{seat['id']}/permissions", {
                "canCreateAgents": False, "canCreateSkills": False, "canAssignTasks": name in MANAGERS,
            }, "PATCH")
        budget("agent", seat["id"], agent_cents, apply=True)
    return roster


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin")
    parser.add_argument("company_id")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("opencode_config", type=Path, help="JSON adapter config, not provider credentials")
    parser.add_argument("--company-cents", type=int, required=True)
    parser.add_argument("--agent-cents", type=int, required=True)
    parser.add_argument("--reconcile", metavar="COUNCIL_MODELS_JSON", type=Path,
                        help="Reconcile the v4.1 roster using the three configured Council model IDs")
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
                       json.loads(args.opencode_config.read_text()), args.company_cents, args.agent_cents,
                       reconcile=args.reconcile is not None,
                       judge_models=json.loads(args.reconcile.read_text()) if args.reconcile else None)
    print(json.dumps(roster, indent=2))


if __name__ == "__main__":
    main()
