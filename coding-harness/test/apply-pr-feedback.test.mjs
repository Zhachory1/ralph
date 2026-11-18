import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, chmodSync } from "node:fs";
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

// A repo with a GitHub origin URL and a worktree on branch `b` for ticket `tk`,
// plus a bin dir with a fake gh (prints $PRS) and a fake adapter that edits a file.
function setup(prs) {
  const root = mkdtempSync(join(tmpdir(), "harness-test-"));
  const repo = join(root, "a");
  mkdirSync(repo);
  git(repo, "init", "-q", "-b", "main");
  git(repo, "commit", "-q", "--allow-empty", "-m", "init");
  git(repo, "remote", "add", "origin", "https://github.com/test/a.git");
  const worktree = join(root, ".harness-worktrees", "tk");
  git(repo, "worktree", "add", "-q", "-b", "b", worktree);
  const bin = join(root, "bin");
  mkdirSync(bin);
  script(join(bin, "gh"), `echo '${JSON.stringify(prs)}'`);
  script(join(bin, "mewrite"),
    `echo fixed > fb.txt\necho '{"type":"turn_end","message":{"content":[{"type":"text","text":"done"}]}}'`);
  return { root, repo, worktree, bin };
}

async function applyFeedback(bin, args) {
  const transport = new StdioClientTransport({
    command: "node",
    args: [SERVER],
    env: { ...process.env, PATH: `${bin}:${process.env.PATH}` },
  });
  const client = new Client({ name: "test", version: "0" });
  await client.connect(transport);
  try {
    const res = await client.callTool({ name: "apply_pr_feedback", arguments: args });
    return JSON.parse(res.content[0].text);
  } finally {
    await client.close();
  }
}

const OPEN_PR = [{ headRefName: "b", isCrossRepository: false }];

test("rejects a worktree from a different repository", async () => {
  const { root, bin } = setup(OPEN_PR);
  const other = join(root, "other");
  mkdirSync(other);
  git(other, "init", "-q");
  const out = await applyFeedback(bin, { ticket_id: "tk", repo_path: other, branch: "b", review_comments: ["x"] });
  assert.equal(out.status, "error");
  assert.match(out.open_questions[0], /different repository/);
});

test("reclaims a lock left by a dead process", async () => {
  const { root, repo, bin } = setup([]);
  const dead = execFileSync("sh", ["-c", "sh -c 'echo $$' && true"], { encoding: "utf8" }).trim();
  writeFileSync(join(root, ".harness-worktrees", ".tk.lock"), dead);
  const out = await applyFeedback(bin, { ticket_id: "tk", repo_path: repo, branch: "b", review_comments: ["x"] });
  assert.match(out.open_questions[0], /no existing pull request/);
});

test("rejects a branch with no existing pull request", async () => {
  const { repo, bin } = setup([]);
  const out = await applyFeedback(bin, { ticket_id: "tk", repo_path: repo, branch: "b", review_comments: ["x"] });
  assert.equal(out.status, "error");
  assert.match(out.open_questions[0], /no existing pull request/);
});

test("a failed push is an error with no commit_sha, and the local commit is kept", async () => {
  const { repo, worktree, bin } = setup(OPEN_PR);
  script(join(repo, ".git", "hooks", "pre-push"), "exit 1");
  const before = git(worktree, "rev-parse", "HEAD").trim();
  const out = await applyFeedback(bin, { ticket_id: "tk", repo_path: repo, branch: "b", review_comments: ["x"] });
  assert.equal(out.status, "error");
  assert.equal(out.commit_sha, null);
  assert.equal(out.resolved_count, 0);
  assert.match(out.open_questions[0], /could not publish the change/);
  assert.notEqual(git(worktree, "rev-parse", "HEAD").trim(), before);
});
