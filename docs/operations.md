# Operations and onboarding

How to stand Ralph up, change it safely, and recover it. Components are described in [infrastructure.md](infrastructure.md) and flows in [workflows.md](workflows.md).

## A new instance from zero

1. `cd compose && cp .env.example .env.<name>`. Set `RALPH_INSTANCE`, ports, `RALPH_WORKSPACES`, an absolute `RALPH_SANDBOX_ROOT`, the GitHub tokens and logins, and the model provider.
2. `../bootstrap/secrets --env .env.<name>` fills generated secrets into that file.
3. `RALPH_ENV_FILE=.env.<name> docker compose --env-file .env.<name> up -d`. This runs `init` (profiles, boards, configs), then the services.
4. Sign in to the model provider if it uses a browser flow, from the repository root: `RALPH_ENV_FILE=.env.<name> ./bootstrap/login`.
5. Allowlist repositories (`RALPH_REVIEW_ALLOW`, `RALPH_MAINTAIN_ALLOW`) and the docs repo (`RALPH_DOCS_REPO`). Keep `COMPOSE_PROFILES=knowledge` for local search; the first `init` downloads ~1 GB of models and indexes the docs repo.
6. Check, from `compose/`: `RALPH_ENV_FILE=.env.<name> docker compose --env-file .env.<name> ps`, then a dispatched card that runs `gh api user` from the terminal, then `../bootstrap/knowledge-status <instance>`.

The separate v4 pilot uses a [dedicated adapter login](1-projects/v4-architecture/hermes-harness-integration.md#adapter-authentication) rather than a copy of a personal OAuth session.

After caching the two pinned images in `compose/v4/paperclip-canary.Dockerfile`, run from the
repository root. The build context is `compose/v4` (not stdin), so the Dockerfile can apply
`compose/v4/paperclip-handoff.patch` -- vendored from upstream Paperclip PR #18510, see
[infrastructure.md](infrastructure.md) -- and rebuild the patched server before the image is
tagged; `paperclip-canary.Dockerfile.dockerignore` keeps everything else in `compose/v4` out of
that context. The server rebuild needs more than 2.6 GiB of available Docker memory; use a
separate build host if a running board leaves insufficient headroom:

```sh
docker build --network none -t ralph-paperclip-canary:2026.1117.0 \
  -f compose/v4/paperclip-canary.Dockerfile compose/v4
python3 compose/v4/test/paperclip-canary.py
```

The test uses synthetic model responses and a disposable Git repo. It removes its container and
volume and does not use live routing. The built image stays cached.

Run `python3 compose/v4/test/paperclip-rfc-canary.py` with the same cached image to probe
RFC approval and store the approved bytes in a disposable Git repository inside the container.
By default it exercises Paperclip's native behavior: Done resumes a blocked downstream issue
regardless of which revision was approved ([#243](https://github.com/Zhachory1/ralph/issues/243)
tracks hardening this). Add `--require-downstream-gate` only if your deployment wants an
unmatched revision to fail the run instead. The probe's separate Git-storage check
(see [workflows](workflows.md)) runs only in the fixture; it does not enforce live publication.
The probe does not push to a remote or change live routing.

Run `python3 compose/v4/test/paperclip-routing-canary.py` with the same image and Node.js
on the host. It uses scripted proposals and Council verdicts against an authenticated,
disposable Paperclip board. It creates fixture assignments only on `advance`.

### First integrated slice: RFC, Council, and a native Director review request

Set `OPENROUTER_API_KEY` for drafting and the Council panel. Set a temporary scoped
`PAPERCLIP_API_KEY` for the board -- the same loopback-origin, bearer-token convention as
`routine-audit-worker.mjs` above, no new credential scheme. From the repository root, with a
Paperclip board reachable at `http://127.0.0.1:PORT`:

```sh
echo '{"directorUserId":"<director-user-id>","issueId":"<issue-id>",
       "brief":"<what the RFC should cover>","model":"openai/gpt-4.1-mini","domain":"non-coding"}' \
  | node compose/v4/rfc-director-request.mjs http://127.0.0.1:PORT
```

The job is config, never a secret (`directorUserId`, `issueId`, `brief`, `model`, `domain` --
`"coding"` or `"non-coding"` -- and optionally `currentDraft` and `feedback` to revise); it can
also come from a file instead of stdin: `node compose/v4/rfc-director-request.mjs
http://127.0.0.1:PORT job.json`. The command drafts the RFC (`author.mjs`), runs the real
Council panel, and calls `decideRequestRoute` with `initiative: true`; only on route
`"director"` does it `PUT` the draft as the issue's `rfc` document and open a Director-only
`request_confirmation` naming that exact revision, printing the result as JSON. It never polls
for the Director's decision or marks the issue done; accepting, publishing, and releasing a
downstream native blocker are the same Paperclip primitives already covered by the RFC canary
and the Git-storage check above. Drafting and the panel can incur provider charges. Revoke the
temporary key after use.

Run `node --test compose/v4/test/rfc-director-request-cli.test.mjs` for a focused check of the
CLI's own transport against a local HTTP stub instead of a minted board key: it asserts the
bearer token and loopback origin reach every board call, and that a missing `PAPERCLIP_API_KEY`
fails closed before any board call is made. Run
`node --test compose/v4/test/rfc-director-request.test.mjs` to check the library function
directly (stubbed provider, mocked board, no containers). Run
`python3 compose/v4/test/rfc-director-request-canary.py` with the same cached Paperclip image
and Node.js on the host for the full real-board rehearsal: it stubs the provider worker (no
model cost), drafts and reviews a real RFC, opens a real Director-only confirmation, accepts
it, marks the issue done, and confirms a downstream issue blocked on it resumes through
Paperclip's native Done blocker release.

Run `python3 compose/v4/test/paperclip-routine-canary.py` to probe the manual PR audit
flow. It uses the same cached image for Paperclip, the pinned upstream Paperclip image
for the isolated audit checks, and Git and Node.js on the host. No GitHub login is needed.

To process one manual audit Routine run on a local sandbox board, trigger it with
`source: "manual"` and a payload such as
`{"repo":"OWNER/REPO","pr_number":7,"head_sha":"<full lowercase SHA>"}`. Reuse the same
idempotency key for the same PR and head. Set `RALPH_AUDIT_REPOS` to the trusted clone path
and its pinned image/test/lint configuration. Set a temporary `PAPERCLIP_API_KEY` from
an account that can access only the sandbox company. Then run:

```sh
node compose/v4/routine-audit-worker.mjs http://127.0.0.1:PORT COMPANY_ID ROUTINE_ID RUN_ID OWNER/REPO LOCAL_CLONE
```

Use a dedicated clone with `https://github.com/OWNER/REPO.git` as its origin and only read
access. Run one worker at a time. `published` means the linked Paperclip issue has an audit
document, `already_published` means the existing document was left alone, and `stale`
means no dossier was written for the outdated head. Revoke the temporary key after use.
This command does not create triggers, publish GitHub reviews, or close the issue.
Run the worker again on a published Routine run to recheck it. If the PR moved, it
returns `superseded` and adds an **Audit superseded** document without editing the
original dossier. The marker is permanent even if the PR later returns to the same SHA.
Audit the new head with its own run. Nothing checks this on a schedule.

To discover PRs requesting this account's review in one allowlisted repo and queue each
one's current head for the audit above, set `GH_TOKEN`/`gh auth login` for a read-only
account (no write scope needed: this issues only `gh search prs` and `gh pr view`) and
the same temporary scoped `PAPERCLIP_API_KEY`, then run:

```sh
node compose/v4/routine-discovery-worker.mjs http://127.0.0.1:PORT COMPANY_ID ROUTINE_ID OWNER/REPO [REVIEWER=@me]
```

This is a one-tick command, not a scheduled poller: run it by hand and inspect its JSON
output before wiring it to any timer. For each open PR in `OWNER/REPO` requesting
`REVIEWER`'s review (`@me` by default), it reads the current head SHA and triggers a
manual Routine run with idempotency key `pr-<repo>-<number>-<head>` and the same payload shape
`routine-audit-worker.mjs` expects. Paperclip's own idempotency key dedups a repeat
discovery of the same (repo, PR, head): a second trigger returns the same run and linked
issue instead of creating another one, so this command is safe to run repeatedly by hand.
A PR whose head cannot be read is skipped and logged to stderr. To audit and publish
dossiers in the same invocation, configure `RALPH_AUDIT_REPOS` as above and use a
dedicated `always_enqueue` Routine so distinct heads do not coalesce:

```sh
node compose/v4/routine-discovery-worker.mjs http://127.0.0.1:PORT COMPANY_ID ROUTINE_ID OWNER/REPO --audit LOCAL_CLONE
```

Configure the host scheduler to run at most one invocation at a time. Each invocation
audits serially and reports `audit_status` for each run. Failed audits leave the tick
nonzero; rerunning reuses existing runs and dossiers. To close published dossiers after
human review, append the reviewer's literal GitHub login and `--reconcile` to the command.
This checks the most recent 100 Routine runs separately from current review requests,
so a review can be found after GitHub removes the request. Only submitted reviews by the
named human on the dossier's exact head count. The dossier contents are left intact.
No GitHub writes are made.

For the code-review flow, configure a host-local Paperclip process seat with the command
`node /path/to/compose/v4/pr-review-dispatch.mjs MANAGER_ID SYNTHESIS_ID PUBLISHER ACCOUNT1 ACCOUNT2`.
Give it access to both existing `gh` logins, and create a Paperclip Routine assigned to
that seat with `skip_if_active` and a `*/10 * * * *` schedule. Keep its title and
description static, and set the seat's maximum concurrent runs to one. Paperclip supplies
the run token and task context; the worker creates review tasks and completes its own scan
task. Review tasks are visible to the company; store private PR evidence there only with the
owner's consent.

The worker separates authored and incoming PRs, uses the native seven-day task
idempotency key per head, and checks the GitHub review marker before dispatch. Synthesis
posts the review and checks for that marker if a response is unclear. An unconfirmed post
needs human reconciliation. Searches at GitHub's 1,000-result limit, incomplete searches,
300-file comparisons, and diffs over 1 MiB are held rather than truncated. GitHub search
covers at most 4,000 repositories, so discovery is best effort beyond that. This setup does
not enforce no-shell isolation for review seats (#327) or merge PRs.

Run `node --test compose/v4/test/routine-maintenance-worker.test.mjs` and
`node --test compose/v4/test/routine-maintenance-worker-cli.test.mjs`, plus
`python3 compose/v4/test/paperclip-maintenance-canary.py` with the same cached image, to check
the first bounded slice of outbound PR maintenance through Routines (#263,
[routine-maintenance-worker.md](../compose/v4/routine-maintenance-worker.md)): detect, dedupe,
wake the existing OpenCode worktree, or escalate to the Director — never rebase, force-push, or
merge. Linking an issue to the PR it tracks, the exact run command, and the two separate
concurrency requirements (`coalesce_if_active` on the Routine *and* running this worker
serially — one does not imply the other) are documented in that runbook, not repeated here.

Run `node --test compose/v4/test/comms-intake-worker.test.mjs` to check the first
offline slice of Gmail/Chat intake into Paperclip Routines (#266,
[comms-intake.md](1-projects/v4-architecture/comms-intake.md)): a normalized Gmail or
Chat event, read on stdin by a local CLI that trusts its caller already authenticated
it with Google, an allowlist, and one idempotent `POST routines/{id}/run`. Synthetic
throughout, including one test that spawns the real
`compose/v4/comms-intake-worker.mjs` CLI against a loopback-only in-process stand-in —
no Docker, no live Google credentials, and no webhook registered anywhere by this.

For the opt-in Google Chat bridge, configuration and offline tests are in
[comms-intake.md](1-projects/v4-architecture/comms-intake.md#authenticated-chat-bridge).
The bridge listens on loopback only; no public ingress or live routing is created by setup.

Run `python3 compose/v4/test/qmd-approved-canary.py --qmd /path/to/qmd` with qmd 2.8.3
and Git to verify the approved Git search boundary, including replacement of the old search
result after a refresh to a second approved revision. The probe uses lexical search only;
it does not download embedding models, use the live index, or need a running qmd service.

### Refresh a separate qmd index from approved PARA snapshots

Use qmd 2.8.3 and a dedicated detached worktree of the approved PARA repository.
`APPROVED_SHA` must be the full commit ID from the publication of the approved Markdown.
Do not choose it from the newest draft, an issue's Done status, or the branch tip alone.
Set `APPROVED_TREE` and `QMD_STATE` to separate absolute paths outside the ordinary
checkout. The approved commit's `docs/` tree must contain only publishable documents.

```sh
# One-time setup from the local PARA clone:
git worktree add --detach "$APPROVED_TREE" "$APPROVED_SHA"
mkdir -p "$QMD_STATE/config" "$QMD_STATE/cache"
export QMD_CONFIG_DIR="$QMD_STATE/config" XDG_CACHE_HOME="$QMD_STATE/cache"
export INDEX_PATH="$QMD_STATE/cache/index.sqlite"
```

For the first index and each refresh, stop readers of this separate index first.
Use the same environment paths and the verified `APPROVED_SHA`:

```sh
(
  set -eu
  worktree=$(git -C "$APPROVED_TREE" status --porcelain --ignored)
  test -z "$worktree"
  git -C "$APPROVED_TREE" switch --detach "$APPROVED_SHA"
  test "$(git -C "$APPROVED_TREE" rev-parse HEAD)" = "$APPROVED_SHA"
  entries=$(git -C "$APPROVED_TREE" ls-tree -r HEAD -- docs)
  printf '%s\n' "$entries" | awk '$1 == "120000" || $1 == "160000" { bad=1 } END { exit bad }'
  test -d "$APPROVED_TREE/docs"
  cd "$QMD_STATE"
  # Run this command only on the first index:
  # qmd collection add "$APPROVED_TREE/docs" --name approved --mask '**/*.md'
  qmd update
)
```

Do not use `qmd update --pull`, add other collections to this index, or point the
existing Ralph knowledge service at it. Check a distinctive term from the approved
revision with `qmd search '<term>' -c approved` before resuming readers. If refresh
fails, leave readers stopped until the last verified snapshot is re-indexed. This
manual setup is lexical only and does not enable Hindsight retention or a new service.

### Connect the approved qmd index to a Paperclip agent

Paperclip's `hermes_local` adapter runs the Hermes CLI (`/opt/hermes/bin/hermes`) inside
the same combined Paperclip/Hermes container a seat's run uses -- there is no separate
Hermes container or named profile (`-p`) to target; `HERMES_HOME` (default `/paperclip/.hermes`,
whatever the seat's `adapterConfig.env.HERMES_HOME` names) is what selects its config.
Paperclip itself has no agent-level MCP registration of its own (its "Gateways"/"remote MCP"
UI exposes Paperclip's *own* data to external MCP clients, the opposite direction); the
native connector remains Hermes' own `hermes mcp add`.

With `APPROVED_TREE` and `QMD_STATE` already built by the runbook above and reachable inside
that same container (there are no host bind mounts; keep them under the container's own
`/paperclip` volume and run the runbook's commands with `docker exec`), grant the seat's
exact `HERMES_HOME` the index through the discovery-first native connector:

```sh
PAPERCLIP_CONTAINER=my-paperclip-container \
HERMES_HOME=/paperclip/.hermes \
QMD_STATE_DIR="$QMD_STATE" \
  compose/v4/bootstrap-qmd-gateway.sh
```

It installs qmd 2.8.3 into `/paperclip/npm-global` inside that container (network access
depends on the deployment; the offline canary below copies a pre-built binary in instead),
then runs `hermes mcp add` with `--command` pointed at that binary and `--env` pointed at
`$QMD_STATE_DIR`'s `config`/`cache`. Discovery connects immediately and lists exactly the
four read tools qmd's own MCP server exposes (`query`, `get`, `multi_get`, `status`) --
there is no write or admin tool to exclude. It prompts to confirm enabling them and, on a
rerun, to overwrite the existing registration; the script pipes "y" to both. Run
`hermes mcp test qmd` (with the same `HERMES_HOME`) afterward to re-verify on demand, and
`hermes tools list` to confirm "MCP servers: qmd all tools enabled".

Run `python3 compose/v4/test/paperclip-qmd-gateway-canary.py` to rehearse this against the
actual Paperclip Compose target offline: it starts the same disposable, network-disabled
canary container as `paperclip-canary.py`, copies in a qmd 2.8.3 binary built from the
existing `compose/Dockerfile.knowledge` image (no network inside the canary container),
builds its own approved/draft/transient Git fixture entirely inside that one container
(nothing shared or bind-mounted), runs the bootstrap script above against it, confirms
Paperclip's own `adapters/hermes_local/test-environment` check passes for an agent whose
`adapterConfig.env.HERMES_HOME` is that exact directory, creates that `hermes_local` agent,
and then speaks MCP directly to the exact command/env Hermes stores to confirm the result
is the approved text and excludes the draft revision and transient log, before and after a
refresh to a second approved revision. It does not enable Hindsight, dispatch a model-driven
run, or touch a live target, and it removes its container and volumes on exit. Simulating
the model's own tool discovery through Hermes' deferred tool_search/describe/call catalog is
a separate, not-yet-covered gap (see the canary's module docstring).

### Add QMD to an existing Paperclip network

Cache the pinned `ralph-knowledge-qmd:2.8.3` image, then set `PAPERCLIP_NETWORK` to the
existing board network. Start only the QMD service; this does not recreate Paperclip:

```sh
PAPERCLIP_NETWORK=EXISTING_NETWORK docker compose -p PROJECT \
  -f compose/v4/paperclip-knowledge.yml up -d --no-deps --no-build --pull never knowledge
```

The service exposes MCP only on the Docker network and stores its data in a named volume.
Use one QMD service per Paperclip network; the service name `knowledge` is its network alias.
Any seat that can reach that network may query it. The default approved-only snapshot
procedure remains above. If the Director explicitly chooses a whole-repository index,
drafts and unmarked files become searchable; retrieval does not approve their contents.
Create a clean detached worktree inside the QMD volume, apply the same symlink and submodule
checks as above, and add the worktree root as the collection path:

```sh
PAPERCLIP_NETWORK=EXISTING_NETWORK docker compose -p PROJECT \
  -f compose/v4/paperclip-knowledge.yml exec knowledge \
  qmd collection add /var/lib/ralph-knowledge/snapshot --name docs --mask '**/*.md'
```

Register `http://knowledge:8181/mcp` in the intended seat's Hermes home with `hermes mcp add`
and include `qmd` in explicit toolsets. This service does not need the in-process
`bootstrap-qmd-gateway.sh` installation. The snapshot does not advance automatically.
Initial lexical indexing does not build vectors; `qmd embed` downloads a local model.

### Paperclip cold restore drill

Run `python3 compose/v4/test/paperclip-restore-canary.py` with the cached canary image.
Use a separate Docker host if the preview runs on a small VM. The drill refuses
to run alongside the named pilot preview when Docker has less than 4 GiB of memory.
The drill creates two authenticated, network-disabled containers without published ports,
each with its own disposable named volume at `/paperclip`. It shuts down the source
cleanly, copies `/paperclip` with `docker cp -a`, restores into the stopped target, and
signs in to verify the original issue and run. To rehearse rollback, it
writes a target-only comment, stops the target, restarts the untouched source checkpoint,
and verifies that the comment is absent. The containers, volumes, and archive are removed on exit.

This exercises a cold copy of the embedded database and instance files at the same image
version. It does not back up the current preview, test scheduled backups or external
PostgreSQL, or preserve changes written after switching to the restored instance. A real
restore also needs the matching deployment configuration and signing secret, handled separately.

Add `--native` to exercise Paperclip's built-in manual database backup and upstream
restore helper instead. The drill imports the compressed SQL archive into a new
database on a separate disposable instance and checks the account, issue, and completed
run rows. This proves logical data recovery, not application boot from the restored
database, scheduled backups, or recovery of filesystem artifacts such as worktrees.

### Bootstrap a fresh private Paperclip preview

This starts a separate Compose project and named state volume. Do not reuse the name or
state of an existing installation. Cache both images pinned in
`compose/v4/paperclip-canary.Dockerfile`; then, from the repository root, build locally:

```sh
docker build --network none -t ralph-paperclip-private:2026.1117.0 \
  -f compose/v4/paperclip-canary.Dockerfile compose/v4
```

Set a new random `PAPERCLIP_AGENT_JWT_SECRET` in your environment from a private store;
keep that same secret when recreating the instance. Start with sign-up open only long
enough to create and claim the first admin:

```sh
PAPERCLIP_AUTH_DISABLE_SIGN_UP=false docker compose --env-file /dev/null -p ralph-private -f compose/v4/paperclip-private.yml up -d
```

Open `http://127.0.0.1:4320/`, create the admin, and confirm `/api/health` reports
`bootstrapStatus: ready`. Close sign-up immediately by recreating the container with
the same state volume and secret:

```sh
PAPERCLIP_AUTH_DISABLE_SIGN_UP=true docker compose --env-file /dev/null -p ralph-private -f compose/v4/paperclip-private.yml up -d
```

`PAPERCLIP_PORT` overrides both sides of the loopback binding and the auth origin.
Keep it the same in both commands. The bridge allows outbound traffic. This file starts
no provider relay, agent jobs, or companion services, and does not migrate or replace
the running pilot. `python3 compose/v4/test/paperclip-private-canary.py` rehearses this
with a disposable account and volume and verifies sign-in after container recreation.

### Prepare a private Paperclip target

Build the pinned image as above. Use a new Compose project and a dedicated host. Set
`PAPERCLIP_AGENT_JWT_SECRET` and a dedicated URL-safe `PAPERCLIP_POSTGRES_PASSWORD`
(for example, a random hex value) from a private store. For remote access, set
`PAPERCLIP_TAILNET_HOSTNAME` to the host's Tailscale DNS name and `PAPERCLIP_PUBLIC_URL`
to its `https://` origin. On the Docker host, use `tailscale serve --bg http://127.0.0.1:4320`
(not Funnel), and restrict access with tailnet policy. For localhost-only setup,
set the public URL to `http://127.0.0.1:4320` instead.

```sh
docker compose --env-file /dev/null -p ralph-target \
  -f compose/v4/paperclip-private.yml -f compose/v4/paperclip-postgres.yml up -d
```

Use the same temporary sign-up procedure as for the preview, and leave sign-up closed
before admitting other users. This initializes a new database, not a migration. Keep
both named volumes: Postgres holds board data; Paperclip state holds filesystem artifacts.
Set `OTEL_EXPORTER_OTLP_ENDPOINT` only for an approved collector. Rehearse bootstrap
with `python3 compose/v4/test/paperclip-private-canary.py --postgres`; it does not
configure Tailscale, agents, or live routing.

For the external database recovery rehearsal, run
`python3 compose/v4/test/paperclip-private-canary.py --recovery`. It stops a disposable
board, takes a custom-format `pg_dump`, restores into a separate PostgreSQL database,
and signs in after switching Paperclip to it with `PAPERCLIP_DATABASE_NAME`. It switches
back to the original database and verifies a post-restore write did not affect the source.
This is a database-only rehearsal. For the real switch, retain the matching state volume,
configuration and signing secret, suspend ingress and scheduling, and decide how to
handle any writes made after the switch before rolling back.

### Provision the Paperclip organization

Create an empty company on the target and mint a temporary key from its administrator.
Export it as `PAPERCLIP_API_KEY`. Supply your governance Markdown and an OpenCode adapter
JSON file containing an explicit `model`, plus any `OPENCODE_CONFIG_CONTENT` overrides.
Do not put provider keys in that file. Set budgets in integer cents:

```sh
python3 compose/v4/provision-paperclip.py "$PAPERCLIP_PUBLIC_URL" "$COMPANY_ID" \
  "$MANIFEST_FILE" "$OPENCODE_ADAPTER_FILE" --company-cents 1000 --agent-cents 200
```

The command creates five seats, installs the shared instructions, and configures monthly
budgets with 80% warnings and hard stops. The orchestrator retains Paperclip's built-in
CEO task assignment authority but cannot hire agents. The command does not schedule tasks or add GitHub, provider,
or deploy credentials. Use protected branches and scoped connections before enabling work;
instructions alone cannot enforce the human merge/deploy boundary. Revoke the temporary
board key after setup. If provisioning stops partway through, inspect the company before
retrying: this command does not overwrite or reconcile existing seats.

For v4.1 startup, run the same command **serially** after the board is ready, with
`--reconcile "$COUNCIL_MODELS_FILE"`. The JSON object maps `Council Architecture`,
`Council Security`, and `Council Scope` to explicit `provider/model` IDs from three
distinct families. Use the same governance manifest, budgets and SWE model on restart.
This mode adds the Feature and Review Workflow Managers, Librarian, Comms and Council;
Comms reports to the human Director. The managers receive native task-assignment permission.
It recognizes the pilot's `Ralph Orchestrator`, `RFC Author`, `OpenCode Developer` and
`SRE Ops` names. Other seats, instructions, paused states and credentials are preserved.
Resolve reported identity, model, budget, hiring, heartbeat or reporting conflicts on
the board before retrying. A matched manager must have an explicit `tasks:assign`
grant; an implicit membership default is not sufficient. Existing instructions and grants are not rewritten. This
provisions seat definitions; the adapter credentials and dispatch checks are separate.
If an OpenCode seat already has `GH_TOKEN`, it can use that credential through its terminal
without requesting a separate GitHub connection. Configure the credential separately; the
provisioner does not create it or rewrite existing seat instructions.
When the Feature Manager's Paperclip MCP tools are explicitly allowlisted, include the native
`paperclipListComments` tool so it can read judges' task comments under its own run identity.

### Arm a prepared Paperclip sandbox task

This is for the localhost preview only. Task and agent setup is separate. In Paperclip,
verify that the sandbox has only the intended task ready to launch, that its Hermes and
OpenCode agents use the `ralph-pilot` relay and issue worktrees, and that periodic runs
are off. The launcher checks Docker and the checkout, not the board's agent settings.

1. Run `python3 compose/v4/pilot-launch.py --check` from the repo root. It prints the
   issue selected by the preview's `/paperclip/pilot-next-task` file. Confirm that issue
   in the dashboard. If the file is missing, the task is not prepared.
2. With `OPENROUTER_API_KEY` already set privately in the local environment, run
   `python3 compose/v4/pilot-launch.py`. Once it prints **Relay ready**, launch only the
   named issue in Paperclip. Launching can incur provider charges.
3. Wait for both assignment runs to succeed in Paperclip, then stop the launcher
   with Ctrl-C and pause both sandbox agents. The relay also stops at four minutes.
   A successful run is not the same as a Done issue. If a run fails or exceeds the
   limit, check its state before retrying.
4. For the digest title-order pilot, run
   `python3 compose/v4/pilot-launch.py --finish-digest-pilot <sandbox-company-uuid>`.
   Approve the temporary Paperclip prompt. The command checks the assignment and
   verifies the expected test in a network-disabled container, then records Done and
   prevents rearming the same task. It does not use the provider key or rerun the agent.
Other task types need a verifier specific to their expected result.

Paperclip board keys inherit the approving user's authority. Selecting a company on the
CLI approval prompt does not restrict the key to that company. Keep admin keys out of
agents and long-lived configuration. For a credential rotation drill, run
`python3 compose/v4/test/paperclip-board-key-canary.py`. It checks that a sandbox-only
operator's key is denied access to the other company, that its replacement works, and
that revoking either key does not leave it usable. This uses a disposable board and tests
Paperclip board keys, not model-provider keys or deployment signing secrets.

The relay allows up to 12 provider requests with bounded input, output, and per-response
size; this is not a dollar cap. It cannot distinguish between agents sharing the preview
container. Do not run unrelated tasks while it is armed or treat it as production isolation.

To rotate a dedicated pilot provider key, disarm the relay and pause the sandbox
agents first. Provision a replacement with the same or tighter spend limit and
update the private source of `OPENROUTER_API_KEY`; do not add the key to Paperclip.
The next launcher process reads the replacement on stdin. Verify it only on an
authorized trial, then revoke the old key at the provider. If the old key is suspected
compromised, revoke it immediately instead of keeping it for rollback. Run
`python3 compose/v4/test/test-pilot-model-relay.py` for the dummy-key rehearsal;
it does not verify provider-side revocation or rotate a real credential.

For the standalone v4 Council panel, `python3 compose/v4/test/council-judge-docker.py`
checks the cached pinned Hermes image and launcher/panel wiring with three synthetic model routes,
no external network, and a non-functional provider key. It also checks disposable-container cleanup.
This does not verify real model availability, paid calls, or production routing.
The launcher uses `--pull=never`; its pinned Hermes image must already be cached.

For an opt-in live check, set `OPENROUTER_API_KEY` in the environment and run
`node compose/v4/test/council-live-smoke.mjs --live`. This sends a fixed synthetic RFC
and the v1 rubric to three model routes, costs money, and has no enforced spend cap.
Costs depend on token use and [OpenRouter's current model prices](https://openrouter.ai/models).
The result reports the panel decision and verdicts; it does not route work or prove
the identity of the models actually served. Judge egress remains unrestricted.

### Standalone v4 Council tool

Run `npm ci` in `coding-harness/` and cache the pinned Hermes image first. For the isolated
profile, `host.docker.internal` must reach the host's loopback port (verified with Colima).
Set `OPENROUTER_API_KEY` privately and generate a fresh 32-byte hex
`RALPH_COUNCIL_MCP_TOKEN`; grant it only to the host service and launcher. Set
`RALPH_COUNCIL_STATE_DIR` to an existing absolute directory owned by the service user with mode
`0700`. Reuse the same directory for all instances under this user and Docker daemon; separate
state directories or users do not fence each other. Start
`node coding-harness/council-mcp.mjs` on the host. It listens on `127.0.0.1:8766`;
`RALPH_COUNCIL_MCP_PORT` overrides the port. Stop the service when finished and discard the token.

To invoke the profile from a host process, import `runCouncilProfile` from
`compose/v4/council-profile.mjs` and pass `{artifact, revisions, model, port}`. The artifact is
text of at most 16 KiB; `revisions` is 0–2; `model` is an OpenRouter `provider/model`; and
`port` must match the service. Both host processes need the same master token and provider key.
The profile gets a separate single-job token, not the master token. The launcher returns
`{jobId, result}`, where `result` is retrieved from the service rather than the model's prose.
**A profile invocation and its panel use paid model calls, without an enforced spend cap.**
Only send artifacts suitable for the provider. The tool exposes no edit or board tools. Anyone with
the master token can register jobs repeatedly; there is no total spend limit. The legacy
`ralph-council` profile is not this isolated runtime.

Results remain in service memory until acknowledged; they do not survive a service restart.
After recording a result, send an authenticated `DELETE /jobs/<jobId>` to release the slot.
The master token also permits `GET /jobs` (current ID and status) and `GET /jobs/<jobId>` (status,
profile activity, result, and the reviewed artifact's SHA-256). A disconnected tool client does
not cancel paid judges: the result remains retrievable while the service stays up. If registration's response is lost, use `GET /jobs`
to find the ID. The slot cannot be released until the host confirms profile cleanup; the launcher
does this after the container exits or its removal is confirmed. Do not put bearer tokens in
command arguments or logs.

The state directory's `council-job.json` contains only the job ID. A live job, crash, or unconfirmed
cleanup blocks new jobs, including after restart. `GET /jobs` can report `locked` because another
service owns the job, not just after a crash. Before recovering, stop **all** services and launchers
using that state directory, including paused launchers and pending Docker commands. Check the
marker's job ID, service logs, relevant Docker CLI processes, and containers named
`ralph-council-judge-<jobId>-...` or `ralph-council-profile-<jobId>`. Confirm the daemon is reachable
and no associated process can still create a container; wait for watchdogs and resolve any remaining
containers. Only then may the operator remove the marker and restart the service. A profile cleanup
error also reports its container ID or name and recovery-file path. Never remove unrelated containers.

Run `node --test coding-harness/test/council-mcp.test.mjs coding-harness/test/council-job.test.mjs compose/v4/test/council-profile.test.mjs`
and `python3 compose/v4/test/council-profile-docker.py` for synthetic checks. The latter uses the
cached image and Colima's bridge, sends no external model requests, and creates temporary disposable
containers. These checks do not verify real provider access or model output.

### Standalone v4 RFC drafting

From a host process, import `draftRfc` from `compose/v4/author.mjs` and pass `{brief, model}`.
For a revision, also pass `currentDraft` and `feedback`. Use an OpenRouter `provider/model`
and set `OPENROUTER_API_KEY` privately. The brief and feedback together can be at most 4 KiB;
the current draft and returned Markdown can each be at most 16 KiB. The pinned Hermes image
must be cached. Drafting sends the input to the provider and can incur charges. It never
publishes, previews, or requests Council automatically.

Run `node --test compose/v4/test/author.test.mjs` and
`python3 compose/v4/test/council-judge-docker.py` for synthetic draft and revision checks,
including the actual pinned Hermes worker without external networking.

### Standalone v4 Docs-as-PRs

Set `RALPH_DOCS_REPO` to the single GitHub docs repository and
`RALPH_DOCS_GITHUB_TOKEN` to a fine-grained token for a dedicated bot account scoped to that
repository, with Contents and Pull requests write permissions. Pass draft text to the host; the
author worker does not need this token. Do not use the legacy
credentialed `ralph-author` profile for that handoff. Before using it, protect the default branch:
require pull requests and approval from the human docs owner (for example with CODEOWNERS),
dismiss stale approvals after updates, and do not give the bot bypass rights. Limit writes to
`docs/rfc-*` to trusted publishers. Verify these protections in a sandbox; a draft PR is not a
merge guard.

Run `node compose/v4/docs-pr.mjs <job.json>` on the host with `slug` and `artifact` (Markdown
text, up to 16 KiB). It returns `pr_url` and `head_sha`. To revise an existing draft, include
`expectedHead` with its current SHA; the publisher appends a commit and refuses stale updates.
A retry with the same content returns the same draft. The publisher refuses PRs already marked
ready for review, but GitHub cannot atomically check draft status and push a Git ref. Finish
publishing before marking a PR ready or closing it. If they race, the update may land and the
publisher reports the pushed SHA for inspection. The publisher does not approve or merge.

Council is separate and not required to publish. If you use it, match `artifactSha256` from
the completed job to the RFC bytes at the commit being reviewed before attaching any findings.
Do not carry a verdict forward to changed text.

Run `node --test compose/v4/test/docs-pr.test.mjs` for the local Git and synthetic GitHub check.

### Optional v4 docs preview

Set `RALPH_V4_DOCS_ROOT` to an absolute path shared with Docker. It must be a docs checkout
with `mkdocs.yml` at its root. From the Ralph repository root:

```bash
export RALPH_V4_DOCS_ROOT=/absolute/path/to/org-docs
COMPOSE_DISABLE_ENV_FILE=1 docker compose -f compose/v4/docs-preview.yml up -d
COMPOSE_DISABLE_ENV_FILE=1 docker compose -f compose/v4/docs-preview.yml down
```

The preview serves the selected checkout at `127.0.0.1:8000` and Kroki at `127.0.0.1:8001`;
override ports with `RALPH_V4_DOCS_PORT` and `RALPH_V4_KROKI_PORT`. These endpoints have no
authentication; other local users can read the preview. MkDocs and Kroki have outbound network
access. The services have no resource limits; do not expose them to untrusted users or diagrams.
Use only a trusted MkDocs configuration and plugins. See Material's
[privacy guidance](https://squidfunk.github.io/mkdocs-material/setup/ensuring-data-privacy/) to
disable external theme assets if browsers must not contact third parties. Kroki provides Mermaid,
Graphviz and PlantUML PNG, and Excalidraw SVG; it does not render Excalidraw PNG. For example,
`curl -H 'Content-Type: text/plain' --data-binary @diagram.mmd http://127.0.0.1:8001/mermaid/svg`
returns SVG. MkDocs and Kroki are separate: this stack does not automatically embed Kroki diagrams.
It serves drafts present in the checkout, but does not fetch drafts from other PR branches.
Material for MkDocs reaches end of life on 2027-05-05; its built-in server is for local preview,
not production hosting.

For a disposable integration check, run `TMPDIR="$HOME" python3 compose/v4/test/docs-preview.py`
with `TMPDIR` set to a Docker-shared directory. It pulls the pinned images if missing, creates a
synthetic docs tree and a separate Compose project, and removes that project's containers.

## Changing things safely

- **Profile or install change:** merge, then re-run `init` for every instance. Verify a SOUL or config hash against `main` ([infrastructure.md](infrastructure.md#is-what-is-live-what-is-on-main)). Setting `container_persistent: false` affects new sessions; containers started under the old persistent setting remain until separately removed when idle. Workspace files remain in the host-mounted `/workspace`; files and caches under `/root` do not survive session cleanup.
- **Pinned third-party skills:** `init` alone mounts `skills/` writable to install the commits in `skills/third-party.yaml` into gitignored checkout paths. Hermes and Chat keep read-only mounts. A fetch failure warns but does not stop `init`; run `bootstrap/install-third-party-skills --check` from the repository root, then re-run `init` when the upstream is reachable.
- **New profile:** only for a new permission, resource or lifecycle boundary. Add `profiles/<name>/{SOUL.md,config.yaml}`; decide its toolsets and whether it may use the boards; add it to the README table.
- **New credential:** put it in the instance `.env`, write it into only the profiles that need it in `bootstrap/install`, and forward it by name. Never pass it on a command line. Record a decision under `docs/decisions/` if it widens access.
- **New scheduled job:** a private job goes in the overlay with a `job.env`; a one-off can be created from the WebUI Crons panel.

## Pause, stop, resume

| Situation | Command |
|---|---|
| Halt new work, keep in-flight | `docker exec <instance>-hermes-1 hermes pause --reason "..."` (stops Kanban and cron dispatch) |
| Resume | `docker exec <instance>-hermes-1 hermes resume` |
| An agent is doing damage | `./bootstrap/stop` (pause, kill workers, revoke credentials, draft open agent PRs). Rehearse with `--drill`. |

## Recovering stuck cards

List what is stuck and why. Use the card's actual board: `ralph` for feature work, `intake` for Chat, email and the librarian, `queues` for older cards, `lenses` for new reviews, or `maintain` for new maintenance:

```bash
docker exec -u hermes <instance>-hermes-1 hermes kanban boards list
docker exec -u hermes <instance>-hermes-1 hermes kanban --board <board> list --status blocked --json
docker exec -u hermes <instance>-hermes-1 hermes kanban --board <board> show <id> --json   # read the last blocked / gave_up event
```

| Reason | Action |
|---|---|
| `stale_lock` / `gave_up` after a restart | `unblock <id>`, then `assign <id> <profile>` |
| A human decision is needed (policy, approval) | Decide, comment the decision on the card, then unblock it or archive it |
| Card in `triage` | The notifier releases parked doc gates. For others, release with care (see Hermes `specify_triage_task`); do not use `kanban specify`, which rewrites the card with a model. |
| `respawn_guarded: active_pr` | `assign <id> none`, then `assign <id> <owner>` |
| The PR moved on or merged | `archive <id>`; the next pass rebuilds from the current head |
| Blocked with a `tracker-record` reason | See [Tracker sync](#tracker-sync-chat-requests-and-the-work-tracker) below |

## Reading logs

| What | Where |
|---|---|
| Review discovery, publish, maintenance, cleanup | `docker logs <instance>-review-loop-1` |
| Approvals, gate alerts, digest | `docker logs <instance>-notifier-1` |
| Chat sidecar (producer + reply loop) | `docker logs <instance>-chat-sender-1` (before the cutover below: `~/Library/Logs/ralph-chat-reply-<instance>.{log,err.log}`) |
| A card's run | `docker exec -u hermes <instance>-hermes-1 hermes kanban --board <board> show <id> --json` |
| Disk | `colima ssh -- df -h /` and `du -sh compose/workspaces*/sandboxes` |
| Knowledge index | `bootstrap/knowledge-status <instance>` (the `RALPH_INSTANCE`, e.g. `ralph-personal`): collections, counts, models, last refresh (`status.json`) |

## Notifier board state

`run-notifier` enumerates active boards on every pass, including `default` and any test board that is not archived. It caches the last valid board list at `/var/lib/ralph/notifier-boards.json`. A failed read uses that cache; with no valid list, it skips the whole pass and retries later. Alerts use `notifier-state-<board>.json`; digests use `digest-sent-<board>`, both in the notifier-state volume.

On the first cutover run, the notifier preserves older sent-once history: the old `queues` state and digest are misleadingly suffixed `-ralph`, while `ralph` uses bare filenames. It stages the old `queues` files first, then moves the bare files to their per-board names under a lock. The `.notifier-board-migration/ready.json` and `done` markers let a restart finish an interrupted migration. If a target already exists, a marker is invalid, or an old bare file reappears after completion, the notifier logs `board state unavailable` and skips approvals, alerts and digests. Stop the notifier and inspect its state volume before attempting recovery; do not delete or overwrite the files to make the error disappear.

## Builder Chat space listing

After the `chat-sender` sidecar is running with its token, re-run `init` to grant builder card workers the read-only `list_spaces` MCP tool. It returns bounded space IDs, display names and types; neither Chat credentials nor message content reach the worker. If the sidecar is stopped or has no token, the tool reports an error. The private digest job has its own credential path and does not enable this tool by itself.

## Chat sidecar cutover (owner-only)

Not this card's job -- not any build card's job. None of these is a pull request, and nothing schedules them; do them by hand, in order, once chat PR 8 has merged.

1. **Start the sidecar first (`docker compose --profile chat up -d chat-sender`).** The token copy in step 3 `docker exec`s into this container, so it has to be running. With no token it is idle: it reads nothing, sends nothing and alerts nothing, and says so once in `docker logs <instance>-chat-sender-1`.
2. **Stop the private overlay's `chat_inbox.py` intake cron job.** The sidecar's producer replaces it; two producers would stage the same messages twice. Intake is paused from here until step 3 lands.
3. **Copy today's Chat token into the sidecar's volume (D8).** Not a browser step, not a second OAuth grant: `bootstrap/chat-creds-copy --instance-env .env.<name>` re-encrypts the token you already have (`~/.config/gws-cli/chat_token.json.enc`) for the `chat-sender` container's own pinned identity and writes it into that container's `chat-creds` volume over `docker exec`/`docker cp`. Your host token is untouched; a second copy now exists that can post as you until you revoke the grant in your Google account. Within one interval the sidecar starts.
4. **Check that the sidecar sends, then retire the LaunchAgent.** Send yourself a message in a pilot space, confirm `docker logs <instance>-chat-sender-1` shows the scan picking it up and a reply posted in that thread, and only then unload the host `chat-reply` LaunchAgent. Until then both run; the markers live in the shared `chat-inbox` directory and every part is sent with a fixed id, so the Chat API drops a repeat. **Rollback:** the reverse, in reverse order.
5. **Bring skipped threads back, when you want them (no deadline).** `bootstrap/chat-producer`'s cutover guard prints `guarded <op> <thread>` and stages nothing for a thread Ralph already answered under the overlay's own operation ids; `--allow-unseen-history` lifts it at the cost of at most one duplicate acknowledgement per thread.
6. **Set the send switch.** `CHAT_SEND` (`compose/.env.example`) defaults **on**, so replies keep going out after the cutover. Set it to `0`/`off` as a stop button and recreate the sidecar to apply it (`docker compose --profile chat up -d chat-sender`): the container reads it when it is created, so editing `.env` alone changes nothing in a running one. The host LaunchAgent, by contrast, re-reads its instance file every scan. See `docs/workflows.md`'s Chat loop.
7. **Run one threaded reply in a space of your own before the first real send.** The code asks the Chat API for `REPLY_MESSAGE_OR_FAIL`, not for proof that a thread id captured minutes earlier is still the right one; the token holds no delete scope.
8. **Pick the pilot spaces (`CHAT_ROUTE_SPACES`) knowing Ralph reads every human message in them.** Every one is an operation, an intake card and one model run.

## Knowledge index

| Situation | Action |
|---|---|
| `status.json` says `ok: false` | Read `error`. The previous index still answers. A fetch failure is usually the token or the network; the next hourly refresh retries. Force one: `docker exec -e GH_TOKEN <instance>-knowledge-1 /ralph/bootstrap/refresh-knowledge`. |
| New text is found by keywords but not by meaning | Embedding failed or is pending: `docker exec <instance>-knowledge-1 qmd embed`. |
| Index corrupt or models changed | Rebuild from the checkout: `docker exec <instance>-knowledge-1 sh -c 'rm -f "$INDEX_PATH"'`, then re-run `init`. |
| Container restarting | `docker inspect <instance>-knowledge-1 -f '{{.State.OOMKilled}}'`; a re-ranked query needs more than 2 GB. |
| A memory write is rejected (409) | The docs repo's default branch requires pull requests; memories are written directly by design. Allow the write or turn the rule off. |
| Turn it off | Remove `knowledge` from `COMPOSE_PROFILES`, stop the container, re-run `init`: the MCP block and index step go away. |

## Librarian

| Situation | Action |
|---|---|
| Force a run now | Clear the stamp inside the review-loop container, then poll once: `docker exec <instance>-review-loop-1 rm -f /workspaces/review-work/.librarian-stamp`, then `docker exec <instance>-review-loop-1 /ralph/bootstrap/run-review-loop --once`. `/workspaces/review-work` is the container path; on the host the same folder is under `RALPH_WORKSPACES` (`compose/workspaces/review-work` by default), so removing the file there also works. `--once` alone does nothing while the stamp is under seven days old. Needs `RALPH_DOCS_REPO` set, no open pull request with a `librarian/` head, and no `ralph-librarian` card in any state other than done or archived (that includes ready, running, blocked, triage and review). Each card carries a per-ISO-week idempotency key, so if a librarian card already exists this week, even a finished one, the poll normally returns it and starts nothing. To run again in the same week, archive that card first, but only if it is done. Never archive a ready, running, blocked, triage or review card: the guard stops waiting on an archived card, so a second run could start while the first is still working. Let an active card finish first. Then: `docker exec <instance>-hermes-1 hermes kanban --board intake archive <card-id>` (`--board queues` for a card made before the move to `intake`). |
| A librarian pull request or card is open | Blocks the next weekly card. `run-review-loop` skips creation while a `librarian/*` head is open (it reads every open pull request) or a `ralph-librarian` card is neither done nor archived, and the run checks the pull request again for itself. If either check cannot be read, it skips that poll and retries on the next. Each card carries a per-week idempotency key, which stops a repeat poll creating a second card. It is not an atomic lock: Hermes checks the key and then creates the card as two steps, so two loops polling in the same instant can in rare cases both create one. If you see two librarian cards, archive the one that is not running and let the other finish. Every blocker has to clear: merge or close the open librarian pull request, and let any librarian card that is neither done nor archived finish (archive only a done card; archiving an active one lifts the guard and can start a second run). One cleared with the other still open does not let the next card through. |
| After the profile and `install` changes merge | Re-run `init` on every instance once the image is pulled, so `ralph-librarian` exists there. No build pull request or card does this; it is an owner-only step. |

## Tracker sync (chat requests and the work tracker)

`bootstrap/tracker-sync` (called by `chat-reply`, never scheduled on its own) records
a chat request in the work tracker before its acknowledgement goes out, and settles
pending and closed items once per scan, after every reply for that scan is already
sent. With no key file it prints "tracker not configured" and exits 0: the
requester-facing path runs unchanged, and nothing below applies.

- **Key file:** `RALPH_TRACKER_CREDS`, in the `tracker-creds` volume, mode `0600` --
  the base URL, the account's per-user API key, the project identifier, and the
  thread-key custom field id.
- **The `tracker-sync` container:** the `chat-sender` sidecar calls it with
  `docker exec` into `<instance>-tracker-sync-1`, which exists only when
  `COMPOSE_PROFILES` includes `tracker`. Until you start it, a failed `docker exec`
  ("no such container", "is not running") is treated as the tracker not being
  configured: `--record` and `--settle` are skipped and the acknowledgement goes out
  as it does today. `docker logs <instance>-chat-sender-1` says so on each skipped
  `--record`. After you create the key file, start the container
  (`docker compose --profile tracker up -d tracker-sync`) or nothing is recorded.
- **One-time setup, by hand, on the tracker instance (not an API call):** create
  Ralph's own account with access to the project, generate its per-user API key,
  create the thread-key custom field, and create (or map onto stock statuses)
  `Waiting on requester` and `Duplicate`.
- **`.alerted-tracker-record`** (in `<chat-inbox>/<operation>/`): a `--record`
  failure repeated five scans in a row and blocked that operation's intake card.
  The acknowledgement never went out.
- **`.alerted-tracker-settle`** (in `<chat-inbox>/<operation>/`): a `--settle` pass
  failed for that operation. No card is blocked -- by the time `--settle` runs, the
  reply it is settling is already sent -- so this is alert-only, and
  `notifier/poller.py` is the backstop if the alert itself is missed.
- **Clearing a card blocked with a `tracker-record` reason:** fix the tracker, or
  rename the `RALPH_TRACKER_CREDS` key file to make `tracker-sync` inert again;
  delete `.alerted-tracker-record` and `.tracker-record-failures` from
  `<chat-inbox>/<operation>/`; then unblock the intake card. Nothing here is urgent:
  the request is already an orchestrator card, so what is missing is the
  acknowledgement, not the work.

## Glossary

| Term | Meaning |
|---|---|
| **Card** | A Kanban task assigned to one profile, with a completion condition. |
| **Board** | `ralph` for feature work; `intake` for Chat/email intake and the librarian; `queues` for older cards (legacy, no new cards); `lenses` for new review graphs; `maintain` for new PR maintenance rounds. Existing cards are never moved. |
| **Gate** | A blocked card waiting on the owner. A doc gate opens on merge or `dd-changes`; an `Approve PRD:` / `Approve design:` / `Approve plan:` card opens only on merge. |
| **Lens** | One review perspective (generalist, reliability, MVP, security); review-synthesis combines them. |
| **Round** | One maintenance pass on a PR's current feedback. |
| **operation_id** | The stable id tying a review, maintenance round or chat request to its cards. |
| **Overlay** | The private checkout holding work-specific skills and jobs. |
| **Sandbox** | The per-profile Docker container every agent command runs in. |
| **Knowledge** | The per-instance QMD index over the docs repo and Ralph's `docs/`, searched through MCP. |
| **Memory** | A note an agent wrote under `memory/` in the docs repo: a decision, root cause, convention, rejected approach or report. |
| **Repo cache** | Full clones under `/workspaces/repo-cache`, refreshed by review discovery and borrowed by checkouts. |
