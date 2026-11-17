# Coding harness (spec Sec 11)

Host-native MCP server. Its tools are thin wrappers over a configured CLI
(`adapters.json`), per the harness-agnostic contract in `docs/v4-spec.docx`
Sec 11.0 — not a reimplementation of coding, testing, or worktree logic.
Swapping the underlying CLI is an `adapters.json` change, never a change to
the tool signatures below.

## Status

One tool implemented this slice: `implement_spec`, against one adapter
(`mewrite`). Not yet built: `apply_pr_feedback`, `fix_ci_failure`, `audit_pr`,
`reproduce_and_patch`, `get_worktree_status` (spec Sec 11's other five), real
GitHub PR creation (`pr_url` is always `null`), and the skill-synthesis loop
(Sec 11.0.1) — that needs a real judgment-making profile to own it, not this
server.

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
  "pr_url": null,
  "branch": "...",
  "test_summary": "the adapter's final message",
  "diff_stats": "git diff --stat output, or null if nothing changed",
  "open_questions": ["present only on error/failed"]
}
```

`"error"` means setup/infra failed before the adapter ever ran (bad
`rfc_path`, a broken or raced worktree, the adapter binary missing). `"failed"`
means the adapter ran but did not complete cleanly (non-zero exit, or exited
0 without emitting a parseable final message). A caller should treat these
differently: an `"error"` is a request/environment problem to fix before
retrying; a `"failed"` is a legitimate adapter outcome.

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
