# 0001: Every profile gets repository access

- **Status:** accepted
- **Date:** 2026-11-04
- **Decided by:** the owner
- **Issue:** [#74](https://github.com/Zhachory1/ralph/issues/74)

## Decision

Every Ralph profile has the `terminal` toolset, so it can use `gh` with the repository token its instance forwards into agent sandboxes. No profile is kept credential-free.

## Context

The original design kept profiles that read untrusted text (the researcher, chat-maintainer, chat-summarizer, inbox-drafter, the review lenses, and review synthesis) away from any repository credential. Text from a web page, a Chat message, or an outside pull request could not talk them into a GitHub action, because they had nothing to act with.

In practice the boundary kept stopping ordinary work. A Chat request needed facts from a private repository; the orchestrator routed that research to the researcher, which had no repository access, so the card blocked and the request stalled until a person reassigned it. The owner judged that the cost of these dead ends outweighs the protection.

## Consequences

- Any profile can read repositories, so the orchestrator can route research to the profile that fits the question rather than the one that happens to hold a token.
- `terminal`, not `code_execution`, carries the token: `code_execution` scrubs variables whose names contain `TOKEN`.
- **Accepted risk.** A profile that reads untrusted text can now be prompted toward a GitHub action with the instance token, for example a comment or review as that token's account. SOULs say which profiles must not write, but that is prose, not a permission.
- Merging stays a human act, enforced by branch protection on each repository, not by missing tools.
- Token identities are unchanged: builder, maintainer and author still push as the PR author; every other profile uses the stack's review token. [0002](0002-personal-profiles-read-with-the-owner-token.md) lets an instance give those profiles the owner's token instead.
- Credentials other than the repository token are unchanged. The Google Chat and Gmail credentials still live on the host, outside every profile.

## Not decided here

MCP servers. Profiles do not load `mcp_servers` today, because `bootstrap/write-profile-config.py` does not copy that block. Giving every profile the same MCPs is a separate change.
