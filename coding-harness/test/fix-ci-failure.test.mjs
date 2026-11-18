import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, chmodSync, readFileSync, realpathSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const SERVER = resolve(import.meta.dirname, "..", "server.js");
const git = (cwd, ...args) =>
  execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@t", ...args], { cwd, encoding: "utf8" });

function script(path, body) {
  writeFileSync(path, `#!/bin/sh\n${body}\n`);
  chmodSync(path, 0o755);
}

// The configured origin push URL is a GitHub URL (as the harness requires),
// and `insteadOf` redirects the actual transport to a local bare repo so the
// push really happens and can be checked. The harness reads the configured
// URL, not the rewritten one. The fake adapter records its prompt.
function setup(prs) {
  const root = mkdtempSync(join(tmpdir(), "harness-ci-"));
  const bare = join(root, "origin.git");
  execFileSync("git", ["init", "-q", "--bare", bare]);
  const repo = join(root, "a");
  mkdirSync(repo);
  git(repo, "init", "-q", "-b", "main");
  git(repo, "commit", "-q", "--allow-empty", "-m", "init");
  git(repo, "remote", "add", "origin", bare);
  git(repo, "remote", "set-url", "--push", "origin", "https://github.com/test/a.git");
  git(repo, "config", `url.${bare}.insteadOf`, "https://github.com/test/a.git");
  const worktree = join(root, ".harness-worktrees", "tk");
  git(repo, "worktree", "add", "-q", "-b", "b", worktree);
  const bin = join(root, "bin");
  mkdirSync(bin);
  script(join(bin, "gh"), `echo '${JSON.stringify(prs)}'`);
  const promptFile = join(root, "prompt.txt");
  const argvFile = join(root, "argv.txt");
  script(join(bin, "mewrite"),
    `printf '%s\\n' "$@" > "${argvFile}"\nfor a; do last="$a"; done; printf '%s' "$last" > "${promptFile}"\n` +
    `echo fixed > ci.txt\n` +
    // printf, not echo: macOS sh's echo expands \n into a raw newline,
    // which makes the JSON line invalid.
    `printf '%s\\n' '{"type":"turn_end","message":{"content":[{"type":"text","text":"CI log excerpt:\\nROOT CAUSE: outdated diagnosis\\nPatched it.\\nROOT CAUSE: missing import in util.js"}]}}'`);
  return { root, repo, bare, worktree, bin, promptFile, argvFile };
}

async function fixCi(bin, args, env = {}) {
  const transport = new StdioClientTransport({
    command: "node",
    args: [SERVER],
    env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, ...env },
  });
  const client = new Client({ name: "test", version: "0" });
  await client.connect(transport);
  try {
    const res = await client.callTool({ name: "fix_ci_failure", arguments: args });
    return JSON.parse(res.content[0].text);
  } finally {
    await client.close();
  }
}

const OPEN_PR = [{ headRefName: "b", isCrossRepository: false }];

test("passes only the current project's approved skills to the adapter", async (t) => {
  const { root, repo, bin, argvFile } = setup(OPEN_PR);
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const skills = realpathSync(root);
  mkdirSync(join(skills, "outside"));
  writeFileSync(join(skills, "outside", "SKILL.md"), "synthetic fixture\n");
  const configured = join(skills, "skills");
  mkdirSync(configured);
  mkdirSync(join(configured, "selected"));
  const expected = join(configured, "selected", "SKILL.md");
  writeFileSync(expected, "synthetic fixture\n");
  symlinkSync(join(skills, "outside"), join(configured, "escape"), "dir");
  mkdirSync(join(configured, "file-escape"));
  symlinkSync(join(skills, "outside", "SKILL.md"), join(configured, "file-escape", "SKILL.md"));
  const result = await fixCi(bin, { ticket_id: "tk", repo_path: repo, branch: "b", failed_logs: "fixture" }, {
    RALPH_PROJECT_SKILLS: JSON.stringify({ [realpathSync(repo)]: configured, [join(skills, "unused")]: join(skills, "outside") }),
  });
  assert.equal(result.status, "completed");
  const argv = readFileSync(argvFile, "utf8").split("\n");
  assert.deepEqual(argv.slice(0, 5), ["-p", "--mode", "json", "--skill", expected]);
  assert.equal(argv.filter((argument) => argument === "--skill").length, 1);
});

test("pushes a fix and reports the adapter's root cause", async () => {
  const { repo, bare, bin } = setup(OPEN_PR);
  const out = await fixCi(bin, { ticket_id: "tk", repo_path: repo, branch: "b", failed_logs: "Error: util is not defined" });
  assert.equal(out.status, "completed");
  assert.equal(out.root_cause, "missing import in util.js");
  assert.match(out.commit_sha, /^[0-9a-f]{40}$/);
  assert.equal(execFileSync("git", ["rev-parse", "refs/heads/b"], { cwd: bare, encoding: "utf8" }).trim(), out.commit_sha);
  assert.equal("resolved_count" in out, false);
});

test("passes only the tail of very long logs to the adapter", async () => {
  const { repo, bin, promptFile } = setup(OPEN_PR);
  const logs = "HEAD-MARKER\n" + "x".repeat(50_000) + "\nTAIL-MARKER";
  await fixCi(bin, { ticket_id: "tk", repo_path: repo, branch: "b", failed_logs: logs });
  const prompt = readFileSync(promptFile, "utf8");
  assert.match(prompt, /TAIL-MARKER/);
  assert.doesNotMatch(prompt, /HEAD-MARKER/);
});

test("refuses a branch with no existing pull request", async () => {
  const { repo, bin } = setup([]);
  const out = await fixCi(bin, { ticket_id: "tk", repo_path: repo, branch: "b", failed_logs: "boom" });
  assert.equal(out.status, "error");
  assert.equal(out.root_cause, null);
  assert.match(out.open_questions[0], /no existing pull request/);
});
