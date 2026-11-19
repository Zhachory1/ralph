// Discover PRs requesting this account's review in one allowlisted repo and
// trigger a read-only, idempotent Paperclip Routine run per (repo, pr, head).
// Reuses the Routine's own idempotencyKey dedup (compose/v4/test/paperclip-routine-canary.py)
// instead of a new state store. With --audit, process each returned run serially.
import { execFile } from "node:child_process";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";
import { auditPr } from "../../coding-harness/audit-pr.js";
import { auditCloneHead, runRoutineAudit } from "./routine-audit-worker.mjs";

const sha = /^[a-f0-9]{40}$/;
const uuid = /^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const repoSlug = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const login = /^[\w.@-]+$/;
const submitted = new Set(["APPROVED", "CHANGES_REQUESTED", "COMMENTED"]);

async function reconcileReviews({ board, companyId, routineId, repoName, prReviews, reviewer }) {
  const runs = await board(`routines/${routineId}/runs?limit=100`);
  for (const { id, source, linkedIssueId, triggerPayload: payload } of runs) {
    if (source !== "manual" || typeof linkedIssueId !== "string" || !uuid.test(linkedIssueId)
        || payload?.repo !== repoName || !Number.isSafeInteger(payload.pr_number) || payload.pr_number < 1
        || typeof payload.head_sha !== "string" || !sha.test(payload.head_sha)) continue;
    const path = `issues/${linkedIssueId}`;
    const issue = await board(path);
    if (issue.id !== linkedIssueId || issue.companyId !== companyId || issue.originKind !== "routine_execution"
        || issue.originId !== routineId || issue.originRunId !== id || ["done", "cancelled"].includes(issue.status)) continue;
    if (!(await board(`${path}/documents`)).some((doc) => doc.key === "audit")) continue;
    const reviews = await prReviews(payload.pr_number);
    if (!reviews.some((review) => review.user?.type === "User"
        && review.user.login?.toLowerCase() === reviewer.toLowerCase()
        && review.commit_id === payload.head_sha && review.submitted_at && submitted.has(review.state))) continue;
    await board(path, "PATCH", { status: "done" });
  }
}

export async function runInboundDiscovery({
  board, companyId, routineId, repoName, listRequestedPrs, prHead, auditRun, prReviews, reviewer,
}) {
  const routine = await board(`routines/${routineId}`);
  if (routine.id !== routineId || routine.companyId !== companyId) throw new Error("Unexpected Routine company");
  if (auditRun && routine.concurrencyPolicy !== "always_enqueue") {
    throw new Error("An audit tick requires an always_enqueue Routine to keep PR heads separate");
  }
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
    const row = { pr_number, head_sha, run_id: run.id, linked_issue_id: run.linkedIssueId };
    if (auditRun) {
      try {
        row.audit_status = await auditRun(run.id, { pr_number, head_sha });
      } catch (error) {
        console.error(`  audit ${repoName}#${pr_number}: ${error.message}`);
        row.audit_status = "failed";
      }
    }
    triggered.push(row);
  }
  if (prReviews) await reconcileReviews({ board, companyId, routineId, repoName, prReviews, reviewer });
  return triggered;
}

async function main() {
  const argv = process.argv.slice(2);
  const reconcile = argv.at(-1) === "--reconcile";
  if (reconcile) argv.pop();
  const [origin, companyId, routineId, repoName, ...options] = argv;
  let repoPath;
  if (options[0] === "--audit") {
    options.shift();
    repoPath = options.shift();
    if (!repoPath || repoPath.startsWith("-")) throw new Error("--audit requires a local clone path");
  }
  const [reviewer, ...extra] = options;
  const reviewLogin = reviewer || "@me";
  if (!/^http:\/\/127\.0\.0\.1:[0-9]+$/.test(origin) || ![companyId, routineId].every((id) => uuid.test(id))
      || !repoSlug.test(repoName) || !login.test(reviewLogin) || extra.length
      || (reconcile && (!reviewer || !/^[a-z0-9-]+$/i.test(reviewer)))) {
    throw new Error("Usage: routine-discovery-worker.mjs ORIGIN COMPANY_ID ROUTINE_ID OWNER/REPO [--audit LOCAL_CLONE] [REVIEWER] [--reconcile]");
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
  const currentHead = repoPath && await auditCloneHead(repoName, repoPath);
  const auditRun = currentHead && ((runId, expected) => runRoutineAudit({
    board, companyId, routineId, runId, repoName, repoPath, expected, audit: auditPr, currentHead,
  }));
  const prReviews = reconcile && (async (pr) => JSON.parse(await gh("api", "--paginate", "--slurp",
    `repos/${repoName}/pulls/${pr}/reviews?per_page=100`)).flat());
  const rows = await runInboundDiscovery({
    board, companyId, routineId, repoName, listRequestedPrs, prHead, auditRun, prReviews, reviewer,
  });
  console.log(JSON.stringify(rows));
  if (rows.some((row) => row.audit_status === "failed")) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
