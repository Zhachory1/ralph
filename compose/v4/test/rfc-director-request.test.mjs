import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { requestDirectorReview } from "../rfc-director-request.mjs";

function stubDocker(dir) {
  writeFileSync(join(dir, "docker"), `#!/usr/bin/env node
const fs = require("node:fs");
const args = process.argv.slice(2);
if (args[0] === "rm") process.exit(0);
const job = JSON.parse(fs.readFileSync(0, "utf8"));
fs.writeFileSync(args[args.indexOf("--cidfile") + 1], "a".repeat(64));
if (job.role) {
  const revise = job.artifact.includes("(revise)") && job.role === "architecture_scale";
  const issue = revise ? [{severity: "major", location: "Section 1", issue: "Needs scope",
    recommendation: "Narrow scope"}] : [];
  process.stdout.write(JSON.stringify({judge_role: job.role, verdict: revise ? "REVISE" : "PASS",
    confidence: 0.9, blocking_issues: issue, non_blocking_notes: []}));
} else {
  process.stdout.write(job.brief.includes("revise-me") ? "# Fixture RFC (revise)\\n" : "# Fixture RFC\\n");
}
`, { mode: 0o700 });
}

function withFixture(t) {
  const dir = mkdtempSync(join(tmpdir(), "ralph-rfc-director-fixture-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const saved = { ...process.env };
  t.after(() => { process.env = saved; });
  stubDocker(dir);
  Object.assign(process.env, { PATH: `${dir}:${saved.PATH}`, OPENROUTER_API_KEY: "synthetic-provider" });
}

test("drafts, reviews, and requests a native Director review when Council advances", async (t) => {
  withFixture(t);
  const calls = [];
  const board = async (path, method, body) => {
    calls.push({ path, method, body });
    if (path.endsWith("/documents/rfc")) return { id: "doc-1", latestRevisionId: "rev-1", latestRevisionNumber: 1 };
    return { id: "confirm-1" };
  };
  const result = await requestDirectorReview({
    board, directorUserId: "director-1", issueId: "issue-1",
    brief: "Draft a fixture RFC", model: "test/model", domain: "non-coding",
  });
  assert.equal(result.route, "director");
  assert.equal(result.artifact, "# Fixture RFC\n");
  assert.equal(result.verdicts.length, 3);
  assert.ok(result.verdicts.every((verdict) => verdict.verdict === "PASS"));
  assert.equal(result.documentId, "doc-1");
  assert.equal(result.revisionId, "rev-1");
  assert.equal(result.interactionId, "confirm-1");
  assert.deepEqual(calls.map((call) => call.path), ["issues/issue-1/documents/rfc", "issues/issue-1/interactions"]);
  assert.deepEqual(calls[0], { path: "issues/issue-1/documents/rfc", method: "PUT",
    body: { format: "markdown", body: "# Fixture RFC\n" } });
  assert.equal(calls[1].body.kind, "request_confirmation");
  assert.equal(calls[1].body.resolverPolicy, "human_only");
  assert.equal(calls[1].body.addresseeUserId, "director-1");
  assert.equal(calls[1].body.idempotencyKey, "rev-1");
  assert.deepEqual(calls[1].body.payload.target, {
    type: "issue_document", issueId: "issue-1", key: "rfc",
    documentId: "doc-1", revisionId: "rev-1", revisionNumber: 1,
  });
});

test("never calls the board when Council does not advance", async (t) => {
  withFixture(t);
  const board = async () => assert.fail("board should not be called without a Council advance");
  const result = await requestDirectorReview({
    board, directorUserId: "director-1", issueId: "issue-1",
    brief: "Draft a fixture RFC (revise-me)", model: "test/model", domain: "non-coding",
  });
  assert.equal(result.route, "revise");
  assert.equal(result.artifact, "# Fixture RFC (revise)\n");
});

test("rejects invalid input before drafting", async (t) => {
  withFixture(t);
  const board = async () => assert.fail("board should not be called on invalid input");
  const valid = { board, directorUserId: "director-1", issueId: "issue-1",
    brief: "Draft a fixture RFC", model: "test/model", domain: "non-coding" };
  for (const bad of [
    {},
    { ...valid, directorUserId: "" },
    { ...valid, issueId: "" },
    { ...valid, domain: "other" },
    { ...valid, board: "not a function" },
    { ...valid, extra: true },
  ]) {
    await assert.rejects(requestDirectorReview(bad), TypeError);
  }
});
