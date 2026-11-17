# First build slice: PR review on the v4 control plane

Proves the v4 skeleton (Postgres+pgvector, Vikunja, n8n) by mapping ralph's
existing PR-review flow onto it, before anything else migrates. Scope matches
the slice approved in `docs/v4-spec.docx` Sec 5/6/11: stand up the three new
primitives, port one flow, leave everything else (including the live Hermes
board) untouched.

## What exists today (Hermes Kanban)

```
discover-reviews (cron/poll) -> kanban create, assignee ralph-review-workflow
  -> review-workflow: stage evidence, pick mode, create lens cards + synthesis card
    -> lenses (parallel) -> review-synthesis -> verdict (approve | request-changes)
  -> publish-review: reads the done synthesis card, posts exactly one GitHub review
```

State lives entirely in Hermes' own Kanban board (`lenses` board): card status,
idempotency keys, parent/child graph, blocked state. Dedup is "already has a
review graph for this head" read back from board card titles.

## What changes under v4

Same five responsibilities, same boundaries (discovery never decides depth;
review-workflow never publishes; synthesis never reads live GitHub; publish
is the only writer) — the only thing that moves is *where state and routing
live*.

| Today (Hermes board) | v4 | Why |
|---|---|---|
| `discover-reviews`' own head-already-reviewed check (`reviews_by_me`) | unchanged — still a `gh api` read, not a new store | GitHub is still the source of truth for "already reviewed"; no reason to duplicate it in Postgres |
| Card idempotency key (`<op_id>-intake`) dedupes a re-discovered head | `processed_pr_events` (repo, pr_number, event_type keyed) + an `inbound_pr_reviews` row keyed `(repo, pr_number)` tracking `head_sha` and `status` | Sec 6.2's own table for exactly this; replaces reading board card titles back to infer state |
| `review_graphs()` / `intake_in_flight()` reading Kanban card status to avoid a second graph for one head | a Postgres row's `status` column (`pending` / `audited` / `reviewed_by_human`) on `inbound_pr_reviews`, read directly instead of re-parsing card titles | one authoritative row per (repo, pr) instead of scanning archived+active cards |
| Hermes board as the only trigger (cron calling `discover-reviews`) | n8n's 3-minute GraphQL poller (spec Sec 6.2) calling the same discovery logic, writing straight to Postgres | matches the spec's event-ingestion model; `discover-reviews`' Python logic is reused as the poller's payload, not rewritten |
| review-workflow / lenses / review-synthesis as Hermes profiles, Kanban cards as the work graph | **unchanged.** These stay Hermes profiles exactly as written. What they read/write moves from Kanban cards to a Vikunja ticket (one ticket per PR, same lifecycle: Triage -> created -> audited -> closed) for human visibility, while internal lens coordination can stay a direct profile call (orchestrator/council fanning out), no board needed for machine-to-machine handoff | Tier B workers do not need Kanban specifically — Vikunja is the human-facing board per spec Sec 5; lens coordination was already not using the board for communication, only for existence/status |
| `council` (new, this branch) | **folded in here** for RFC/plan/high-risk-PR review only, not ordinary PR review. Ordinary PR review keeps the lens→synthesis shape; `council`'s 3-judge panel is reserved for the heavier artifacts per spec Sec 4 | avoids forcing every PR through a 3-judge panel when the existing lens system already does graded-depth review (lite/default/deep) |
| `publish-review` (deterministic, no model) | **unchanged**, except it reads `inbound_pr_reviews` to confirm no newer head raced it, instead of re-reading Kanban card bodies | same exactly-once guarantees (marker check, head-match, lock), different state backend |

## What does NOT change in this slice

- No Hermes code, profile, or board is touched. `compose/docker-compose.yml`
  (the live stack) is untouched.
- `discover-reviews`, `stage-review-evidence`, `publish-review`, the lens and
  synthesis profiles: none are rewritten. This slice only proves the new
  state/trigger layer can hold the same guarantees (dedup, lease locks,
  exactly-once publish) that Kanban card idempotency keys hold today.
- No cutover. This is a parallel proof, not a replacement; main's flow keeps
  running.

## Verification done this slice

```
cd compose/v4 && docker compose --env-file .env up -d
docker compose --env-file .env ps        # postgres healthy, vikunja/n8n up
docker exec ralph-v4-postgres-1 psql -U ralph -d agent_memory -c '\dt'
                                           # workflow_registry, processed_pr_events,
                                           # pr_maintenance_locks, inbound_pr_reviews present
curl 127.0.0.1:3456/api/v1/info           # 200, Vikunja v0.24.6
curl 127.0.0.1:5678/healthz               # 200, n8n
```

This infra-boot check is from the prior commit. This slice adds two things
proven against that infra, both with synthetic data rather than a live
GitHub token — no real discovery yet, see Non-goals below:

1. **Vikunja project + the 8 columns from spec Sec 5.** `bootstrap-vikunja.sh`
   creates the `ralph` project and its kanban buckets (`Triage`, `RFC Author`,
   `Council Review`, `Backlog`, `Ready`, `Peer Review`, `Director PR Merge`,
   `Done`) through Vikunja's own REST API, idempotently -- re-running it a
   second time against the same instance creates nothing and reports every
   bucket already present. Verified end-to-end: ran it against a fresh stack,
   confirmed exactly 8 buckets with no duplicates and in spec Sec 5's order
   by position, created a test task, and moved it into Triage through
   `/projects/{id}/views/{view}/tasks`.

   One real gotcha surfaced and fixed during verification: Vikunja's API
   refuses to delete a view's last remaining bucket, so the script cannot
   delete the project's auto-created default bucket until after the 8 named
   buckets exist, and the default bucket's own title (`"Backlog"`) has to be
   excluded from the "does this column already exist" check -- otherwise the
   script would skip creating a real `Backlog` column (seeing the default's
   title as a match) and then delete that same default bucket at the end,
   leaving the board with 7 columns and no `Backlog` at all. Caught by
   actually counting buckets after a run, not by inspecting the script.
2. **One real n8n workflow** (`workflows/inbound-pr-review-intake.json`):
   a webhook trigger that upserts into `inbound_pr_reviews` on
   `(repo, pr_number)`, matching the spec's own dedup shape for Sec 6.2.
   Imported the committed file into a fresh n8n instance via its REST API,
   activated it, and fired two webhook calls for the same `(repo, pr_number)`
   with different `head_sha` values: confirmed exactly one row, with
   `head_sha` advancing to the second call's value -- the "triggers... when
   `head_sha` advances" dedup behavior Sec 6.2 describes, proven against a
   real Postgres write, not asserted.

   The Postgres node binds its 5 parameters via an n8n array expression
   (`[$json.body.repo, ...]`), not a comma-joined string -- the first version
   of this workflow used a comma-joined string, which n8n splits on `,` to
   bind positional parameters, so any field containing a comma (a PR title
   like "fix: X, improve Y" is common) would desync every parameter after it.
   Verified by firing a webhook call with a 3-comma title and confirming the
   full, unsplit title landed in Postgres.

Both were torn down after verification; nothing is left running.

## What this slice does NOT prove

- No GitHub GraphQL polling. The webhook was fired by hand with a synthetic
  payload, not by n8n's own 3-minute poller reading real PRs. That poller
  node is not built yet.
- No webhook authentication. The committed workflow accepts any POST with
  no shared secret or signature check, safe only because n8n is bound to
  `127.0.0.1` only (`compose/v4/docker-compose.yml`). Add a shared-secret
  header check before this is ever reachable beyond localhost -- noted
  in-file on the webhook node itself, not only here.
- No connection to the live Hermes `review-workflow` profile. The n8n
  workflow writes to Postgres and stops; nothing calls `review-workflow` or
  any other Hermes profile from this stack yet. Bridging `ralph-v4`'s n8n
  into the live `ralph` compose project's `hermes` container is its own
  decision (network, credential, and container-naming implications) not
  made in this slice.
- No `publish-review` integration. `publish-review` still reads/writes
  nothing in this schema; wiring it to update `inbound_pr_reviews.status`
  alongside its GitHub write is unstarted.

## Next slice

1. Add the GraphQL-poll node in front of the webhook (or replace the webhook
   entirely with a cron-triggered poll), reading real PRs for one test repo
   under `review-requested:@me`, still writing only to `inbound_pr_reviews`
   — no Hermes call yet.
2. Decide how `ralph-v4`'s n8n reaches the live `hermes` container (shared
   Docker network vs. a second bridge network vs. keeping them fully
   separate until a real cutover), before wiring the webhook-to-profile leg.
3. Only after both: `publish-review` updates `inbound_pr_reviews.status`
   alongside its existing GitHub write, against one real low-risk PR.
