import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { once } from "node:events";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

test("registered jobs bind input, recover results, and fail closed on cleanup uncertainty", async (t) => {
  const probe = createServer().listen(0, "127.0.0.1");
  await once(probe, "listening");
  const port = probe.address().port;
  await new Promise((resolve) => probe.close(resolve));
  const dir = mkdtempSync(join(tmpdir(), "council-jobs-"));
  const stateDir = join(dir, "state");
  mkdirSync(stateDir, { mode: 0o700 });
  const calls = join(dir, "calls");
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  writeFileSync(join(dir, "docker"), `#!/usr/bin/env node
const fs = require("fs");
if (process.argv[2] === "rm") process.exit(1);
let input = "";
process.stdin.on("data", (part) => input += part);
process.stdin.on("end", () => {
  const job = JSON.parse(input);
  fs.appendFileSync(${JSON.stringify(calls)}, JSON.stringify(job) + "\\n");
  if (job.model === "test/broken-profile") {
    fs.writeFileSync(process.argv[process.argv.indexOf("--cidfile") + 1], "b".repeat(64));
    process.exit(1);
  }
  if (job.artifact === "cleanup failure" && job.role === "architecture_scale") {
    console.log("invalid verdict");
    return;
  }
  if (job.artifact === "cleanup failure" && job.role === "security_blast_radius") {
    fs.writeFileSync(process.argv[process.argv.indexOf("--cidfile") + 1], "a".repeat(64));
    process.exit(1);
  }
  const revise = job.artifact === "original artifact" && job.role === "architecture_scale";
  setTimeout(() => console.log(JSON.stringify({judge_role: job.role, verdict: revise ? "REVISE" : "PASS",
    confidence: 0.9, blocking_issues: revise ? [{severity: "major", location: "section",
      issue: "synthetic issue", recommendation: "revise"}] : [], non_blocking_notes: []})), 300);
});
`, { mode: 0o755 });
  const master = randomBytes(32).toString("hex");
  const script = fileURLToPath(new URL("../council-mcp.mjs", import.meta.url));
  const env = {
    ...process.env, PATH: `${dir}${delimiter}${process.env.PATH}`, TMPDIR: dir,
    OPENROUTER_API_KEY: "synthetic-provider", RALPH_COUNCIL_MCP_TOKEN: master,
    RALPH_COUNCIL_MCP_PORT: String(port), RALPH_COUNCIL_STATE_DIR: stateDir,
  };
  const start = () => {
    const child = spawn(process.execPath, [script], { env, stdio: ["ignore", "ignore", "pipe"] });
    child.stderr.resume();
    return child;
  };
  let child = start();
  t.after(() => child.kill());
  const base = `http://127.0.0.1:${port}`;
  const headers = { Authorization: `Bearer ${master}`, "Content-Type": "application/json" };
  let ready;
  for (let i = 0; i < 100 && !ready; i++) {
    ready = await fetch(`${base}/jobs`, { method: "POST", headers, body: "{}" }).catch(() => null);
    if (!ready) await delay(20);
  }
  assert.equal(ready?.status, 400);
  const register = async (artifact) => fetch(`${base}/jobs`, {
    method: "POST", headers, body: JSON.stringify({ artifact, revisions: 2 }),
  });
  const registration = await register("original artifact");
  assert.equal(registration.status, 201);
  const { id, token } = await registration.json();
  assert.match(id, /^[a-f0-9]{64}$/);
  assert.match(token, /^[a-f0-9]{64}$/);
  assert.notEqual(token, master);
  assert.deepEqual(await (await fetch(`${base}/jobs`, { headers })).json(), { id, status: "queued" });
  assert.equal((await register("different artifact")).status, 409);
  const otherProbe = createServer().listen(0, "127.0.0.1");
  await once(otherProbe, "listening");
  const otherPort = otherProbe.address().port;
  await new Promise((resolve) => otherProbe.close(resolve));
  const other = spawn(process.execPath, [script], {
    env: { ...env, RALPH_COUNCIL_MCP_PORT: String(otherPort) }, stdio: ["ignore", "ignore", "pipe"],
  });
  other.stderr.resume();
  t.after(() => other.kill());
  let otherResponse;
  for (let i = 0; i < 100 && !otherResponse; i++) {
    otherResponse = await fetch(`http://127.0.0.1:${otherPort}/jobs`, {
      method: "POST", headers, body: JSON.stringify({ artifact: "other service", revisions: 0 }),
    }).catch(() => null);
    if (!otherResponse) await delay(20);
  }
  assert.equal(otherResponse?.status, 409);
  assert.equal((await fetch(`${base}/jobs/${id}`, { headers: { Authorization: `Bearer ${token}` } })).status, 401);
  const client = new Client({ name: "fixture", version: "1" });
  t.after(() => client.close());
  await client.connect(new StreamableHTTPClientTransport(new URL(`${base}/mcp`), {
    requestInit: { headers: { Authorization: `Bearer ${token}` } },
  }));
  assert.deepEqual((await client.listTools()).tools.map(({ name }) => name), ["review_artifact"]);
  await assert.rejects(client.callTool({
    name: "review_artifact", arguments: { artifact: "substituted", revisions: 0 },
  }));
  const running = client.callTool({ name: "review_artifact", arguments: {} });
  for (let i = 0; i < 100 && !existsSync(calls); i++) await delay(10);
  assert.ok(existsSync(calls));
  assert.equal((await fetch(`${base}/jobs/${id}`, { method: "DELETE", headers })).status, 409);
  await assert.rejects(client.callTool({ name: "review_artifact", arguments: {} }));
  await client.close();
  await running.catch(() => {});
  let job;
  for (let i = 0; i < 200; i++) {
    job = await (await fetch(`${base}/jobs/${id}`, { headers })).json();
    if (job.status !== "running") break;
    await delay(10);
  }
  assert.equal(job.status, "completed");
  assert.equal(job.result.decision, "escalate");
  assert.equal(job.artifactSha256, createHash("sha256").update("original artifact").digest("hex"));
  const jobs = readFileSync(calls, "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(jobs.length, 3);
  assert.ok(jobs.every((job) => job.artifact === "original artifact"));
  assert.equal((await fetch(`${base}/jobs/${id}`, { method: "DELETE", headers })).status, 409);
  assert.equal((await fetch(`${base}/jobs/${id}/profile-stopped`, { method: "POST", headers })).status, 204);
  assert.equal((await fetch(`${base}/jobs/${id}`, { method: "DELETE", headers })).status, 204);

  const profileModule = new URL("../../compose/v4/council-profile.mjs", import.meta.url).href;
  const launcher = `import {runCouncilProfile} from ${JSON.stringify(profileModule)};
try {
  await runCouncilProfile({artifact:"profile cleanup failure", revisions:0, model:"test/broken-profile", port:${port}});
  process.exit(1);
} catch (error) {
  if (!error.message.includes("cleanup unconfirmed")) throw error;
}`;
  await promisify(execFile)(process.execPath, ["--input-type=module", "-e", launcher], { env, timeout: 10_000 });
  const stuck = await (await fetch(`${base}/jobs`, { headers })).json();
  const stuckJob = await (await fetch(`${base}/jobs/${stuck.id}`, { headers })).json();
  assert.equal(stuckJob.profileActive, true);
  assert.equal((await fetch(`${base}/jobs/${stuck.id}`, { method: "DELETE", headers })).status, 409);
  assert.equal((await fetch(`${base}/jobs/${stuck.id}/profile-stopped`, { method: "POST", headers })).status, 204);
  assert.equal((await fetch(`${base}/jobs/${stuck.id}`, { method: "DELETE", headers })).status, 204);

  const failing = await (await register("cleanup failure")).json();
  const second = new Client({ name: "fixture", version: "1" });
  t.after(() => second.close());
  await second.connect(new StreamableHTTPClientTransport(new URL(`${base}/mcp`), {
    requestInit: { headers: { Authorization: `Bearer ${failing.token}` } },
  }));
  await assert.rejects(second.callTool({ name: "review_artifact", arguments: {} }), (error) => {
    assert.doesNotMatch(error.message, /a{64}|recovery file/);
    return true;
  });
  const failure = await (await fetch(`${base}/jobs/${failing.id}`, { headers })).json();
  assert.equal(failure.status, "cleanup_unconfirmed");
  assert.match(failure.error, /a{64}/);
  assert.equal((await fetch(`${base}/jobs/${failing.id}/profile-stopped`, { method: "POST", headers })).status, 204);
  assert.equal((await fetch(`${base}/jobs/${failing.id}`, { method: "DELETE", headers })).status, 409);
  assert.equal((await register("must not start")).status, 409);
  child.kill();
  await once(child, "exit");
  child = start();
  let afterRestart;
  for (let i = 0; i < 100 && !afterRestart; i++) {
    afterRestart = await register("must stay blocked").catch(() => null);
    if (!afterRestart) await delay(20);
  }
  assert.equal(afterRestart?.status, 409);
  assert.equal((await (await fetch(`${base}/jobs`, { headers })).json()).status, "locked");
});
