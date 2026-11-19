"""Probe native RFC review on a disposable authenticated Paperclip instance."""

import argparse
import json
import runpy
import secrets
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path

fixture = runpy.run_path(str(Path(__file__).with_name("paperclip-canary.py")))
docker = fixture["docker"]
ORIGIN = "http://127.0.0.1:3100"
IMAGE = "ralph-paperclip-canary:2026.1117.0"


def request(container, actor, path, body=None, method="POST", expected=(200, 201, 202)):
    jar = f"/tmp/rfc-{actor}.cookies"
    args = ["-X", method, "--json", "@-"] if body is not None else []
    result = subprocess.run(
        ["docker", "exec", "-i", container, "curl", "-sS", "--max-time", "10",
         "-b", jar, "-c", jar, "-H", f"Origin: {ORIGIN}", "-w", "\n%{http_code}",
         *args, f"{ORIGIN}/api/{path}"],
        input=json.dumps(body) if body is not None else "", text=True,
        stdout=subprocess.PIPE, timeout=15, check=True,
    )
    raw, status = result.stdout.rsplit("\n", 1)
    status = int(status)
    # Do not print credentials, cookies, or invite URLs, including on failure.
    route = "invites/<token>/accept" if path.startswith("invites/") else path
    assert status in expected, (actor, route, status)
    return status, json.loads(raw)


def check_git_storage(container, director, path, director_id, document_id, approved_id, draft_id, approved_body):
    repo = "/tmp/rfc-para"
    file = "docs/1-projects/fixture/rfc.md"

    def git(*args):
        return subprocess.check_output(
            ["docker", "exec", "-u", "node", container, "env",
             "GIT_CONFIG_GLOBAL=/dev/null", "GIT_CONFIG_NOSYSTEM=1", "git", "-C", repo, *args],
            timeout=10,
        )

    def store(revision_id):
        for interaction in director(f"{path}/interactions"):
            target = interaction["payload"].get("target") or {}
            if (
                interaction["kind"] == "request_confirmation"
                and interaction["status"] == "accepted"
                and interaction["result"]["outcome"] == "accepted"
                and interaction["resolvedByUserId"] == director_id
                and target.get("type") == "issue_document"
                and target.get("issueId") == path.split("/")[-1]
                and target.get("key") == "rfc"
                and target.get("documentId") == document_id
                and target.get("revisionId") == revision_id
            ):
                break
        else:
            return None

        revision = director(f"{path}/documents/rfc/revisions/{revision_id}")
        assert revision["id"] == revision_id
        body = revision["body"].encode("utf-8")
        if file.encode() + b"\0" in git("ls-tree", "-z", "--name-only", "HEAD", "--", file):
            assert git("show", f"HEAD:{file}") == body, "Refusing to replace a different stored RFC"
            return git("rev-parse", "HEAD")
        subprocess.run(
            ["docker", "exec", "-i", "-u", "node", container, "sh", "-ec",
             'mkdir -p "$(dirname "$1")"; cat > "$1"', "store", f"{repo}/{file}"],
            input=body, check=True, timeout=10,
        )
        git("add", "--", file)
        git("-c", "user.name=RFC Canary", "-c", "user.email=canary@fixture.invalid",
            "commit", "-m", f"Store RFC revision {revision_id}")
        return git("rev-parse", "HEAD")

    docker("exec", "-u", "node", container, "mkdir", repo)
    git("init", "--template=", "-b", "main")
    git("-c", "user.name=RFC Canary", "-c", "user.email=canary@fixture.invalid",
        "commit", "--allow-empty", "-m", "Seed disposable PARA repository")
    base = git("rev-parse", "HEAD")
    assert store(draft_id) is None
    assert git("rev-parse", "HEAD") == base and not git("status", "--porcelain")
    stored = store(approved_id)
    assert stored and stored != base
    assert git("show", f"HEAD:{file}") == approved_body.encode("utf-8")
    assert store(approved_id) == stored
    assert store(draft_id) is None
    assert git("rev-parse", "HEAD^") == base and git("rev-parse", "HEAD") == stored
    assert not git("status", "--porcelain")
    print("PASS: Git stored only the approved bytes; retry did not create another commit")


def check_review(container, *, require_downstream_gate):
    def director(path, body=None, method="POST"):
        return request(container, "director", path, body, method)[1]

    for actor in ("director", "operator"):
        request(container, actor, "auth/sign-up/email", {
            "name": actor, "email": f"{actor}@fixture.invalid", "password": secrets.token_hex(16),
        })
    director("bootstrap/claim", {})
    company = director("companies", {"name": "RFC canary"})
    company_path = f"companies/{company['id']}"
    invite = director(f"{company_path}/invites", {"allowedJoinTypes": "human", "humanRole": "operator"})
    _, joined = request(container, "operator", f"invites/{invite['token']}/accept", {"requestType": "human"})
    assert joined["status"] == "approved", joined["status"]

    issue = director(f"{company_path}/issues", {"title": "RFC review", "status": "backlog"})
    path = f"issues/{issue['id']}"
    doc_path = f"{path}/documents/rfc"
    quote = "Keep this sentence."

    def revise(body, base=None):
        return director(doc_path, {
            "format": "markdown", "body": body, "baseRevisionId": base,
        }, "PUT")

    def ask(document):
        revision = document["latestRevisionId"]
        return director(f"{path}/interactions", {
            "kind": "request_confirmation", "idempotencyKey": revision,
            "resolverPolicy": "human_only", "addresseeUserId": director_id,
            "payload": {
                "version": 1, "prompt": "Approve this exact RFC revision?",
                "target": {
                    "type": "issue_document", "issueId": issue["id"], "key": "rfc",
                    "documentId": document["id"], "revisionId": revision,
                    "revisionNumber": document["latestRevisionNumber"],
                },
            },
        })

    director_id = director("auth/get-session")["user"]["id"]
    first = revise(f"{quote}\n\nRevision A.\n")
    thread = director(f"{doc_path}/annotations", {
        "baseRevisionId": first["latestRevisionId"], "baseRevisionNumber": first["latestRevisionNumber"],
        "selector": {
            "quote": {"exact": quote},
            "position": {"normalizedStart": 0, "normalizedEnd": len(quote),
                         "markdownStart": 0, "markdownEnd": len(quote)},
        },
        "body": "Please retain this sentence.",
    })
    pending = ask(first)
    director(path, {"status": "in_review", "reviewInteractionId": pending["id"]}, "PATCH")
    downstream = director(f"{company_path}/issues", {
        "title": "Downstream canary", "status": "blocked", "blockedByIssueIds": [issue["id"]],
    })
    downstream_path = f"issues/{downstream['id']}"
    second_body = f"{quote}\n\nRevision B — approved text.\n"
    second = revise(second_body, first["latestRevisionId"])
    assert second["latestRevisionId"] != first["latestRevisionId"]
    assert director(f"{doc_path}/revisions/{first['latestRevisionId']}")["body"] == first["body"]
    remapped = director(f"{doc_path}/annotations/{thread['id']}")
    assert remapped["selectedText"] == quote
    assert remapped["currentRevisionNumber"] == second["latestRevisionNumber"]
    print("PASS: original revision survives and the anchored comment remaps")

    def accept(actor, confirmation):
        return request(container, actor, f"{path}/interactions/{confirmation['id']}/accept", {},
                       expected=(200, 403, 404, 409, 422))

    def is_accepted(result):
        status, body = result
        return status == 200 and body["status"] == "accepted"

    assert not is_accepted(accept("director", pending)), "Director could approve stale revision A"
    assert director(downstream_path)["status"] == "blocked"
    current = ask(second)
    assert not is_accepted(accept("operator", current)), "Operator approved a Director-only confirmation"
    decision = accept("director", current)
    assert is_accepted(decision), "Director could not approve current revision B"
    assert decision[1]["payload"]["target"]["revisionId"] == second["latestRevisionId"]
    print("PASS: only the Director could accept the current addressed revision")
    third = revise(f"{quote}\n\nRevision C.\n", second["latestRevisionId"])
    assert third["latestRevisionId"] != second["latestRevisionId"]
    assert director(f"{doc_path}/revisions/{second['latestRevisionId']}")["body"] == second_body
    assert director(downstream_path)["status"] == "blocked"
    resume = {"status": "todo", "resume": True, "comment": "Resume downstream work"}
    _, denied = request(container, "director", downstream_path, resume, "PATCH", expected=(409,))
    assert denied["error"] == "Issue follow-up blocked by unresolved blockers"

    check_git_storage(container, director, path, director_id, second["id"],
                      second["latestRevisionId"], third["latestRevisionId"], second_body)

    status, updated = request(container, "operator", path, {"status": "done"}, "PATCH",
                              expected=(200, 403, 409, 422))
    if status == 200 and updated["status"] == "done":
        resumed_status, resumed = request(container, "director", downstream_path, resume, "PATCH",
                                          expected=(200, 409, 422))
        downstream_status = resumed.get("status") if resumed_status == 200 else f"HTTP {resumed_status}"
        message = f"Operator completed RFC without approval of revision C; downstream resume: {downstream_status}"
        if require_downstream_gate:
            raise AssertionError(message)
        label = "KNOWN GAP (#243)" if downstream_status == "todo" else "RFC COMPLETED WITHOUT APPROVAL"
        print(f"{label}: {message}")
        return
    assert director(downstream_path)["status"] == "blocked"
    print("PASS: downstream stayed blocked without matching approval")


@contextmanager
def authenticated_fixture(name):
    container = f"ralph-paperclip-{name}-{uuid.uuid4().hex[:10]}"
    docker(
        "run", "-d", "--rm", "--pull", "never", "--network", "none", "--name", container,
        "-e", "HOST=127.0.0.1", "-e", "PAPERCLIP_DEPLOYMENT_MODE=authenticated",
        "-e", "PAPERCLIP_DEPLOYMENT_EXPOSURE=private", "-e", "PAPERCLIP_TELEMETRY_DISABLED=1",
        "-e", "PAPERCLIP_AUTH_BASE_URL_MODE=explicit", "-e", f"PAPERCLIP_AUTH_PUBLIC_BASE_URL={ORIGIN}",
        "-e", "PAPERCLIP_AUTH_DISABLE_SIGN_UP=false",
        "-e", f"PAPERCLIP_AGENT_JWT_SECRET={secrets.token_hex(32)}", IMAGE,
    )
    try:
        fixture["wait_for_health"](container, f"{ORIGIN}/api/health", "Paperclip", paperclip=True)
        yield container
    finally:
        if docker("ps", "-q", "--filter", f"name=^{container}$"):
            docker("stop", "-t", "5", container)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-downstream-gate", action="store_true",
                        help="Fail on the native downstream approval gap tracked in #243.")
    args = parser.parse_args()
    with authenticated_fixture("rfc") as container:
        check_review(container, require_downstream_gate=args.require_downstream_gate)
