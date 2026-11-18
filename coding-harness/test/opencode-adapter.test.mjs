import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const SERVER = resolve(import.meta.dirname, "..", "server.js");

const events = {
  success: [
    '{"type":"step_start","part":{"type":"step-start"}}',
    '{"type":"text","part":{"type":"text","text":"Implemented the fixture."}}',
    '{"type":"step_finish","part":{"type":"step-finish","reason":"stop"}}',
  ],
  midTurn: [
    '{"type":"step_start","part":{"type":"step-start"}}',
    '{"type":"step_finish","part":{"type":"step-finish","reason":"tool-calls"}}',
  ],
  error: ['{"type":"error","error":{"type":"provider.auth","message":"fixture rejected"}}'],
};

function fixture(t, scenario, { code = 0, writeFile = true } = {}) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), "ralph-opencode-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const git = (cwd, ...args) => execFileSync("git", ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", ...args], { cwd, encoding: "utf8" }).trim();
  const remote = join(root, "remote.git"), repo = join(root, "repo"), bin = join(root, "bin"), out = join(root, "out");
  mkdirSync(repo); mkdirSync(bin); mkdirSync(out);
  git(root, "init", "-q", "--bare", remote);
  git(repo, "init", "-q", "-b", "main");
  git(repo, "commit", "-qm", "init", "--allow-empty");
  git(repo, "remote", "add", "origin", remote);
  const rfc = join(root, "accepted.md");
  writeFileSync(rfc, "Write the fixture.");
  writeFileSync(join(bin, "opencode"), `#!/bin/sh
printf '%s\\0' "$@" > "${out}/argv"
printf '%s\\n' "$HOME" > "${out}/home"
env | sort > "${out}/env"
cp "$HOME/.config/opencode/opencode.jsonc" "${out}/config.jsonc" 2>/dev/null
${writeFile ? "echo implemented > fixture.txt" : ":"}
cat <<'EVENTS'
${scenario.join("\n")}
EVENTS
exit ${code}
`, { mode: 0o755 });
  writeFileSync(join(bin, "gh"), `#!/bin/sh
case "$2" in create) echo 'https://github.com/example/project/pull/7' ;; esac
`, { mode: 0o755 });
  return { root, repo, remote, bin, out, rfc, git };
}

async function implement(f, env = {}) {
  const client = new Client({ name: "opencode-fixture", version: "1" });
  await client.connect(new StdioClientTransport({ command: process.execPath, args: [SERVER], env: {
    ...process.env, PATH: `${f.bin}:${process.env.PATH}`, RALPH_ADAPTER: "opencode",
    GH_TOKEN: "synthetic-github", VIKUNJA_API_TOKEN: "synthetic-board", OPENROUTER_API_KEY: "synthetic-provider",
    ...env,
  } }));
  try {
    const result = await client.callTool({ name: "implement_spec", arguments: {
      ticket_id: "vikunja-4", product: "fixture", repo_path: f.repo, branch: "ralph/vikunja-4",
      rfc_path: f.rfc, acceptance_criteria: ["Create the file"],
    } });
    return JSON.parse(result.content[0].text);
  } finally {
    await client.close();
  }
}

test("runs OpenCode headless in an isolated home without credentials or global instructions", async (t) => {
  const f = fixture(t, events.success);
  const result = await implement(f);
  assert.equal(result.status, "completed");
  assert.equal(result.pr_url, "https://github.com/example/project/pull/7");
  assert.equal(result.test_summary, "Implemented the fixture.");

  const argv = readFileSync(join(f.out, "argv"), "utf8").split("\0").slice(0, -1);
  assert.deepEqual(argv.slice(0, 6), ["run", "--standalone", "--format", "json", "--auto", "--archived"]);
  assert.equal(argv.length, 7, "the whole prompt is one final argument");
  assert.match(argv[6], /Write the fixture\.[\s\S]*Create the file/);
  assert.ok(!argv.includes("-p") && !argv.includes("--skill"));

  const home = readFileSync(join(f.out, "home"), "utf8").trim();
  assert.notEqual(home, homedir());
  assert.equal(existsSync(home), false, "isolated home must be removed after the run");

  const env = readFileSync(join(f.out, "env"), "utf8");
  assert.match(env, /^OPENROUTER_API_KEY=synthetic-provider$/m);
  assert.doesNotMatch(env, /GH_TOKEN|GITHUB_TOKEN|VIKUNJA|RALPH_/);

  const config = JSON.parse(readFileSync(join(f.out, "config.jsonc"), "utf8"));
  assert.match(config.model, /^openrouter\//);
  assert.equal(config.providers.openrouter.settings.apiKey, "{env:OPENROUTER_API_KEY}");
  const denied = config.policies.filter((rule) => rule.effect === "deny").map((rule) => rule.resource);
  assert.ok(denied.includes("shell:*") && denied.includes("external_directory:*"));
  assert.deepEqual(config.skills, []);
  assert.equal(f.git(f.remote, "log", "-1", "--format=%s", "refs/heads/ralph/vikunja-4"), "[ralph] vikunja-4");
});

test("supplies approved skills through isolated config and never writes them into the worktree", async (t) => {
  const f = fixture(t, events.success);
  const skills = join(f.root, "skills");
  mkdirSync(join(skills, "format-check"), { recursive: true });
  writeFileSync(join(skills, "format-check", "SKILL.md"), "---\nname: format-check\ndescription: Run the fixture check.\n---\n");
  const result = await implement(f, { RALPH_PROJECT_SKILLS: JSON.stringify({ [realpathSync(f.repo)]: skills }) });
  assert.equal(result.status, "completed");
  const config = JSON.parse(readFileSync(join(f.out, "config.jsonc"), "utf8"));
  assert.deepEqual(config.skills, [skills]);
  assert.equal(f.git(f.remote, "ls-tree", "-r", "--name-only", "refs/heads/ralph/vikunja-4"), "fixture.txt");
});

test("does not treat a mid-turn stop or a provider error as completion", async (t) => {
  const mid = fixture(t, events.midTurn);
  assert.equal((await implement(mid)).status, "failed");
  const failed = fixture(t, events.error, { code: 1, writeFile: false });
  const result = await implement(failed);
  assert.equal(result.status, "failed");
  assert.equal(result.pr_url, null);
  assert.match(result.open_questions[0], /adapter exited 1/);
  for (const f of [mid, failed]) {
    const home = readFileSync(join(f.out, "home"), "utf8").trim();
    assert.equal(existsSync(home), false);
  }
});

test("refuses an unknown adapter without running anything", async (t) => {
  const f = fixture(t, events.success);
  const result = await implement(f, { RALPH_ADAPTER: "nonesuch" });
  assert.equal(result.status, "error");
  assert.match(result.open_questions[0], /Unknown adapter/);
  assert.equal(existsSync(join(f.out, "argv")), false);
});

test("an earlier stopped step does not complete a run with an unfinished final step", async (t) => {
  const f = fixture(t, [...events.success, events.midTurn[0]]);
  const result = await implement(f);
  assert.equal(result.status, "failed");
  assert.equal(result.pr_url, null);
});
