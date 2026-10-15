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

## Hard rules

- **Nothing private, internal, or employer-specific in this repository.** No internal hostnames, service names, document-standard names, MCP endpoints, ticket IDs, or incident-process detail. This repository is public. Layer 3 exists precisely so that material has somewhere else to live.
- **No secrets, tokens, or credentials**, including in examples, tests, and fixtures. Examples use obvious placeholders.
- **Invariants are enforced in permissions, not prose.** A safety property that exists only as text in a `SKILL.md` is not enforced, because Hermes can rewrite skill text. If you add a safety rule, add the token scope, blocked command, or branch protection that backs it, and say in the same change which mechanism enforces it.
- **No agent approves agent work, and no agent merges without explicit human permission.** Any change that weakens either property needs its own discussion first.
- **Pin image versions.** Never `latest`, anywhere.

## Conventions

- **One concern per PR**, preferably under 400 changed lines. Don't split an indivisible working slice to hit the number.
- **Create directories when the first real file lands in them.** The README's layout is a proposal; empty placeholder trees are scaffolding to match a diagram.
- **Reach for the existing thing first.** Reuse before adding a helper; the standard library before a dependency; an installed dependency before a new one.
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
