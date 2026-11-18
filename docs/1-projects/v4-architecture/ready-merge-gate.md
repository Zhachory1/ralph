# v4 Ready-to-Done merge proof

The merge observer must read the pull request directly from GitHub, not
trust a webhook body or a worker summary. A receipt requires the ticket's
pinned repo, PR number, branch and head SHA; the expected base and draft
author; and a closed, merged PR with a merge commit and timestamp. The
merge actor must be a configured Director and must not be the automation
author. A changed head requires re-verification and is not accepted by
this canary gate.

Use a distinct Director identity and enforce human review on the target
branch with no automation bypass. GitHub's actor field cannot distinguish
a human click from software using their credential. The observer needs
read-only GitHub access; it never calls a merge endpoint.

Apply `compose/v4/init-db/03-ready-merge.sql` after the Ready lease
migration. `record_ready_merge` matches the proof against the existing
pinned handoff and stores the receipt before any board update. A new
receipt cannot replace a conflicting one. `receipt_rejected` indicates
either a handoff mismatch or conflicting previously recorded proof. After confirming the Done
transition, use `mark_ready_board_done` with the same merge commit;
repeating the acknowledgement is harmless. Receipts without a board
acknowledgement remain retryable. The handoff row stays as a tombstone
against duplicate Ready events.

Before enabling the observer, configure the view's `done_bucket_id`
to point to Done. Vikunja 0.24.6 was verified to mark the task done when
moving it into that configured bucket. The observer should accept only
the Director PR Merge bucket or a task already in Done. The observer
rechecks membership before the move. Vikunja does not provide an atomic
compare-and-move in this API, so concurrent manual moves can still race.

Add `directorBucketId` and `doneBucketId` to the private runner config's
`board`, plus a top-level `directors` array of approved GitHub logins.
The config's `expectedAuthor` must not appear in `directors`. Set
`VIKUNJA_DONE_TOKEN` to a token scoped to the reads and bucket move; it is
separate from the ticket dispatcher's read credential. Run:

```bash
RALPH_READY_CONFIG=/path/to/private-ready-config.json \
  node compose/v4/run-ready-merge.mjs 101
```

This reads `gh api` only. A ticket outside the Director/Done bucket is
never acknowledged. `done` is recorded after checking both task status
and membership, and retries after a successful move are idempotent.

Run `test/ready-merge.sql` on a disposable migrated v4 database and
`node --test compose/v4/test/*.test.mjs` to check the guards. The manual
observer was tested with real Postgres and Vikunja and synthetic GitHub
responses; a live canary still needs its separate Director identity.
