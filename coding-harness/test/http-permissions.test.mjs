import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { once } from "node:events";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { randomBytes } from "node:crypto";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

test("loopback MCP requires a bearer grant for discovery and direct calls", async (t) => {
  const probe = createServer().listen(0, "127.0.0.1");
  await once(probe, "listening");
  const { port } = probe.address();
  await new Promise((resolve) => probe.close(resolve));
  const token = randomBytes(32).toString("hex");
  const server = spawn(process.execPath, [fileURLToPath(new URL("../server.js", import.meta.url)), "--http"], {
    env: { ...process.env, RALPH_HARNESS_HTTP_PORT: String(port),
      RALPH_HARNESS_HTTP_GRANTS: JSON.stringify([{ token, tools: ["get_worktree_status"] }]) },
    stdio: ["ignore", "ignore", "inherit"],
  });
  t.after(() => server.kill());
  const url = new URL(`http://127.0.0.1:${port}/mcp`);
  let response;
  for (let i = 0; i < 50 && !response && server.exitCode === null; i++) {
    response = await fetch(url).catch(() => null);
    if (!response) await delay(20);
  }
  assert.equal(response?.status, 401);
  assert.equal((await fetch(url, { headers: { Origin: "https://example.invalid", Authorization: `Bearer ${token}` } })).status, 403);
  const client = new Client({ name: "permissions-fixture", version: "1" });
  t.after(() => client.close());
  await client.connect(new StreamableHTTPClientTransport(url, { requestInit: { headers: { Authorization: `Bearer ${token}` } } }));
  assert.deepEqual((await client.listTools()).tools.map(({ name }) => name), ["get_worktree_status"]);
  await assert.rejects(client.callTool({ name: "audit_pr", arguments: {} }), /Tool not granted/);
  const result = await client.callTool({ name: "get_worktree_status", arguments: {} });
  assert.equal(JSON.parse(result.content[0].text).status, "completed");
});
