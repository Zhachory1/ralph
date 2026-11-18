import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { auditPr } from "../audit-pr.js";

test("MCP exposes audit_pr and rejects unconfigured repositories", async (t) => {
  const client = new Client({ name: "audit-fixture", version: "1" });
  t.after(() => client.close());
  await client.connect(new StdioClientTransport({
    command: process.execPath,
    args: [fileURLToPath(new URL("../server.js", import.meta.url))],
    env: { RALPH_AUDIT_REPOS: "{}" },
  }));
  const { tools } = await client.listTools();
  assert.ok(tools.some(({ name }) => name === "audit_pr"));
  await assert.rejects(client.callTool({ name: "audit_pr", arguments: {
    repo_path: process.cwd(), pr_number: 1, head_sha: "0".repeat(40),
  } }), /requires an operator-configured image/);
});

test("pins the PR head and runs checks without host mounts or networking", async (t) => {
  const cwd = realpathSync(mkdtempSync(join(tmpdir(), "ralph-audit-test-")));
  t.after(() => rmSync(cwd, { recursive: true, force: true }));
  const git = (...args) => execFileSync("git", args, { cwd, encoding: "utf8" }).trim();
  git("init", "-q");
  git("config", "user.name", "Audit Fixture");
  git("config", "user.email", "fixture@example.invalid");
  git("commit", "-qm", "base", "--allow-empty");
  const base = git("rev-parse", "HEAD");
  writeFileSync(join(cwd, "fixture.txt"), "synthetic\n");
  git("add", "fixture.txt");
  git("commit", "-qm", "PR head");
  const head = git("rev-parse", "HEAD");
  const merge = git("commit-tree", "HEAD^{tree}", "-p", base, "-p", head, "-m", "synthetic merge");
  git("update-ref", "refs/pull/1/head", head);
  git("update-ref", "refs/pull/1/merge", merge);
  git("remote", "add", "origin", cwd);
  const previous = process.env.RALPH_AUDIT_REPOS;
  t.after(() => {
    if (previous === undefined) delete process.env.RALPH_AUDIT_REPOS;
    else process.env.RALPH_AUDIT_REPOS = previous;
  });
  const config = {
    image: "missing-fixture@sha256:" + "0".repeat(64),
    test: 'test "$(id -u)" = 65534 && test "$(ls /sys/class/net)" = lo && test ! -e .git && test ! -e .env && grep -qx synthetic fixture.txt',
    lint: "test -f fixture.txt",
  };
  const configure = () => { process.env.RALPH_AUDIT_REPOS = JSON.stringify({ [cwd]: config }); };
  configure();
  const input = { repo_path: cwd, pr_number: 1, head_sha: head };
  await assert.rejects(auditPr({ ...input, head_sha: base }), /head SHA mismatch/);
  const image = process.env.RALPH_AUDIT_TEST_IMAGE;
  await t.test("real Docker isolation and missing-image denial", { skip: !image && "set RALPH_AUDIT_TEST_IMAGE" }, async () => {
    await assert.rejects(auditPr(input), /Audit sandbox failed/);
    config.image = image;
    configure();
    writeFileSync(join(cwd, ".env"), "SYNTHETIC_FIXTURE=1\n");
    assert.deepEqual(await auditPr(input), {
      tests_passed: true, test_summary: "", lint_issues: [], changed_files: ["fixture.txt"],
    });
  });
});
