import { isAbsolute } from "node:path";
import { dispatchReadyTicket } from "./dispatch-ready.mjs";
import { readReadyTicket } from "./ready-ticket.mjs";
import { commandEnvironment, loadReadyTicket, query, run } from "./ready-commands.mjs";

async function main() {
  const { config, job, ticketId } = loadReadyTicket();
  if (!isAbsolute(job.repoPath) || !isAbsolute(job.rfcPath) || typeof job.product !== "string"
      || !Array.isArray(job.acceptanceCriteria) || !job.acceptanceCriteria.length
      || job.acceptanceCriteria.some((criterion) => typeof criterion !== "string")) {
    throw new Error("Ticket needs an operator-configured checkout and accepted RFC with criteria");
  }
  const worker = config.sweCommand;
  if (!Array.isArray(worker) || !worker.length || worker.some((arg) => typeof arg !== "string") || !isAbsolute(worker[0])) {
    throw new Error("sweCommand must be an operator-configured absolute executable plus arguments");
  }

  const token = process.env.VIKUNJA_API_TOKEN;
  const commandEnv = commandEnvironment();
  const workerEnv = { ...commandEnv };
  for (const key of ["GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"]) delete workerEnv[key];

  const result = await dispatchReadyTicket({ ...job, ticketId }, {
    readReady: (id) => readReadyTicket({ ...config.board, token, ticketId: id }),
    claim: (id) => query("SELECT claim_ready_dispatch(:'ticket'::bigint);", { ticket: id }, commandEnv),
    async findDraft(repo, branch) {
      const fields = "number,headRefName,headRefOid,baseRefName,author,isDraft,isCrossRepository";
      const candidates = JSON.parse(await run("gh", ["pr", "list", "--repo", `github.com/${repo}`, "--head", branch,
        "--state", "open", "--limit", "2", "--json", fields], { env: commandEnv }));
      if (candidates.length > 1) throw new Error("More than one open PR matched the configured ticket branch");
      const pr = candidates[0];
      return pr && { repo, branch: pr.headRefName, headSha: pr.headRefOid, baseBranch: pr.baseRefName,
        author: pr.author?.login, number: pr.number, isDraft: pr.isDraft, isCrossRepository: pr.isCrossRepository };
    },
    invokeSwe: (args) => run(worker[0], [...worker.slice(1), JSON.stringify(args)], { env: workerEnv, timeout: 20 * 60_000 }),
    record: async (id, lease, repo, number, sha) => (await query(
      "SELECT record_ready_draft(:'ticket'::bigint, :'lease'::uuid, :'repo', :'number'::integer, :'sha');",
      { ticket: id, lease, repo, number, sha }, commandEnv)) === "t",
  });
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.name}: ${error.message}\n`);
  process.exitCode = 1;
});
