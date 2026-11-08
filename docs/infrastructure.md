# Infrastructure

Where each part of Ralph runs, what it can touch, and how to check it is healthy. Workflows are in [workflows.md](workflows.md); day-to-day operations in [operations.md](operations.md).

## Instances

One checkout runs several independent Ralphs. Each is a compose project named by `RALPH_INSTANCE`, with its own env file, containers, volumes, boards, ports and workspaces.

```bash
cd compose
docker compose --env-file .env ps                                   # personal
RALPH_ENV_FILE=.env.work docker compose --env-file .env.work ps      # work
```

| Setting | What it separates |
|---|---|
| `RALPH_INSTANCE` | compose project and container names (`<instance>-hermes-1`, ...) |
| `RALPH_ENV_FILE` | which env file the containers load |
| `RALPH_WORKSPACES` | the host folder mounted at `/workspaces` (repo cache, review evidence, chat inbox) |
| `RALPH_SANDBOX_ROOT` | the host folder for agent sandboxes, mounted at the same path inside |
| `RALPH_DASHBOARD_PORT`, `RALPH_WEBUI_PORT` | localhost ports |

`.env.example` documents every setting. Env files are gitignored and never committed.

## Containers

```mermaid
flowchart LR
    subgraph instance[one instance]
        init[init<br/>runs bootstrap/install, exits]
        hermes[hermes<br/>gateway, dispatcher, cron]
        webui[hermes-webui<br/>chat UI]
        notifier[notifier<br/>gates, approvals, digest]
        loop[review-loop<br/>reviews, maintenance, cleanup]
        proxy[socket-proxy]
    end
    sandbox[(agent sandboxes<br/>one per profile)]
    hermes -->|create/exec via proxy| proxy
    webui -->|create/exec via proxy| proxy
    proxy --> docker[(Docker daemon)]
    docker --> sandbox
    notifier -->|docker exec hermes| hermes
    loop -->|docker exec hermes| hermes
```

| Container | Runs | Can touch |
|---|---|---|
| `socket-proxy` | Docker API filter | Containers, exec, images. Not volumes, not `info`. |
| `init` | `bootstrap/install`, then exits | Writes profiles, SOULs, configs and tokens into the hermes volume. Starts after `hermes` (it configures hermes through the CLI there); `hermes-webui` waits for it to finish. Run it by hand after a change. |
| `hermes` | Gateway, Kanban dispatcher, cron scheduler | The board database, every profile's home, the Docker API through the proxy. |
| `hermes-webui` | Chat UI on `127.0.0.1:$RALPH_WEBUI_PORT` | Same hermes home; runs profiles in-process, with commands in the sandbox. |
| `notifier` | `bootstrap/run-notifier`: approvals, gate alerts, morning digest | GitHub with `RALPH_GITHUB_TOKEN`, the board through `docker exec`, the Chat webhook. |
| `review-loop` | `bootstrap/run-review-loop`: review discovery and publishing, maintenance, sandbox cleanup | GitHub with `RALPH_GITHUB_TOKEN`, the board through `docker exec`, `/workspaces`. |

**Agent sandboxes** are Docker containers that Hermes creates per profile (`terminal.backend: docker`, image `ghcr.io/zhachory1/ralph-sandbox`). Every agent command runs in one. A sandbox sees its own `/workspace` and home, plus only these extras:

| Mount (read-only) | Profiles |
|---|---|
| `/repo-cache` (the instance's clone cache) | author, builder, maintainer, researcher, default |
| `/workspaces/review-work` (staged review evidence) | the four lenses, review-synthesis |
| `/workspaces/chat-inbox` (staged chat evidence) | chat-maintainer |

The sandbox cannot see the host, the Docker socket, the board or another profile's files; `evals/test_sandbox_isolation.py` checks this against a live sandbox.

## Profiles

Every profile's SOUL starts with `profiles/PRINCIPLES.md` and `profiles/STANDARDS.md`, then its own `SOUL.md`. The table in the [README](../README.md#profiles-exist-at-permission-boundaries) lists what each may change. Tools differ by role:

- **Kanban tools:** every general profile and the WebUI `default` profile. The lenses, review-synthesis, chat-maintainer, chat-summarizer and inbox-drafter do not have them, by design.
- **Terminal:** every profile, so `gh` works ([0001](decisions/0001-every-profile-gets-repository-access.md)). `git` and `gh` run only in the terminal, never `execute_code` ([0003](decisions/0003-repository-credentials-only-in-the-terminal.md)).

## Credentials

| Credential | Lives in | Reaches |
|---|---|---|
| `RALPH_GITHUB_TOKEN` (review account) | instance `.env` | notifier, review-loop; read profiles when `RALPH_PROFILE_READ_TOKEN` is blank |
| `RALPH_PROFILE_READ_TOKEN` | instance `.env` | `GH_TOKEN` of every read profile and `default` ([0002](decisions/0002-personal-profiles-read-with-the-owner-token.md)) |
| `RALPH_MAINTAIN_TOKEN` (PR author) | instance `.env` | `GH_TOKEN` of author, builder, maintainer |
| `RALPH_BUILDKITE_TOKEN` | instance `.env` | `BUILDKITE_API_TOKEN` of builder and maintainer |
| Model provider keys | instance `.env` / hermes auth | hermes; never forwarded into a sandbox |
| Google Chat send token | host `~/.config/gws-cli/` | host LaunchAgent `chat-reply` only |
| Chat webhook (`RALPH_NOTIFY_URL`) | instance `.env` | notifier, overlay digest job |

A profile token lives in that profile's own `.env` (mode 600) and is forwarded into its sandbox by name (`terminal.docker_forward_env`), never on a command line.

## Scheduled work

| What | Where | Interval |
|---|---|---|
| Kanban dispatch | hermes gateway | continuous |
| Approvals, gate alerts | notifier | `RALPH_POLL_SECONDS` (900) |
| Morning digest | notifier | once a day after `RALPH_DIGEST_HOUR` |
| Review discovery, publish, maintenance | review-loop | `RALPH_REVIEW_POLL_SECONDS` (900); `run-review-loop --once` on demand |
| Sandbox cleanup | review-loop, `bootstrap/cleanup-sandboxes` | at most hourly |
| Overlay jobs (chat inbox, chat digest) | Hermes cron on `ralph-inbox-drafter` | per job (`hermes -p ralph-inbox-drafter cron list`) |
| Chat reply sender | host LaunchAgent `dev.ralph.chat-reply.<instance>` | every 60 s |

Cron jobs can also be created from the WebUI's Crons panel; pick the Ralph profile the job runs as. `hermes pause` stops cron and Kanban dispatch together.

## Private overlay

`RALPH_PRIVATE_SKILLS` and `RALPH_PRIVATE_JOBS` point at a private checkout (`rokt-it-ralph`) beside this one. Skills mount read-only at `/ralph/private-skills`; each job's `job.env` names its profile and options, and `bootstrap/install-overlay-jobs` copies its scripts into that profile. Nothing work-specific lives in this repo.

## Is what is live what is on `main`?

```bash
# scripts the loops run come straight from this checkout:
docker exec <instance>-review-loop-1 sha256sum /ralph/bootstrap/discover-reviews
sha256sum bootstrap/discover-reviews
# a profile's SOUL is PRINCIPLES + STANDARDS + SOUL:
{ cat profiles/PRINCIPLES.md; echo; cat profiles/STANDARDS.md; echo; cat profiles/builder/SOUL.md; } | sha256sum
docker exec <instance>-hermes-1 sha256sum /home/hermes/.hermes/profiles/ralph-builder/SOUL.md
```

After a merge that changes profiles or `bootstrap/install`, re-run `init` for each instance, from `compose/`: `RALPH_ENV_FILE=.env.<name> docker compose --env-file .env.<name> run --rm init`.
