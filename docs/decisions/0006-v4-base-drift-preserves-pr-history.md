# 0006: v4 base-drift handling preserves published PR history

- **Status:** proposed for v4 implementation
- **Date:** 2026-11-18
- **Issues:** [#203](https://github.com/Zhachory1/ralph/issues/203), [#206](https://github.com/Zhachory1/ralph/issues/206)

## Context

[`docs/v4-spec.docx`](../v4-spec.docx), §12.2, lists `rebase_and_resolve`
under **Host Coding Harness MCP**, but the §11 tool table gives it no
signature or return contract. §6.1 calls for
detecting `BEHIND` and `DIRTY` PRs without defining how to publish a
rebased branch. Remote history rewrites and conflict resolutions can
invalidate existing human review.

## Decision

Implement this in #206, which owns PR maintenance and base-drift events.
This decision does not add a runtime tool.
Handle `BEHIND` using a history-preserving update against the event's exact
PR head. On `DIRTY`, prepare a local candidate on the pinned head and base
under the same ticket lease, or report the conflicts if no candidate can be
verified. Neither path may force-push, merge into the base branch, or
publish a conflict resolution. The `DIRTY` path has no GitHub write
credential or publishing endpoint. For `BEHIND`, #206 must expose only a
fixed update-branch operation to the worker, with an expected-head check;
keep its GitHub credential behind that operation. Require protected base
branches, required human review with stale approvals dismissed, and no
automation bypass. Until #206 implements and verifies these controls,
this document grants nothing and v4 must not enable drift maintenance.

Build `rebase_and_resolve` as a local candidate operation in #206. Inputs
are the trusted checkout, ticket, PR branch,
expected head SHA, and base SHA; the response distinguishes stale input,
conflicts, verification failure, and a verified candidate SHA. A candidate
must not replace the active worktree or its branch. Since a true rebase
changes commit IDs, the Director may publish the candidate under a **new
branch and draft PR** using their own credential, then supersede the old
PR. Never force-push the old branch. Review starts fresh on the new PR.

## Verification for the maintenance slice

Use a disposable canary PR. Show that an outdated event makes no change,
a clean update preserves published commits, and a conflict either produces
an isolated candidate or escalates without pushing. Exercise a Director
publishing the candidate as a new draft and verify that the old head and
review history are unchanged. Verify the maintenance worker cannot publish
a candidate, force-push, or merge the PR.
