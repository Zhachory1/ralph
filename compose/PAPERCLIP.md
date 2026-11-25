# Paperclip platform

This is the first v4 infrastructure slice: Paperclip on the host and a
persistent PostgreSQL container. The gateway, QMD, agents, and PR workflows
are not configured here. The approved design is
[docs#37](https://github.com/Zhachory1/docs/pull/37).

## Prerequisites

- Docker Compose, Node `26.13.0`, and a private tailnet.
- Paperclip source at
  [`af7577b`](https://github.com/zhachbot/paperclip/commit/af7577bfdc743777f0409e4c3a8615d0480a7784);
  the OpenCode V2 fix is still an unmerged upstream PR.
- A free local PostgreSQL port. The default is `5432`.

## First setup

Create the isolated instance directory and a random database credential.
This command refuses to overwrite an existing instance:

```sh
python3 - <<'PY'
import os
from pathlib import Path
import secrets

home = Path.home() / ".local" / "share" / "ralph-v4"
home.parent.mkdir(parents=True, exist_ok=True)
home.mkdir(mode=0o700, exist_ok=False)
password = secrets.token_hex(32)
fd = os.open(home / "database.env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as file:
    file.write(f"PAPERCLIP_DB_PASSWORD={password}\n")
    file.write("PAPERCLIP_DB_PORT=5432\n")
    file.write(f"DATABASE_URL=postgres://paperclip:{password}@127.0.0.1:5432/paperclip\n")
    file.write("PAPERCLIP_TELEMETRY_DISABLED=1\n")
    file.write("DO_NOT_TRACK=1\n")
PY
```

If another service uses port `5432`, change both `PAPERCLIP_DB_PORT` and the
port in `DATABASE_URL` in that private file before starting. Keep the password
and its file out of Git. Changing a password in the file does not change the
password in an existing PostgreSQL volume. The file also disables Paperclip
telemetry for this instance.

From the Ralph repository root:

```sh
docker compose --env-file "$HOME/.local/share/ralph-v4/database.env" \
  -f compose/compose.yaml up -d --wait postgres
```

Clone the source into the instance directory, verify its commit, and install
its pinned dependencies:

```sh
git clone --no-tags --depth 1 --branch fix/opencode-v2-compatibility \
  https://github.com/zhachbot/paperclip.git "$HOME/.local/share/ralph-v4/paperclip-src"
git -C "$HOME/.local/share/ralph-v4/paperclip-src" rev-parse HEAD
```

The output must be `af7577bfdc743777f0409e4c3a8615d0480a7784`. Stop if it
differs; do not silently use a different revision.

```sh
git -C "$HOME/.local/share/ralph-v4/paperclip-src" switch --detach \
  af7577bfdc743777f0409e4c3a8615d0480a7784
cd "$HOME/.local/share/ralph-v4/paperclip-src"
npx --yes pnpm@9.15.4 install --frozen-lockfile --prefer-offline
```

Load the private database configuration and start Paperclip in the foreground:

```sh
set -a
. "$HOME/.local/share/ralph-v4/database.env"
set +a
PAPERCLIP_NO_BROWSER=true npx --yes pnpm@9.15.4 paperclipai onboard \
  --yes --bind tailnet --data-dir "$HOME/.local/share/ralph-v4/paperclip" \
  --no-install-service
```

Paperclip uses authenticated private mode. If it cannot detect a Tailscale
address, onboarding warns and the server refuses to start. Connect Tailscale
and make its CLI available on `PATH`. Onboarding may print an access-bearing
invite URL; do not publish it. Do not expose the UI publicly. Finish the normal
browser claim before use. Launch approval is still required before enabling the
live instance.

For subsequent starts, load `database.env` as above and run from the Paperclip
source directory:

```sh
PAPERCLIP_NO_BROWSER=true npx --yes pnpm@9.15.4 paperclipai run \
  --data-dir "$HOME/.local/share/ralph-v4/paperclip"
```

## Check and recover

Check the database with:

```sh
docker compose --env-file "$HOME/.local/share/ralph-v4/database.env" \
  -f compose/compose.yaml ps
```

Use Paperclip's `/api/health` on its configured private address to check the
server. A healthy database or process alone does not prove the gateway or
agents work.

Paperclip also schedules database backups. Its default tiered retention prunes
older managed backups; review the instance's retention settings before relying
on them.

Create a database backup without overwriting an existing file:

```sh
(
  umask 077
  set -C
  docker compose --env-file "$HOME/.local/share/ralph-v4/database.env" \
    -f compose/compose.yaml exec -T postgres pg_dump -U paperclip -d paperclip -Fc \
    > "$HOME/.local/share/ralph-v4/paperclip-$(date +%Y%m%d-%H%M%S).dump"
)
```

Retain the matching `database.env` and Paperclip instance directory, including
configuration, persistent files, and encryption/signing keys. Store backups
and keys securely and separately. For recovery, restore the dump into a new,
empty database before starting Paperclip. Set `BACKUP_FILE` to the chosen dump
path:

```sh
docker compose --env-file "$HOME/.local/share/ralph-v4/database.env" \
  -f compose/compose.yaml exec -T postgres pg_restore --no-owner --no-privileges \
  -U paperclip -d paperclip < "$BACKUP_FILE"
```

Restore the matching instance files and keys as well. Do not restore over an
existing instance or volume. Test recovery before relying on a backup.
