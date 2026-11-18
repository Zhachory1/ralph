# 0006: v4 base-drift handling preserves published PR history

- **Status:** proposed for v4 implementation
- **Date:** 2026-11-18
- **Issues:** [#203](https://github.com/Zhachory1/ralph/issues/203), [#206](https://github.com/Zhachory1/ralph/issues/206)

## Context

The v4 spec's bootstrap list mentions `rebase_and_resolve`, but the Sec 11
tool table gives it no signature or return contract. Sec 6.1 calls for
detecting `BEHIND` and `DIRTY` PRs without defining how to publish a
rebased branch. Remote history rewrites and conflict resolutions can
invalidate existing human review.

## Decision

Build this with the PR maintenance workflow, not as a generic Git tool.
Handle `BEHIND` using a history-preserving update against the event's exact
PR head. On `DIRTY`, prepare a local candidate on the pinned head and base
under the same ticket lease, or report the conflicts if no candidate can be
verified. Neither path may force-push, merge into the base branch, or
publish a conflict resolution without Director authorization.

Use `rebase_and_resolve` for the local candidate operation if the caller
needs a harness tool: inputs are the trusted checkout, ticket, PR branch,
expected head SHA, and base SHA; the response distinguishes stale input,
conflicts, verification failure, and a verified candidate SHA. A candidate
must not replace the active worktree or its branch. Renew review when the
published head changes.

## Verification for the maintenance slice

Use a disposable canary PR. Show that an outdated event makes no change,
a clean update preserves published commits, and a conflict either produces
an isolated candidate or escalates without pushing. Verify that no path
can merge the PR using the maintenance credential.
