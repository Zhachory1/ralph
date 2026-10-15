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
