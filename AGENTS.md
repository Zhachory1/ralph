# Working in this repository

Ralph is layer 2 of a three-layer system: runtime-agnostic workflows upstream in [autopraxis](https://github.com/Zhachory1/autopraxis), the Hermes machinery here, and a private overlay downstream. Read the [README](README.md) before changing anything; the layer boundary decides where most changes belong.

## What belongs here, and what doesn't

This repository ships **machinery**, not content.

| Belongs here | Belongs elsewhere |
| --- | --- |
| Gate and council adapters, dispatcher glue | A workflow's prose and procedures (layer 1) |
| `bootstrap`, `extend`, `retire`, `doctor` | A user's skills, `SOUL.md` files, invariant values (their config repo) |
| Kill-switch scripts, the eval harness | Employer-specific doc standards, internal MCP servers (layer 3) |
| Card and profile templates | Anything naming a private company system |

The default profiles and templates shipped here are starting points. Users copy and edit them, and their edits are never overwritten by an upgrade. Write them to be forked, not to be depended on.

## Shared principles

**Build Ralph; do not do Ralph’s job.** When implementing or testing a live workflow, the responsible Paperclip seat must perform its own task under its own identity. Authorization for an outcome, destination, or credential does not transfer the task to the coding assistant or host operator.

For example, approval to publish an RFC in the docs repo means the Feature Workflow Manager delegates publication and the Librarian opens the PR. Do not push it from the host, manually create the missing handoff, or ask the Librarian to claim an existing operator-created PR.

If a handoff fails, preserve the evidence, fix the underlying code or configuration, and rerun through the intended actor. Verify **who acted, on which run, and what artifact resulted**. If the native path remains blocked, report the blocker. An operator-assisted result is not an end-to-end pass.

Read [profiles/PRINCIPLES.md](profiles/PRINCIPLES.md) before working in this repository. Ground the current system, reuse what exists, apply MVP/Occam/80-20 to scope, respect the Law of Demeter and profile boundaries, check system-wide effects, and carry work through review and maintenance. Then follow [profiles/STANDARDS.md](profiles/STANDARDS.md) for code, tests, ownership, and documentation. `bootstrap/install` puts both sources before every profile's role instructions; do not maintain separate copies here. Principles never override the narrower permissions and human approval rules below.

## Hard rules

- **Use the native path. Do not build a workaround.** Before adding code or changing a workflow, check the pinned platform and repository for an existing feature, service, module, class, function, or library that already does the job. Trace how an existing caller uses it and verify the smallest relevant case. If it fits, use it through the intended actor. If it fails, fix its configuration or underlying implementation and retest that path. If no supported path fits, report what you checked and the concrete gap before proposing new machinery. A missing approval card, for example, calls for checking the native confirmation workflow—not adding a button that changes task status.
- **Agent handoffs are natural language.** Agents read each other's messages for meaning, not compliance with a rigid output template. If a message is unclear, ask its author. Typed schemas still apply at software and API boundaries; preserve identity, authorization, and human approval checks.
- **Nothing private, internal, or employer-specific in this repository.** No internal hostnames, service names, document-standard names, MCP endpoints, ticket IDs, or incident-process detail. This repository is public. Layer 3 exists precisely so that material has somewhere else to live.
- **No secrets, tokens, or credentials**, including in examples, tests, and fixtures. Examples use obvious placeholders.
- **Invariants are enforced in permissions, not prose.** A safety property that exists only as text in a `SKILL.md` is not enforced, because Hermes can rewrite skill text. If you add a safety rule, add the token scope, blocked command, or branch protection that backs it, and say in the same change which mechanism enforces it.
- **No self-review.** When the Feature Workflow Manager creates a build issue, it must configure and verify a native review stage with a Reviewer distinct from the Developer. Without that stage Paperclip can mark work done without independent review; do not treat that as technical acceptance. A distinct Reviewer may accept or request changes to the exact commit in its assigned stage. This is not GitHub approval, RFC sign-off, merge, or deployment authorization.
- **Pin image versions.** Never `latest`, anywhere.

## Conventions

- **Pull requests are cheap.** Open focused PRs for review. Every PR must have fewer than 750
  changed lines (additions plus deletions) and include tests. Never merge unless the user
  explicitly tells you to. Use protected branches and scoped credentials to enforce that boundary.
- **One concern per PR**, preferably under 400 changed lines. Don't split an indivisible working slice to hit the number.
- **Create directories when the first real file lands in them.** The README's layout is a proposal; empty placeholder trees are scaffolding to match a diagram.
- **Prefer deletion.** The design calls for reviewing the profile split once a year and asking, for each boundary, whether it still protects something or only adds handoffs. Apply that to code too.
- **Don't add error handling for conditions that can't happen.** Validate at system boundaries; trust internal code.
- **Comments explain why, not what.** Default to none.

## Changes to behavior need a failing test first

Orchestration is code and is tested like code. The eval harness uses stub profiles returning canned outputs, so tests assert behavior rather than prose: that a gate card blocks and resumes, that a review loop escalates at its cap, that an approved design doc with N planned PRs produces N chains with the right stacking parents, that the notifier posts exactly once per blocked card.

Never weaken or skip a test to make code pass. If a test looks wrong, say so rather than working around it.

## Verify before you build on it

The README's open-questions list exists because Hermes moves quickly and several design decisions rest on behavior that hasn't been confirmed. Don't write code that assumes an unverified answer. Confirm it against your installed version, then remove the item in the same PR as the code that depends on it.

## If you are an agent working here

- The request is the issue or card brief you were given. Stop when its acceptance criteria are met.
- Record every inference you made that your brief didn't state, one line each, under **Assumptions** in your PR. "None" is valid and should be rare.
- Out-of-scope problems you notice go under **Other observations**, one line each. Report them; don't fix them.
- If you can't finish without broader changes, stop and say what's needed and why. Don't proceed on an assumption you can't defend.

Before changing infrastructure or a workflow, read [docs/infrastructure.md](docs/infrastructure.md), [docs/workflows.md](docs/workflows.md) and [docs/operations.md](docs/operations.md), and update them in the same pull request when behavior changes.
