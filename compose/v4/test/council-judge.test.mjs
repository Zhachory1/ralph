import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { invokeCouncilJudge } from "../council-judge.mjs";

const models = {
  architecture_scale: "anthropic/claude-sonnet-5.5",
  security_blast_radius: "google/gemini-3.8-flash",
  scope_pragmatism: "openai/gpt-6.2-sol",
};

test("uses a separate restricted container and only the provider credential for each role", async (t) => {
  const root = mkdtempSync(join(tmpdir(), "judge-fixture-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const before = { ...process.env };
  t.after(() => { process.env = before; });
  const capture = join(root, "capture");
  writeFileSync(join(root, "docker"), `#!/usr/bin/env node
const fs = require('fs');
if (process.argv[2] === 'rm') {
  fs.appendFileSync(${JSON.stringify(capture)}, JSON.stringify({ cleanup: process.argv.slice(2) }) + '\\n');
  process.exit(0);
}
let input = '';
process.stdin.on('data', (part) => input += part);
process.stdin.on('end', () => {
  const args = process.argv.slice(2);
  fs.writeFileSync(args[args.indexOf('--cidfile') + 1], 'a'.repeat(64));
  fs.appendFileSync(${JSON.stringify(capture)}, JSON.stringify({ args: process.argv.slice(2), env: process.env, input: JSON.parse(input) }) + '\\n');
  if (JSON.parse(input).artifact === 'timeout') process.exit(124);
  if (JSON.parse(input).artifact === 'invalid') { console.log('not JSON'); return; }
  if (JSON.parse(input).artifact === 'wrong') { console.log(JSON.stringify({judge_role: 'wrong'})); return; }
  if (JSON.parse(input).artifact === 'malformed') { console.log(JSON.stringify({judge_role: JSON.parse(input).role})); return; }
  if (JSON.parse(input).artifact === 'oversized') { console.log('x'.repeat(1100000)); return; }
  console.log(JSON.stringify({ judge_role: JSON.parse(input).role, verdict: 'PASS', confidence: 0.9, blocking_issues: [], non_blocking_notes: [] }));
});
`, { mode: 0o755 });
  process.env.PATH = `${root}:${before.PATH}`;
  process.env.OPENROUTER_API_KEY = "synthetic-provider";
  process.env.GH_TOKEN = "synthetic-github";
  process.env.VIKUNJA_API_TOKEN = "synthetic-board";
  for (const [role, model] of Object.entries(models)) {
    const result = await invokeCouncilJudge({ judge_role: role, artifact: "fixture artifact", rubric: "fixture rubric" });
    assert.equal(result.judge_role, role);
    const call = JSON.parse(readFileSync(capture, "utf8").trim().split("\n").at(-1));
    assert.equal(call.env.OPENROUTER_API_KEY, "synthetic-provider");
    assert.equal(call.env.GH_TOKEN, undefined);
    assert.equal(call.env.VIKUNJA_API_TOKEN, undefined);
    assert.equal(call.input.role, role);
    assert.equal(call.input.model, model);
    assert.equal(call.input.artifact, "fixture artifact");
    assert.equal(call.input.rubric, "fixture rubric");
    assert.ok(call.args.includes("--read-only") && call.args.includes("--cap-drop"));
    assert.ok(call.args.includes("--rm") && call.args.includes("--init") && call.args.includes("--user"));
    assert.ok(!call.args.includes("--volume") && !call.args.includes("-v"));
    assert.ok(!call.args.join(" ").includes("fixture artifact"));
  }
  await assert.rejects(invokeCouncilJudge({ judge_role: "architecture_scale", artifact: "timeout", rubric: "r" }));
  const last = JSON.parse(readFileSync(capture, "utf8").trim().split("\n").at(-1));
  assert.deepEqual(last.cleanup, ["rm", "-f", "a".repeat(64)]);
  await assert.rejects(invokeCouncilJudge({ judge_role: "architecture_scale", artifact: "invalid", rubric: "r" }), /JSON/);
  await assert.rejects(invokeCouncilJudge({ judge_role: "architecture_scale", artifact: "wrong", rubric: "r" }), /role/);
  await assert.rejects(invokeCouncilJudge({ judge_role: "architecture_scale", artifact: "malformed", rubric: "r" }), /verdict/i);
  await assert.rejects(invokeCouncilJudge({ judge_role: "architecture_scale", artifact: "oversized", rubric: "r" }));
  const oversizedCleanup = JSON.parse(readFileSync(capture, "utf8").trim().split("\n").at(-1));
  assert.deepEqual(oversizedCleanup.cleanup, ["rm", "-f", "a".repeat(64)]);
});

test("rejects unknown roles or missing provider key without starting a container", async () => {
  await assert.rejects(invokeCouncilJudge({ judge_role: "unknown", artifact: "a", rubric: "r" }), /role/i);
  const before = process.env.OPENROUTER_API_KEY;
  delete process.env.OPENROUTER_API_KEY;
  try {
    await assert.rejects(invokeCouncilJudge({ judge_role: "architecture_scale", artifact: "a", rubric: "r" }), /key/i);
  } finally {
    if (before !== undefined) process.env.OPENROUTER_API_KEY = before;
  }
});
