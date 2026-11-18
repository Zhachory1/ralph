import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { draftRfc } from "../author.mjs";

test("drafts and revises RFC text in a restricted container without publishing credentials", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "ralph-author-fixture-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const saved = { ...process.env };
  t.after(() => { process.env = saved; });
  const log = join(dir, "calls");
  writeFileSync(join(dir, "docker"), `#!/usr/bin/env node
const fs = require("node:fs");
const args = process.argv.slice(2);
if (args[0] === "rm") {
  fs.appendFileSync(${JSON.stringify(log)}, JSON.stringify({cleanup: args}) + "\\n");
  process.exit(0);
}
const job = JSON.parse(fs.readFileSync(0, "utf8"));
fs.writeFileSync(args[args.indexOf("--cidfile") + 1], "a".repeat(64));
fs.appendFileSync(${JSON.stringify(log)}, JSON.stringify({job, args, env: process.env}) + "\\n");
if (job.brief === "too much output") {
  process.on("SIGTERM", () => {});
  process.stdout.write("é".repeat(9000));
  setInterval(() => {}, 1000);
}
else if (job.brief === "bad encoding") process.stdout.write(Buffer.from([0xff]));
else if (job.brief !== "empty output") console.log(job.feedback ? "# Revised RFC" : "# RFC");
`, { mode: 0o700 });
  Object.assign(process.env, { PATH: `${dir}:${saved.PATH}`, OPENROUTER_API_KEY: "synthetic-provider",
    GH_TOKEN: "synthetic-github", GITHUB_TOKEN: "synthetic-github", RALPH_DOCS_GITHUB_TOKEN: "synthetic-docs",
    VIKUNJA_API_TOKEN: "synthetic-board", RALPH_COUNCIL_MCP_TOKEN: "synthetic-council",
    RALPH_COUNCIL_JOB_TOKEN: "synthetic-job" });
  const job = { brief: "Draft an RFC about fixtures", model: "test/model" };
  for (const invalid of [{ ...job, brief: "é".repeat(8193) }, { ...job, model: "--other" },
    { ...job, currentDraft: "# Existing" }, { ...job, feedback: "Revise it" },
    { ...job, repo: "elsewhere/docs" }]) {
    await assert.rejects(draftRfc(invalid), TypeError);
  }
  assert.equal(await draftRfc(job), "# RFC\n");
  const revision = { ...job, currentDraft: "# RFC\n", feedback: "Add a migration note" };
  assert.equal(await draftRfc(revision), "# Revised RFC\n");
  const [, call] = readFileSync(log, "utf8").trim().split("\n").map(JSON.parse);
  assert.deepEqual(call.job, revision);
  assert.equal(call.env.OPENROUTER_API_KEY, "synthetic-provider");
  for (const key of ["GH_TOKEN", "GITHUB_TOKEN", "RALPH_DOCS_GITHUB_TOKEN", "VIKUNJA_API_TOKEN",
    "RALPH_COUNCIL_MCP_TOKEN", "RALPH_COUNCIL_JOB_TOKEN"]) assert.equal(call.env[key], undefined);
  assert.ok(call.args.includes("--read-only") && call.args.includes("--cap-drop") && call.args.includes("--user"));
  assert.ok(!call.args.some((arg) => ["--volume", "-v", "--mount", "--env-file"].includes(arg)));
  assert.ok(call.args.includes("/opt/data:ro,nosuid,size=1m") && call.args.includes("--log-driver=none"));
  assert.ok(!call.args.includes(revision.brief) && !call.args.includes(revision.feedback));
  await assert.rejects(draftRfc({ ...job, brief: "empty output" }), /empty RFC/);
  await assert.rejects(draftRfc({ ...job, brief: "bad encoding" }), /UTF-8/i);
  await assert.rejects(draftRfc({ ...job, brief: "too much output" }), /output limit/);
  const cleanup = JSON.parse(readFileSync(log, "utf8").trim().split("\n").at(-1));
  assert.deepEqual(cleanup.cleanup, ["rm", "-f", "a".repeat(64)]);
});
