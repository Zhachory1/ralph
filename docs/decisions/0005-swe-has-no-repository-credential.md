# 0005: swe gets no repository credential

- **Status:** accepted
- **Date:** 2026-11-17
- **Issue:** [#197](https://github.com/Zhachory1/ralph/pull/197)

## Decision

`swe` is the one profile without `terminal`, so it carries no `GH_TOKEN` and
cannot run `git` or `gh`. It changes a repository only through the
`coding-harness` MCP tool's configured adapter, inside a worktree the harness
manages.

## Context

[0001](0001-every-profile-gets-repository-access.md) gives every profile
`terminal` so it can reach GitHub with the forwarded repository token, and
says "no profile is kept credential-free." `swe` (spec Sec 3) is the first
profile built since that decision whose whole job is calling one MCP tool,
`implement_spec`; it never runs `git` or `gh` itself; a shell is the only way
its SOUL instruction not to edit code directly could actually be violated.
Granting it `terminal` anyway would add a live repository credential with no
matching capability need, for a profile whose input (an RFC path) an
upstream router controls.

## Consequences

- `evals/test_profile_config.py`'s `test_every_profile_can_reach_github_from_a_terminal`
  excludes `swe` by name, and `test_swe_has_no_terminal_or_code_execution`
  enforces the opposite: no `terminal`, no `code_execution`.
- If a later slice gives `swe` a reason to run `git`/`gh` directly, this
  decision needs revisiting, not a silent toolset change.
