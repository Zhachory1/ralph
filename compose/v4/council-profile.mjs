import { execFile, spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const IMAGE = "nousresearch/hermes-agent@sha256:36099926da316742423544e055d4734777105ffe6341873af5459b957d0c2919";
const WORKER = readFileSync(new URL("./council-profile-worker.py", import.meta.url), "utf8");
const MAX_OUTPUT = 1024 * 1024;
// JSON escaping must leave room under Linux's per-argument limit in judge workers.
const MAX_ARTIFACT_BYTES = 16 * 1024;

export async function runCouncilProfile({ artifact, revisions, model, port }) {
  if (typeof artifact !== "string" || !artifact.trim() || Buffer.byteLength(artifact) > MAX_ARTIFACT_BYTES
      || !Number.isInteger(revisions) || revisions < 0 || revisions > 2
      || typeof model !== "string" || !/^[\w.-]+\/[\w.-]+$/.test(model)
      || !Number.isInteger(port) || port < 1 || port > 65535) {
    throw new TypeError("Expected an artifact, 0–2 revisions, a provider/model, and an MCP port");
  }
  if (!process.env.OPENROUTER_API_KEY || !/^[a-f0-9]{64}$/.test(process.env.RALPH_COUNCIL_MCP_TOKEN ?? "")) {
    throw new Error("Council requires a provider key and a 32-byte hex MCP token");
  }
  const base = `http://127.0.0.1:${port}`;
  const headers = { Authorization: `Bearer ${process.env.RALPH_COUNCIL_MCP_TOKEN}`, "Content-Type": "application/json" };
  const registration = await fetch(`${base}/jobs`, {
    method: "POST", headers, body: JSON.stringify({ artifact, revisions }), signal: AbortSignal.timeout(10_000),
  });
  if (registration.status !== 201) throw new Error(`Council job registration failed (${registration.status})`);
  const { id, token } = await registration.json();
  if (!/^[a-f0-9]{64}$/.test(id ?? "") || !/^[a-f0-9]{64}$/.test(token ?? "")) {
    throw new Error("Council job registration returned invalid credentials");
  }
  const env = { PATH: process.env.PATH, HOME: process.env.HOME,
    OPENROUTER_API_KEY: process.env.OPENROUTER_API_KEY, RALPH_COUNCIL_JOB_TOKEN: token };
  for (const name of ["DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG"]) {
    if (process.env[name]) env[name] = process.env[name];
  }
  let dir;
  try { dir = mkdtempSync(join(tmpdir(), "ralph-council-profile-")); }
  catch { throw new Error(`Council job ${id}: could not prepare profile container`); }
  const cidfile = join(dir, "cid");
  const name = `ralph-council-profile-${id}`;
  let completed = false;
  let cleanupUnconfirmed = false;
  let containerId;
  let launchError;
  try {
    const args = ["run", "--rm", "--pull=never", "--init", "-i", "--name", name, "--cidfile", cidfile, "--read-only",
      "--tmpfs", "/tmp:rw,nosuid,size=256m", "--user", "10000:10000", "--cap-drop", "ALL",
      "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "1g",
      "--env", "OPENROUTER_API_KEY", "--env", "RALPH_COUNCIL_JOB_TOKEN",
      "--entrypoint", "/usr/bin/timeout", IMAGE, "--kill-after=5s", "180s", "python3", "-c", WORKER];
    await new Promise((resolve, reject) => {
      const child = spawn("docker", args, { env, timeout: 200_000, stdio: ["pipe", "pipe", "pipe"] });
      let stdout = "", oversized = false;
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", (chunk) => {
        stdout += chunk;
        if (stdout.length > MAX_OUTPUT) { oversized = true; child.kill(); }
      });
      child.stderr.resume();
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify({ model, port }));
      child.on("error", reject);
      child.on("close", (code, signal) => {
        if (code !== 0 || oversized) reject(new Error(`Council container did not complete (${oversized ? "output limit" : code ?? signal})`));
        else resolve();
      });
    });
    completed = true;
  } catch (error) {
    launchError = error;
  } finally {
    try {
      if (!completed) {
        try { containerId = readFileSync(cidfile, "utf8").trim(); }
        catch (error) { if (error.code !== "ENOENT") throw error; }
        if (containerId && /^[a-f0-9]{64}$/.test(containerId)) {
          await new Promise((resolve, reject) => {
            execFile("docker", ["rm", "-f", containerId], { env, timeout: 10_000 }, (error, _out, stderr) => {
              if (error && !String(stderr ?? "").includes("No such container")) {
                reject(new Error("Docker removal failed"));
              } else resolve();
            });
          });
        } else throw new Error("Council container ID unavailable");
      }
    } catch {
      cleanupUnconfirmed = true;
      const identifier = /^[a-f0-9]{64}$/.test(containerId ?? "") ? containerId : name;
      throw new Error(`Council job ${id}: cleanup unconfirmed for ${identifier}; recovery file: ${cidfile}`);
    } finally {
      if (!cleanupUnconfirmed) rmSync(dir, { recursive: true, force: true });
    }
  }
  try {
    const stopped = await fetch(`${base}/jobs/${id}/profile-stopped`, {
      method: "POST", headers, signal: AbortSignal.timeout(10_000),
    });
    if (stopped.status !== 204) throw new Error("Profile acknowledgment failed");
  } catch {
    throw new Error(`Council job ${id}: could not confirm profile shutdown to service`);
  }
  for (let attempt = 0; attempt < 130; attempt++) {
    let response;
    try {
      response = await fetch(`${base}/jobs/${id}`, { headers, signal: AbortSignal.timeout(10_000) });
    } catch {
      throw new Error(`Council job ${id}: result unavailable`);
    }
    if (!response.ok) throw new Error(`Council job ${id}: result unavailable (${response.status})`);
    const state = await response.json();
    if (state.status === "completed") return { jobId: id, result: state.result };
    if (state.status !== "running") {
      throw new Error(`Council job ${id}: ${state.status}${launchError ? ` (${launchError.message})` : ""}`);
    }
    await delay(1000);
  }
  throw new Error(`Council job ${id}: result still running`);
}
