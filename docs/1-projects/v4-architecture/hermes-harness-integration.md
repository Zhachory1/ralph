# Hermes calling the coding-harness: what's proven, what's scripted, what's deferred

Fourth build slice on `v4-architecture`, following the `coding-harness` MCP
server (`coding-harness/README.md`). Confirms Hermes itself -- not a manual
MCP client -- can discover and call `implement_spec`, and turns the manual
steps that took to get there into a repeatable script plus one Tier B
profile.

## Proven manually, once

Ran `hermes chat -q "..."` against a real Hermes container (`nousresearch/
hermes-agent:v0.22.2`), authenticated with a Claude Pro/Max OAuth login
(`hermes auth add anthropic --type oauth`, not a Nous subscription -- that
was only what the free-tier fallback message suggested, Anthropic OAuth is a
first-class, separate path). Hermes's own `tool_search` / `tool_describe` /
`tool_call` loop found `implement_spec` on the `coding-harness` MCP server,
called it with the exact Sec 11 argument shape, and the file it asked for
landed on disk with `status: "completed"` and accurate `test_summary` /
`diff_stats`.

The first pass of this proof ran against Hermes's default profile (no `-p`
flag), not `ralph-swe` -- a gap an independent review caught: `bootstrap-
hermes.sh`'s first draft granted the MCP server globally, and `profiles/
swe/config.yaml`'s own `mcp_servers` block was dead text, since Hermes never
merges that block from a profile's own YAML (the same mechanism ralph's
`write-profile-config.py` already follows, #134). Fixed by scoping every
`hermes config set` call to `-p ralph-swe` and re-ran the proof against that
profile specifically: `hermes -p ralph-swe chat -q "..."` found and called
`implement_spec` and the requested file landed correctly. The claim below
now reflects that re-run, not the first one.

## Four environment gotchas found doing this, now scripted

`compose/v4/bootstrap-hermes.sh` automates all four, each with the finding
that produced it:

1. **`HERMES_HOME` is `/opt/data` by default**, not `/home/hermes/.hermes`
   -- that path only exists because ralph's own compose file sets it as an
   env override. Any volume meant to persist Hermes's state across runs has
   to mount at `/opt/data`.
2. **mewrite's native deps need `--allow-scripts`.** A plain
   `npm install -g` blocks `better-sqlite3`/`node-pty`/etc.'s install
   scripts by default; the binary installs but fails at runtime, not
   install time, which makes the actual cause far harder to place. Also
   installed into the Hermes home volume rather than the image's own npm
   prefix (root-owned, EACCES for the container's uid-10000 user), so it
   survives a container restart.
3. **mewrite needs a writable `$HOME`** to create its session/state files;
   the image's own `$HOME` isn't writable by uid 10000.
4. **Git refuses "dubious ownership."** A worktree the harness creates is
   owned by uid 10000; git won't operate on a repository whose on-disk
   owner doesn't match the running user unless the path is marked
   `safe.directory`. A wildcard is the practical choice, since the harness
   only ever touches repos the operator already pointed it at.

Verified the script itself, not just the steps: ran it twice from a clean
Hermes home volume and confirmed no duplicated config or gitconfig entries
(the first draft had a real idempotency bug here -- `git config --global
--add` always appends, so a naive re-run doubled the `safe.directory`
entry; fixed with a check-before-add).

## Adapter authentication

The earlier bootstrap copied a short-lived OAuth snapshot from the host.
It now leaves authentication to the operator and preserves the adapter's
writable home across restarts. For OAuth, sign in once with a dedicated
automation identity from the same mounted home the adapter uses:

```bash
docker run --rm -it -v "$HERMES_HOME_VOLUME:/opt/data" "$HERMES_IMAGE" \
  sh -c 'HOME=/opt/data/mewrite-adapter-home /opt/data/npm-global/bin/mewrite login anthropic'
```

Use the provider configured for your adapter. Mewrite owns token refresh
and writes its refreshed credential back to the same directory. Do not
share that writable directory with an interactive login or mount a host
credential snapshot. On upgrade, rerun bootstrap and sign in again: it
uses a new home and neither reads nor removes the old snapshot. Once the
new login works, the operator can remove the old adapter home. The test
verifies persistence wiring, not a live provider refresh. If refresh fails,
check the provider and reauthenticate from the same dedicated home.

## `swe` profile (new)

`profiles/swe/{SOUL.md,config.yaml}`: the Tier B worker from spec Sec 3 that
owns `implement_spec` calls for one product. Deliberately has no `terminal`
and no `code_execution` -- the one way it changes a repository is the
`coding-harness` MCP tool, enforced in `config.yaml`'s toolset grants and
`disabled_toolsets`, not only described in `SOUL.md` (AGENTS.md's
invariants-in-permissions rule, same pattern as `council`).

The `mcp_servers.coding-harness` grant itself is **not** in `config.yaml`
-- `bootstrap-hermes.sh` applies it with `hermes -p ralph-swe config set`,
including an explicit `tools.include: [implement_spec]` allowlist so a
future Sec 11 tool landing on this same server does not silently become
reachable without a deliberate change here.

A second, independent-review-only finding also fixed this slice: the
first `config.yaml` draft put `skills` in `disabled_toolsets` while also
setting a `create_dir` for Tier 3 skill writing -- the two directly
contradicted each other, since `create_dir` means nothing with the
`skills` toolset disabled. Every other profile with a `create_dir`
(`builder`, `author`, `council`) leaves `skills` enabled; `swe` now
matches.

## Not in this slice

- No n8n or Postgres wiring to `swe` or the harness. The profile still takes
  an RFC path as a direct argument; nothing routes a real ticket to it yet.
- No other Sec 11 tools (`apply_pr_feedback`, `fix_ci_failure`, `audit_pr`,
  `reproduce_and_patch`, `get_worktree_status`).
- No real GitHub PR creation (`pr_url` is still always `null`, per the
  harness's own README).
- No skill-synthesis loop actually exercised -- `SOUL.md` describes the
  judgment call `swe` is meant to make, but nothing has triggered it yet.

## Verification done this slice

```
cd compose/v4
HERMES_IMAGE=nousresearch/hermes-agent:v0.22.2 \
  HERMES_HOME_VOLUME=<fresh dir> \
  ./bootstrap-hermes.sh
# ... run twice, confirmed no duplicated config/gitconfig entries

# ralph-swe needs its own model + auth set once (bootstrap-hermes.sh
# creates the profile and grants the MCP server; it does not set up
# Hermes's own model credential -- that is an operator step, same as the
# default profile's):
docker run --rm -v <hermes_home_volume>:/opt/data nousresearch/hermes-agent:v0.22.2 \
  hermes -p ralph-swe config set model.provider anthropic
# ... plus model.default, platform_toolsets.cli, agent.disabled_toolsets,
# and a copy of a valid (non-expired) Hermes auth.json into the profile

# after adapter login and with a repo_path/rfc_path writable
# by the container's uid (created inside the Colima VM directly, not
# bind-mounted from macOS -- see the virtiofs note below):
docker run --rm -v <hermes_home_volume>:/opt/data \
  -v coding-harness/server.js:/harness/server.js:ro \
  -v coding-harness/adapters.json:/harness/adapters.json:ro \
  -v coding-harness/node_modules:/harness/node_modules:ro -v <test fixtures>:... \
  nousresearch/hermes-agent:v0.22.2 \
  hermes -p ralph-swe chat -q "Use the implement_spec tool ..."
# -> status: "completed", correct diff_stats, file confirmed on disk --
# as ralph-swe specifically, not Hermes's default profile
```

One unrelated environment note, not a harness bug: Colima's virtiofs mount
has real host-to-VM sync lag/visibility gaps for bind mounts -- a `chmod`
or file write from macOS is not reliably visible inside the container, and
vice versa for `ls` from the host. Test fixtures that need to be writable
by the container's uid were created directly inside the Colima VM
(`colima ssh`) rather than bind-mounted from macOS, which is why the
verification commands above reference that pattern.

## Reviewer audit grant

Run `bash /path/to/compose/v4/bootstrap-reviewer-grant.sh` inside the
Hermes container with its intended `HERMES_HOME` mounted. This creates
`ralph-v4-reviewer` without cloning the default profile and grants only
`audit_pr` on the host harness. It doesn't provision a model or GitHub
review credentials. `--no-alias` and `--no-skills` are creation defaults,
not permission controls. Reruns reapply the MCP grant without removing
the operator's existing profile files or changing its other permissions.

On the host, start `coding-harness/server.js --http` with an `audit_pr`-only
entry in `RALPH_HARNESS_HTTP_GRANTS`. Give the matching bearer only to the
reviewer via its profile secret source as `RALPH_REVIEWER_HARNESS_TOKEN`.
The bootstrap writes only a variable reference; it never copies the
bearer. The container reaches the loopback listener through
`host.docker.internal:8765`. That path was verified on Colima; other
runtimes must provide a host-loopback bridge without widening the bind.

Verified on `nousresearch/hermes-agent:v0.22.2`: running bootstrap twice
left only `audit_pr` in this grant and did not change the default profile.
Hermes loaded the reviewer config, substituted the bearer, discovered
`audit_pr`, and received a denial for a direct `implement_spec` call.
A separate cross-origin redirect fixture confirmed that Hermes's strict
header policy does not forward the bearer or configured custom headers.
