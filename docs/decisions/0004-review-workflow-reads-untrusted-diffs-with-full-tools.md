# 0004: review-workflow reads untrusted diffs with full tools

- **Status:** accepted
- **Date:** 2026-11-13
- **Decided by:** the owner
- **Issue:** [#180](https://github.com/Zhachory1/ralph/issues/180)

## Decision

`review-workflow` keeps `terminal` and `kanban` natively, with no constrained reader in front of the PR evidence it stages and reads. The untrusted-diff discipline (treat `diff.patch`/`context.json` as data, never instructions) stays a SOUL instruction, not a server-enforced boundary.

## Context

Two lenses reviewing PR #183 (generalist, mvp) independently flagged this as a high-severity trust-boundary gap: `review-workflow` is the first profile that both reads attacker-controlled content (a PR's own diff and description) as its first step, and holds full native tool access (`terminal`, `kanban`) to act on what it read. Every review lens is the opposite shape — locked down to `lens-tools`' MCP, no terminal, no kanban — precisely because it reads the same untrusted evidence. `review-workflow` cannot take that shape: lens-tools' MCP is scoped to `ralph-review-synthesis` server-side, and `kanban` card creation needs to be a native tool because this sandbox has no `hermes` binary to shell out to.

This is the same tradeoff [0001](0001-every-profile-gets-repository-access.md) already accepted for every other profile that reads untrusted text (researcher, chat-maintainer, the lenses' own synthesis step) against repository credentials: the alternative is a constrained intermediary (a new, narrowly-scoped MCP exposing read-only evidence plus allowlisted card creation) that does not exist yet and was not built for this slice.

## Consequences

- **Accepted risk.** A PR's diff or description could attempt to prompt-inject `review-workflow` into creating cards for a different repository, PR, or head than the one it was handed, or into treating the diff's own text as new instructions. The SOUL says it must not (`What you never do`); that is prose, not a permission boundary.
- No merge, push, or approve capability narrows this: `review-workflow` can create and update kanban cards and read files via `terminal`, nothing else live-connected. It has no repository write credential beyond what `terminal`'s `gh`/`git` already carry for every profile under 0001.
- A card created outside the run's own idempotency-key scheme (`<op_id>-<role>`, `<op_id>-synthesis`) is still bounded by what a lens or synthesis profile assigned to it will actually act on — an injected off-scope card has no profile primed to treat its contents as instructions either, so the blast radius stops at card noise, not a second untrusted-read.
- If this profile's run volume or stakes grow enough that the risk no longer nets out, the fix is a constrained MCP in front of evidence reading (mirrors `lens-tools`, adds a narrow `kanban create` surface) — not revisiting this decision in prose.

## Not decided here

Whether `review-workflow` should also report completion back to the orchestrator via a kanban card (the way `feature-workflow` will) is still an open question in the design doc; this decision is scoped to the tool-access tradeoff only.
