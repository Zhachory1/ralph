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

Confirms the schema applies and all three services boot and respond. Wiring
an actual n8n workflow against this schema, and an actual Vikunja board/ticket
flow, is the next slice — this one is infra-exists-and-boots, not
flow-is-live.

## Next slice

1. One n8n workflow: GraphQL poll -> upsert `inbound_pr_reviews` -> webhook
   into the (still-Hermes) `review-workflow` profile, replacing the cron call
   to `discover-reviews` for one test repo.
2. One Vikunja project/board with the column set from spec Sec 5, and
   `publish-review` updating the ticket's status alongside its existing
   GitHub write.
3. Only after both run cleanly against a real low-risk PR: consider whether
   `discover-reviews`' Python moves into an n8n Code node, or stays a
   script n8n shells out to (no decision made yet; both satisfy the spec).
