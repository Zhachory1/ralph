# ✊ Ralph

**R**eliable **A**gent **L**oop using **P**ersonal **H**ermes.

[![licence](https://img.shields.io/badge/licence-MIT-blue)](LICENSE)
[![Hermes Agent](https://img.shields.io/badge/Hermes%20Agent-v0.22.2-6b4fbb)](https://hermes-agent.nousresearch.com/)
[![skills](https://img.shields.io/badge/skills-50%20public%20%2B%203%20pinned-2f8f4e)](skills/README.md)
[![scope check](https://github.com/Zhachory1/ralph/actions/workflows/scope-check.yml/badge.svg)](https://github.com/Zhachory1/ralph/actions/workflows/scope-check.yml)
[![skills public](https://github.com/Zhachory1/ralph/actions/workflows/skills-public.yml/badge.svg)](https://github.com/Zhachory1/ralph/actions/workflows/skills-public.yml)

Ralph is a personal developer assistant that runs on your own computer, built on [Hermes Agent](https://hermes-agent.nousresearch.com/). You write requirements and approve design docs; agents draft the designs, open the pull requests, review them, and keep them healthy. You decide what merges.

The job it creates is **director, not reviewer-of-leftovers**. Review isn't the residue left after automation — it's the work. Every design choice here answers "does this make review faster and more trustworthy?", not "does this reduce the number of things you look at."

> **Status: pre-release.** This repository holds the design and the layer boundary. The phase-1 core loop is not built yet. See [Build order](#build-order) for what lands first, and [Open questions](#open-questions) for what has to be verified before it can.

## How it works

Work runs as a graph of linked cards on a Hermes Kanban board. The orchestrator plans the graph; the dispatcher runs it. Profiles never chat with each other — they hand off through cards and through GitHub, which keeps coordination deterministic and keeps one bad agent turn from derailing a feature mid-flight.

```
PRD → scope check → DD draft → council critique → [you approve] →
decompose → build → review → [you merge] → launch plan
```

Two human gates sit in the main line: design-doc approval and merge. Agents prepare everything between them.

### Profiles exist at permission boundaries

A new profile is justified by different permissions, resources, or lifecycle — never by different know-how. Different know-how is a skill.

| Profile | Can | Cannot |
| --- | --- | --- |
| orchestrator | Talk to you, own the board | Write to any repo |
| author | Write the docs repo | Touch code repos |
| reviewer | Comment, request changes | Approve, push |
| builder | Branch, push, open PRs | Merge |
| researcher | Read the web | Hold any credential |
| analyst | Read the warehouse | Write anywhere |
| experimenter | Spend compute, under a hard cap | Deploy, write production data |
| auditor | Judge the system | Edit what it judges |
| watcher | Read observability | Write anything |
| chat-maintainer | Read staged chat, decide routing, draft replies | Hold the chat credential, send, create cards |

All ten exist. Five have done real work: the orchestrator planned a feature's card graph, the author wrote a design that shipped, the builder wrote the three pull requests that implemented it, the reviewer found a status-code enumeration leak nothing else caught, and the researcher answered a question about this repository's own sandbox. The other five are built and unexercised.

### Invariants live in permissions, not prose

Hermes can rewrite skill text, so a rule that exists only in a `SKILL.md` can be optimized away. The rules that matter are enforced where an agent can't reach them: per-profile scoped tokens, blocked commands in a profile's toolset, branch protection, and hard spend caps.

The property that holds unconditionally:

- **No agent can approve agent work.** Agent PRs are opened under your account, and GitHub never lets a PR's author approve their own PR. This one needs no configuration and no cooperation.

Merging is guarded rather than impossible. Branch protection on the target repository is the real control, backed by token scope; `hermes tools` works at toolset granularity, so it can't block `merge` while leaving the builder the terminal access it needs to run tests. Treat "agents don't merge" as a repository setting you own, not a property of this system, and configure branch protection before pointing a builder at a repo that matters.

A two-level kill switch halts everything in minutes without relying on any agent's cooperation: `hermes pause` stops dispatch, and the Stop level additionally revokes credentials and drafts open agent PRs.

### Agents record what they guessed

A builder with repo access, search, and a researcher behind it rarely gets *stuck* — it finds a plausible answer and proceeds. A blocked card is visible and cheap; a confident wrong inference arrives in the review queue looking finished.

So every handoff and every PR carries an **Assumptions** section: each inference the agent made that the design doc didn't state, one line apiece. The reviewer reads it before the diff. A rising count means designs are under-specified, and an overturned assumption is the leading indicator of an escaped defect.

## The three layers

Ralph is the middle of three layers. Each one is a starting point, not a framework you stay inside.

| Layer | Repository | Holds |
| --- | --- | --- |
| 1 | [autopraxis](https://github.com/Zhachory1/autopraxis) | Runtime-agnostic workflows, contracts, modes, evals |
| 2 | **ralph** (this repo) | Profiles, adapters, card templates, cron, invariants, bootstrap, evals |
| 3 | Your private overlay | Your employer's doc standards, internal MCP servers, notifier config |

Layer 2 stays public because most of it isn't specific to anyone: gate adapters, council via subagents, durable run directories, profiles, card templates.

### Mechanism upgrades; your content is yours

The split runs along the container boundary.

- **The image owns machinery:** gate and council adapters, dispatcher glue, bootstrap, `extend` and `retire`, kill-switch scripts, the eval harness, the notifier. Images are versioned and pinned, so a bug fixed in an adapter reaches everyone through a normal upgrade.
- **Your config repo owns content:** skills, `SOUL.md` files, invariant values, workflow templates, your user profile. Nothing here is ever overwritten by an upgrade. Skills should be specific to their user; divergence is the point.

Each image version declares the config-repo schema it expects, and `bootstrap migrate` moves a config repo forward before the new image runs. `doctor` fails on a schema mismatch rather than guessing.

## Prior art

Ralph draws on two repositories of mine and replaces neither:

- **[autopraxis](https://github.com/Zhachory1/autopraxis)** is layer 1 and stays upstream. Its workflows (`plan-to-launch`, `pr-review`, `ml-experiments`, `council`, `backprop`) are ported here, not rewritten. Its bounded loops, explicit contracts, and model-free deterministic evals are the parts that matter most when nobody is watching the session.
- **[ai-pr-automation](https://github.com/Zhachory1/ai-pr-automation)** is the working predecessor: a Compose-managed fleet doing bounded PR review and maintenance against a Postgres queue. It proved the shape. Ralph replaces its bespoke queue with Hermes Kanban, widens the scope from PR review to the whole feature pipeline, and generalizes the deployment so other people can install it.

The PR reviewer is not speculative — it's a port of an agent already reviewing a team's PRs today.

## Build order

Safety mechanisms come before builder autonomy.

1. **Core loop** — hand-written orchestrator and author profiles; a notifier polling both `blocked` and `review`; deterministic orchestration tests. No approval-gate or council adapter: the spike found both are native. No `extend` script yet, because a template is easier to get right after writing the same profile a few times. *Done when* a PRD becomes a design-doc PR, you're notified, and the tests pass.
2. **Safety before autonomy** — kill-switch scripts, per-profile scoped tokens, branch protection, merge commands blocked in the builder's toolset. *Done when* the first kill-switch drill and the sandbox canary's security checks pass.
3. **Builder and reviewer** — agent PR conventions and scope discipline, with the CI scope check live. *Done when* an approved design doc produces reviewed PRs in a sandbox org, then on one real low-risk feature.
4. **Cron** — maintenance sweep, morning digest carrying the day's operational metrics, docs gardener, auditor running the weekly trend report. *Done when* a full week runs with no missed or duplicate alerts.
5. **Local search** over the docs repo. *Done when* every profile can find the current design doc for a feature.
6. **Launch plans**, with you advancing every stage.
7. **Analyst, then experimenter.** Analyst is read-only and low risk; experimenter spends money, so it comes last.

## Open questions

These are verified before the code that depends on them is written, not after. A first round is done: see [`docs/spikes/hermes-capabilities.md`](docs/spikes/hermes-capabilities.md), which found that the approval gate, the council fan-out, durable workspaces, and multiple boards are all native and don't need building.

Still open, and each needs a live worker rather than CLI probing:

- [ ] Whether a worker re-spawns with the review thread in context after `unblock`
- [ ] Whether `request-changes` round-trips a design doc back to its author
- [ ] Whether a `dir:` workspace stays stable across repeated dispatches of one card
- [ ] How Hermes scopes its Docker sandbox, and whether builder and experimenter can get separate ones
- [ ] How Hermes resolves two skills with the same name, needed for layer-3 overrides
- [ ] Whether skills can be pinned or made read-only, so self-optimization can't touch invariant-adjacent skills
- [ ] Whether one Hermes container can host all profiles with a shared board, versus one container per profile

## Running it

Everything self-hosted runs as one Docker Compose stack on your own machine, on an open-source runtime (Colima or Podman on macOS, Docker Engine on Linux). Every connection starts from your machine: GitHub is polled rather than receiving webhooks, because a local machine can't accept them without a public tunnel.

Nothing here depends on a cloud service beyond the ones you already use: your model API, GitHub, and your notifier.

Installation instructions land with phase 1.

## License

MIT
