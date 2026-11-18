# v4 Ready dispatch state

The Ready-to-Draft workflow owns one Postgres row per Vikunja ticket.
`claim_ready_dispatch(ticket_id)` returns a fence token for a new or
expired 25-minute lease, or `NULL` when the ticket is already held or has
a draft. The workflow must recheck the Ready bucket and dependencies
after taking a lease and before invoking SWE.

After independently looking up the result in GitHub,
`record_ready_draft(ticket_id, lease_token, repo, pr_number, head_sha)`
records the exact PR head and moves the row to `waiting_merge`. It
returns `false` for a stale or expired lease. This fences database writes,
not external side effects: the dispatch workflow must use a stable ticket
and branch and reconcile an already opened draft before retrying.

This migration does not dispatch jobs or verify merges itself.

## Existing v4 database

Fresh installs apply `02-ready-dispatch.sql` automatically. For an
existing v4 database, apply the migration from `compose/v4` before
enabling a workflow that uses it:

```bash
docker compose exec -T postgres sh -ec \
  'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < init-db/02-ready-dispatch.sql
```

Run `test/ready-dispatch.sql` on a disposable migrated database. It
rolls back its fixtures and checks duplicates, expired workers,
reclamation, and the pinned draft handoff.
