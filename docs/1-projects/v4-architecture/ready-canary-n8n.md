# Schedule the v4 Ready canary from n8n

First verify the [manual dispatcher](ready-canary-runner.md) and
[merge observer](ready-merge-gate.md) against the intended disposable
board and repository. Start `node compose/v4/ready-tick-server.mjs` on
the Docker host with the same private configuration and scoped
credentials. Set `RALPH_READY_TICK_TOKEN` to a distinct bearer generated
with `openssl rand -hex 32`. The listener binds only `127.0.0.1:8766`.

Import `compose/v4/workflows/ready-canary-tick.json` into n8n. Bind the
HTTP Request node to a Header Auth credential named `ralph-v4-canary-tick`
with `Authorization: Bearer <tick token>`. Keep the workflow inactive
until a manual execution succeeds and the operator verifies the actual
board and PR effects. On Colima the container connects through
`host.docker.internal`; other runtimes must provide an equivalent
host-loopback bridge without exposing the listener publicly.

The endpoint accepts no request body, paths, commands, or ticket IDs.
It runs only operator-configured tickets, one pass at a time. `202 accepted`
acknowledges reception, not delivery or merge. `202 busy` means the last
pass is still running. Completion appears in the board and ledger; host
logs contain only ticket IDs and status, not credentials or model output.
The bridge does not need Docker socket access inside n8n.
`401` means missing or invalid bearer, `403` rejects a browser Origin,
and `400` rejects a body or chunked transfer. A client timeout does not
prove the host pass failed to start.

Verified with n8n `1.70.3`: imported the workflow and a fixture credential
into a disposable instance, executed the manual trigger, and observed one
authenticated pass on the host. Scheduled activation and live canary
credentials remain operator steps.

For a repeatable local exercise with real Postgres and Vikunja and
synthetic SWE/GitHub, run from the repository root:

```bash
python3 compose/v4/test/ready-canary-integration.py
```

It uses the cached pinned Postgres and Vikunja images, creates disposable
containers, and doesn't use provider credentials or the operator's board.

## Live canary checklist

1. Use a disposable repo whose target branch supports enforced protection.
   Check required human review, stale-approval dismissal, and no automation
   bypass. A repo that cannot enable those settings is not suitable.
2. Provision a separate automation identity for the draft and a Director
   identity for review and merge. Keep router/observer GitHub credentials
   read-only and do not forward them to SWE.
3. Run the manual trigger with a Ready ticket mapped to an accepted RFC.
   Confirm its draft and pinned handoff. Move the ticket to Director PR
   Merge only after review. The Director merges the pinned head; run the
   observer and verify the task reaches Done. The pilot doesn't automate
   the peer-review approval or the human merge.
4. Replay the trigger. It must not rerun SWE or move an already acknowledged
   ticket. Activate the schedule only after these checks pass.
