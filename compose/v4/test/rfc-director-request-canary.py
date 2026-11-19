"""Exercise the first integrated slice against a real, disposable authenticated Paperclip board:
draft an RFC, run it through Council, request a native Director review, and resume a downstream
issue through Paperclip's native Done blocker release once the Director accepts."""

import json
import os
import runpy
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = runpy.run_path(str(HERE / "paperclip-rfc-canary.py"))

# Synthetic provider stand-in: no real Docker image, provider network, or model cost.
DOCKER_STUB = """#!/usr/bin/env node
const fs = require("node:fs");
const args = process.argv.slice(2);
if (args[0] === "rm") process.exit(0);
const job = JSON.parse(fs.readFileSync(0, "utf8"));
fs.writeFileSync(args[args.indexOf("--cidfile") + 1], "a".repeat(64));
if (job.role) {
  process.stdout.write(JSON.stringify({judge_role: job.role, verdict: "PASS", confidence: 0.9,
    blocking_issues: [], non_blocking_notes: []}));
} else {
  process.stdout.write(job.feedback ? "# Revised fixture RFC\\n" : "# Fixture RFC\\n\\nDraft body.\\n");
}
"""

# Replays the Director's own cookie session (set up by Python below) to call the real
# compose/v4/rfc-director-request.mjs against the real, disposable Paperclip container.
SCRIPT = """
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { requestDirectorReview } from "./rfc-director-request.mjs";
const { container, origin, realDocker, params } = JSON.parse(readFileSync(0, "utf8"));
const jar = "/tmp/rfc-director.cookies";
async function board(path, method = "GET", body) {
  const args = ["exec", "-i", container, "curl", "-sS", "--max-time", "10", "-b", jar, "-c", jar,
    "-H", `Origin: ${origin}`, "-X", method, ...(body !== undefined ? ["--json", "@-"] : []),
    `${origin}/api/${path}`];
  const raw = execFileSync(realDocker, args, { input: body !== undefined ? JSON.stringify(body) : "", encoding: "utf8" });
  return JSON.parse(raw);
}
console.log(JSON.stringify(await requestDirectorReview({ board, ...params })));
"""


def run_slice(container, params):
    real_docker = shutil.which("docker")
    node = shutil.which("node")
    assert real_docker and node, "docker and node must be on PATH to run this canary"
    with tempfile.TemporaryDirectory() as directory:
        docker_stub = Path(directory) / "docker"
        docker_stub.write_text(DOCKER_STUB)
        docker_stub.chmod(0o700)
        # The stub directory shadows the real "docker" on PATH; node itself still resolves normally.
        env = {"PATH": f"{directory}{os.pathsep}{os.environ.get('PATH', '')}",
               "HOME": os.environ.get("HOME", "/tmp"), "OPENROUTER_API_KEY": "synthetic-provider"}
        job = {"container": container, "origin": FIXTURE["ORIGIN"], "realDocker": real_docker, "params": params}
        result = subprocess.run(
            ["node", "--input-type=module", "-e", SCRIPT], cwd=HERE.parent,
            input=json.dumps(job), capture_output=True, text=True, env=env, timeout=30,
        )
        if result.returncode:
            raise RuntimeError(f"requestDirectorReview failed: {result.stderr}")
        return json.loads(result.stdout.strip())


def check_slice(container):
    def director(path, body=None, method="POST"):
        return FIXTURE["request"](container, "director", path, body, method)[1]

    director("auth/sign-up/email", {
        "name": "Director", "email": "director@fixture.invalid", "password": "fixture-not-a-credential-0",
    })
    director("bootstrap/claim", {})
    director_id = director("auth/get-session")["user"]["id"]
    company = director("companies", {"name": "RFC director request canary"})
    company_path = f"companies/{company['id']}"
    issue = director(f"{company_path}/issues", {"title": "Fixture RFC", "status": "backlog"})
    path = f"issues/{issue['id']}"
    downstream = director(f"{company_path}/issues", {
        "title": "Downstream canary", "status": "blocked", "blockedByIssueIds": [issue["id"]],
    })
    downstream_path = f"issues/{downstream['id']}"

    outcome = run_slice(container, {
        "directorUserId": director_id, "issueId": issue["id"],
        "brief": "Draft a fixture RFC", "model": "test/model", "domain": "non-coding",
    })
    assert outcome["route"] == "director", outcome
    assert len(outcome["verdicts"]) == 3 and all(v["verdict"] == "PASS" for v in outcome["verdicts"]), outcome
    assert outcome["artifact"].startswith("# Fixture RFC")
    print("PASS: draftRfc, Council panel, and decideRequestRoute produced a Director request")

    pending, = [row for row in director(f"{path}/interactions") if row["id"] == outcome["interactionId"]]
    assert pending["status"] == "pending" and pending["addresseeUserId"] == director_id
    assert pending["payload"]["target"]["revisionId"] == outcome["revisionId"]
    assert director(f"{path}/documents/rfc/revisions/{outcome['revisionId']}")["body"] == outcome["artifact"]
    print("PASS: Paperclip stored the exact drafted revision and addressed the confirmation to the Director")

    director(path, {"status": "in_review", "reviewInteractionId": outcome["interactionId"]}, "PATCH")
    assert director(downstream_path)["status"] == "blocked"
    decision = director(f"{path}/interactions/{outcome['interactionId']}/accept", {})
    assert decision["status"] == "accepted" and decision["result"]["outcome"] == "accepted"
    assert decision["resolvedByUserId"] == director_id
    print("PASS: the Director accepted the addressed revision through the native confirmation")

    updated = director(path, {"status": "done"}, "PATCH")
    assert updated["status"] == "done"
    resume = {"status": "todo", "resume": True, "comment": "Resume downstream work"}
    resumed = director(downstream_path, resume, "PATCH")
    assert resumed["status"] == "todo", "Native Done blocker release did not resume the downstream issue"
    print("PASS: native Done blocker release resumed the downstream issue after Director approval")


if __name__ == "__main__":
    with FIXTURE["authenticated_fixture"]("director-request") as container:
        check_slice(container)
