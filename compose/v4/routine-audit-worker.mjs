// Process one manually triggered Paperclip Routine run without a model or GitHub write.
import { execFile } from "node:child_process";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";
import { auditPr } from "../../coding-harness/audit-pr.js";
import { findRoutineRun } from "./routine-runs.mjs";

const sha = /^[a-f0-9]{40}$/;
const uuid = /^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const repoSlug = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

export async function runRoutineAudit({
  board, companyId, routineId, runId, repoPath, repoName, audit, currentHead, expected,
}) {
  const routine = await board(`routines/${routineId}`);
  if (routine.id !== routineId || routine.companyId !== companyId) throw new Error("Unexpected Routine company");
  const run = await findRoutineRun(board, routineId, runId);
  const payload = run?.triggerPayload;
  if (!run?.linkedIssueId || run.source !== "manual" || payload?.repo !== repoName || !Number.isSafeInteger(payload.pr_number)
      || payload.pr_number < 1 || typeof payload.head_sha !== "string" || !sha.test(payload.head_sha)) {
    throw new Error("Expected linked Routine run with the configured repository, PR number and exact head");
  }
  if (expected && (payload.pr_number !== expected.pr_number || payload.head_sha !== expected.head_sha)) {
    throw new Error("Routine run does not match the discovered PR head");
  }
  const path = `issues/${run.linkedIssueId}`;
  const issue = await board(path);
  if (issue.id !== run.linkedIssueId || issue.companyId !== companyId || issue.originKind !== "routine_execution"
      || issue.originId !== routineId || issue.originRunId !== runId) {
    throw new Error("Unexpected linked Routine issue");
  }
  const { head_sha: head, pr_number: pr } = payload;
  const documents = () => board(`${path}/documents`);
  const existing = await documents();
  if (existing.some((doc) => doc.key === "audit-status")) return "superseded";
  const observed = await currentHead(pr);
  if (typeof observed !== "string" || !sha.test(observed)) throw new Error("Could not verify current PR head");
  if (existing.some((doc) => doc.key === "audit")) {
    if (observed === head) return "already_published";
    await board(`${path}/documents/audit-status`, "PUT", {
      title: "Audit superseded",
      format: "markdown",
      body: `The audit for \`${head}\` is historical. The PR moved to \`${observed}\`; run a new exact-head audit.\n`,
    });
    return "superseded";
  }
  if (observed !== head) return "stale";
  const result = await audit({ repo_path: repoPath, pr_number: pr, head_sha: head });
  if ((await documents()).some((doc) => doc.key === "audit")) return "already_published";
  if (await currentHead(pr) !== head) return "stale";
  const report = JSON.stringify(result, null, 2).split("\n").map((line) => `    ${line}`).join("\n");
  await board(`${path}/documents/audit`, "PUT", {
    format: "markdown",
    body: `Audit for \`${repoName}#${pr}\` at \`${head}\`\n\n${report}\n`,
  });
  return "published";
}

async function main() {
  const [origin, companyId, routineId, runId, repoName, repoPath, ...extra] = process.argv.slice(2);
  if (!/^http:\/\/127\.0\.0\.1:[0-9]+$/.test(origin) || ![companyId, routineId, runId].every((id) => uuid.test(id))
      || !repoSlug.test(repoName) || !repoPath || extra.length) {
    throw new Error("Usage: routine-audit-worker.mjs BOARD_ORIGIN COMPANY_ID ROUTINE_ID RUN_ID OWNER/REPO LOCAL_CLONE");
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
  const currentHead = await auditCloneHead(repoName, repoPath);
  console.log(await runRoutineAudit({ board, companyId, routineId, runId, repoPath, repoName, audit: auditPr, currentHead }));
}

export async function auditCloneHead(repoName, repoPath) {
  const exec = promisify(execFile);
  process.env.GIT_TERMINAL_PROMPT = "0";
  const git = async (...args) => (await exec("git", args, { cwd: repoPath, timeout: 15_000 })).stdout.trim();
  const remote = await git("remote", "get-url", "--all", "origin");
  if (remote !== `https://github.com/${repoName}.git`) throw new Error("Clone origin does not match the configured repository");
  return async (pr) => {
    const ref = `refs/pull/${pr}/head`;
    const [head, actualRef] = (await git("ls-remote", "--exit-code", "origin", ref)).split(/\s+/);
    if (actualRef !== ref || !sha.test(head)) throw new Error("Could not verify current PR head");
    return head;
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
