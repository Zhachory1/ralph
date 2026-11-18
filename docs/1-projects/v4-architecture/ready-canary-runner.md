# v4 manual Ready-to-Draft canary runner

Run this from the Docker host after provisioning the v4 board, migrating
Postgres, and setting up the dedicated SWE worker. It does not merge a PR
or advance a ticket to Done. Use a disposable repository with branch
protection and a dedicated, scoped automation identity.

Create a private JSON file outside this repository. Example shape:

```json
{
  "board": {
    "origin": "http://127.0.0.1:3456",
    "projectId": 2,
    "viewId": 8,
    "readyBucketId": 12
  },
  "sweCommand": ["/operator/bin/start-ralph-swe"],
  "jobs": {
    "101": {
      "repo": "example/project",
      "baseBranch": "main",
      "expectedAuthor": "example-bot",
      "repoPath": "/workspace/project",
      "product": "example",
      "rfcPath": "/workspace/docs/accepted-rfc.md",
      "acceptanceCriteria": ["Run the project check"]
    }
  }
}
```

The command is an operator-supplied executable. Its final argument is a
JSON object containing the `implement_spec` arguments. Invoke the
isolated `ralph-swe` profile with that object; use checkout and RFC paths
as seen by that worker. Do not give the profile or command the router's
Vikunja or GitHub credential. Provision any GitHub publishing credential
for the harness separately, with branch protection enforced on the repo.

With the board and read-only GitHub credentials supplied from operator
secret sources, run:

```bash
RALPH_READY_CONFIG=/path/to/private-ready-config.json \
  node compose/v4/run-ready-ticket.mjs 101
```

The runner uses `VIKUNJA_API_TOKEN` for board reads and the host `gh`
identity for PR verification. It reads the Ready bucket twice and verifies
unresolved `blocked` relationships before invoking SWE. A stale lease
cannot record a draft. A retry after expiry checks for the existing draft
on `ralph/vikunja-<ticket>` before invoking SWE again. It validates the
GitHub repo, author, base, branch, draft status, and head SHA before
putting the row in `waiting_merge`. If a ticket is moved or a worker does
not produce a draft, the claim expires after 25 minutes; no merge or
board update is attempted by this command.
