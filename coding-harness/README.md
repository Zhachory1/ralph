# Coding harness (spec Sec 11)

Host-native MCP server. Its tools are thin wrappers over a configured CLI
(`adapters.json`), per the harness-agnostic contract in `docs/v4-spec.docx`
Sec 11.0 — not a reimplementation of coding, testing, or worktree logic.
Swapping the underlying CLI is an `adapters.json` change, never a change to
the tool signatures below.

## Status

Three tools implemented: `implement_spec`, `get_worktree_status`, and
`apply_pr_feedback`, against one adapter (`mewrite`). `implement_spec` and
`apply_pr_feedback` publish changes when the adapter produces a diff.
Only `implement_spec` can open a draft PR. Not yet built: `fix_ci_failure`, `audit_pr`,
`reproduce_and_patch` (spec Sec 11's other three), and the skill-synthesis
loop (Sec 11.0.1) — that needs a real judgment-making profile to own it,
not this server.

## Setup

```
cd coding-harness
npm install
```

## Running

Not wired into any control plane yet (no MCP client calls this
automatically). To exercise it directly, point an MCP client at
`node server.js` over stdio and call `implement_spec` with:

```json
{
  "ticket_id": "some-id",
  "repo_path": "/absolute/path/to/a/git/repo",
  "branch": "some-branch-name",
  "rfc_path": "/absolute/path/to/an/rfc.md",
  "acceptance_criteria": ["one line per criterion"]
}
```

It provisions (or reuses) a worktree at `<repo_path>/../.harness-worktrees/<ticket_id>`,
runs the configured adapter non-interactively inside it, and returns:

```json
{
  "status": "completed | failed | error",
  "pr_url": "a real https://github.com/... draft PR URL, or null",
  "branch": "...",
  "test_summary": "the adapter's final message",
  "diff_stats": "git diff --stat output, or null if nothing changed",
  "open_questions": ["present on error/failed, or a non-fatal PR/commit note"]
}
```

`pr_url` is only populated when `status` is `"completed"` **and** the
adapter actually produced a diff: a run that finds nothing to change (the
spec was already satisfied) commits nothing and opens nothing. When a diff
exists, the harness commits it under its own identity (`-c user.name=
ralph-coding-harness`, not the host's or operator's git config -- found
missing by hitting "Author identity unknown" on a host with no global git
identity set at all), pushes the branch, and opens a draft PR
(`gh pr create --draft`) or reuses the existing one for that branch
(`gh pr list --head`). A push/PR failure is never fatal to the call: it is
reported as an `open_questions` note alongside the real `status`/`diff_stats`
that already reflect whether the adapter's code change succeeded,
independent of whether GitHub access happened to work this run.

Two concurrent `implement_spec` calls targeting the same branch (not
necessarily the same `ticket_id`) can both pass the `pr list` check before
either creates one; `gh pr create` itself refuses a second PR for a branch
that already has one, and that refusal is caught and resolved by
re-checking `pr list` rather than treated as a real failure -- confirmed
directly by forcing a real `gh pr create` collision against a live PR and
confirming the loser recovers the winner's URL rather than erroring.

`"error"` means setup/infra failed before the adapter ever ran (bad
`rfc_path`, a broken or raced worktree, the adapter binary missing). `"failed"`
means the adapter ran but did not complete cleanly (non-zero exit, or exited
0 without emitting a parseable final message). A caller should treat these
differently: an `"error"` is a request/environment problem to fix before
retrying; a `"failed"` is a legitimate adapter outcome.

`get_worktree_status` takes `{ ticket_id, repo_path }` and returns:

```json
{
  "status": "completed | error",
  "branch": "current branch name, or null on error or detached HEAD",
  "clean": "true iff `git status --porcelain` is empty, or null on error",
  "ci_state": null,
  "pending_notes_count": 0,
  "open_questions": ["present on error, or a detached-HEAD note"]
}
```

`ticket_id` is carried but not used to derive a path: `repo_path` is
already the worktree to inspect (matching the spec's description for the
harness's other "checks out existing worktree" tools), unlike
`implement_spec`'s `repo_path`, which is the base repo a *new* worktree is
provisioned from. A detached-HEAD worktree returns `branch: null` with an
explanatory `open_questions` entry rather than the literal string `"HEAD"`,
which git itself would otherwise report as if it were a real branch name.

`ci_state` and `pending_notes_count` are always stubbed (`null`/`0`): no CI
integration and no `pending_human_notes` table exist yet. An honest stub is
correct here, not a shortcut to fix later in this same tool -- those need
their own slices (CI webhook wiring, the Postgres table from spec Sec 11.2).

`apply_pr_feedback` takes `{ ticket_id, repo_path, branch, review_comments }`
and returns:

```json
{
  "status": "completed | failed | error",
  "commit_sha": "the pushed commit's SHA, or null if nothing changed/was committed",
  "resolved_count": 0,
  "test_summary": "the adapter's final message, or null on error",
  "open_questions": ["present on error/failed, or a non-fatal commit/PR note"]
}
```

Unlike `implement_spec`, this tool **never creates a worktree**: it
operates on the worktree `implement_spec` already created for `ticket_id`,
and returns `status: "error"` if none exists -- a caller passing a
`ticket_id`/`repo_path` that was never run through `implement_spec` first
gets a clear error, not a worktree silently created out of thin air for a
tool whose whole point is reacting to feedback on existing work.

Feedback requires an existing open PR on the branch in the GitHub `origin`
push repository. It never creates a PR. `resolved_count` stays `0` because
this tool does not resolve review threads. If publishing fails, it returns
an error with `commit_sha: null`; any local commit is retained.

Before touching the worktree, `apply_pr_feedback` verifies it is actually
checked out to the `branch` argument the caller passed (not just that it
is *some* valid worktree) -- a stale or mismatched `branch` would
otherwise push whatever the worktree happens to be on to a remote ref
named after the caller's claim, silently landing feedback on the wrong
branch.

Both `implement_spec` and `apply_pr_feedback` serialize every call for one
`ticket_id` through a filesystem lock (`.<ticket_id>.lock` next to the
worktrees, created with `O_EXCL`), so two concurrent calls -- either tool,
against either tool -- never run their adapter processes or mutate the
git index in the same worktree at the same time. An in-process lock would
not work here: each MCP client connection spawns its own `node server.js`
process, confirmed directly while testing this. Verified against a real
race: two concurrent `apply_pr_feedback` calls for the same ticket_id ran
sequentially (not concurrently), producing two real, correctly-ordered
commits on the sandbox repo rather than a corrupted worktree.

## Known limitations, accepted for this slice

- **Unbounded output buffering.** The adapter's stdout/stderr are
  accumulated as plain strings with no size cap. A very long-running or
  unusually chatty adapter run grows memory proportionally; fine for a
  single-ticket proof of concept, worth a cap before this runs unattended
  against arbitrary tickets.
- **RFC/criteria content can resurface verbatim.** `test_summary` is the
  adapter's own final message, which may echo back content from the RFC or
  acceptance criteria; `open_questions` on a `"failed"` run includes a slice
  of the adapter's raw stderr. Neither is redacted. If an RFC or criteria
  ever carries sensitive data, that data can appear in this tool's return
  payload -- a concern for whatever calls this harness, not solved here.

## A real gotcha this slice found

`child_process.spawn()`'s default stdio (`pipe` for stdin) leaves stdin open
with no EOF. `mewrite -p` blocks waiting on it even though `-p` mode never
reads stdin itself — not documented behavior, found by the spawned call
hanging indefinitely with zero output while the identical invocation from a
shell (which closes stdin or inherits a TTY) completed in seconds. Fixed by
setting `stdio: ["ignore", "pipe", "pipe"]` explicitly (`server.js`).

## Known limitation, accepted for this slice: a transient `gh` failure right after a successful push can leave a branch with no PR

If `git push` succeeds but the immediate `gh pr list` call fails (a network
blip), `ensurePullRequest` returns a note and `pr_url: null`. On retry, if
the adapter finds nothing further to change, `diffStat` is empty and the
whole commit/PR step is skipped entirely -- the branch stays pushed with no
PR and no further attempt, until a caller notices the stale `open_questions`
note from the one run that hit it. Narrow (needs a `gh` failure in that
exact few-hundred-millisecond window) and not reproduced; documented rather
than guarded against, same as the OAuth-token-expiry limitation above --
fixing it means always attempting `ensurePullRequest` even when `diffStat`
is empty, which needs its own slice to get the "was this branch ever
actually PR'd" check right, not stapled onto this one.

## A second bug this slice found: a stale PR on re-run

The first version of PR creation checked for an existing open PR on the
branch and returned its URL immediately if found -- skipping the push
entirely. That meant a second `implement_spec` call against a branch that
already had an open PR committed its new changes locally but never pushed
them: the returned `pr_url` and `diff_stats` both looked correct, but the
PR on GitHub silently went stale. Caught by actually pushing a second real
change through this path on a live test repo and finding the remote branch
one commit behind local -- not by code inspection. Fixed by always pushing
first, then checking for an existing PR to reuse or creating a new one.

## A bug the pipeline itself caught

The first implementation of `get_worktree_status` was written by `swe` (the
Tier B profile, via `implement_spec`) against a real Vikunja ticket whose
acceptance criteria asked for "the same error-payload pattern `implement_spec`
uses." `swe`'s own self-report claimed `status: "completed"` with all criteria
met. Independently calling the resulting tool directly (not trusting that
report) found the error path actually returned `{status, open_questions}`
only, missing `branch`/`clean`/`ci_state`/`pending_notes_count`. Fixed to
return its own full payload shape, nulled out, and re-verified directly
against three real cases (invalid repo, dirty repo, clean repo) before
trusting it again.

An independent review on this PR caught two more gaps the self-report also
missed: `ticket_id` was declared required in the tool's schema with no
comment on why it is unused (it genuinely is unused -- `repo_path` is
already the worktree to inspect, same as the harness's other "checks out
existing worktree" tools -- now documented, not just silently dropped),
and a detached-HEAD worktree would have silently reported the literal
string `"HEAD"` as if it were a real branch name. Both fixed and
re-verified (a detached-HEAD checkout now returns `branch: null` with an
explanatory note) before merge.
