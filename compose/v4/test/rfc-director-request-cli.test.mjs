// Focused check of the host CLI's transport: a loopback origin and a PAPERCLIP_API_KEY
// bearer token (routine-audit-worker.mjs's convention), not a real minted board key.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const CLI = fileURLToPath(new URL("../rfc-director-request.mjs", import.meta.url));

// Runs the CLI asynchronously: the stub board below lives in this same process's event loop,
// so a synchronous execFileSync would deadlock waiting on its own blocked server.
function runCli(args, { input, env }) {
  return new Promise((resolve, reject) => {
    const child = spawn("node", [CLI, ...args], { env, stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, stdout, stderr }));
    child.stdin.end(input);
  });
}
const DOCKER_STUB = `#!/usr/bin/env node
const fs = require("node:fs");
const args = process.argv.slice(2);
if (args[0] === "rm") process.exit(0);
const job = JSON.parse(fs.readFileSync(0, "utf8"));
fs.writeFileSync(args[args.indexOf("--cidfile") + 1], "a".repeat(64));
if (job.role) {
  process.stdout.write(JSON.stringify({judge_role: job.role, verdict: "PASS", confidence: 0.9,
    blocking_issues: [], non_blocking_notes: []}));
} else {
  process.stdout.write("# Fixture RFC\\n");
}
`;

function withStubDocker(t) {
  const dir = mkdtempSync(join(tmpdir(), "ralph-rfc-director-cli-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  writeFileSync(join(dir, "docker"), DOCKER_STUB, { mode: 0o700 });
  return dir;
}

async function withStubBoard(t, handler) {
  const calls = [];
  const server = createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      const body = chunks.length ? JSON.parse(Buffer.concat(chunks)) : undefined;
      calls.push({ method: request.method, path: request.url, authorization: request.headers.authorization,
        origin: request.headers.origin, body });
      handler(request, response, body);
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  return { calls, origin: `http://127.0.0.1:${server.address().port}` };
}

test("the CLI reads a stdin job and calls the real board with the configured bearer token", async (t) => {
  const dockerDir = withStubDocker(t);
  const { calls, origin } = await withStubBoard(t, (request, response) => {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(request.url.endsWith("/documents/rfc")
      ? JSON.stringify({ id: "doc-1", latestRevisionId: "rev-1", latestRevisionNumber: 1 })
      : JSON.stringify({ id: "confirm-1" }));
  });
  const job = { directorUserId: "director-1", issueId: "issue-1",
    brief: "Draft a fixture RFC", model: "test/model", domain: "non-coding" };
  const { code, stdout, stderr } = await runCli([origin], {
    input: JSON.stringify(job),
    env: { PATH: `${dockerDir}:${process.env.PATH}`, OPENROUTER_API_KEY: "synthetic-provider",
      PAPERCLIP_API_KEY: "synthetic-board-key" },
  });
  assert.equal(code, 0, stderr);
  const result = JSON.parse(stdout);
  assert.equal(result.route, "director");
  assert.equal(result.documentId, "doc-1");
  assert.equal(result.interactionId, "confirm-1");
  assert.deepEqual(calls.map((call) => call.path), ["/api/issues/issue-1/documents/rfc", "/api/issues/issue-1/interactions"]);
  for (const call of calls) {
    assert.equal(call.authorization, "Bearer synthetic-board-key");
    assert.equal(call.origin, origin);
  }
  assert.equal(calls[1].body.addresseeUserId, "director-1");
});

test("the CLI fails closed without a configured board token, before touching the board", async (t) => {
  const dockerDir = withStubDocker(t);
  const { calls, origin } = await withStubBoard(t, (request, response) => {
    response.writeHead(200, { "content-type": "application/json" });
    response.end("{}");
  });
  const job = { directorUserId: "director-1", issueId: "issue-1",
    brief: "Draft a fixture RFC", model: "test/model", domain: "non-coding" };
  const { code, stderr } = await runCli([origin], {
    input: JSON.stringify(job),
    env: { PATH: `${dockerDir}:${process.env.PATH}`, OPENROUTER_API_KEY: "synthetic-provider" },
  });
  assert.notEqual(code, 0);
  assert.match(stderr, /PAPERCLIP_API_KEY/);
  assert.deepEqual(calls, []);
});
