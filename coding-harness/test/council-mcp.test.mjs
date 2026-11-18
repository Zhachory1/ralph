import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { once } from "node:events";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const script = fileURLToPath(new URL("../council-mcp.mjs", import.meta.url));

test("Council MCP refuses missing or malformed credentials", () => {
  for (const token of ["", "not-a-token"]) {
    const result = spawnSync(process.execPath, [script], {
      env: { PATH: process.env.PATH, HOME: process.env.HOME, OPENROUTER_API_KEY: "synthetic-provider",
        RALPH_COUNCIL_MCP_TOKEN: token }, encoding: "utf8", timeout: 3000,
    });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /token/i);
  }
});

test("Council MCP requires a private state directory", (t) => {
  const root = mkdtempSync(join(tmpdir(), "council-state-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const state = join(root, "public");
  mkdirSync(state, { mode: 0o755 });
  chmodSync(state, 0o755);
  for (const directory of ["", state]) {
    const result = spawnSync(process.execPath, [script], {
      env: { PATH: process.env.PATH, HOME: process.env.HOME, OPENROUTER_API_KEY: "synthetic-provider",
        RALPH_COUNCIL_MCP_TOKEN: "a".repeat(64), RALPH_COUNCIL_STATE_DIR: directory },
      encoding: "utf8", timeout: 3000,
    });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /state directory/i);
  }
});

test("Council MCP grants only the bounded review tool and returns validated panel findings", async (t) => {
  const probe = createServer().listen(0, "127.0.0.1");
  await once(probe, "listening");
  const port = probe.address().port;
  await new Promise((resolve) => probe.close(resolve));
  const directory = mkdtempSync(join(tmpdir(), "ralph-council-mcp-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const state = join(directory, "state");
  mkdirSync(state, { mode: 0o700 });
  const calls = join(directory, "calls");
  writeFileSync(join(directory, "docker"), `#!/usr/bin/env node
const fs = require("fs");
if (process.argv[2] === "rm") process.exit(0);
if (process.env.GH_TOKEN || process.env.GITHUB_TOKEN || process.env.VIKUNJA_API_TOKEN ||
    process.env.RALPH_COUNCIL_MCP_TOKEN || process.env.RALPH_COUNCIL_JOB_TOKEN) process.exit(1);
let input = "";
process.stdin.on("data", (part) => input += part);
process.stdin.on("end", () => {
  const job = JSON.parse(input);
  fs.appendFileSync(${JSON.stringify(calls)}, JSON.stringify(job) + "\\n");
  if (job.artifact === "failing" && job.role === "architecture_scale") {
    fs.writeFileSync(process.argv[process.argv.indexOf("--cidfile") + 1], "a".repeat(64));
    process.exit(1);
  }
  setTimeout(() => console.log(JSON.stringify({judge_role: job.role, verdict: "PASS",
    confidence: 0.9, blocking_issues: [], non_blocking_notes: []})), 200);
});
`, { mode: 0o755 });
  const token = randomBytes(32).toString("hex");
  const child = spawn(process.execPath, [script], {
    env: { ...process.env, PATH: `${directory}${delimiter}${process.env.PATH}`, GH_TOKEN: "synthetic-github",
      VIKUNJA_API_TOKEN: "synthetic-board", OPENROUTER_API_KEY: "synthetic-provider",
      RALPH_COUNCIL_MCP_TOKEN: token, RALPH_COUNCIL_MCP_PORT: String(port), RALPH_COUNCIL_STATE_DIR: state },
    stdio: ["ignore", "ignore", "inherit"],
  });
  t.after(() => child.kill());
  const url = new URL(`http://127.0.0.1:${port}/mcp`);
  let response;
  for (let i = 0; i < 100 && !response && child.exitCode === null; i++) {
    response = await fetch(url).catch(() => null);
    if (!response) await delay(20);
  }
  assert.equal(response?.status, 401);
  assert.equal((await fetch(url, { headers: { Authorization: `Bearer ${randomBytes(32).toString("hex")}` } })).status, 401);
  assert.equal((await fetch(url, { headers: { Origin: "https://example.invalid", Authorization: `Bearer ${token}` } })).status, 403);
  assert.equal((await fetch(url, { headers: { Authorization: `Bearer ${token}` } })).status, 401);
  const base = `http://127.0.0.1:${port}`;
  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  const register = async (args) => fetch(`${base}/jobs`, { method: "POST", headers, body: JSON.stringify(args) });
  for (const bad of [{ artifact: "", revisions: 0 }, { artifact: "fixture", revisions: 3 },
    { artifact: "fixture", revisions: 0, rubric: "override" }, { artifact: "x".repeat(16385), revisions: 0 }]) {
    assert.equal((await register(bad)).status, 400);
  }
  const registration = await register({ artifact: "synthetic artifact", revisions: 0 });
  assert.equal(registration.status, 201);
  const job = await registration.json();
  const client = new Client({ name: "council-fixture", version: "1" });
  t.after(() => client.close());
  await client.connect(new StreamableHTTPClientTransport(url, { requestInit: { headers: { Authorization: `Bearer ${job.token}` } } }));
  assert.deepEqual((await client.listTools()).tools.map(({ name }) => name), ["review_artifact"]);
  await assert.rejects(client.callTool({ name: "implement_spec", arguments: {} }));
  await assert.rejects(client.callTool({ name: "review_artifact", arguments: { artifact: "substitution" } }));
  const first = client.callTool({ name: "review_artifact", arguments: {} });
  for (let i = 0; i < 100 && !existsSync(calls); i++) await delay(10);
  assert.ok(existsSync(calls), "first panel did not start");
  await assert.rejects(client.callTool({ name: "review_artifact", arguments: {} }));
  const result = JSON.parse((await first).content[0].text);
  assert.equal(result.decision, "advance");
  assert.equal(result.rubricVersion, "v1");
  assert.equal(result.verdicts.length, 3);
  const jobs = readFileSync(calls, "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(jobs.length, 3);
  assert.ok(jobs.every(({ artifact }) => artifact === "synthetic artifact"));
  assert.equal((await fetch(`${base}/jobs/${job.id}/profile-stopped`, { method: "POST", headers })).status, 204);
  assert.equal((await fetch(`${base}/jobs/${job.id}`, { method: "DELETE", headers })).status, 204);
  for (const artifact of ["failing", "\x01".repeat(16384), "recovered"]) {
    const next = await (await register({ artifact, revisions: 0 })).json();
    const nextClient = new Client({ name: "council-fixture", version: "1" });
    await nextClient.connect(new StreamableHTTPClientTransport(url, {
      requestInit: { headers: { Authorization: `Bearer ${next.token}` } },
    }));
    if (artifact === "failing") {
      await assert.rejects(nextClient.callTool({ name: "review_artifact", arguments: {} }));
    } else {
      const result = await nextClient.callTool({ name: "review_artifact", arguments: {} });
      assert.equal(JSON.parse(result.content[0].text).decision, "advance");
    }
    await nextClient.close();
    assert.equal((await fetch(`${base}/jobs/${next.id}/profile-stopped`, { method: "POST", headers })).status, 204);
    assert.equal((await fetch(`${base}/jobs/${next.id}`, { method: "DELETE", headers })).status, 204);
  }
});
