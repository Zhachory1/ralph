# Stage 0 spike: what Hermes already does

Run against **Hermes Agent v0.21.5 (2026.9.24)** on macOS, on a throwaway `ralph-spike` board. No production board, profile, or config was modified. Re-run these checks against your own version before relying on them; Hermes moves quickly.

The headline: **most of what the design treats as phase-1 work already exists.** The approval gate, the council fan-out, durable workspaces, multiple boards, and the Pause level of the kill switch are native. What's left to build is smaller and differently shaped than planned.

## Answers

| Question | Answer | Consequence |
| --- | --- | --- |
| Can a card gate on a human and resume? | Yes, two ways: `block` / `unblock`, and a first-class `review` status via `request-review` / `request-changes` / `reopen-review` | Don't build an approval-gate adapter. Wire to these |
| Can the council run without an adapter? | Yes. `kanban swarm` builds parallel workers → verifier → synthesizer in one command | The design's "biggest piece of work" disappears |
| Are durable run directories possible? | Yes. `create --workspace dir:<path>`, and `boards create --default-workdir` | `.workflow-runs/` works; backprop keeps its input |
| Multiple boards? | Yes, and more isolated than assumed: each board has its own DB, workspace directory, **and dispatcher loop** | Side projects get real isolation |
| Can the notifier poll for pending gates? | Yes, but not the way the design describes. See [Notifier contract](#notifier-contract) | Poller needs two calls, not one |

## What's native that the design planned to build

### The approval gate

`request-review` carries both a human-readable summary and structured metadata:

```bash
hermes kanban request-review <task> \
  --summary "DD ready: segment refresh v2. 3 decisions needed, scope M." \
  --metadata '{"dd_path":"projects/x/dd.md","pr":"...","decisions":3}' \
  --reviewer <profile>
```

The summary persists as `latest_summary` and appears in `show`, in the run history, and in `show --json`. `--reviewer` reassigns the task as part of the transition. There is also a `review_dispatch: true` setting in `config.yaml`.

**`request-review` moves a task to `review`, not `blocked`.** These are separate statuses with separate workflows, and a poller watching only `blocked` will miss every review gate.

### The council

`kanban swarm` is the council pattern as a built-in:

```bash
hermes kanban swarm "Council critique of DD: segment refresh v2" \
  --worker "council-security-v2:Security lens" \
  --worker "council-reliability-v2:Operability lens" \
  --worker "mvp:Scope lens" \
  --verifier council-reviewer-v2 \
  --synthesizer council-orchestrator-v2
```

Verified result: three worker cards `ready` in parallel under one root, with verifier and synthesizer held at `todo` until the workers finish. Each worker can carry its own skills (`PROFILE:TITLE:SKILL,SKILL`) and its own profile, so persona diversity and model diversity both come free.

### Completion evidence is enforced by the platform

An empty completion is refused:

```
cannot complete t_53c048aa: completion blocked: t_53c048aa has no result or
summary evidence. Pass --result/--summary describing what was done
(an empty completion is not evidence).
```

This is the handoff contract enforced below the prompt layer, where a skill rewrite can't reach it. The Assumptions field still has to be a convention inside that summary, but the requirement to produce evidence at all is structural.

### Dependency promotion

Verified end to end: a child created with `--parent` sits at `todo`, and moves to `ready` the moment the parent completes. This is the mechanism the whole orchestration design rests on, and it behaves as assumed.

### Other primitives already present

`--completion-contract`, `--idempotency-key` (cron safety), `--max-retries`, `--max-runtime`, `--model`/`--provider` per card, `--workflow-template-id` and `--step-key` for template-driven graphs, `specify` and `decompose`, a `triage` column, `schedule` for time-waits, `notify-subscribe`, `diagnostics`, `gc`, `repair`, and `hermes pause` / `resume` (the Pause level of the kill switch).

## Notifier contract

The design assumes one poll returns everything the alert needs. It doesn't.

**`list --json` omits the blocked reason and the blocked kind.** The task object carries `id`, `title`, `status`, assignee, workspace, timing, and contract fields — but nothing about *why* a task is blocked. The alert text the design specifies ("DD ready: … 3 decisions · Scope: M") cannot be built from `list` alone.

The reason is recorded in three other places, all reachable through `show --json`:

- a **comment**, `BLOCKED: <reason>`
- a **`blocked` event**, with `{reason, kind, recurrences, source_status}`
- the **run record**, and `latest_summary` for review transitions

So the poller is two calls, not one:

```bash
hermes kanban --board <b> list --status blocked --json   # ids
hermes kanban --board <b> list --status review  --json   # ids (separate status!)
hermes kanban --board <b> show <id> --json               # reason, per task
```

`show --json` returns `{task, latest_summary, parents, children, comments, events, runs}` — everything the alert and the dedupe state file need.

## Gotchas

**`block --kind <k> "<reason>"` always fails.** Every combination tested (`simple`, `has:colon`, `has-dash`, `two words`) errors with `unrecognized arguments`. Each works alone:

```bash
hermes kanban block <task> "DD ready for review"   # reason, kind=null
hermes kanban block <task> --kind needs_input      # kind, reason=null
```

Worse, the second call **silently clears a reason set by the first** (`{'kind': 'needs_input', 'reason': None, 'classified_in_place': True}`). Until this is fixed: **pass the reason, skip `--kind`**, and put the category in the reason text. Don't try to set both.

**`request-changes` needs an active review run.** Calling it on a task moved to `review` by hand returns `cannot request changes: task is not in an active review run`. The state machine expects a real worker run, so the DD rework loop has to be exercised with a live worker, not simulated by CLI.

**`unblock` takes task IDs, not a reason.** `unblock <task> "some text"` tries to unblock a task named `some text`, prints an error for it, and unblocks the real task anyway. Feedback goes in a separate `comment`.

**Colons and leading dashes in reasons are fragile.** The design's `review-required:` / `question:` / `scope-escalation:` prefixes survive as plain positional reasons, but combined with other flags they trip argument parsing. Keep prefixes, keep them simple, and always quote.

## What this changes in the plan

**Drop from phase 1:** the approval-gate adapter, the council adapter, custom workflow-graph machinery, and the Pause half of the kill switch. All native.

**Add to phase 1:** a two-call notifier poller (`list` for ids, `show --json` for reasons) covering **both** `blocked` and `review`, with a dedupe state file.

## Live worker run

A second round ran a real `author` profile on `claude-opus-5` against a real PRD, with a `dir:` workspace holding a clone of a scratch repo.

**It worked unattended.** From one card brief the worker read the PRD, wrote a design document, committed it to a branch, pushed, opened a pull request, and moved its own card to `review` with a summary naming the decisions, the scope rating, and the pull-request URL. It then revised across three rounds. The Assumptions section arrived populated and honest, including *"I did not inspect the digest implementation… component names describe roles, not verified modules."*

Confirmed by this run:

- A worker re-spawns with feedback in context, revises, and re-requests review.
- `reopen-review` round-trips a card to its author; the revision commit named the two decisions it had been given.
- A `dir:` workspace survives repeated dispatches: four runs reused one checkout.
- Completion evidence, review summaries, and run history behave as the CLI probe suggested.

### `kanban.review_dispatch` makes the agent review itself

**This defaults to `true`, and it quietly defeats human review.**

The moment a card enters `review`, the dispatcher spawns a reviewing worker. On this run that worker was the same profile that wrote the document. It reviewed its own work, posted `changes_requested`, revised, and re-requested review — twice — before the human watcher ever saw the card. Every poll returned `0 cards resumed`, because by then the card was already `running` again.

Nothing errors. The board looks busy and productive. The human is simply not in the loop, and the design's central claim — that an independent reviewer and a human gate stand between an agent and a merge — silently does not hold.

Set it to `false` for any board with a human gate:

```bash
hermes config set kanban.review_dispatch false
```

With it off, the card stayed in `review`, the watcher picked it up, the human's comments reached the card, and the next run applied them. Revisit it only once a genuinely separate reviewer profile exists, and even then the human gate has to come after it, not instead of it.

**Still to verify, with a live worker rather than the CLI:**

- How Hermes scopes the Docker sandbox, and whether two profiles can get separate ones
- Skill-name resolution between layers, for layer-3 overrides

## Reproducing

```bash
hermes kanban boards create ralph-spike --default-workdir /tmp/ralph-spike-workdir
hermes kanban --board ralph-spike create "probe" --workspace dir:/tmp/ralph-spike-workdir/probe --json
# ... see command examples above
hermes kanban boards rm ralph-spike
```

The spike board is safe to delete. It was created solely for this writeup.
