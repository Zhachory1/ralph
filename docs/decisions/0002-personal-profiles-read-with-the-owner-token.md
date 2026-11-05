# 0002: Personal-instance profiles read GitHub with the owner's token

- **Status:** accepted
- **Date:** 2026-11-05
- **Decided by:** the owner
- **Builds on:** [0001](0001-every-profile-gets-repository-access.md)

## Decision

On the personal instance, every profile without push rights reads GitHub with the owner's token (`Zhachory1`) instead of the review account's (`zhachbot`). It is set per instance by `RALPH_PROFILE_READ_TOKEN`. The work instance is unchanged.

## Context

Ralph could not read `rcwolf97/backoffice`, a private repository the owner was invited to. The review account is not a collaborator there, so it got 404. Asking every repository owner to invite `zhachbot` does not scale, and Ralph is meant to act as an extension of the owner.

## Consequences

- Ralph reads every repository the owner can.
- **Accepted risk.** Those profiles hold a token with the owner's full permissions. A profile reading untrusted text (a web page, a Chat message, a pull request) can now be prompted into an action as the owner, including writes the owner can make. Under 0001 the same risk applied with the review account's narrower permissions.
- Anything those profiles post to GitHub shows as the owner. Published reviews still go out as the review account: `publish-review` runs in the review loop with `RALPH_GITHUB_TOKEN`, not in a profile.
- Merging stays a human act, enforced by branch protection.
- Leaving `RALPH_PROFILE_READ_TOKEN` blank restores the 0001 behavior.
