# Operations and onboarding

How to stand Ralph up, change it safely, and recover it. Components are described in [infrastructure.md](infrastructure.md) and flows in [workflows.md](workflows.md).

## A new instance from zero

1. `cd compose && cp .env.example .env.<name>`. Set `RALPH_INSTANCE`, ports, `RALPH_WORKSPACES`, an absolute `RALPH_SANDBOX_ROOT`, the GitHub tokens and logins, and the model provider.
2. `../bootstrap/secrets --env .env.<name>` fills generated secrets into that file.
3. `RALPH_ENV_FILE=.env.<name> docker compose --env-file .env.<name> up -d`. This runs `init` (profiles, boards, configs), then the services.
4. Sign in to the model provider if it uses a browser flow, from the repository root: `RALPH_ENV_FILE=.env.<name> ./bootstrap/login`.
5. Allowlist repositories (`RALPH_REVIEW_ALLOW`, `RALPH_MAINTAIN_ALLOW`) and the docs repo (`RALPH_DOCS_REPO`).
6. Check: `docker compose ps`, then a dispatched card that runs `gh api user` from the terminal.

## Changing things safely

- **Profile or install change:** merge, then re-run `init` for every instance. Verify a SOUL or config hash against `main` ([infrastructure.md](infrastructure.md#is-what-is-live-what-is-on-main)).
- **New profile:** only for a new permission, resource or lifecycle boundary. Add `profiles/<name>/{SOUL.md,config.yaml}`; decide its toolsets and whether it may use the boards; add it to the README table.
- **New credential:** put it in the instance `.env`, write it into only the profiles that need it in `bootstrap/install`, and forward it by name. Never pass it on a command line. Record a decision under `docs/decisions/` if it widens access.
- **New scheduled job:** a private job goes in the overlay with a `job.env`; a one-off can be created from the WebUI Crons panel.

## Pause, stop, resume

| Situation | Command |
|---|---|
| Halt new work, keep in-flight | `docker exec <instance>-hermes-1 hermes pause --reason "..."` (stops Kanban and cron dispatch) |
| Resume | `docker exec <instance>-hermes-1 hermes resume` |
| An agent is doing damage | `./bootstrap/stop` (pause, kill workers, revoke credentials, draft open agent PRs). Rehearse with `--drill`. |

## Recovering stuck cards

List what is stuck and why:

```bash
docker exec -u hermes <instance>-hermes-1 hermes kanban --board queues list --status blocked --json
docker exec -u hermes <instance>-hermes-1 hermes kanban --board queues show <id> --json   # read the last blocked / gave_up event
```

| Reason | Action |
|---|---|
| `stale_lock` / `gave_up` after a restart | `unblock <id>`, then `assign <id> <profile>` |
| A human decision is needed (policy, approval) | Decide, comment the decision on the card, then unblock it or archive it |
| Card in `triage` | The notifier releases parked doc gates. For others, release with care (see Hermes `specify_triage_task`); do not use `kanban specify`, which rewrites the card with a model. |
| `respawn_guarded: active_pr` | `assign <id> none`, then `assign <id> <owner>` |
| The PR moved on or merged | `archive <id>`; the next pass rebuilds from the current head |

## Reading logs

| What | Where |
|---|---|
| Review discovery, publish, maintenance, cleanup | `docker logs <instance>-review-loop-1` |
| Approvals, gate alerts, digest | `docker logs <instance>-notifier-1` |
| Chat reply sender | `~/Library/Logs/ralph-chat-reply-<instance>.{log,err.log}` |
| A card's run | `docker exec -u hermes <instance>-hermes-1 hermes kanban --board <board> show <id> --json` |
| Disk | `colima ssh -- df -h /` and `du -sh compose/workspaces*/sandboxes` |

## Glossary

| Term | Meaning |
|---|---|
| **Card** | A Kanban task assigned to one profile, with a completion condition. |
| **Board** | `ralph` (feature work, planned by the orchestrator) or `queues` (machine-created: reviews, maintenance, chat intake). |
| **Gate** | A blocked card waiting on the owner. A doc gate opens on merge or `dd-changes`; an `Approve PRD:` / `Approve design:` card opens only on merge. |
| **Lens** | One review perspective (generalist, reliability, MVP, security); review-synthesis combines them. |
| **Round** | One maintenance pass on a PR's current feedback. |
| **operation_id** | The stable id tying a review, maintenance round or chat request to its cards. |
| **Overlay** | The private checkout holding work-specific skills and jobs. |
| **Sandbox** | The per-profile Docker container every agent command runs in. |
| **Repo cache** | Full clones under `/workspaces/repo-cache`, refreshed by review discovery and borrowed by checkouts. |
