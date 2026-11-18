import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

test("publishes only a patch independently verified against a registered application check", async (t) => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), "ralph-regression-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const repo = join(root, "repo"), remote = join(root, "repo.git"), bin = join(root, "bin");
  mkdirSync(repo);
  mkdirSync(bin);
  const git = (cwd, ...args) => execFileSync("git", ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", ...args], { cwd, encoding: "utf8" }).trim();
  git(root, "init", "-q", "--bare", remote);
  git(repo, "init", "-q", "-b", "main");
  writeFileSync(join(repo, "format.txt"), "broken\n");
  git(repo, "add", ".");
  git(repo, "commit", "-qm", "fixture");
  git(repo, "remote", "add", "origin", remote);
  const executable = (name, body) => {
    const path = join(bin, name);
    writeFileSync(path, `#!/bin/sh\n${body}\n`, { mode: 0o755 });
    return path;
  };
  const verify = executable("verify-format", 'grep -qx fixed format.txt');
  const passing = executable("verify-passing", "exit 0");
  executable("mewrite", `case "$PWD" in
  */not-fixed) printf '%s\\n' still-broken > format.txt ;;
  *) printf '%s\\n' fixed > format.txt ;;
esac
printf '%s\\n' '{"type":"turn_end","message":{"content":[{"type":"text","text":"ROOT CAUSE: incorrect formatting"}]}}'`);
  executable("gh", `case "$2" in create) echo 'https://github.com/example/project/pull/1';; esac`);
  const client = new Client({ name: "regression-fixture", version: "1" });
  t.after(() => client.close());
  await client.connect(new StdioClientTransport({ command: process.execPath,
    args: [resolve(import.meta.dirname, "../server.js")], env: { ...process.env,
      PATH: `${bin}:${process.env.PATH}`, RALPH_REGRESSION_CHECKS: JSON.stringify({ [repo]: { format: [verify], passing: [passing] } }),
    } }));
  const run = async (ticket_id, alert_payload) => {
    const out = await client.callTool({ name: "reproduce_and_patch", arguments: { ticket_id, repo_path: repo, alert_payload } });
    return JSON.parse(out.content[0].text);
  };
  const rejected = await run("bad", { kind: "application_regression", check: "unknown" });
  assert.equal(rejected.reproduced, false);
  assert.equal(rejected.pr_url, null);
  assert.equal(existsSync(join(root, ".harness-worktrees", "bad")), false);
  const notReproduced = await run("already-passing", { kind: "application_regression", check: "passing" });
  assert.equal(notReproduced.reproduced, false);
  assert.equal(notReproduced.pr_url, null);
  const stillFailing = await run("not-fixed", { kind: "application_regression", check: "format" });
  assert.equal(stillFailing.reproduced, true);
  assert.equal(stillFailing.pr_url, null);
  assert.equal(git(remote, "for-each-ref", "--format=%(refname)"), "");
  const ok = await run("tk", { kind: "application_regression", check: "format" });
  assert.equal(ok.reproduced, true);
  assert.equal(ok.root_cause, "incorrect formatting");
  assert.equal(ok.pr_url, "https://github.com/example/project/pull/1");
  assert.equal(git(remote, "show", "refs/heads/hotfix/tk:format.txt"), "fixed");
});
