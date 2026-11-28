# Paperclip platform

This is the v4 infrastructure slice: Paperclip on the host with authenticated
private access and persistent PostgreSQL. The approved design is
[docs#37](https://github.com/Zhachory1/docs/pull/37).

## Verified baseline (2026-11-25)

The selected Paperclip source candidate is the pinned fork revision below, not
a release claim. Fork PRs [1](https://github.com/zhachbot/paperclip/pull/1),
[2](https://github.com/zhachbot/paperclip/pull/2), and
[3](https://github.com/zhachbot/paperclip/pull/3) are merged. This evidence covers
prelaunch validation; Director launch approval remains a separate gate.

One shared gateway exposes QMD and GitHub. OpenCode and Hermes configure that
gateway as their only MCP server through the native `managedMcpOnly: true` opt-in.
Real Paperclip runs of both runtimes completed reads through both gateway
connections with run-attributed audit; the final Hermes run exited 0. This is
not evidence of launch readiness. Hermes usage is unpriced, not zero.

The current native gateway has one shared `require_approval` policy at priority
0 for the exact GitHub connection and `merge_pull_request`, scoped to the three
initial agent actors. It precedes the default remembered-trust priority 40.
Confirmation is human-only after reviewing the arguments. Update this fixed
audience before giving new agents merge access. All three retain 116 GitHub
tools (115 allowed, one Ask first, none off) and five QMD tools, all allowed.
Non-executing policy checks for all three show merge as Ask first and comments,
reviews, and approving reviews as allowed. The human-principal simulation is
unchanged. These are configuration simulations, not a live approval or merge
end-to-end test.
The gate governs MCP only, not `gh`, shell, or direct API access. Upstream
GitHub credential write-permission evidence remains pending; a tool catalog is
not proof of credential permission.

Agents may comment, review, and approve agent-authored work, but merging needs
explicit human instruction; a review approval, green checks, or an agent request
is not that instruction. PRD, design, and launch human approval gates are separate.
The actual review workflow is outside this platform slice.

## Prerequisites

- Docker Compose, Node `26.13.0`, and a private tailnet with MagicDNS and HTTPS
  certificates enabled.
- Paperclip source at
  [`e716c6c`](https://github.com/zhachbot/paperclip/commit/e716c6cea0e3672d02fd97df20804743be4d8308).
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
git -C "$HOME/.local/share/ralph-v4/paperclip-src" switch --detach \
  e716c6cea0e3672d02fd97df20804743be4d8308
git -C "$HOME/.local/share/ralph-v4/paperclip-src" rev-parse HEAD
```

The output must be `e716c6cea0e3672d02fd97df20804743be4d8308`. Stop if it
differs; do not silently use a different revision.

```sh
cd "$HOME/.local/share/ralph-v4/paperclip-src"
npx --yes pnpm@9.15.4 install --frozen-lockfile --prefer-offline
```

Onboarding defaults to Paperclip Runner on supported hosts. Source installs
must build its native daemon too; installing JavaScript dependencies alone
does not provide it. Use the Rust version pinned by `rust-toolchain.toml`.
If Rust is not installed, install it using the
[official installer](https://rust-lang.github.io/rustup/installation/index.html).
For an isolated installation, set these locations before installing Rust:

```sh
export CARGO_HOME="$HOME/.local/share/ralph-v4/toolchains/cargo"
export RUSTUP_HOME="$HOME/.local/share/ralph-v4/toolchains/rustup"
export PATH="$CARGO_HOME/bin:$PATH"
```

From the Paperclip source directory, build and stage the native daemon, then
build the UI:

```sh
npx --yes pnpm@9.15.4 --filter @paperclipai/paperclip-runner build:binary
npx --yes pnpm@9.15.4 --filter @paperclipai/ui build
```

`build:binary` uses the locked Rust dependency graph and stages the signed
daemon on macOS. If provider setup reports `paperclip_runner_binary_missing`,
finish this build before retrying the provider test. This error does not
establish whether a provider key is valid.

Load the private database configuration and bootstrap Paperclip in the foreground:

```sh
set -a
. "$HOME/.local/share/ralph-v4/database.env"
set +a
PAPERCLIP_NO_BROWSER=true npx --yes pnpm@9.15.4 paperclipai onboard \
  --yes --bind tailnet --data-dir "$HOME/.local/share/ralph-v4/paperclip" \
  --no-install-service
```

If Tailscale is unavailable, the server refuses to start. Onboarding may print
an access-bearing invite URL; do not publish it. Stop the foreground server
with Ctrl+C after bootstrap, then configure HTTPS below before opening the UI.

## Private HTTPS

The browser needs HTTPS for `crypto.randomUUID`; opening the UI at a tailnet
HTTP address breaks agent onboarding. Tailscale's transport encryption does not
make an HTTP page a browser secure context.

Check `tailscale serve status` first. Do not overwrite an existing route.
With port 443 free, add the private proxy (not Funnel):

```sh
tailscale serve --bg --https=443 http://127.0.0.1:3100
```

From the Paperclip source directory, use its native configuration writers to
preserve the database, secrets, and storage while changing the backend to
authenticated loopback and the browser URL to HTTPS:

```sh
node --import ./cli/node_modules/tsx/dist/loader.mjs --input-type=module - <<'JS'
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { homedir } from "node:os";
import path from "node:path";
import { readConfig, writeConfig } from "./cli/src/config/store.ts";
import { mergePaperclipEnvEntries } from "./cli/src/config/env.ts";

const status = JSON.parse(execFileSync("tailscale", ["status", "--json"], { encoding: "utf8" }));
const hostname = status.Self?.DNSName?.replace(/\.$/, "");
assert(hostname && status.CertDomains?.includes(hostname), "Enable Tailscale HTTPS first");
const directory = path.join(homedir(), ".local/share/ralph-v4/paperclip/instances/default");
const filename = path.join(directory, "config.json");
const config = readConfig(filename);
assert(config?.server.deploymentMode === "authenticated" && config.server.exposure === "private");
config.server.bind = "loopback";
config.server.host = "127.0.0.1";
delete config.server.customBindHost;
config.server.allowedHostnames = [...new Set([...config.server.allowedHostnames, hostname])];
config.auth.baseUrlMode = "explicit";
config.auth.publicBaseUrl = `https://${hostname}`;
writeConfig(config, filename);
mergePaperclipEnvEntries({ PAPERCLIP_UI_DEV_MIDDLEWARE: "false" }, path.join(directory, ".env"));
JS
```

The built UI avoids a development-only HMR listener. Do not use
`onboard --bind loopback` to make this change: that preset selects
`local_trusted`, whereas this instance must keep authenticated private mode.

For subsequent starts, load `database.env` as above and run from the Paperclip
source directory:

```sh
PAPERCLIP_NO_BROWSER=true npx --yes pnpm@9.15.4 paperclipai run \
  --data-dir "$HOME/.local/share/ralph-v4/paperclip"
```

Open the HTTPS address shown by `tailscale serve status`. Existing users must
sign in again at the new origin; do not create a replacement account or
instance. On a new private install, sign in and select **Claim this instance**.
If onboarding asks for an initial agent, create it; runtime and gateway
configuration follow later. Launch approval remains separate.

## Check and recover

Check the database with:

```sh
docker compose --env-file "$HOME/.local/share/ralph-v4/database.env" \
  -f compose/compose.yaml ps
```

Use Paperclip's `/api/health` on its HTTPS address to check the server. In the
browser, `window.isSecureContext` must be true and `crypto.randomUUID()` must
succeed. A healthy database or process alone does not prove agent creation,
the gateway, or agent runs work.

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
and keys securely and separately. Set `BACKUP_FILE` to the chosen dump path and
inspect the archive without restoring it:

```sh
docker compose --env-file "$HOME/.local/share/ralph-v4/database.env" \
  -f compose/compose.yaml exec -T postgres pg_restore --list < "$BACKUP_FILE"
```

For recovery, obtain approval for a new, empty database and instance target,
separate from all existing data; verify the target before restoring the dump.
Use `pg_restore --exit-on-error --no-owner --no-privileges`, explicitly selecting
that empty database rather than the running database from `database.env`.
Copy the backed-up database environment to a private recovery-only file and
update its `DATABASE_URL` to the verified recovery target. Leave the original
environment untouched. Restore the matching instance files and encryption/signing
keys into the new instance target. Verify the effective database connection
without printing its secret URL, then start the pinned Paperclip source with the
recovery environment and `--data-dir` pointing to that new instance directory.
Never overwrite the live database, instance, or volume. Recovery has not been
drilled; test it before relying on a backup.

## Assigned PR reviews (prelaunch)

The [approved review design](https://github.com/Zhachory1/docs/pull/39), with the
Director's [single-summary amendment](https://github.com/Zhachory1/docs/pull/40), has a local helper
implementation. It has not passed a live native-agent acceptance run.

- `bootstrap/paperclip_client.py`: native run/gateway/document requests.
- `bootstrap/paperclip_review.py`: discovery, native expert/reviewer handoff,
  preflight, result recording and one-write summary publication.
- `compose/pr-review.example.json`: placeholder configuration with a deny-all
  author list and an unrestricted empty repository filter.

Inspect the disabled requests without credentials or API calls:

```sh
python3 bootstrap/paperclip_review.py routine --config compose/pr-review.example.json
```

This prints JSON only, with a paused routine and disabled trigger. Do not apply
it or enable the routine yet. Keep real
identities, routing and native company/project/connection references in private
configuration, not this public example.
Generate requests from the permanent installed checkout: commands embed the
interpreter, helper and private-config paths. Regenerate them if those paths
move; do not apply copied temporary/CI previews.

Only normal Paperclip task runs may use the other actions. They require the
injected `PAPERCLIP_API_URL`, `PAPERCLIP_API_KEY`, company/agent/run/task IDs
and matching native authority. Run them in that task's terminal with the
injected environment, not a code-execution tool that strips credential
variables. Do not substitute board credentials or the operator's `gh` identity.

| Action | Intended caller | Effect |
| --- | --- | --- |
| `discover` | Native routine's coordinator | Filter assigned open/non-draft PRs by author/repository policy; dispatch keyed standalone expert tasks. |
| `delegate` | Expert task | Create an independent reviewer child with a self-contained plan and a native dependency. |
| `preflight` | Reviewer task | Revalidate pinned metadata and policy before reading contents or executing local checks. |
| `record-result --result <json-file>` | Reviewer task | Save its structured result in its own `review-result` document. The reviewer then completes its native task. |
| `publish` | Resumed expert task | Validate completed reviewer provenance and submit one APPROVE/COMMENT summary, or report a non-completion state. |

The result file contains verified `reviewedHead`, `reviewedBase`, `verdict`,
`coverage`, `findings` and `checks`. The reviewer must check the pinned head and
base rather than a moving branch; the helper rejects mismatched declarations.
Findings use `classification` (`blocking` or `non-blocking`), repository-relative
`path`, positive `line`, `evidence` and `correction`. Checks contain `command`
and `outcome`; omitted checks also need a `reason`. These strings are evidence,
not shell commands for the helper to execute.

Checks run through the normal reviewer tools in a separate local checkout with
the OS user's access. Author filtering is a trust decision, not isolation.
Review summaries include separate Blocking findings, Non-blocking suggestions
and Checks sections, plus file/line references and reviewed head/base.

Publication uses native `pull_request_review_write`, `method: create`, with an
explicit APPROVE/COMMENT event, commit SHA and marked summary. It never creates
a pending draft, writes inline threads or submits another pending review.
Any visible pending conflict is left untouched. Existing submitted reviews
by the account/head suppress another write, without turning manual work into
automatic acceptance.

Treat the JSON `status` as authoritative, not a successful process exit.
`submitted` includes a confirmed remote review link; `existing_review` skips
an already reviewed head. `pending` needs a native continuation.
`submission_unknown`, `conflict` and stale submissions need reconciliation or
operator attention, not another POST or a new key. Preserve native send intents
and receipts across restarts; never delete them to clear a blocked operation.

Each coordinator execution scans and hands off; it does not wait for review
delivery or a single key's reconciliation. Report partial failures and keep
operator waits on separate key-specific native tasks, not children/blockers of
the scan task. Otherwise routine coalescing would hold future ticks.

The configured publishing identity and intended-agent path remain unverified.
Qualify complete, unfiltered review history before enabling publication; MCP
lockdown/filtering modes can make bare-array pagination incomplete. The private
`reviewHistoryUnfiltered` flag defaults false and records that qualification,
not proof by itself. No live calls were made to establish compatibility.

No sandbox provider, publisher plugin or spend-limit setup is required. Design
approval does not authorize launch. A passing fixture, tool catalog or local
process does not close #339: first prove one authorized native
routine/expert/reviewer/gateway journey and duplicate-safe readback.

## PR maintenance (prelaunch)

The approved [product brief](https://github.com/Zhachory1/docs/pull/41) and
[design](https://github.com/Zhachory1/docs/pull/42) have a local helper, not a
qualified live workflow. #339 remains open. Setup, native runs, recurrence and
launch are paused pending separate authorization and acceptance, including a
zero-provider process run, actor permissions, managed identity, attachment
round-trip and real GitHub writes.

- `bootstrap/paperclip_maintain.py` implements the workflow;
  `compose/pr-maintenance.example.json` is placeholder-only. Keep real IDs,
  logins and routing in private config.
- Render the proposed routine offline; this makes no API calls:

  ```sh
  python3 bootstrap/paperclip_maintain.py routine --config compose/pr-maintenance.example.json
  ```

  The JSON includes a PATCH for the existing checker identity
  (`adapterType: process`, legacy runner, `runtimeConfig: {}`), a paused
  routine, and a disabled three-minute trigger. The checker must be a
  dedicated process agent; its built-in adapter declares `models: []`, not an
  agent PATCH-body field. Do not apply or enable the rendered requests yet.
- Configure the actual PR author login **and numeric account ID**; eligibility
  is authorship, not `@me`, assignment or review request. Configure the
  publisher separately. The ledger is one stable issue in the same company
  and project, with neither human nor agent assignee.
- Native actions other than `routine` require the owning run's injected
  `PAPERCLIP_API_URL`, `PAPERCLIP_API_KEY` and `PAPERCLIP_RUN_ID`; a missing
  `PAPERCLIP_TASK_ID` is resolved from that run. Git access additionally
  requires `PAPERCLIP_GITHUB_BROKER_URL`,
  `PAPERCLIP_GITHUB_BROKER_TOKEN` and `PAPERCLIP_GITHUB_LAUNCHER_DIR`. No host
  `gh` identity fallback is allowed.

| Action | Purpose |
| --- | --- |
| `poll` | Code-only GitHub intake and bounded advancement; no recurring success reports. |
| `preflight` | Recheck native actor/run/task and return pinned job evidence. |
| `record-result --result <json-file>` | Validate and save the current agent's structured result; preparation/review also uses `--workspace <checkout>`. |
| `prepare-review --workspace <checkout>` | Verify the material bundle; check out its returned candidate commit before running checks and recording review. |
| `push`, `reply --source <version> --body <text>`, `resolve --source <version>` | Perform only checked, intent-recorded effects and read them back. |
| `finish` | Push the admitted fix, reply to verified fixed feedback, resolve verified threads, and settle the run. |

The expert returns `noop` with `handled` feedback versions, or `plan` with
`writer` (`builder` or `sre`), `instructions` and boolean `material`. The writer
may return one `clarify` with a `question`; the expert returns an `answer` with
`instructions`. Preparation returns `prepared` with `commit`, or `existing`
without a new commit, plus `material`, `fixed` feedback versions and `checks`
(`command`/`outcome`). Optional `responses` maps fixed versions to relevant reply
text. Review returns `approve` or `changes` with checks and correction
instructions (at most one correction). Every result has an `outcome` field.
Material changes require independent approval of the exact prepared
commit/tree/base and result revision, from a clean reviewer checkout at that
commit. `block` must name `reason` and the exact human `unblock` action.
Holds use one native `human_only` question, not Chat or routine reports.
An answer closes the old job after all admitted runs and writes settle. A fresh
expert receives the verified human decision as context, not executable
instructions or extra old-job budget. Pending human input or a held ancestor
prevents writes.

Agents diagnose, test, and fetch/merge the actual target base in a separate
checkout; the helper exposes no arbitrary-command runner. No merge, rebase,
force-push or permission bypass is allowed. Repository permissions remain the
real trust boundary; this is not a full-shell sandbox. On errors or unknown
write outcomes, preserve intent and reconcile by readback—never reset a key or
blindly resend. Pausing future intake does not stop already admitted jobs.
