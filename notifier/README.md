# Notifier

Polls a Kanban board for gates waiting on a human and posts each one once to a Google Chat space.

## Why it looks like this

Three findings from the [stage 0 spike](../docs/spikes/hermes-capabilities.md) shape the design, and each would otherwise fail silently:

- **Two statuses, not one.** `request-review` moves a task to `review`; `block` moves it to `blocked`. A poller watching only `blocked` misses every design-doc gate.
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

## Approval watcher

`approvals.py` is the other half of the loop: it turns your decision in GitHub into a card transition.

```bash
python3 approvals.py --board <board>
python3 approvals.py --board <board> --dry-run
```

### Comment normally; label when you are done

Review the design doc however you like — inline on the diff, at the top level, or in a review. Every comment is collected and handed to the worker, oldest first, with inline comments keeping their `file:line` so "this is wrong" still points somewhere.

Comments cannot say whether you have *finished*, though. Three comments might mean "fix these and resubmit" or "answered your questions, carry on", and guessing either resumes work on unresolved feedback or stalls the card in silence. So the decision is a separate, explicit act:

| Signal | Means | Notes |
| --- | --- | --- |
| `dd-changes` label | rework | Outranks `dd-approved` if both are present |
| `dd-approved` label | approved | Approve without merging yet |
| Pull request merged | approved | Merging the design doc *is* approving it |
| Review state | either | Only works on pull requests opened by somebody else |

Labels are the main path because they also give you a filterable queue in the GitHub UI: everything carrying a decision label is work the system is waiting on you to finish.

**Labels are removed once acted on.** A label left in place is a decision the next poll makes again, reopening a card the author has already revised.

Agent pull requests are opened under your account, and GitHub refuses **both** `APPROVED` and `CHANGES_REQUESTED` on your own pull request, leaving `COMMENTED` as the only submittable state. That is the same rule that stops an agent approving its own work; it just also applies to you, which is why review states are the fallback rather than the main path.

### Transitions depend on where the card waits

`unblock` only accepts a blocked card. A card in `review` needs `reopen-review` to go back to its author, or `complete` to finish. The watcher picks per card status; calling the wrong one fails loudly.

Feedback is always commented on the card *before* the transition, because resuming re-spawns the worker and it must wake with the feedback already in context.
