import { execFile } from "node:child_process";
import { readFileSync } from "node:fs";
import { isAbsolute } from "node:path";
import { dispatchReadyTicket } from "./dispatch-ready.mjs";
import { readReadyTicket } from "./ready-ticket.mjs";

function run(command, args, { input, env, timeout = 30_000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = execFile(command, args, { timeout, env, maxBuffer: 1024 * 1024 }, (error, stdout) => {
      if (error) reject(new Error(`${command.split("/").at(-1)} did not complete (${error.code ?? error.signal ?? "error"})`));
      else resolve(stdout.trim());
    });
    child.stdin.on("error", () => {});
    child.stdin.end(input);
  });
}

async function main() {
  const ticketId = Number(process.argv[2]);
  const file = process.env.RALPH_READY_CONFIG;
  if (process.argv.length !== 3 || !Number.isSafeInteger(ticketId) || ticketId < 1 || !file || !isAbsolute(file)) {
    throw new Error("Usage: set RALPH_READY_CONFIG to an absolute operator config path and pass one ticket ID");
  }
  const config = JSON.parse(readFileSync(file, "utf8"));
  const job = Object.hasOwn(config.jobs, String(ticketId)) ? config.jobs[ticketId] : undefined;
  if (!job || !isAbsolute(job.repoPath) || !isAbsolute(job.rfcPath) || typeof job.product !== "string"
      || !Array.isArray(job.acceptanceCriteria) || !job.acceptanceCriteria.length
      || job.acceptanceCriteria.some((criterion) => typeof criterion !== "string")) {
    throw new Error("Ticket needs an operator-configured checkout and accepted RFC with criteria");
  }
  const worker = config.sweCommand;
  if (!Array.isArray(worker) || !worker.length || worker.some((arg) => typeof arg !== "string") || !isAbsolute(worker[0])) {
    throw new Error("sweCommand must be an operator-configured absolute executable plus arguments");
  }

  const token = process.env.VIKUNJA_API_TOKEN;
  const commandEnv = { ...process.env };
  delete commandEnv.VIKUNJA_API_TOKEN;
  delete commandEnv.RALPH_READY_CONFIG;
  const workerEnv = { ...commandEnv };
  for (const key of ["GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"]) delete workerEnv[key];
  const query = (sql, variables) => run("docker", ["compose", "--project-directory", import.meta.dirname,
    "exec", "-T", "postgres", "sh", "-ec", 'exec psql -XAtq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"',
    "psql", ...Object.entries(variables).flatMap(([key, value]) => ["-v", `${key}=${value}`])],
  { input: `${sql}\n`, env: commandEnv });

  const result = await dispatchReadyTicket({ ...job, ticketId }, {
    readReady: (id) => readReadyTicket({ ...config.board, token, ticketId: id }),
    claim: (id) => query("SELECT claim_ready_dispatch(:'ticket'::bigint);", { ticket: id }),
    async findDraft(repo, branch) {
      const fields = "number,headRefName,headRefOid,baseRefName,author,isDraft,isCrossRepository";
      const candidates = JSON.parse(await run("gh", ["pr", "list", "--repo", repo, "--head", branch,
        "--state", "open", "--limit", "2", "--json", fields], { env: commandEnv }));
      if (candidates.length > 1) throw new Error("More than one open PR matched the configured ticket branch");
      const pr = candidates[0];
      return pr && { repo, branch: pr.headRefName, headSha: pr.headRefOid, baseBranch: pr.baseRefName,
        author: pr.author?.login, number: pr.number, isDraft: pr.isDraft, isCrossRepository: pr.isCrossRepository };
    },
    invokeSwe: (args) => run(worker[0], [...worker.slice(1), JSON.stringify(args)], { env: workerEnv, timeout: 20 * 60_000 }),
    record: async (id, lease, repo, number, sha) => (await query(
      "SELECT record_ready_draft(:'ticket'::bigint, :'lease'::uuid, :'repo', :'number'::integer, :'sha');",
      { ticket: id, lease, repo, number, sha })) === "t",
  });
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.name}: ${error.message}\n`);
  process.exitCode = 1;
});
