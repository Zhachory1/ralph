# v4-architecture handoff

State of the migration as of 2026-11-18, for whoever picks this up next —
human or agent. Written by the session that did the work; every claim here
that says "verified" was verified by running the thing, not by reading code.

## What this effort is

Migrating ralph to the architecture in `docs/v4-spec.docx` ("Autonomous
Engineering Organization: System Architecture & Implementation
Specification", v3.2). Two explicit constraints from the owner:

1. **`main` is untouched.** Everything lands on the `v4-architecture`
   branch via PRs against it. Cut over only once the new system is proven.
2. **Do not adopt ralph's architecture or workflow decisions.** This is a
   new system derived from the spec, not ralph re-platformed. Take from
   ralph only: `profiles/PRINCIPLES.md` and `STANDARDS.md` content, the
   "invariants live in permissions, not prose" rule, and the
   one-SOUL-per-profile convention. The mapping doc
   `docs/1-projects/v4-architecture/pr-review-slice.md` predates this
   constraint and frames migration as ralph-pipeline-onto-new-infra; treat
   it as historical context, not a plan.

## Working conventions this session established

- **Every slice is a PR** onto `v4-architecture`, independently reviewed by
  the `reviewer` subagent before merge, findings fixed, re-reviewed, then
  merged. Review caught real bugs in every single PR so far; do not skip
  it.
- **Verified means ran it.** "I read the code and it should work" has been
  wrong repeatedly (Vikunja bucket deletion, n8n comma-joined params, the
  first get_worktree_status error shape, the stale-PR push skip). Trust
  only what was executed and checked independently of its own self-report.
- **Findings get documented, not silently fixed or ignored.** Real
  limitations are written down as limitations (OAuth expiry, transient-gh
  window) in the relevant README rather than hidden.
- **Test credentials/artifacts get cleaned up.** Sandbox PRs closed,
  branches deleted, temp files removed, no secrets committed, ever.
- Local runtime: Colima + Docker + docker-compose (installed during this
  effort; `colima start` if the VM is down).

## Merged work, PR by PR

### #193 — spec doc
`docs/v4-spec.docx` added. v3.2 over the original v3.1: `council` promoted
from an orchestrator function to its own persistent Tier B profile; Sec
11.0 harness-agnostic CLI contract (any coding CLI with a non-interactive
prompt flag, repeatable skill flag, predictable skill-discovery path, and a
parseable completion signal is pluggable); Sec 11.0.1 skill-synthesis loop
owned by the coding-specialist profile, not the CLI.

### #194 — control-plane skeleton + council profile
`compose/v4/` (postgres+pgvector, vikunja, n8n — separate compose project
from ralph's live stack), `profiles/council/` (scaffold; judges and rubric
do not exist yet and the SOUL says so), `pr-review-slice.md`. Review
findings fixed: council's safety claims moved from prose to
`disabled_toolsets`; `VECTOR(1536)` → `VECTOR(768)` to match this repo's
embeddinggemma-300M; `N8N_ENCRYPTION_KEY` pinned.

### #195 — Vikunja board provisioning + n8n intake leg
`compose/v4/bootstrap-vikunja.sh` (idempotent: creates the project and the
8 Sec-5 columns, deletes Vikunja's auto-created default bucket last because
the API refuses to delete a view's last bucket), `compose/v4/workflows/
inbound-pr-review-intake.json` (n8n webhook → Postgres upsert into
`inbound_pr_reviews`, dedup on (repo, pr_number) with head_sha advancing).
Bugs found by running it: the first bootstrap script version left the board
with the wrong column order because Vikunja's auto-created "Backlog" bucket
satisfied the title check before being deleted; n8n's `queryReplacement`
comma-joined string desyncs positional params on any field containing a
comma — use an n8n array expression.

### #196 — coding-harness MCP server, `implement_spec`
New `coding-harness/` Node MCP server; `adapters.json` holds the mewrite
adapter mapping. Bugs found: `spawn()` default stdio leaves stdin as an
open pipe and mewrite blocks on it forever (`stdio: ["ignore", ...]`
required — `-p` mode never reads stdin but waits for EOF anyway);
`git diff --stat HEAD` misses untracked files (`git add -A` first);
unhandled `error` event on ENOENT crashed the whole server; a directory at
the worktree path is not proof of a valid worktree (check with
`git rev-parse --is-inside-work-tree`).

### #197 — Hermes wiring + `swe` profile
`compose/v4/bootstrap-hermes.sh` automates the four environment gotchas
below; `profiles/swe/`; `docs/decisions/0005-swe-has-no-repository-
credential.md`; `hermes-harness-integration.md`. Review caught: the first
draft granted the MCP server to Hermes's default profile, not `ralph-swe`
— every `hermes config set` is now `-p ralph-swe` scoped, and
`mcp_servers` never goes in a profile's YAML (Hermes does not merge it;
ralph's own #134 rule). A collaborator pushed a separate fix directly to
this branch mid-review (`docs/decisions/0005` + a test fix); the branch
was rebased on top of it. If that happens again, rebase, don't force-push
away their commit.

### #198 — `get_worktree_status`, sourced from a real ticket
First slice sourced from the pipeline itself: Vikunja ticket → Ready →
description pulled via API → dispatched to `ralph-swe` on a disposable
clone. `swe`'s self-report claimed all acceptance criteria met; independent
verification (calling the tool directly) found the error payload was
missing fields. The committed version is the corrected one. Review then
caught a detached-HEAD edge (`git rev-parse --abbrev-ref HEAD` returns the
literal string "HEAD", now returned as `branch: null` with an
`open_questions` note) and an undocumented unused `ticket_id`.

### #199 — `implement_spec` opens real draft PRs
`gh()` helper + `ensurePullRequest()`. Verified against
`Zhachory1/ralph-canary` (the sandbox repo kept for this). Bugs found by
running it: no global git identity on the host → commits use `-c
user.name=ralph-coding-harness` scoped flags, never a config write; first
version skipped the push when a PR already existed → branch silently went
stale (now: always push first, then find-or-create); review caught the
concurrent-PR-creation race → a failed `gh pr create` now re-checks
`gh pr list` and returns the winner's URL. Documented, not fixed: a
transient `gh` failure in the exact window after a successful push can
leave a branch pushed with no PR, and a retry with no new diff never
retries the PR step.

### #200 — `apply_pr_feedback` + shared `commitAndSync`
Third Sec 11 tool. Review blockers fixed: no verification that the
worktree's checked-out branch equals the caller's `branch` argument; no
concurrency guard on an existing shared worktree (fixed with an
`O_EXCL` filesystem lock per ticket_id — an in-process lock cannot work
because each MCP client spawns its own `node server.js`). Second round:
repo identity check via git common dir (sibling clones share one
`.harness-worktrees` root), an existing open same-repo PR is now required
(this tool never opens PRs), push failures return `status: "error"` with
`commit_sha: null` and the local commit retained, `resolved_count` is
always 0 (no GitHub thread resolution yet). Self-review then found the
lock's own failure mode — a killed holder wedges the ticket — fixed by
recording the holder pid in the lock file and reclaiming once that pid is
gone. Offline tests added: `cd coding-harness && npm test` (fake `gh`,
fake adapter, real git).

### #201 — `fix_ci_failure`
Fourth tool, sharing `apply_pr_feedback`'s path via a new
`updateExistingPr` helper. Only the last 20,000 chars of `failed_logs`
reach the adapter; `root_cause` is the adapter's own `ROOT CAUSE:` line
or null. The origin repo is read from `git config remote.origin.pushurl`
(falling back to `remote.origin.url`), not `git remote get-url --push` —
that command applies `url.<base>.insteadOf` rewrites and hid which GitHub
repo the remote named; the push test caught this. `npm test`: 7 pass,
all offline (local bare repo transport via `insteadOf`, fake `gh`, fake
adapter). The resumed review found that a line echoed from CI logs could
be mistaken for the diagnosis. The parser now uses only the final
nonblank line; the regression failed before the fix and passed afterward.

### #210–#213 — sandbox audit and permission boundaries

`audit_pr` checks an exact PR head in a network-isolated Docker sandbox.
The real Docker fixture verified missing-image denial, SHA pinning and
isolation. The loopback HTTP transport enforces per-caller tool and
checkout grants. The reviewer bootstrap was exercised in Hermes: only
`audit_pr` was discoverable and an implementation call was denied.

### #214–#215 — dedicated adapter home and verified hotfixes

Bootstrap no longer copies personal OAuth state. Two real bootstrap runs
left the legacy fixture untouched and retained new state in the private
adapter home. A live provider refresh still needs an operator identity.
`reproduce_and_patch` uses an operator-registered application verifier;
the fixture showed a draft is published only for an observed red-to-green
change and failed edits survive a refused retry.

## Current architecture state

| Piece | State |
| --- | --- |
| compose/v4 (postgres, vikunja, n8n) | Boots clean; schema applies; board provisions idempotently; one n8n workflow proven end-to-end on synthetic payloads |
| coding-harness | All six tools in the Sec 11 table, including sandboxed `audit_pr` and registered `reproduce_and_patch`; loopback HTTP and stdio |
| adapters | mewrite only, via `adapters.json` (prompt flag, skill flag, skill placement, JSONL completion signal) |
| profiles | `council` (scaffold), `swe` (proven on real tickets via `hermes -p ralph-swe chat`) |
| Vikunja board | 8 columns per spec Sec 5, created by `bootstrap-vikunja.sh` |
| PR flow | `implement_spec` commits + pushes + opens/reuses a draft PR on a real repo |

Not wired: skill synthesis in a live SWE run (Sec 11.0.1), real
`ci_state`/`pending_notes_count`, real GitHub
review-thread resolution, any wiring from n8n/Vikunja into `swe` or the
harness (every dispatch so far has been a manual `hermes chat -q`), OAuth
token refresh (below).

## Environment gotchas catalog

These cost real time. Do not rediscover them.

- **Colima/virtiofs sync lag.** Host-side `chmod`/file writes on a bind
  mount are not reliably visible inside the VM, and vice versa (`ls` from
  macOS shows stale emptiness while the container sees the files fine).
  Writable test fixtures: create them inside the VM with `colima ssh`,
  never bind-mount from macOS.
- **`HERMES_HOME` is `/opt/data`** in the hermes image, not
  `/home/hermes/.hermes` — that path is only an override ralph's own
  compose sets.
- **Installing mewrite in the hermes container:** global prefix is
  root-owned; use `npm install -g --prefix /opt/data/npm-global
  --allow-scripts='@zhachory1/mewrite-code,better-sqlite3,node-pty,
  onnxruntime-node,@google/genai,protobufjs,koffi'` — without
  `--allow-scripts` the native deps silently skip and mewrite fails at
  runtime, not install time.
- **mewrite needs a writable `$HOME`** inside the container (uid 10000's
  default HOME is root's).
- **git "dubious ownership"** on container-created worktrees →
  `git config --global --add safe.directory "*"` under the same HOME the
  harness's children inherit (check before adding — bare `--add`
  duplicates on re-run).
- **`spawn()` + mewrite hangs without `stdio: ["ignore", ...]`** (see #196).
- **Vikunja:** refuses to delete a view's last bucket; auto-creates a
  "Backlog" bucket on project creation; task update is a full-replace
  POST (include every field you want to keep); bucket membership is set
  through `/projects/{id}/views/{view}/tasks`, not a task `bucket_id`
  field.
- **n8n:** positional Postgres params must be an n8n array expression, not
  a comma-joined string; this n8n version's postgres node maxes at
  `typeVersion: 2.5` (2.6 silently fails activation with an unrelated
  error).
- **OAuth access tokens expire in minutes.** A copied `auth.json`
  snapshot worked for a demo, then failed with "No API key for provider".
  v4 bootstrap now uses a dedicated persistent adapter home instead of
  copying a personal OAuth session. Live provider refresh still needs a
  pilot with an operator-provisioned identity.
- **No global git identity on this host.** Any commit from automation
  needs `-c user.name=... -c user.email=...` scoped flags.
- **macOS `/bin/sh` `echo` expands `\n`** — use `printf` for JSON in
  shell fixtures.
- **`mewrite exec --json` event stream:** final assistant text lives in
  the last `turn_end` event's `message.content` text parts; a `turn_end`
  with empty content means the adapter errored (check
  `stopReason: "error"` + `errorMessage`).

## Historical mewrite reviewer rate limit (2026-11-18)

The `reviewer` subagent (and any subagent whose frontmatter pins
`model: claude-sonnet-5`) resolves that model to the **Anthropic OAuth
credential** in `~/.mewrite/agent/auth.json` — a Claude Pro/Max OAuth
token — not to OpenRouter, despite `defaultProvider: "openrouter"` in
`~/.mewrite/agent/settings.json`. Verified: `mewrite exec --model
claude-sonnet-5` reports `provider: "anthropic"`, and a direct
`api.anthropic.com` call with that token returns HTTP 429 right now
("This request would exceed your account's rate limit"). This session's
own volume burned the OAuth window.

- The main mewrite session survives on auto-retry; the subagent spawn
  path treats the repeated 429 as a hard failure, which is why the
  parent kept working while reviewers failed 6+ times.
- The OpenRouter account itself is clean (verified via
  `GET /api/v1/key`: paid tier, $86 of $100 remaining, no guardrails, 0
  free-model requests). OpenRouter's docs: there is no `/settings/limits`
  page; the check is `GET /api/v1/key`; rate limits are per-model and
  "governed capacity globally."
- **mewrite bug found:** overriding the subagent model to
  `~moonshotai/kimi-latest` still produced an Anthropic `req_011...`
  request ID — i.e. the override never reached OpenRouter.
  `resolveSubagentModelRef` (`dist/core/agent-session.js:147`) falls back
  to the parent/anthropic model when the requested id isn't in the
  parent's `authed` pool; `auth.json` only has `anthropic` and
  `openai-codex` entries, and the tilde-prefixed id didn't match the
  registry. An explicit override should be honored or should error
  loudly, not silently swap to the parent model.
- Workarounds: wait for the Anthropic window to reset; or pin
  `agents/reviewer.md`'s `model:` to a non-anthropic credential that
  exists in `auth.json` (e.g. something on `openai-codex`); or fix
  `resolveSubagentModelRef` (owner's codebase, their call).

## Open items / backlog

1. Complete [#203](https://github.com/Zhachory1/ralph/issues/203), then
   continue the sequential v4 backlog through #209.
2. Resolve base drift in the maintenance workflow per
   [decision 0006](../../decisions/0006-v4-base-drift-preserves-pr-history.md).
3. Wire a real ticket source end to end: Vikunja Ready-column ticket →
   n8n (or manual trigger) → `swe`/`implement_spec` dispatch, closing the
   loop #198 did by hand.
4. Skill-synthesis loop (Sec 11.0.1).
5. Verify live provider refresh for the dedicated mewrite home during the pilot.
6. Verify draft recovery on a real canary after a transient GitHub failure;
   the local transport regression is covered by `implement-spec-retry.test.mjs`.
7. `.gitignore` has `coding-harness/.harness-worktrees/`, but worktrees
   are created as a sibling of the target repo, not inside
   `coding-harness/` — the pattern never matches anything. Unfixed,
   unimportant until someone points the harness at this repo itself.

## Resume recipes

Control plane:

```
cd compose/v4
cp .env.example .env   # fill POSTGRES_PASSWORD and N8N_ENCRYPTION_KEY
docker compose --env-file .env up -d
VIKUNJA_USERNAME=ralph VIKUNJA_PASSWORD=<pw> ./bootstrap-vikunja.sh
```

Hermes + harness + swe:

```
HERMES_IMAGE=nousresearch/hermes-agent:v0.22.2 \
HERMES_HOME_VOLUME=/tmp/hermes-home \
  ./compose/v4/bootstrap-hermes.sh
# Sign in to mewrite from the persistent adapter home; see hermes-harness-integration.md.
# then once per fresh profile: hermes -p ralph-swe config set
#   model.provider anthropic / model.default claude-sonnet-5 /
#   platform_toolsets.cli ["file"] / agent.disabled_toolsets [...]
# and copy a valid hermes auth.json into profiles/ralph-swe/
docker run --rm -v /tmp/hermes-home:/opt/data \
  -v coding-harness/server.js:/harness/server.js:ro \
  -v coding-harness/adapters.json:/harness/adapters.json:ro \
  -v coding-harness/node_modules:/harness/node_modules:ro \
  -v <fixtures>:/work nousresearch/hermes-agent:v0.22.2 \
  hermes -p ralph-swe chat -q "Use the implement_spec tool ..."
```

Harness tests:

```
cd coding-harness && npm install && npm test   # offline, ~2s
```

Sandbox repo for any real-GitHub verification: `Zhachory1/ralph-canary`.
Close and delete anything you open there.
