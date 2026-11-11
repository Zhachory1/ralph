# Operations and onboarding

How to stand Ralph up, change it safely, and recover it. Components are described in [infrastructure.md](infrastructure.md) and flows in [workflows.md](workflows.md).

## A new instance from zero

1. `cd compose && cp .env.example .env.<name>`. Set `RALPH_INSTANCE`, ports, `RALPH_WORKSPACES`, an absolute `RALPH_SANDBOX_ROOT`, the GitHub tokens and logins, and the model provider.
2. `../bootstrap/secrets --env .env.<name>` fills generated secrets into that file.
3. `RALPH_ENV_FILE=.env.<name> docker compose --env-file .env.<name> up -d`. This runs `init` (profiles, boards, configs), then the services.
4. Sign in to the model provider if it uses a browser flow, from the repository root: `RALPH_ENV_FILE=.env.<name> ./bootstrap/login`.
5. Allowlist repositories (`RALPH_REVIEW_ALLOW`, `RALPH_MAINTAIN_ALLOW`) and the docs repo (`RALPH_DOCS_REPO`). Keep `COMPOSE_PROFILES=knowledge` for local search; the first `init` downloads ~1 GB of models and indexes the docs repo.
6. Check, from `compose/`: `RALPH_ENV_FILE=.env.<name> docker compose --env-file .env.<name> ps`, then a dispatched card that runs `gh api user` from the terminal, then `../bootstrap/knowledge-status <instance>`.

## Changing things safely

- **Profile or install change:** merge, then re-run `init` for every instance. Verify a SOUL or config hash against `main` ([infrastructure.md](infrastructure.md#is-what-is-live-what-is-on-main)). Setting `container_persistent: false` affects new sessions; containers started under the old persistent setting remain until separately removed when idle. Workspace files remain in the host-mounted `/workspace`; files and caches under `/root` do not survive session cleanup.
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
| Blocked with a `tracker-record` reason | See [Tracker sync](#tracker-sync-chat-requests-and-the-work-tracker) below |

## Reading logs

| What | Where |
|---|---|
| Review discovery, publish, maintenance, cleanup | `docker logs <instance>-review-loop-1` |
| Approvals, gate alerts, digest | `docker logs <instance>-notifier-1` |
| Chat sidecar (producer + reply loop) | `docker logs <instance>-chat-sender-1` (before the cutover below: `~/Library/Logs/ralph-chat-reply-<instance>.{log,err.log}`) |
| A card's run | `docker exec -u hermes <instance>-hermes-1 hermes kanban --board <board> show <id> --json` |
| Disk | `colima ssh -- df -h /` and `du -sh compose/workspaces*/sandboxes` |
| Knowledge index | `bootstrap/knowledge-status <instance>` (the `RALPH_INSTANCE`, e.g. `ralph-personal`): collections, counts, models, last refresh (`status.json`) |

## Chat sidecar cutover (owner-only)

Not this card's job -- not any build card's job. None of these is a pull request, and nothing schedules them; do them by hand, in order, once chat PR 8 has merged.

1. **Start the sidecar first (`docker compose --profile chat up -d chat-sender`).** The token copy in step 3 `docker exec`s into this container, so it has to be running. With no token it is idle: it reads nothing, sends nothing and alerts nothing, and says so once in `docker logs <instance>-chat-sender-1`.
2. **Stop the private overlay's `chat_inbox.py` intake cron job.** The sidecar's producer replaces it; two producers would stage the same messages twice. Intake is paused from here until step 3 lands.
3. **Copy today's Chat token into the sidecar's volume (D8).** Not a browser step, not a second OAuth grant: `bootstrap/chat-creds-copy --instance-env .env.<name>` re-encrypts the token you already have (`~/.config/gws-cli/chat_token.json.enc`) for the `chat-sender` container's own pinned identity and writes it into that container's `chat-creds` volume over `docker exec`/`docker cp`. Your host token is untouched; a second copy now exists that can post as you until you revoke the grant in your Google account. Within one interval the sidecar starts.
4. **Check that the sidecar sends, then retire the LaunchAgent.** Send yourself a message in a pilot space, confirm `docker logs <instance>-chat-sender-1` shows the scan picking it up and a reply posted in that thread, and only then unload the host `chat-reply` LaunchAgent. Until then both run; the markers live in the shared `chat-inbox` directory and every part is sent with a fixed id, so the Chat API drops a repeat. **Rollback:** the reverse, in reverse order.
5. **Bring skipped threads back, when you want them (no deadline).** `bootstrap/chat-producer`'s cutover guard prints `guarded <op> <thread>` and stages nothing for a thread Ralph already answered under the overlay's own operation ids; `--allow-unseen-history` lifts it at the cost of at most one duplicate acknowledgement per thread.
6. **Set the send switch.** `CHAT_SEND` (`compose/.env.example`) defaults **on**, so replies keep going out after the cutover. Set it to `0`/`off` as a stop button and recreate the sidecar to apply it (`docker compose --profile chat up -d chat-sender`): the container reads it when it is created, so editing `.env` alone changes nothing in a running one. The host LaunchAgent, by contrast, re-reads its instance file every scan. See `docs/workflows.md`'s Chat loop.
7. **Run one threaded reply in a space of your own before the first real send.** The code asks the Chat API for `REPLY_MESSAGE_OR_FAIL`, not for proof that a thread id captured minutes earlier is still the right one; the token holds no delete scope.
8. **Pick the pilot spaces (`CHAT_ROUTE_SPACES`) knowing Ralph reads every human message in them.** Every one is an operation, an intake card and one model run.

## Knowledge index

| Situation | Action |
|---|---|
| `status.json` says `ok: false` | Read `error`. The previous index still answers. A fetch failure is usually the token or the network; the next hourly refresh retries. Force one: `docker exec -e GH_TOKEN <instance>-knowledge-1 /ralph/bootstrap/refresh-knowledge`. |
| New text is found by keywords but not by meaning | Embedding failed or is pending: `docker exec <instance>-knowledge-1 qmd embed`. |
| Index corrupt or models changed | Rebuild from the checkout: `docker exec <instance>-knowledge-1 sh -c 'rm -f "$INDEX_PATH"'`, then re-run `init`. |
| Container restarting | `docker inspect <instance>-knowledge-1 -f '{{.State.OOMKilled}}'`; a re-ranked query needs more than 2 GB. |
| A memory write is rejected (409) | The docs repo's default branch requires pull requests; memories are written directly by design. Allow the write or turn the rule off. |
| Turn it off | Remove `knowledge` from `COMPOSE_PROFILES`, stop the container, re-run `init`: the MCP block and index step go away. |

## Librarian

| Situation | Action |
|---|---|
| Force a run now | Clear the stamp inside the review-loop container, then poll once: `docker exec <instance>-review-loop-1 rm -f /workspaces/review-work/.librarian-stamp`, then `docker exec <instance>-review-loop-1 /ralph/bootstrap/run-review-loop --once`. `/workspaces/review-work` is the container path; on the host the same folder is under `RALPH_WORKSPACES` (`compose/workspaces/review-work` by default), so removing the file there also works. `--once` alone does nothing while the stamp is under seven days old. Needs `RALPH_DOCS_REPO` set, no open pull request with a `librarian/` head, and no `ralph-librarian` card in any state other than done or archived (that includes ready, running, blocked, triage and review). Each card carries a per-ISO-week idempotency key, so if a librarian card already exists this week, even a finished one, the poll normally returns it and starts nothing. To run again in the same week, archive that card first, but only if it is done. Never archive a ready, running, blocked, triage or review card: the guard stops waiting on an archived card, so a second run could start while the first is still working. Let an active card finish first. Then: `docker exec <instance>-hermes-1 hermes kanban --board queues archive <card-id>`. |
| A librarian pull request or card is open | Blocks the next weekly card. `run-review-loop` skips creation while a `librarian/*` head is open (it reads every open pull request) or a `ralph-librarian` card is neither done nor archived, and the run checks the pull request again for itself. If either check cannot be read, it skips that poll and retries on the next. Each card carries a per-week idempotency key, which stops a repeat poll creating a second card. It is not an atomic lock: Hermes checks the key and then creates the card as two steps, so two loops polling in the same instant can in rare cases both create one. If you see two librarian cards, archive the one that is not running and let the other finish. Every blocker has to clear: merge or close the open librarian pull request, and let any librarian card that is neither done nor archived finish (archive only a done card; archiving an active one lifts the guard and can start a second run). One cleared with the other still open does not let the next card through. |
| After the profile and `install` changes merge | Re-run `init` on every instance once the image is pulled, so `ralph-librarian` exists there. No build pull request or card does this; it is an owner-only step. |

## Tracker sync (chat requests and the work tracker)

`bootstrap/tracker-sync` (called by `chat-reply`, never scheduled on its own) records
a chat request in the work tracker before its acknowledgement goes out, and settles
pending and closed items once per scan, after every reply for that scan is already
sent. With no key file it prints "tracker not configured" and exits 0: the
requester-facing path runs unchanged, and nothing below applies.

- **Key file:** `RALPH_TRACKER_CREDS`, in the `tracker-creds` volume, mode `0600` --
  the base URL, the account's per-user API key, the project identifier, and the
  thread-key custom field id.
- **The `tracker-sync` container:** the `chat-sender` sidecar calls it with
  `docker exec` into `<instance>-tracker-sync-1`, which exists only when
  `COMPOSE_PROFILES` includes `tracker`. Until you start it, a failed `docker exec`
  ("no such container", "is not running") is treated as the tracker not being
  configured: `--record` and `--settle` are skipped and the acknowledgement goes out
  as it does today. `docker logs <instance>-chat-sender-1` says so on each skipped
  `--record`. After you create the key file, start the container
  (`docker compose --profile tracker up -d tracker-sync`) or nothing is recorded.
- **One-time setup, by hand, on the tracker instance (not an API call):** create
  Ralph's own account with access to the project, generate its per-user API key,
  create the thread-key custom field, and create (or map onto stock statuses)
  `Waiting on requester` and `Duplicate`.
- **`.alerted-tracker-record`** (in `<chat-inbox>/<operation>/`): a `--record`
  failure repeated five scans in a row and blocked that operation's intake card.
  The acknowledgement never went out.
- **`.alerted-tracker-settle`** (in `<chat-inbox>/<operation>/`): a `--settle` pass
  failed for that operation. No card is blocked -- by the time `--settle` runs, the
  reply it is settling is already sent -- so this is alert-only, and
  `notifier/poller.py` is the backstop if the alert itself is missed.
- **Clearing a card blocked with a `tracker-record` reason:** fix the tracker, or
  rename the `RALPH_TRACKER_CREDS` key file to make `tracker-sync` inert again;
  delete `.alerted-tracker-record` and `.tracker-record-failures` from
  `<chat-inbox>/<operation>/`; then unblock the intake card. Nothing here is urgent:
  the request is already an orchestrator card, so what is missing is the
  acknowledgement, not the work.

## Glossary

| Term | Meaning |
|---|---|
| **Card** | A Kanban task assigned to one profile, with a completion condition. |
| **Board** | `ralph` (feature work, planned by the orchestrator) or `queues` (machine-created: reviews, maintenance, chat intake). |
| **Gate** | A blocked card waiting on the owner. A doc gate opens on merge or `dd-changes`; an `Approve PRD:` / `Approve design:` / `Approve plan:` card opens only on merge. |
| **Lens** | One review perspective (generalist, reliability, MVP, security); review-synthesis combines them. |
| **Round** | One maintenance pass on a PR's current feedback. |
| **operation_id** | The stable id tying a review, maintenance round or chat request to its cards. |
| **Overlay** | The private checkout holding work-specific skills and jobs. |
| **Sandbox** | The per-profile Docker container every agent command runs in. |
| **Knowledge** | The per-instance QMD index over the docs repo and Ralph's `docs/`, searched through MCP. |
| **Memory** | A note an agent wrote under `memory/` in the docs repo: a decision, root cause, convention, rejected approach or report. |
| **Repo cache** | Full clones under `/workspaces/repo-cache`, refreshed by review discovery and borrowed by checkouts. |
