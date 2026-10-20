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

## Signing in to a model provider

If your provider uses an API key, put it in `.env` and skip this.

If you reach Claude through a **Claude Pro/Max subscription**, it authorizes through a browser rather than an API key, so there is one interactive step after the first start:

```bash
./bootstrap/login            # defaults to anthropic
./bootstrap/login openai-codex
```

Open the URL it prints, authorize, paste the code back. Hermes stores the access and refresh tokens in Ralph's own volume and refreshes them itself, so this is once per install, not once a day.

Two things worth knowing:

**Ralph does not borrow another tool's credentials.** If you already run Claude Code, its token lives in `~/.claude/.credentials.json` on your host and is refreshed by Claude Code. Ralph deliberately does not read it: mounting another tool's credential file would couple Ralph's uptime to that tool still running, and silently break when it is not.

**A GitHub token can be a model credential.** Hermes treats `GH_TOKEN` as a GitHub Copilot credential, so if you reach Claude through Copilot, `GH_TOKEN` must be a token on an account holding a Copilot licence. That is often a *different* account from the one owning your repositories, which is why `.env` separates `GH_TOKEN` (model access) from `RALPH_GITHUB_TOKEN` (repository work).

Verify either way:

```bash
docker compose exec hermes hermes auth list
```

## Services

| Service | What it does | Lifetime |
| --- | --- | --- |
| `socket-proxy` | Narrows the Docker API the stack may use | Long-running |
| `hermes` | Gateway and Kanban dispatcher | Long-running |
| `init` | Creates the board and profiles from `profiles/`, sets board settings | Runs once, exits |
| `notifier` | Posts gates to chat, turns GitHub decisions into card moves | Long-running loop |
| `hermes-webui` | Chat space for talking with the agents, alongside the dashboard | Long-running |

`init` and `notifier` share one small image. They drive the Hermes CLI through the `hermes` container rather than carrying it: the Hermes image is about 4.5GB, and shipping two copies of it to run a handful of commands doubled the stack's disk for nothing.

**Disk.** The Hermes and sandbox images are large, so give the container runtime room. On Colima, `colima start --disk 100` at least; the default is small enough that a few image builds fill it, and a full disk surfaces as `kanban: could not initialize database: disk I/O error` rather than as anything about disk.

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

## hermes-webui (the chat space)

The dashboard above stays the monitor: board, cards, the "needs attention" banner. `hermes-webui` is
a separate service, alongside it, for actually talking with the agents — a chat application rather
than a terminal in a tab. Neither replaces the other. See the decision record this follows from,
`projects/hermes-webui-evaluation/decisions.md`.

It binds to `127.0.0.1:${RALPH_WEBUI_PORT:-9220}`, a different port from the dashboard so the two run
side by side. **It has no authentication; keep it on localhost, same as the dashboard.** There is no
exception: condition 2 of the decision record this follows from rules out exposure beyond 127.0.0.1
under any circumstances for this deployment.

It shares the `hermes-home` volume with the `hermes` service, so it sees the same profiles, board, and
sessions — this runs alongside the already-running agent rather than installing a second one. Its
`environment:` block clears `HERMES_KANBAN_DB` and `HERMES_KANBAN_BOARD` explicitly: hermes-webui
resolves which board to display from those two variables rather than from `HERMES_HOME`, so leaving
them merely unset is not the same guarantee as setting them, and an inherited pin from an ambient
shell would silently mislabel the live board.

**Pin it in a matched pair with Hermes, and keep it that way.** hermes-webui runs the agent in-process
against its internal module layout rather than through a versioned API, so a hermes-webui pinned to
one Hermes Agent version is not known-good against another. There is no published compatibility
matrix between the two projects; `HERMES_WEBUI_VERSION` in `.env.example` is pinned by release-date
proximity to `HERMES_VERSION`, not by a verified pairing. Every time `HERMES_VERSION` moves, re-qualify
and bump `HERMES_WEBUI_VERSION` with it — this is a standing obligation, not a one-time check.

**Tool calls from its chat run in the hermes-webui container, not the agent's sandboxed one.**
Read that plainly: the sandbox model this stack otherwise relies on — agent shell commands running in
a per-profile sibling container, isolated from the host, the Docker socket, the board, and other
profiles' credentials (see "Sandboxing" below) — does not cover tools invoked from hermes-webui's
chat. A shell command started there runs directly in the `hermes-webui` container, which holds the
`hermes-home` volume. This is a known, accepted cost of adopting hermes-webui, not something this
deployment fixes.

## Upgrading

Bump `HERMES_VERSION` or `RALPH_VERSION` in `.env`, then:

```bash
docker compose up -d --build
```

`init` re-runs on every start and is idempotent: it updates profile definitions from the repo and leaves your board, cards, and history alone. Your own edits to a profile's `config.yaml` survive, since only the `model:` block is Ralph's.

**Bumping `HERMES_VERSION` also means re-qualifying and bumping `HERMES_WEBUI_VERSION`** — see
"hermes-webui" above. And bumping the agent image alone is not enough to update what hermes-webui
sees: the `hermes-agent-src` volume is populated once, from the agent image's own `/opt/hermes`, the
first time the `hermes` service starts, and a later `docker pull` or `docker compose up` reuses it
verbatim rather than refreshing it. To actually pick up a newer agent's source:

```bash
docker compose down
docker volume rm ralph_hermes-agent-src
docker compose up -d
```

## Uninstall

```bash
docker compose down          # stop, keep all state
docker compose down -v       # stop and delete the board, profiles, and history
```

`down -v` is not recoverable. Nothing in it is backed up anywhere.

## Sandboxing

Agent shell commands run in a per-profile sandbox container, not in the hermes container. The builder runs dependency installs, test suites, and code it just wrote, from repositories whose READMEs and Makefiles can carry prompt injection, so none of that may sit next to the credentials.

The sandbox image carries `git`, `gh` and Chromium on top of the stock one. Chromium is 587MB and about 16 seconds of build, in its own layer, and costs nothing at rest: a build that never captures never launches it. It is there because a repository whose rules require screenshots cannot be satisfied by an agent that has no browser, and a pull request that fails its own repository's rules is not finished. It is also the widest thing in the image, which is why the sandbox has no host access and no credential beyond the repository token.

A sandbox gets a read-write `/workspace` and scratch home, plus read-only skills and caches. Verified it cannot reach the host filesystem, the Docker socket, the Kanban database, any credential store, or another profile's state. Every mount is under `profiles/<profile>/`, so two profiles running code never share one.

The `socket-proxy` service narrows the Docker API the hermes container itself may use. It refuses the volume, network, and daemon-reconfiguration endpoints, though it cannot stop that container bind-mounting the host on a container it creates: a bind mount is a field on container create, which has to be allowed for sandboxes to exist. That is defence in depth around the orchestrator rather than the sandbox boundary, and it is accepted for now.

See [`docs/spikes/sandbox-isolation.md`](../docs/spikes/sandbox-isolation.md); `evals/test_sandbox_isolation.py` probes it from inside a live sandbox.

## Scope checking agent pull requests

A builder has the whole repository and will honestly notice things worth fixing nearby. Across a feature's pull requests those additions compound into a diff nobody agreed to, and the review that was meant to be the safety net becomes too large to be one.

So every agent pull request is checked against the files its card planned:

```bash
bootstrap/scope-check --base origin/main --head HEAD --card <card-id>
```

Copy [`.github/workflows/scope-check.yml`](../.github/workflows/scope-check.yml) into any repository a builder can push to. It runs only on pull requests labelled `agent-authored`, reads the card id from the `Kanban-Card:` commit trailer, and fails on files outside the plan, dependency or build files the design did not call for, and diffs over 400 lines.

**The plan comes from the card, not the pull request.** A plan the builder writes alongside its own diff describes what it did rather than constraining it. The orchestrator puts a `<planned_files>` block in the build card's brief before the work starts.

To accept an out-of-scope diff, label the pull request `scope-approved`. That is a decision with your name on it rather than a silent default, and the builder is told not to apply it to itself.

## Stopping it

Two levels, and the first is usually the right one.

**Pause** halts new work and leaves in-flight work alone:

```bash
docker compose exec hermes hermes pause --reason "looks wrong"
docker compose exec hermes hermes resume
```

**Stop** is for when an agent is actively doing damage. It pauses, stops the containers, helps you revoke credentials, and drafts open agent pull requests so nothing you no longer trust gets reviewed or merged:

```bash
./bootstrap/stop --drill    # rehearse; changes nothing
./bootstrap/stop            # asks before each destructive step
./bootstrap/stop --yes      # no prompts, for a real incident
```

Restarting after a Stop needs fresh credentials. That is deliberate: a system stopped because it misbehaved should not restart by accident.

**Drill it.** `--drill` prints every command it would run and changes nothing, so there is no excuse for the first real use being the first use. Run it monthly at first.

One caveat worth knowing. `hermes pause` stops the gateway's dispatcher, verified, but a **manual** `hermes kanban dispatch` ignores the sentinel and spawns anyway. Pause protects you from the system; it does not protect you from yourself. Stopping the container is the backstop, which is why Stop does that too.

## Tests

```bash
docker compose exec hermes python3 /ralph/evals/test_orchestration.py
```

Asserts the board behaves as designed, against the real dispatcher, without spawning a worker or calling a model.

```bash
python3 evals/test_killswitch.py     # from the repo root
```

Checks the kill-switch drill is safe to run and rehearses every destructive step. See [`evals/README.md`](../evals/README.md).

## Troubleshooting

**`hermes` restarts in a loop.** It is probably missing `command: ["gateway", "run"]`. The image defaults to interactive chat, which exits immediately with no terminal attached.

**Permission denied on `/home/hermes/.hermes`.** A named volume inherits root ownership unless the mount point exists in the image with the right owner. `Dockerfile.init` creates it as UID 10000; rebuild if you have changed the base image.

**Notifier says `GCHAT_WEBHOOK_URL unset`.** Expected without a webhook configured. It skips alerts and still processes GitHub decisions.

**A card sits in `ready` and never runs.** Check `docker compose logs hermes` for `held back: active_pr=1`. A card whose recent comments contain a pull-request URL is guarded against respawn, so a crashed worker cannot open a second PR. The guard lifts on a handoff event, which the approval watcher records; if you moved the card by hand, reassign it elsewhere and back.
