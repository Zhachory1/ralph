"""Exercise decision-only routing against an offline Paperclip board."""

import hashlib
import json
import runpy
import secrets
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = runpy.run_path(str(HERE / "paperclip-rfc-canary.py"))
DECIDE = """
import { readFileSync } from "node:fs";
import { COUNCIL_ROLES } from "./council-verdict.mjs";
import { decideRequestRoute } from "./request-routing.mjs";
const { proposal, evidence } = JSON.parse(readFileSync(0, "utf8"));
const registry = new Map([["triage", ({ ticket } = {}) => Number.isInteger(ticket) && ticket > 0]]);
if (evidence.council?.status === "completed") {
  evidence.council.result = { verdicts: COUNCIL_ROLES.map((judge_role) => ({
    judge_role, verdict: "PASS", confidence: 0.9, blocking_issues: [], non_blocking_notes: [],
  })) };
}
console.log(decideRequestRoute(proposal, { registry, revisions: 0, ...evidence }));
"""


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def check_routing(container):
    def board(path, body=None, method="POST"):
        return FIXTURE["request"](container, "director", path, body, method)[1]

    board("auth/sign-up/email", {
        "name": "Director", "email": "director@fixture.invalid", "password": secrets.token_hex(16),
    })
    board("bootstrap/claim", {})
    director_id = board("auth/get-session")["user"]["id"]
    company = board("companies", {"name": "Routing canary"})
    company_path = f"companies/{company['id']}"
    issues_path = f"{company_path}/issues"
    worker = board(f"{company_path}/agents", {
        "name": "Disposable worker", "adapterType": "process", "adapterConfig": {"command": "/bin/true"},
    })
    board(f"agents/{worker['id']}/pause", {})
    intake = board(issues_path, {"title": "Fixture intake", "status": "backlog"})
    intake_path = f"issues/{intake['id']}"
    doc_path = f"{intake_path}/documents/rfc"

    def assignments():
        return {issue["id"] for issue in board(issues_path) if issue["assigneeAgentId"] == worker["id"]}

    def route(label, proposal, expected, **overrides):
        before = assignments()
        evidence = {"allowed": True, "initiative": False, "consequential": False, **overrides}
        result = subprocess.check_output(
            ["node", "--input-type=module", "-e", DECIDE], cwd=HERE.parent,
            input=json.dumps({"proposal": proposal, "evidence": evidence}), text=True, timeout=10,
        ).strip()
        if result == "advance":
            created = board(issues_path, {
                "title": label, "status": "todo", "parentId": intake["id"], "assigneeAgentId": worker["id"],
            })
        assert result == expected, (label, result, expected)
        after = assignments()
        assert after == (before | {created["id"]} if result == "advance" else before), label

    def pass_for(text):
        # Synthetic service response; council-profile.test.mjs covers the real response boundary.
        return {"status": "completed", "artifactSha256": digest(text)}

    route("Registered", {"kind": "registered", "workflow": "triage", "inputs": {"ticket": 239}}, "advance")
    route("Ambiguous", {"kind": "clarify"}, "clarify")
    for domain in ("coding", "non-coding"):
        artifact = f"Fixture {domain} plan.\n"
        proposal = {"kind": "novel", "domain": domain, "artifact": artifact}
        route(f"Novel {domain}", proposal, "advance", council=pass_for(artifact))
    route("Denied", proposal, "denied", allowed=False, council=pass_for(artifact))
    route("Stale Council", proposal, "escalate", council=pass_for("Earlier plan."))
    route("Unavailable Council", proposal, "escalate", council=None)
    route("Consequential gate", proposal, "human", council=pass_for(artifact), consequential=True)

    artifact = "Fixture initiative RFC.\n"
    proposal = {"kind": "novel", "domain": "non-coding", "artifact": artifact}
    route("Initiative without Council", proposal, "review", initiative=True)
    evidence = {"initiative": True, "council": pass_for(artifact)}
    route("Initiative after Council PASS", proposal, "director", **evidence)
    assert not board(f"{intake_path}/interactions")
    document = board(doc_path, {"format": "markdown", "body": artifact}, "PUT")
    target = {
        "type": "issue_document", "issueId": intake["id"], "key": "rfc",
        "documentId": document["id"], "revisionId": document["latestRevisionId"],
        "revisionNumber": document["latestRevisionNumber"],
    }
    confirmation = board(f"{intake_path}/interactions", {
        "kind": "request_confirmation", "resolverPolicy": "human_only", "addresseeUserId": director_id,
        "payload": {"version": 1, "prompt": "Approve this exact RFC revision?", "target": target},
    })
    route("Pending Director approval", proposal, "director", **evidence)
    board(f"{intake_path}/interactions/{confirmation['id']}/accept", {})
    decision, = [row for row in board(f"{intake_path}/interactions") if row["id"] == confirmation["id"]]
    assert decision["status"] == "accepted" and decision["result"]["outcome"] == "accepted"
    assert decision["resolvedByUserId"] == director_id and decision["payload"]["target"] == target
    revision = board(f"{doc_path}/revisions/{target['revisionId']}")
    evidence["directorApprovedSha256"] = digest(revision["body"])
    route("Approved initiative", proposal, "advance", **evidence)
    changed = artifact + "\nUnapproved change.\n"
    board(doc_path, {"format": "markdown", "body": changed, "baseRevisionId": target["revisionId"]}, "PUT")
    route("Stale Director approval", {**proposal, "artifact": changed}, "director",
          **{**evidence, "council": pass_for(changed)})
    print("PASS: Paperclip assignments match routing decisions and exact Director approval")


if __name__ == "__main__":
    with FIXTURE["authenticated_fixture"]("routing") as container:
        check_routing(container)
