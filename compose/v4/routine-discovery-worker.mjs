// Discover PRs requesting this account's review in one allowlisted repo and
// trigger a read-only, idempotent Paperclip Routine run per (repo, pr, head).
// Reuses the Routine's own idempotencyKey dedup (compose/v4/test/paperclip-routine-canary.py)
// instead of a new state store; runRoutineAudit (routine-audit-worker.mjs) does the audit later.
import { execFile } from "node:child_process";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";

const sha = /^[a-f0-9]{40}$/;
const uuid = /^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const repoSlug = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const login = /^[\w.@-]+$/;

export async function runInboundDiscovery({ board, companyId, routineId, repoName, listRequestedPrs, prHead }) {
  const routine = await board(`routines/${routineId}`);
  if (routine.id !== routineId || routine.companyId !== companyId) throw new Error("Unexpected Routine company");
  const numbers = await listRequestedPrs();
  const triggered = [];
  for (const pr_number of numbers) {
    if (!Number.isSafeInteger(pr_number) || pr_number < 1) continue;
    let head_sha;
    try {
      head_sha = await prHead(pr_number);
    } catch (error) {
      console.error(`  skip ${repoName}#${pr_number}: could not read its head SHA (${error.message})`);
      continue;
    }
    if (typeof head_sha !== "string" || !sha.test(head_sha)) {
      console.error(`  skip ${repoName}#${pr_number}: could not verify its head SHA`);
      continue;
    }
    const run = await board(`routines/${routineId}/run`, "POST", {
      source: "manual",
      idempotencyKey: `pr-${repoName.toLowerCase()}-${pr_number}-${head_sha}`,
      payload: { repo: repoName, pr_number, head_sha },
    });
    triggered.push({ pr_number, head_sha, run_id: run.id, linked_issue_id: run.linkedIssueId });
  }
  return triggered;
}

async function main() {
  const [origin, companyId, routineId, repoName, reviewer, ...extra] = process.argv.slice(2);
  const reviewLogin = reviewer || "@me";
  if (!/^http:\/\/127\.0\.0\.1:[0-9]+$/.test(origin) || ![companyId, routineId].every((id) => uuid.test(id))
      || !repoSlug.test(repoName) || !login.test(reviewLogin) || extra.length) {
    throw new Error("Usage: routine-discovery-worker.mjs BOARD_ORIGIN COMPANY_ID ROUTINE_ID OWNER/REPO [REVIEWER=@me]");
  }
  const token = process.env.PAPERCLIP_API_KEY;
  delete process.env.PAPERCLIP_API_KEY;
  if (!token) throw new Error("Set a temporary scoped PAPERCLIP_API_KEY");
  const board = async (path, method = "GET", body) => {
    const response = await fetch(`${origin}/api/${path}`, {
      method,
      headers: { authorization: `Bearer ${token}`, origin, "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: "error",
      signal: AbortSignal.timeout(15_000),
    });
    if (!response.ok) throw new Error(`Paperclip ${method} failed (${response.status})`);
    return response.json();
  };
  const exec = promisify(execFile);
  const gh = async (...args) => (await exec("gh", args, { timeout: 30_000 })).stdout;
  const listRequestedPrs = async () => {
    const out = await gh("search", "prs", "--review-requested", reviewLogin, "--state", "open",
      "-R", repoName, "--json", "number", "--limit", "100");
    return JSON.parse(out).map((row) => row.number);
  };
  const prHead = async (pr_number) => {
    const out = await gh("pr", "view", String(pr_number), "-R", repoName, "--json", "headRefOid");
    return JSON.parse(out).headRefOid;
  };
  console.log(JSON.stringify(
    await runInboundDiscovery({ board, companyId, routineId, repoName, listRequestedPrs, prHead })));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
