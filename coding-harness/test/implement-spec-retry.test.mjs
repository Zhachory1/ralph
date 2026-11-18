import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

test("retry opens a missing draft for the already-pushed implementation without another commit", async (t) => {
  const root = mkdtempSync(join(tmpdir(), "ralph-implement-retry-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const repo = join(root, "repo"), remote = join(root, "remote.git"), bin = join(root, "bin");
  mkdirSync(repo);
  mkdirSync(bin);
  const git = (cwd, ...args) => execFileSync("git", ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", ...args], { cwd, encoding: "utf8" }).trim();
  git(root, "init", "-q", "--bare", remote);
  git(repo, "init", "-q", "-b", "main");
  git(repo, "commit", "-qm", "fixture", "--allow-empty");
  git(repo, "remote", "add", "origin", remote);
  const rfc = join(root, "accepted.md");
  writeFileSync(rfc, "Write the fixture.");
  writeFileSync(join(bin, "mewrite"), `#!/bin/sh
test -f fixture.txt || echo implemented > fixture.txt
printf '%s\\n' '{"type":"turn_end","message":{"content":[{"type":"text","text":"Verified fixture"}]}}'
`, { mode: 0o755 });
  writeFileSync(join(bin, "gh"), `#!/bin/sh
case "$2" in
  list)
    if [ ! -e "$FIXTURE_GH_MARKER" ]; then touch "$FIXTURE_GH_MARKER"; exit 1; fi
    case " $* " in *" --state all "*) if [ -f "$FIXTURE_CLOSED_HEAD" ]; then cat "$FIXTURE_CLOSED_HEAD"; fi ;; esac ;;
  create) test "$3" = --draft || exit 2; echo 'https://github.com/example/project/pull/7' ;;
esac
`, { mode: 0o755 });

  const client = new Client({ name: "retry-fixture", version: "1" });
  t.after(() => client.close());
  await client.connect(new StdioClientTransport({ command: process.execPath,
    args: [resolve(import.meta.dirname, "../server.js")], env: { ...process.env,
      PATH: `${bin}:${process.env.PATH}`, FIXTURE_GH_MARKER: join(root, "gh-first-call"),
      FIXTURE_CLOSED_HEAD: join(root, "closed-head"),
    } }));
  const args = { ticket_id: "vikunja-4", product: "fixture", repo_path: repo,
    branch: "ralph/vikunja-4", rfc_path: rfc, acceptance_criteria: ["Create the file"] };
  const invoke = async () => {
    const response = await client.callTool({ name: "implement_spec", arguments: args });
    return JSON.parse(response.content[0].text);
  };
  const ref = "refs/heads/ralph/vikunja-4";
  const first = await invoke();
  assert.equal(first.pr_url, null);
  assert.match(first.open_questions[0], /could not open a pull request/);
  const pushed = git(remote, "rev-parse", ref);
  const retry = await invoke();
  assert.equal(retry.pr_url, "https://github.com/example/project/pull/7");
  assert.equal(retry.diff_stats, null);
  assert.equal(git(remote, "rev-parse", ref), pushed);
  writeFileSync(join(root, "closed-head"), `${pushed}\n`);
  const closed = await invoke();
  assert.equal(closed.pr_url, null);
  assert.match(closed.open_questions[0], /closed pull request already recorded this implementation head/);
});
