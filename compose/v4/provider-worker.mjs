// Run a bounded, disposable Hermes worker with only the provider credential.
import { execFile, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// nousresearch/hermes-agent:v0.22.2
const IMAGE = "nousresearch/hermes-agent@sha256:36099926da316742423544e055d4734777105ffe6341873af5459b957d0c2919";
// Explicit overrides prevent Docker's client config from injecting credentialed proxy URLs.
const noProxy = ["HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY", "ALL_PROXY", "NO_PROXY"]
  .flatMap((name) => ["--env", `${name}=`, "--env", `${name.toLowerCase()}=`]);
export class WorkerCleanupError extends Error {}

export async function runProviderWorker({ prefix, worker, job, maxOutput }) {
  if (!process.env.OPENROUTER_API_KEY) throw new Error("Missing OpenRouter API key");
  const input = JSON.stringify(job);
  const env = { PATH: process.env.PATH, HOME: process.env.HOME, OPENROUTER_API_KEY: process.env.OPENROUTER_API_KEY };
  for (const name of ["DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG"]) {
    if (process.env[name]) env[name] = process.env[name];
  }
  const dir = mkdtempSync(join(tmpdir(), `${prefix}-`));
  const cidfile = join(dir, "cid");
  const name = `${prefix}-${randomBytes(16).toString("hex")}`;
  let completed = false;
  let cleanupUnconfirmed = false;
  let id;
  let output;
  try {
    const args = ["run", "--rm", "--pull=never", "--init", "-i", "--name", name, "--cidfile", cidfile,
      // Replace the image's declared anonymous volume and keep output out of daemon logs.
      "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=256m", "--tmpfs", "/opt/data:ro,nosuid,size=1m", "--log-driver=none",
      "--user", "10000:10000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128",
      "--memory", "1g", "--env", "OPENROUTER_API_KEY", ...noProxy, "--entrypoint", "/usr/bin/timeout", IMAGE,
      "--kill-after=5s", "90s", "python3", "-c", worker];
    output = await new Promise((resolve, reject) => {
      // SIGTERM may be proxied into the container without stopping the attached CLI.
      const child = spawn("docker", args, { env, timeout: 110_000, killSignal: "SIGKILL", stdio: ["pipe", "pipe", "pipe"] });
      const stdout = [];
      let bytes = 0, stderr = "", oversized = false;
      child.stderr.on("data", (chunk) => { if (stderr.length < 4096) stderr += chunk.toString().slice(0, 4096 - stderr.length); });
      child.stdout.on("data", (chunk) => {
        bytes += chunk.length;
        if (!oversized && bytes > maxOutput) { oversized = true; child.kill("SIGKILL"); }
        if (!oversized) stdout.push(chunk);
      });
      child.stdin.on("error", () => {});
      child.stdin.end(input);
      child.on("error", reject);
      child.on("close", (code, signal) => {
        const reason = oversized ? "output limit" : /no such image|not present locally/i.test(stderr)
          ? "pinned image unavailable" : code ?? signal;
        if (code !== 0 || oversized) reject(new Error(`Worker container did not complete (${reason})`));
        else resolve(Buffer.concat(stdout));
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
            execFile("docker", ["rm", "-f", id], { env, timeout: 10_000, killSignal: "SIGKILL" }, (error, _out, stderr) => {
              if (error && !String(stderr ?? "").includes("No such container")) {
                reject(new Error("Docker removal failed"));
              }
              else resolve();
            });
          });
        } else throw new Error("Worker container ID unavailable");
      }
    } catch {
      cleanupUnconfirmed = true;
      const identifier = /^[a-f0-9]{64}$/.test(id ?? "") ? id : name;
      throw new WorkerCleanupError(`Could not confirm worker container cleanup: ${identifier}; recovery file: ${cidfile}`);
    } finally {
      if (!cleanupUnconfirmed) rmSync(dir, { recursive: true, force: true });
    }
  }
  return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(output);
}
