import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { runV1Council } from "../council-v1.mjs";

test("uses the checked-in v1 rubric for all three role-bound judges", async (t) => {
  const root = mkdtempSync(join(tmpdir(), "ralph-v1-panel-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const original = { ...process.env };
  t.after(() => { process.env = original; });
  const capture = join(root, "calls");
  writeFileSync(join(root, "docker"), `#!/usr/bin/env node
const fs = require("fs");
let input = "";
process.stdin.on("data", (part) => input += part);
process.stdin.on("end", () => {
  const job = JSON.parse(input);
  fs.appendFileSync(${JSON.stringify(capture)}, JSON.stringify(job) + "\\n");
  const reply = () => console.log(JSON.stringify({
    judge_role: job.artifact === "mismatched" && job.role === "architecture_scale" ? "wrong" : job.role,
    verdict: "PASS", confidence: 0.9, blocking_issues: [], non_blocking_notes: []}));
  if (job.artifact === "mismatched" && job.role === "architecture_scale") setTimeout(reply, 250);
  else reply();
});
`, { mode: 0o755 });
  process.env.PATH = `${root}:${original.PATH}`;
  process.env.OPENROUTER_API_KEY = "synthetic-provider";
  const result = await runV1Council({ artifact: "fixture artifact", rubric: "attacker rubric", revisions: 0 });
  const calls = readFileSync(capture, "utf8").trim().split("\n").map(JSON.parse);
  const rubric = readFileSync(new URL("../../../profiles/council/rubric-v1.md", import.meta.url), "utf8");
  assert.equal(result.rubricVersion, "v1");
  assert.equal(result.decision, "advance");
  assert.equal(result.verdicts.length, 3);
  assert.deepEqual(new Set(calls.map((call) => call.role)),
    new Set(["architecture_scale", "security_blast_radius", "scope_pragmatism"]));
  for (const call of calls) {
    assert.equal(call.artifact, "fixture artifact");
    assert.equal(call.rubric, rubric);
  }
  await assert.rejects(runV1Council({ artifact: "mismatched", revisions: 0 }), /role/i);
});

test("rejects invalid input without starting judges", async () => {
  await assert.rejects(runV1Council({ artifact: "", revisions: 0 }), /artifact/i);
  await assert.rejects(runV1Council({ artifact: "fixture", revisions: 3 }), /revisions/i);
});
