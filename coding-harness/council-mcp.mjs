// Standalone Council job service and job-scoped MCP tool.
import { randomBytes, timingSafeEqual } from "node:crypto";
import { existsSync, lstatSync, unlinkSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { isAbsolute, join } from "node:path";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { CouncilCleanupError } from "../compose/v4/council-judge.mjs";
import { runV1Council } from "../compose/v4/council-v1.mjs";

const masterToken = process.env.RALPH_COUNCIL_MCP_TOKEN ?? "";
const port = Number(process.env.RALPH_COUNCIL_MCP_PORT ?? "8766");
const stateDir = process.env.RALPH_COUNCIL_STATE_DIR ?? "";
if (!/^[a-f0-9]{64}$/.test(masterToken)) throw new Error("Council MCP requires a 32-byte hex bearer token");
if (!Number.isInteger(port) || port < 1 || port > 65535 || !process.env.OPENROUTER_API_KEY) {
  throw new Error("Council MCP requires a valid port and provider key");
}
if (!isAbsolute(stateDir)) throw new Error("Council MCP requires an absolute private state directory");
const state = lstatSync(stateDir);
if (!state.isDirectory() || state.uid !== process.getuid() || (state.mode & 0o077) !== 0) {
  throw new Error("Council state directory must be owned by this user and inaccessible to others");
}
const marker = join(stateDir, "council-job.json");
for (const key of Object.keys(process.env)) {
  if (!["PATH", "HOME", "TMPDIR", "OPENROUTER_API_KEY", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG"].includes(key)) {
    delete process.env[key];
  }
}
// JSON escaping must leave room under Linux's per-argument limit in the judge launcher.
const MAX_ARTIFACT_BYTES = 16 * 1024;
const MAX_BODY_BYTES = 128 * 1024;
let job;

function authorized(req, expected) {
  const candidate = /^Bearer ([a-f0-9]{64})$/.exec(req.headers.authorization ?? "")?.[1];
  return Boolean(candidate && expected && timingSafeEqual(Buffer.from(candidate), Buffer.from(expected)));
}

function send(res, status, body) {
  if (body === undefined) return res.writeHead(status).end();
  return res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" })
    .end(JSON.stringify(body));
}

function cleanupFailures(error) {
  if (error instanceof CouncilCleanupError) return [error.message];
  if (error instanceof AggregateError) return error.errors.flatMap(cleanupFailures);
  return [];
}

createServer(async (req, res) => {
  if (req.headers.origin) return send(res, 403);
  if (req.url === "/mcp") {
    if (!authorized(req, job?.token)) return send(res, 401);
    const entry = job;
    const server = new Server({ name: "ralph-council", version: "1" }, { capabilities: { tools: {} } });
    server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: [{
      name: "review_artifact", description: "Review the artifact registered for this job.",
      inputSchema: { type: "object", properties: {}, additionalProperties: false },
    }] }));
    server.setRequestHandler(CallToolRequestSchema, async ({ params }) => {
      if (params.name !== "review_artifact" || Object.keys(params.arguments ?? {}).length !== 0) {
        throw new Error("Tool accepts no arguments");
      }
      if (entry !== job || entry.status !== "queued") throw new Error("Council job already started");
      entry.status = "running";
      process.env.RALPH_COUNCIL_RUN_ID = entry.id;
      try {
        const result = await runV1Council({ artifact: entry.artifact, revisions: entry.revisions });
        entry.result = result;
        entry.status = "completed";
        return { content: [{ type: "text", text: JSON.stringify(result) }] };
      } catch (error) {
        const failures = cleanupFailures(error);
        entry.status = failures.length ? "cleanup_unconfirmed" : "failed";
        entry.error = failures.length ? failures.join("; ") : "Council panel failed";
        if (failures.length) console.error(entry.error);
        throw new Error(failures.length ? "Council cleanup unconfirmed; host recovery required" : entry.error);
      } finally {
        delete process.env.RALPH_COUNCIL_RUN_ID;
      }
    });
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined, enableJsonResponse: true, maxRequestBodySize: MAX_BODY_BYTES,
    });
    res.on("close", () => { void server.close(); });
    try {
      await server.connect(transport);
      await transport.handleRequest(req, res);
    } catch {
      if (!res.headersSent) res.writeHead(500);
      res.end();
    }
    return;
  }
  if (!authorized(req, masterToken)) return send(res, 401);
  if (req.url === "/jobs" && req.method === "GET") {
    return send(res, 200, job ? { id: job.id, status: job.status }
      : existsSync(marker) ? { status: "locked" } : {});
  }
  if (req.url === "/jobs" && req.method === "POST") {
    if (job || existsSync(marker)) return send(res, 409);
    const chunks = [];
    let size = 0;
    try {
      for await (const chunk of req) {
        size += chunk.length;
        if (size > MAX_BODY_BYTES) return send(res, 413);
        chunks.push(chunk);
      }
      const args = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      if (!args || Object.keys(args).length !== 2 || typeof args.artifact !== "string" || !args.artifact.trim()
          || Buffer.byteLength(args.artifact) > MAX_ARTIFACT_BYTES
          || !Number.isInteger(args.revisions) || args.revisions < 0 || args.revisions > 2) {
        return send(res, 400);
      }
      if (job) return send(res, 409);
      const entry = { id: randomBytes(32).toString("hex"), token: randomBytes(32).toString("hex"),
        artifact: args.artifact, revisions: args.revisions, status: "queued", profileActive: true };
      try {
        writeFileSync(marker, JSON.stringify({ jobId: entry.id }), { flag: "wx", mode: 0o600 });
      } catch (error) {
        return send(res, error.code === "EEXIST" ? 409 : 500);
      }
      job = entry;
      return send(res, 201, { id: job.id, token: job.token });
    } catch {
      return send(res, 400);
    }
  }
  if (job && req.url === `/jobs/${job.id}/profile-stopped` && req.method === "POST") {
    job.profileActive = false;
    return send(res, 204);
  }
  if (job && req.url === `/jobs/${job.id}`) {
    if (req.method === "GET") {
      return send(res, 200, { status: job.status, profileActive: job.profileActive, ...(job.result && { result: job.result }),
        ...(job.error && { error: job.error }) });
    }
    if (req.method === "DELETE") {
      if (job.profileActive || ["running", "cleanup_unconfirmed"].includes(job.status)) return send(res, 409);
      try { unlinkSync(marker); } catch { return send(res, 500); }
      job = undefined;
      return send(res, 204);
    }
  }
  return send(res, 404);
}).listen(port, "127.0.0.1");
