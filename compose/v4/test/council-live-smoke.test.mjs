import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const smoke = fileURLToPath(new URL("./council-live-smoke.mjs", import.meta.url));

test("requires explicit live opt-in before starting any judge", (t) => {
  const root = mkdtempSync(join(tmpdir(), "ralph-council-smoke-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const marker = join(root, "started");
  writeFileSync(join(root, "docker"), `#!/bin/sh\ntouch ${JSON.stringify(marker)}\nexit 99\n`, { mode: 0o755 });
  const result = spawnSync(process.execPath, [smoke], {
    env: { ...process.env, PATH: `${root}:${process.env.PATH}`, OPENROUTER_API_KEY: "synthetic-provider" },
    encoding: "utf8",
  });
  assert.equal(result.status, 2);
  assert.match(result.stderr, /--live/);
  assert.equal(existsSync(marker), false);
});

test("runs one panel against the fixed synthetic artifact and reports the decision", (t) => {
  const root = mkdtempSync(join(tmpdir(), "ralph-council-smoke-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const capture = join(root, "calls");
  writeFileSync(join(root, "docker"), `#!/usr/bin/env node
const fs = require("fs");
let input = "";
process.stdin.on("data", (part) => input += part);
process.stdin.on("end", () => {
  const job = JSON.parse(input);
  fs.appendFileSync(${JSON.stringify(capture)}, JSON.stringify(job) + "\\n");
  console.log(JSON.stringify({judge_role: job.role, verdict: "PASS", confidence: 0.9,
    blocking_issues: [], non_blocking_notes: []}));
});
`, { mode: 0o755 });
  const result = spawnSync(process.execPath, [smoke, "--live"], {
    env: { ...process.env, PATH: `${root}:${process.env.PATH}`, OPENROUTER_API_KEY: "synthetic-provider" },
    encoding: "utf8", timeout: 10000,
  });
  assert.equal(result.status, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.rubricVersion, "v1");
  assert.equal(output.decision, "advance");
  assert.equal(output.verdicts.length, 3);
  const calls = readFileSync(capture, "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(calls.length, 3);
  assert.deepEqual(new Set(calls.map((call) => call.role)),
    new Set(["architecture_scale", "security_blast_radius", "scope_pragmatism"]));
  assert.equal(new Set(calls.map((call) => call.artifact)).size, 1);
  assert.match(calls[0].artifact, /synthetic/i);
});

test("rejects a missing provider key before starting any judge", (t) => {
  const root = mkdtempSync(join(tmpdir(), "ralph-council-smoke-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const marker = join(root, "started");
  writeFileSync(join(root, "docker"), `#!/bin/sh\ntouch ${JSON.stringify(marker)}\nexit 99\n`, { mode: 0o755 });
  const env = { ...process.env, PATH: `${root}:${process.env.PATH}` };
  delete env.OPENROUTER_API_KEY;
  const result = spawnSync(process.execPath, [smoke, "--live"], {
    env, encoding: "utf8", timeout: 10000,
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /missing openrouter api key/i);
  assert.equal(existsSync(marker), false);
});

test("reports failure rather than a decision when one judge returns invalid output", (t) => {
  const root = mkdtempSync(join(tmpdir(), "ralph-council-smoke-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const capture = join(root, "calls");
  writeFileSync(join(root, "docker"), `#!/usr/bin/env node
const fs = require("fs");
let input = "";
process.stdin.on("data", (part) => input += part);
process.stdin.on("end", () => {
  const job = JSON.parse(input);
  fs.appendFileSync(${JSON.stringify(capture)}, job.role + "\\n");
  console.log(job.role === "architecture_scale" ? "invalid" : JSON.stringify({
    judge_role: job.role, verdict: "PASS", confidence: 0.9, blocking_issues: [], non_blocking_notes: []}));
});
`, { mode: 0o755 });
  const result = spawnSync(process.execPath, [smoke, "--live"], {
    env: { ...process.env, PATH: `${root}:${process.env.PATH}`, OPENROUTER_API_KEY: "synthetic-provider" },
    encoding: "utf8", timeout: 10000,
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /smoke failed/i);
  assert.equal(result.stdout, "");
  assert.equal(readFileSync(capture, "utf8").trim().split("\n").length, 3);
});
