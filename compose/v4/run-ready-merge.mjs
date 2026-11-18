import { commandEnvironment, loadReadyTicket, query, run } from "./ready-commands.mjs";
import { moveVikunjaDone } from "./move-done.mjs";
import { completeReadyTicket } from "./verify-ready-merge.mjs";

async function main() {
  const { config, job, ticketId } = loadReadyTicket();
  if (!Array.isArray(config.directors) || !config.directors.length
      || !job.expectedAuthor || config.directors.some((actor) => typeof actor !== "string" || !actor
        || actor.toLowerCase() === job.expectedAuthor.toLowerCase())) {
    throw new Error("Configure a Director identity distinct from the expected draft author");
  }
  const env = commandEnvironment();
  const current = await query(`SELECT row_to_json(ready) FROM (
    SELECT d.repo, d.pr_number, d.head_sha, r.board_done_at
    FROM ready_dispatches d LEFT JOIN ready_merge_receipts r USING (ticket_id)
    WHERE d.ticket_id = :'ticket'::bigint AND d.status = 'waiting_merge'
  ) ready;`, { ticket: ticketId }, env);
  if (!current) return { status: "no_draft" };
  const handoff = JSON.parse(current);
  if (handoff.repo !== job.repo) return { status: "stale_dispatch" };
  if (handoff.board_done_at) return { status: "already_done" };

  return completeReadyTicket({ ...job, ticketId, prNumber: handoff.pr_number, headSha: handoff.head_sha,
    directors: config.directors }, {
    readPull: async (repo, pr) => JSON.parse(await run("gh", ["api", "--hostname", "github.com", "--method", "GET",
      `repos/${repo}/pulls/${pr}`], { env })),
    recordMerge: async (expected, proof) => (await query(`SELECT record_ready_merge(
      :'ticket'::bigint, :'repo', :'number'::integer, :'head', :'merge', :'actor', :'at'::timestamptz
    );`, { ticket: expected.ticketId, repo: expected.repo, number: expected.prNumber, head: proof.headSha,
      merge: proof.mergeCommitSha, actor: proof.mergedBy, at: proof.mergedAt }, env)) === "t",
    moveBoardDone: (id) => moveVikunjaDone({ ...config.board, token: process.env.VIKUNJA_DONE_TOKEN, ticketId: id }),
    recordDone: async (id, sha) => (await query("SELECT mark_ready_board_done(:'ticket'::bigint, :'sha');",
      { ticket: id, sha }, env)) === "t",
  });
}

main().then((result) => process.stdout.write(`${JSON.stringify(result)}\n`)).catch((error) => {
  process.stderr.write(`${error.name}: ${error.message}\n`);
  process.exitCode = 1;
});
