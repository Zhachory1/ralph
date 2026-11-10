# ✊ Ralph

**R**eliable **A**gent **L**oop using **P**ersonal **H**ermes.

[![licence](https://img.shields.io/badge/licence-MIT-blue)](LICENSE)
[![Hermes Agent](https://img.shields.io/badge/Hermes%20Agent-v0.22.2-6b4fbb)](https://hermes-agent.nousresearch.com/)
[![skills](https://img.shields.io/badge/skills-50%20public%20%2B%203%20pinned-2f8f4e)](skills/README.md)
[![scope check](https://github.com/Zhachory1/ralph/actions/workflows/scope-check.yml/badge.svg)](https://github.com/Zhachory1/ralph/actions/workflows/scope-check.yml)
[![skills public](https://github.com/Zhachory1/ralph/actions/workflows/skills-public.yml/badge.svg)](https://github.com/Zhachory1/ralph/actions/workflows/skills-public.yml)

Ralph is a personal developer assistant that runs on your own computer, built on [Hermes Agent](https://hermes-agent.nousresearch.com/). You write requirements and approve design docs; agents draft the designs, open the pull requests, review them, and keep them healthy. You decide what merges.

The job it creates is **director, not reviewer-of-leftovers**. Review isn't the residue left after automation — it's the work. Every design choice here answers "does this make review faster and more trustworthy?", not "does this reduce the number of things you look at."

Every profile starts with the shared [Ralph principles](profiles/PRINCIPLES.md): ground the current system, reuse before building, ship the smallest complete slice, respect role boundaries, consider the whole workflow, clarify before claiming progress, and finish with evidence. The shared [engineering standards](profiles/STANDARDS.md) follow them: coding, testing, ownership, and documentation (the docs repository, never Google Docs). `bootstrap/install` prepends both to each profile's `SOUL.md`; narrower role and safety rules still apply.

> **Status: pre-release.** This repository holds the design and the layer boundary. The phase-1 core loop is not built yet. See [Build order](#build-order) for what lands first, and [Open questions](#open-questions) for what has to be verified before it can.

## How it works

Work runs as a graph of linked cards on a Hermes Kanban board. The orchestrator plans the graph; the dispatcher runs it. Profiles never chat with each other — they hand off through cards and through GitHub, which keeps coordination deterministic and keeps one bad agent turn from derailing a feature mid-flight.

```
PRD → scope check → DD draft → council critique → [you approve] →
decompose → build → review → [you merge] → launch plan
```

Two human gates sit in the main line: design-doc approval and merge. Agents prepare everything between them.

### Profiles exist at permission boundaries

A new profile is justified by different permissions, resources, or lifecycle — never by different know-how. Different know-how is a skill. Every profile can read repositories with `gh` ([decision 0001](docs/decisions/0001-every-profile-gets-repository-access.md); on the personal instance as the owner, [0002](docs/decisions/0002-personal-profiles-read-with-the-owner-token.md)); the table below is about what each profile may change.

| Profile | Can | Cannot |
| --- | --- | --- |
| orchestrator | Talk to you, own the board | Write to any repo |
| author | Write the docs repo | Touch code repos |
| reviewer | Comment, request changes | Approve, push |
| builder | Branch, push, open PRs | Merge |
| researcher | Read the web and repositories | Change a repository |
| analyst | Read the warehouse | Write anywhere |
| experimenter | Spend compute, under a hard cap | Deploy, write production data |
| auditor | Judge the system | Edit what it judges |
| librarian | Tidy the docs repo through one PR a week | Delete, merge, or judge |
| watcher | Read observability | Write anything |
| chat-maintainer | Read staged chat, route, write replies | Hold the chat credential, send directly, create cards |

The docs repository's default-branch protection is a prerequisite: an active ruleset with an empty bypass list, so no token — not even the owner's — can push straight to its default branch. That ruleset, not the librarian's push token, is what makes "agents don't merge" hold for the docs repository; protect it before installing the librarian on an instance.

All eleven exist. Five have done real work: the orchestrator planned a feature's card graph, the author wrote a design that shipped, the builder wrote the three pull requests that implemented it, the reviewer found a status-code enumeration leak nothing else caught, and the researcher answered a question about this repository's own sandbox. The other six are built and unexercised until the librarian's first pull request merges.

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
4. **Cron** — maintenance sweep, morning digest carrying the day's operational metrics, the librarian (#130), auditor running the weekly trend report. *Done when* a full week runs with no missed or duplicate alerts.
5. **Local search** over the docs repo. *Done when* every profile can find the current design doc for a feature. Built (#121): a QMD service per instance, MCP tools for the searching profiles, memories in the docs repo, hourly refresh.
6. **Launch plans**, with you advancing every stage.
7. **Analyst, then experimenter.** Analyst is read-only and low risk; experimenter spends money, so it comes last.

## Open questions

All answered (2026-11-08), from a first round of CLI probing ([`docs/spikes/hermes-capabilities.md`](docs/spikes/hermes-capabilities.md)) and then from live workers. Each answer links the change or test that settled it.

- [x] **Does a worker re-spawn with the review thread in context after `unblock`?** Yes. The decision and feedback are commented on the card before the move, and the resumed worker reads the card. `unblock` alone does **not** re-spawn a card whose comments hold a PR link: Hermes' `active_pr` guard holds it until a handoff to a *different* profile, so the notifier unassigns and reassigns the owner (#114). Live on docs#7, cycled several times.
- [x] **Does `request-changes` round-trip a design doc back to its author?** Not that verb: Hermes accepts it only for a card in an active review run, and a design gate is blocked, not in review. The round trip is the `dd-changes` label: the notifier attaches the feedback, removes the label and resumes the same author (#102, #104, #114). A design that has to change its premise goes back to a PRD round (#115).
- [x] **Does a `dir:` workspace stay stable across repeated dispatches of one card?** Yes. Probed: `workspace_path` is unchanged after block, unblock and reassignment. Review and chat cards rely on it (`dir:` staged evidence), and each sandbox's `/workspace` is per profile and instance under `RALPH_SANDBOX_ROOT` (#98).
- [x] **How does Hermes scope its Docker sandbox, and can builder and experimenter get separate ones?** One sandbox container per profile per instance, labelled `hermes-profile`; its writable mounts are only that profile's `/workspace` and home (#78, #98). So builder and experimenter are separate by construction. `evals/test_sandbox_isolation.py` checks a live sandbox for host, socket, board and cross-profile access.
- [x] **How does Hermes resolve two skills with the same name?** By tier, lowest wins: trusted project > local profile > `skills.create_dir` > `skills.external_dirs` (`agent/skill_utils.py`). Two different skills with the same name *in one tier* are refused, never guessed. So a private overlay lane mounted as `create_dir` shadows a public `external_dirs` skill of the same name; live on work, the builder's `standards` resolves from `create_dir`.
- [x] **Can skills be pinned or made read-only?** Both. The shipped and overlay skill trees are mounted read-only (`/ralph/skills`, `/ralph/private-skills`), so no agent can rewrite them: writes fail with `Read-only file system`. Hermes' own `pinned` flag guards deletion only, so the read-only mount is the boundary that matters.
- [x] **One Hermes container for all profiles, or one per profile?** One per instance. A single `hermes` container hosts all 18 profiles and both boards; isolation comes from a sandbox per profile, not a container per profile. Two instances (personal, work) run side by side on one VM (#40).

## Docs

- [System diagram](docs/diagrams/system.svg): I/O, System and Tools on one page.
- [Infrastructure](docs/infrastructure.md): instances, containers, sandboxes, profiles, credentials, schedules, knowledge.
- [Workflows](docs/workflows.md): feature, review, maintenance, Chat, digest, cleanup, and knowledge search and memories, with diagrams.
- [Operations and onboarding](docs/operations.md): new instance, safe changes, pause and stop, stuck cards, logs, knowledge index, glossary.
- [Decisions](docs/decisions/).

## Running it

Everything self-hosted runs as one Docker Compose stack on your own machine, on an open-source runtime (Colima or Podman on macOS, Docker Engine on Linux). Every connection starts from your machine: GitHub is polled rather than receiving webhooks, because a local machine can't accept them without a public tunnel.

Nothing here depends on a cloud service beyond the ones you already use: your model API, GitHub, and your notifier.

Installation instructions land with phase 1.

## License

MIT
