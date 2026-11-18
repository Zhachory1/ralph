import { execFile, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { validateCouncilVerdict } from "./council-verdict.mjs";

// nousresearch/hermes-agent:v0.22.2
const IMAGE = "nousresearch/hermes-agent@sha256:36099926da316742423544e055d4734777105ffe6341873af5459b957d0c2919";
const MODELS = Object.freeze({
  architecture_scale: "anthropic/claude-sonnet-5.5",
  security_blast_radius: "google/gemini-3.8-flash",
  scope_pragmatism: "openai/gpt-6.2-sol",
});
const WORKER = readFileSync(new URL("./council-judge-worker.py", import.meta.url), "utf8");
const MAX_OUTPUT = 1024 * 1024;

export class CouncilCleanupError extends Error {}

export async function invokeCouncilJudge({ judge_role, artifact, rubric }) {
  if (!Object.hasOwn(MODELS, judge_role)) throw new TypeError("Unknown judge role");
  if (!process.env.OPENROUTER_API_KEY) throw new Error("Missing OpenRouter API key");
  if (typeof artifact !== "string" || !artifact.trim() || typeof rubric !== "string" || !rubric.trim()) {
    throw new TypeError("Expected an artifact and rubric");
  }
  const env = { PATH: process.env.PATH, HOME: process.env.HOME, OPENROUTER_API_KEY: process.env.OPENROUTER_API_KEY };
  for (const name of ["DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG"]) {
    if (process.env[name]) env[name] = process.env[name];
  }
  const dir = mkdtempSync(join(tmpdir(), "ralph-council-judge-"));
  const cidfile = join(dir, "cid");
  const runId = process.env.RALPH_COUNCIL_RUN_ID;
  const name = `ralph-council-judge-${/^[a-f0-9]{64}$/.test(runId ?? "") ? `${runId}-` : ""}${randomBytes(16).toString("hex")}`;
  let completed = false;
  let cleanupUnconfirmed = false;
  let id;
  let output;
  try {
    const args = ["run", "--rm", "--pull=never", "--init", "-i", "--name", name, "--cidfile", cidfile, "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=256m",
      "--user", "10000:10000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128",
      "--memory", "1g", "--env", "OPENROUTER_API_KEY", "--entrypoint", "/usr/bin/timeout", IMAGE,
      "--kill-after=5s", "90s", "python3", "-c", WORKER];
    const input = JSON.stringify({ role: judge_role, model: MODELS[judge_role], artifact, rubric });
    output = await new Promise((resolve, reject) => {
      const child = spawn("docker", args, { env, timeout: 110_000, stdio: ["pipe", "pipe", "pipe"] });
      let stdout = "", stderr = "", oversized = false;
      child.stdout.setEncoding("utf8");
      child.stderr.on("data", (chunk) => { if (stderr.length < 4096) stderr += chunk.toString().slice(0, 4096 - stderr.length); });
      child.stdout.on("data", (chunk) => {
        stdout += chunk;
        if (stdout.length > MAX_OUTPUT) { oversized = true; child.kill(); }
      });
      child.stdin.on("error", () => {});
      child.stdin.end(input);
      child.on("error", reject);
      child.on("close", (code, signal) => {
        const reason = oversized ? "output limit" : /no such image|not present locally/i.test(stderr)
          ? "pinned image unavailable" : code ?? signal;
        if (code !== 0 || oversized) reject(new Error(`Judge container did not complete (${reason})`));
        else resolve(stdout);
      });
    });
    completed = true;
  } finally {
    try {
      if (!completed) {
        try { id = readFileSync(cidfile, "utf8").trim(); }
        catch (error) { if (error.code !== "ENOENT") throw error; }
        if (id && /^[a-f0-9]{64}$/.test(id)) {
          await new Promise((resolve, reject) => {
            execFile("docker", ["rm", "-f", id], { env, timeout: 10_000 }, (error, _out, stderr) => {
              if (error && !String(stderr ?? "").includes("No such container")) {
                reject(new Error("Docker removal failed"));
              }
              else resolve();
            });
          });
        } else throw new Error("Judge container ID unavailable");
      }
    } catch {
      cleanupUnconfirmed = true;
      const identifier = /^[a-f0-9]{64}$/.test(id ?? "") ? id : name;
      throw new CouncilCleanupError(`Could not confirm judge container cleanup: ${identifier}; recovery file: ${cidfile}`);
    } finally {
      if (!cleanupUnconfirmed) rmSync(dir, { recursive: true, force: true });
    }
  }
  let verdict;
  try { verdict = JSON.parse(output); } catch { throw new Error("Judge did not return a JSON verdict"); }
  if (verdict?.judge_role !== judge_role) throw new Error("Judge verdict does not match its assigned role");
  validateCouncilVerdict(verdict);
  return verdict;
}
