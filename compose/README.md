# Running Ralph

Ralph runs as its own Hermes in its own container. If you already run Hermes on this machine, installing Ralph does not touch it: separate home volume, separate board database, separate profiles, separate port.

## Install

```bash
cd compose
cp .env.example .env     # fill in HERMES_VERSION and your credentials
docker compose up -d
```

That is the whole install. `init` creates the board and the profiles, then exits; `hermes` runs the gateway and dispatcher; `notifier` polls for gates waiting on you.

Check it came up:

```bash
docker compose ps
docker compose exec hermes hermes kanban --board ralph list
```

## Services

| Service | What it does | Lifetime |
| --- | --- | --- |
| `init` | Creates the board and profiles from `profiles/`, sets board settings | Runs once, exits |
| `hermes` | Gateway and Kanban dispatcher | Long-running |
| `notifier` | Posts gates to chat, turns GitHub decisions into card moves | Long-running loop |

## Configuration

Everything is in `.env`; see [`.env.example`](.env.example). Two notes:

**`HERMES_VERSION` has no default.** Compose refuses to start without it, by design — `latest` turns an upgrade into a surprise. Pin a version and change it deliberately.

**Secrets stay out of the repo.** `.env` is gitignored. The chat webhook in particular is a credential: anyone holding it can post into your space. Point `RALPH_ENV_FILE` at a file outside the repo if you prefer, for example one generated from your OS keychain at startup.

## It does not disturb an existing Hermes

Ralph's state lives in a named Docker volume (`ralph_hermes-home`), never a bind mount of `~/.hermes`. The container cannot see your host install, and the host install cannot see Ralph's.

Verify for yourself:

```bash
docker compose exec hermes hermes kanban boards list   # only default and ralph
hermes kanban boards list                              # your own boards, unchanged
```

The dashboard binds to `127.0.0.1:9219` rather than Hermes' usual port, so it will not collide with a dashboard you already run. It has no authentication; keep it on localhost.

## Upgrading

Bump `HERMES_VERSION` or `RALPH_VERSION` in `.env`, then:

```bash
docker compose up -d --build
```

`init` re-runs on every start and is idempotent: it updates profile definitions from the repo and leaves your board, cards, and history alone. Your own edits to a profile's `config.yaml` survive, since only the `model:` block is Ralph's.

## Uninstall

```bash
docker compose down          # stop, keep all state
docker compose down -v       # stop and delete the board, profiles, and history
```

`down -v` is not recoverable. Nothing in it is backed up anywhere.

## Troubleshooting

**`hermes` restarts in a loop.** It is probably missing `command: ["gateway", "run"]`. The image defaults to interactive chat, which exits immediately with no terminal attached.

**Permission denied on `/home/hermes/.hermes`.** A named volume inherits root ownership unless the mount point exists in the image with the right owner. `Dockerfile.init` creates it as UID 10000; rebuild if you have changed the base image.

**Notifier says `GCHAT_WEBHOOK_URL unset`.** Expected without a webhook configured. It skips alerts and still processes GitHub decisions.
