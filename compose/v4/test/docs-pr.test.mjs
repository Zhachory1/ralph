import assert from "node:assert/strict";
import { execFile, execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";

test("publishes and revises an RFC draft without Council or changes outside its path", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "ralph-docs-pr-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const git = (...args) => execFileSync("git", args, { cwd: dir, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  const artifact = "# Synthetic RFC\n";
  git("init", "--bare", "--initial-branch=main", "remote");
  git("init", "--initial-branch=main", "seed");
  const commit = (message) => git("-C", "seed", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
    "-c", "commit.gpgsign=false", "commit", "-m", message);
  writeFileSync(join(dir, "seed", "seed.md"), artifact);
  git("-C", "seed", "add", "seed.md");
  commit("seed");
  git("-C", "seed", "push", join(dir, "remote"), "main");
  const base = git("-C", "remote", "rev-parse", "main");
  const config = join(dir, "gitconfig");
  git("config", "--file", config, `url.file://${dir}/remote.insteadOf`, "https://github.com/example/org-docs.git");
  const gitBinary = execFileSync("which", ["git"], { encoding: "utf8" }).trim();
  writeFileSync(join(dir, "git"), `#!/usr/bin/env node
const {spawnSync} = require("node:child_process");
const child = spawnSync(${JSON.stringify(gitBinary)}, process.argv.slice(2), {
  env: {...process.env, GIT_CONFIG_GLOBAL: ${JSON.stringify(config)}}, stdio: "inherit",
});
process.exit(child.status ?? 1);
`, { mode: 0o700 });
  const calls = join(dir, "calls");
  const saved = join(dir, "pr");
  const lostResponse = join(dir, "lost-response");
  const readyRace = join(dir, "ready-race");
  writeFileSync(join(dir, "gh"), `#!/usr/bin/env node
const fs = require("node:fs");
const {execFileSync} = require("node:child_process");
const args = process.argv.slice(2);
const head = () => execFileSync(${JSON.stringify(gitBinary)}, ["-C", ${JSON.stringify(join(dir, "remote"))},
  "rev-parse", args[args.indexOf("--head") + 1]], {encoding:"utf8"}).trim();
if (process.env.GH_TOKEN !== "fixture-docs-token" || process.env.GITHUB_TOKEN ||
    process.env.RALPH_COUNCIL_MCP_TOKEN) process.exit(2);
if (args[0] === "auth") process.exit(0);
fs.appendFileSync(${JSON.stringify(calls)}, JSON.stringify(args) + "\\n");
if (args[0] === "api" && args[1] === "user") console.log("fixture-bot");
else if (args[0] === "repo") console.log("main");
else if (args[1] === "list") console.log(fs.existsSync(${JSON.stringify(saved)})
  ? JSON.stringify(JSON.parse(fs.readFileSync(${JSON.stringify(saved)}, "utf8"))
    .map(pr => ({...pr, headRefOid: pr.headRefOid ?? head(),
      isDraft: pr.isDraft && (!fs.existsSync(${JSON.stringify(readyRace)}) ||
        fs.readFileSync(${JSON.stringify(readyRace)}, "utf8") === head())}))) : "[]");
else if (args[1] === "create") {
  if (!args.includes("--draft")) process.exit(3);
  const body = args[args.indexOf("--body") + 1];
  fs.writeFileSync(${JSON.stringify(saved)}, JSON.stringify([{url:"https://github.com/example/org-docs/pull/1",
    isDraft:true, body, state:"OPEN", baseRefName:"main", isCrossRepository:false, author:{login:"fixture-bot"}}]));
  if (fs.existsSync(${JSON.stringify(lostResponse)})) process.exit(1);
  console.log("https://github.com/example/org-docs/pull/1");
} else process.exit(4);
`, { mode: 0o700 });
  const input = join(dir, "job.json");
  const job = { slug: "synthetic", artifact };
  const env = { ...process.env, PATH: `${dir}:${process.env.PATH}`,
    RALPH_DOCS_REPO: "example/org-docs", RALPH_DOCS_GITHUB_TOKEN: "fixture-docs-token",
    RALPH_COUNCIL_MCP_TOKEN: "not-a-valid-token", RALPH_COUNCIL_MCP_PORT: "0",
    GH_TOKEN: "fixture-other-token", GITHUB_TOKEN: "fixture-other-token" };
  const cli = fileURLToPath(new URL("../docs-pr.mjs", import.meta.url));
  const publish = (record) => {
    writeFileSync(input, JSON.stringify(record));
    return promisify(execFile)(process.execPath, [cli, input], { env, timeout: 15_000 });
  };
  for (const invalid of [{ ...job, repo: "elsewhere/docs" }, { ...job, path: "other" },
    { ...job, slug: "../untrusted" }, { ...job, councilJobId: "not-accepted" }]) {
    await assert.rejects(publish(invalid));
  }
  assert.equal(git("-C", "remote", "branch", "--list", "docs/*").trim(), "");
  const first = JSON.parse((await publish(job)).stdout);
  assert.equal(first.pr_url, "https://github.com/example/org-docs/pull/1");
  assert.equal(git("-C", "remote", "show", "docs/rfc-synthetic:docs/1-projects/synthetic/rfc.md"), artifact);
  assert.equal(git("-C", "remote", "diff", "--name-only", "main", "docs/rfc-synthetic").trim(),
    "docs/1-projects/synthetic/rfc.md");
  const body = JSON.parse(readFileSync(saved, "utf8"))[0].body;
  assert.match(body, /does not imply review or approval/);
  assert.deepEqual(JSON.parse((await publish(job)).stdout), first);
  assert.equal(readFileSync(calls, "utf8").split("\n").map((line) => line && JSON.parse(line))
    .filter((args) => args?.[1] === "create").length, 1);
  const head = git("-C", "remote", "rev-parse", "docs/rfc-synthetic");
  const existing = JSON.parse(readFileSync(saved, "utf8"));
  writeFileSync(saved, JSON.stringify([{ ...existing[0], headRefOid: "0".repeat(40) }]));
  await assert.rejects(publish(job), { stderr: /head does not match/ });
  writeFileSync(saved, JSON.stringify([{ ...existing[0], author: { login: "other-user" } }]));
  await assert.rejects(publish(job), { stderr: /refusing to overwrite/ });
  assert.equal(git("-C", "remote", "rev-parse", "docs/rfc-synthetic"), head);
  rmSync(saved);
  writeFileSync(lostResponse, "");
  assert.deepEqual(JSON.parse((await publish(job)).stdout), first);
  assert.equal(git("-C", "remote", "rev-parse", "docs/rfc-synthetic"), head);
  const revised = { ...job, artifact: "# Revised RFC\n" };
  await assert.rejects(publish(revised), { stderr: /requires the current draft head SHA/ });
  const change = { ...revised, expectedHead: first.head_sha };
  const updated = JSON.parse((await publish(change)).stdout);
  assert.equal(updated.pr_url, first.pr_url);
  assert.notEqual(updated.head_sha, first.head_sha);
  assert.equal(git("-C", "remote", "rev-parse", `${updated.head_sha}^`).trim(), first.head_sha);
  assert.equal(git("-C", "remote", "show", `${updated.head_sha}:docs/1-projects/synthetic/rfc.md`), revised.artifact);
  assert.deepEqual(JSON.parse((await publish(change)).stdout), updated);
  await assert.rejects(publish({ ...change, artifact: "# Stale writer\n" }), { stderr: /head SHA/ });
  const ready = JSON.parse(readFileSync(saved, "utf8"))[0];
  writeFileSync(saved, JSON.stringify([{ ...ready, isDraft: false }]));
  await assert.rejects(publish({ ...revised, expectedHead: updated.head_sha, artifact: "# Too late\n" }),
    { stderr: /does not match this draft/ });
  assert.equal(git("-C", "remote", "rev-parse", "docs/rfc-synthetic").trim(), updated.head_sha);
  writeFileSync(saved, JSON.stringify([ready]));
  writeFileSync(readyRace, updated.head_sha);
  const raced = "# Revised while marked ready\n";
  await assert.rejects(publish({ ...revised, expectedHead: updated.head_sha, artifact: raced }),
    { stderr: /was pushed, but draft state could not be confirmed/ });
  assert.equal(git("-C", "remote", "show", "docs/rfc-synthetic:docs/1-projects/synthetic/rfc.md"), raced);
  rmSync(saved);
  mkdirSync(join(dir, "seed", "docs/1-projects/renamed"), { recursive: true });
  git("-C", "seed", "mv", "seed.md", "docs/1-projects/renamed/rfc.md");
  commit("Move a preexisting file instead of adding an RFC");
  git("-C", "seed", "push", join(dir, "remote"), "HEAD:refs/heads/docs/rfc-renamed");
  await assert.rejects(publish({ ...job, slug: "renamed" }), { stderr: /refusing to overwrite/ });
  assert.equal(git("-C", "remote", "rev-parse", "main"), base);
  git("-C", "seed", "push", join(dir, "remote"), "HEAD:main");
  await assert.rejects(publish({ ...job, slug: "renamed" }), { stderr: /already exists on the default branch/ });
});
