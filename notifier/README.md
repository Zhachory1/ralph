# Notifier

Polls a Kanban board for gates waiting on a human and posts each one once to a Google Chat space.

## Why it looks like this

Three findings from the [stage 0 spike](../docs/spikes/hermes-capabilities.md) shape the design, and each would otherwise fail silently:

- **Two statuses, two audiences.** `review` is the agent reviewer's queue and the dispatcher claims it automatically. `blocked` is the human's, and is never auto-dispatched. The alert poller watches both, because you want to know either way; the approval watcher only acts on `blocked`, so it never races the reviewer for a card.
- **Two calls, not one.** `list --json` carries no blocked reason. The reason lives in the review summary, a `BLOCKED:` comment, or a `blocked` event — all returned by `show --json`.
- **Dedupe keyed by reason, not task id.** A second round of review is a new thing to say; an unchanged gate is not.

## Use

```bash
export GCHAT_WEBHOOK_URL="https://chat.googleapis.com/v1/spaces/.../messages?key=...&token=..."

python3 poller.py --board <board> --state ~/.ralph/notifier-state.json
python3 poller.py --board <board> --state ~/.ralph/notifier-state.json --digest
python3 poller.py --board <board> --state ~/.ralph/notifier-state.json --dry-run
```

`--digest` posts one summary of everything pending and does not update the dedupe state, so it is safe to run on a schedule alongside the per-gate poll. `--dry-run` prints instead of posting and needs no webhook.

Exit codes: `0` success, `1` delivery failed (nothing recorded, so the next run retries), `2` no webhook configured.

## Secrets

The webhook URL is a credential: anyone holding it can post into the space. Keep it in a file outside this repository, such as `~/.ralph/notifier.env` with mode `600`, or in your OS keychain. Never commit it, and rotate it by deleting and recreating the webhook in Google Chat if it is ever exposed.

## Tests

```bash
cd notifier && python3 -m unittest test_poller
```

No dependencies beyond the standard library.

## Morning digest

Once a day, three lines: what is waiting on you with the oldest first, and what the board has in flight.

```bash
python3 digest.py --board <board> --dry-run
```

It runs from the notifier loop at `RALPH_DIGEST_HOUR` (default 8, local), and records the date it last sent in a stamp file rather than keeping a timer, so a restart does not send it twice and a machine asleep at the hour still gets it on the first poll after waking.

It leads with what is blocked on you, because that is the only part only you can clear; a digest that opens with throughput is one you stop reading. It lists five at most and counts the rest. A day with nothing waiting says so, because silence reads the same as a broken digest.

## Approval watcher

`approvals.py` is the other half of the loop: it turns your decision in GitHub into a card transition.

It polls `blocked` only. A card in `review` belongs to the agent reviewer, and the dispatcher will spawn it; acting on that status would land a human decision in the middle of a critique. Work reaches you after the reviewer is done, when the author blocks the card.

```bash
python3 approvals.py --board <board>
python3 approvals.py --board <board> --dry-run
```

### A gate card finds its own pull request

A gate card is created at planning time, before the pull request it gates
exists, so the address is never on the card itself. `approvals.py` reads its
own summary and comments first (unchanged), and if that finds nothing, walks
`parents` -- the ids `show --json` carries -- looking for an ancestor that
later completed having opened one. The first address it finds is written back
onto the gate card as a comment, the same act a human used to do by hand, so
from then on the card carries its own address.

A gate reached with nothing resolvable anywhere in its ancestry is not
skipped in silence: it is printed to stderr, so a crashed gate shows up in
the notifier's own logs instead of just never clearing.

### Comment normally; label when you are done

Review the design doc however you like — inline on the diff, at the top level, or in a review. Every comment is collected and handed to the worker, oldest first, with inline comments keeping their `file:line` so "this is wrong" still points somewhere.

Comments cannot say whether you have *finished*, though. Three comments might mean "fix these and resubmit" or "answered your questions, carry on", and guessing either resumes work on unresolved feedback or stalls the card in silence. So the decision is a separate, explicit act:

| Signal | Means | Notes |
| --- | --- | --- |
| Pull request merged | approved | Merging the design doc *is* approving it |
| `dd-changes` label | rework | Sends the card back to its author |
| Review state | either | Only works on pull requests opened by somebody else |

There is no approval label. Merging already says yes, and a second way to say the same thing is one more piece of state to keep in sync.

`dd-changes` also gives you a filterable queue in the GitHub UI: `label:dd-changes` is every design doc currently back with its author. The inverse — design docs waiting on *you* — is `is:open -label:dd-changes` among agent pull requests, and the morning digest lists the same thing from the board.

**The label is removed once acted on.** A label left in place is a decision the next poll makes again, reopening a card the author has already revised.

Agent pull requests are opened under your account, and GitHub refuses **both** `APPROVED` and `CHANGES_REQUESTED` on your own pull request, leaving `COMMENTED` as the only submittable state. That is the same rule that stops an agent approving its own work; it just also applies to you, which is why review states are the fallback rather than the main path.

### Two gates, in order

| Status | Whose queue | Released by |
| --- | --- | --- |
| `review` | the agent reviewer | the dispatcher, automatically |
| `blocked` | yours | a person, and nothing else |

An independent reviewer pass is worth having, so `kanban.review_dispatch` stays on. What matters is that the reviewer is a **different profile** from the author: pass `--reviewer <profile>` to `request-review`, which reassigns the card. Without that, the dispatcher hands the card back to whoever wrote it and the agent reviews itself.

`unblock` only accepts a blocked card. A card in `review` needs `reopen-review` to go back to its author, or `complete` to finish. The watcher picks per card status; calling the wrong one fails loudly.

Feedback is always commented on the card *before* the transition, because resuming re-spawns the worker and it must wake with the feedback already in context.
