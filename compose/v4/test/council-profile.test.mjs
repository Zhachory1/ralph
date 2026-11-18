import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { once } from "node:events";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { runCouncilProfile } from "../council-profile.mjs";

test("runs Council without host mounts or repository and board credentials", async (t) => {
  const root = mkdtempSync(join(tmpdir(), "ralph-council-profile-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const original = { ...process.env };
  t.after(() => { process.env = original; });
  const capture = join(root, "calls");
  const registrations = [];
  const profileStops = [];
  const id = "b".repeat(64);
  const jobToken = "c".repeat(64);
  const server = createServer(async (req, res) => {
    assert.equal(req.headers.authorization, `Bearer ${"a".repeat(64)}`);
    if (req.method === "POST" && req.url === "/jobs") {
      let body = "";
      for await (const chunk of req) body += chunk;
      registrations.push(JSON.parse(body));
      res.writeHead(201, { "Content-Type": "application/json" }).end(JSON.stringify({ id, token: jobToken }));
    } else if (req.method === "POST" && req.url === `/jobs/${id}/profile-stopped`) {
      profileStops.push(id);
      res.writeHead(204).end();
    } else if (req.method === "GET" && req.url === `/jobs/${id}`) {
      res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify(
        registrations.at(-1).artifact === "fail" ? { status: "queued" } :
          { status: "completed", result: { decision: "advance", verdicts: ["validated fixture"] } },
      ));
    } else res.writeHead(404).end();
  }).listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => server.close());
  const port = server.address().port;
  writeFileSync(join(root, "docker"), `#!/usr/bin/env node
const fs = require("fs");
if (process.argv[2] === "rm") {
  fs.appendFileSync(${JSON.stringify(capture)}, JSON.stringify({ cleanup: process.argv.slice(2) }) + "\\n");
  if (fs.existsSync(${JSON.stringify(join(root, "block-cleanup"))})) process.exit(1);
  process.exit(0);
}
let input = "";
process.stdin.on("data", (part) => input += part);
process.stdin.on("end", () => {
  const args = process.argv.slice(2);
  fs.writeFileSync(args[args.indexOf("--cidfile") + 1], "a".repeat(64));
  fs.appendFileSync(${JSON.stringify(capture)}, JSON.stringify({args, env: process.env, job: JSON.parse(input)}) + "\\n");
  if (JSON.parse(input).model === "test/broken") {
    fs.writeFileSync(${JSON.stringify(join(root, "block-cleanup"))}, "");
    process.exit(124);
  }
  if (JSON.parse(input).model === "test/fail") process.exit(124);
  console.log("untrusted model response");
});
`, { mode: 0o755 });
  Object.assign(process.env, {
    PATH: `${root}:${original.PATH}`, TMPDIR: root, OPENROUTER_API_KEY: "synthetic-provider",
    RALPH_COUNCIL_MCP_TOKEN: "a".repeat(64), GH_TOKEN: "synthetic-github",
    VIKUNJA_API_TOKEN: "synthetic-board",
  });
  const result = await runCouncilProfile({ artifact: "synthetic artifact", revisions: 0,
    model: "test/model", port });
  assert.deepEqual(result, { jobId: id, result: { decision: "advance", verdicts: ["validated fixture"] } });
  const call = JSON.parse(readFileSync(capture, "utf8").trim().split("\n")[0]);
  assert.equal(call.env.GH_TOKEN, undefined);
  assert.equal(call.env.VIKUNJA_API_TOKEN, undefined);
  assert.equal(call.env.OPENROUTER_API_KEY, "synthetic-provider");
  assert.equal(call.env.RALPH_COUNCIL_MCP_TOKEN, undefined);
  assert.equal(call.env.RALPH_COUNCIL_JOB_TOKEN, jobToken);
  assert.ok(call.args.includes("--read-only") && call.args.includes("--cap-drop") && call.args.includes("--user"));
  assert.ok(!call.args.includes("--volume") && !call.args.includes("-v"));
  assert.ok(!call.args.join(" ").includes("synthetic artifact"));
  assert.ok(!call.args.join(" ").includes("a".repeat(64)));
  assert.equal(call.job.artifact, undefined);
  assert.deepEqual(registrations[0], { artifact: "synthetic artifact", revisions: 0 });
  assert.deepEqual(profileStops, [id]);
  await assert.rejects(runCouncilProfile({ artifact: "fail", revisions: 0, model: "test/fail", port }),
    new RegExp(id));
  const cleanup = JSON.parse(readFileSync(capture, "utf8").trim().split("\n").at(-1));
  assert.deepEqual(cleanup.cleanup, ["rm", "-f", "a".repeat(64)]);
  const stopsBeforeFailure = profileStops.length;
  await assert.rejects(runCouncilProfile({ artifact: "broken", revisions: 0, model: "test/broken", port }),
    /cleanup unconfirmed/);
  assert.equal(profileStops.length, stopsBeforeFailure);
});

test("rejects invalid input before starting a profile", async () => {
  for (const job of [
    { artifact: "", revisions: 0, model: "test/model", port: 8766 },
    { artifact: "x".repeat(16385), revisions: 0, model: "test/model", port: 8766 },
    { artifact: "fixture", revisions: 3, model: "test/model", port: 8766 },
    { artifact: "fixture", revisions: 0, model: "invalid", port: 8766 },
    { artifact: "fixture", revisions: 0, model: "test/model", port: 0 },
  ]) await assert.rejects(runCouncilProfile(job), TypeError);
});
