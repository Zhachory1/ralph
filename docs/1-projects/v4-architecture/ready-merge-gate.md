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

Before wiring the board update, configure the view's `done_bucket_id`
to point to Done. Vikunja 0.24.6 was verified to mark the task done when
moving it into that configured bucket. The observer should accept only
the Director PR Merge bucket or a task already in Done.

This slice adds the gate and database functions, not the scheduled
observer or a live canary merge. Run `test/ready-merge.sql` on a disposable
migrated v4 database and `node --test compose/v4/test/verify-ready-merge.test.mjs`
to check the gate.
