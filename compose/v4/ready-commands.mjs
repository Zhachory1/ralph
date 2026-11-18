import { execFile } from "node:child_process";
import { readFileSync } from "node:fs";
import { isAbsolute } from "node:path";

export function run(command, args, { input, env, timeout = 30_000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = execFile(command, args, { timeout, env, maxBuffer: 1024 * 1024 }, (error, stdout) => {
      if (error) reject(new Error(`${command.split("/").at(-1)} did not complete (${error.code ?? error.signal ?? "error"})`));
      else resolve(stdout.trim());
    });
    child.stdin.on("error", () => {});
    child.stdin.end(input);
  });
}

export function loadReadyTicket() {
  const ticketId = Number(process.argv[2]);
  const file = process.env.RALPH_READY_CONFIG;
  if (process.argv.length !== 3 || !Number.isSafeInteger(ticketId) || ticketId < 1 || !file || !isAbsolute(file)) {
    throw new Error("Usage: set RALPH_READY_CONFIG to an absolute operator config path and pass one ticket ID");
  }
  const config = JSON.parse(readFileSync(file, "utf8"));
  const job = config.jobs && Object.hasOwn(config.jobs, String(ticketId)) ? config.jobs[ticketId] : null;
  if (!job || !/^[\w.-]+\/[\w.-]+$/.test(job.repo)) throw new Error("Ticket needs an operator-configured GitHub repository");
  return { config, job, ticketId };
}

export function commandEnvironment() {
  const env = { ...process.env };
  for (const key of ["VIKUNJA_API_TOKEN", "VIKUNJA_DONE_TOKEN", "RALPH_READY_CONFIG"]) delete env[key];
  return env;
}

export function query(sql, variables, env) {
  return run("docker", ["compose", "--project-directory", import.meta.dirname,
    "exec", "-T", "postgres", "sh", "-ec", 'exec psql -XAtq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"',
    "psql", ...Object.entries(variables).flatMap(([key, value]) => ["-v", `${key}=${value}`])],
  { input: `${sql}\n`, env });
}
