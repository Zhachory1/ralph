// Dispatch inbound discovery and exact-head audits through one native Routine owner.
import { execFile } from "node:child_process";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";
import { auditPr } from "../../coding-harness/audit-pr.js";
import { auditCloneHead, runRoutineAudit } from "./routine-audit-worker.mjs";
import { runInboundDiscovery } from "./routine-discovery-worker.mjs";
import { findRoutineRun } from "./routine-runs.mjs";
import { createPrEligibility, listAssignedPrs, parseAssignees } from "./github-pr-eligibility.mjs";

const uuid = /^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const sha = /^[a-f0-9]{40}$/;
const repoSlug = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const login = /^[a-z0-9-]+$/i;

export async function runNativeInbound(options) {
  const { board, companyId, routineId, agentId, heartbeatRunId, repoName, repoPath,
    listRequestedPrs, prHead, currentHead, audit, prReviews, reviewer, isEligible } = options;
  if (typeof isEligible !== "function") throw new Error("GitHub PR eligibility check is required");
  const agent = await board("agents/me");
  const heartbeat = await board(`heartbeat-runs/${heartbeatRunId}`);
  if (agent.id !== agentId || agent.companyId !== companyId || heartbeat.id !== heartbeatRunId
      || heartbeat.companyId !== companyId || heartbeat.agentId !== agentId || heartbeat.status !== "running"
      || !uuid.test(heartbeat.issueId ?? "")) {
    throw new Error("Unexpected native run context");
  }
  if (agent.runtimeConfig?.heartbeat?.maxConcurrentRuns !== 1) {
    throw new Error("Native worker requires one concurrent run");
  }
  const issue = await board(`issues/${heartbeat.issueId}`);
  const routine = await board(`routines/${routineId}`);
  if (issue.id !== heartbeat.issueId || issue.companyId !== companyId || issue.assigneeAgentId !== agentId
      || issue.originKind !== "routine_execution" || issue.originId !== routineId
      || routine.id !== routineId || routine.companyId !== companyId || routine.assigneeAgentId !== agentId
      || routine.status !== "active" || routine.concurrencyPolicy !== "always_enqueue") {
    throw new Error("Unexpected native Routine context");
  }
  const run = await findRoutineRun(board, routineId, issue.originRunId);
  if (!run || run.companyId !== companyId || run.routineId !== routineId || run.linkedIssueId !== issue.id) {
    throw new Error("Unexpected native Routine run");
  }
  if (["done", "cancelled"].includes(issue.status)) return { kind: "terminal" };
  const operatorTick = run.source === "manual" && run.triggerPayload?.kind === "discovery"
    && !run.createdByRunId && typeof issue.createdByUserId === "string" && issue.createdByUserId.length > 0;
  if (run.source === "schedule" || operatorTick) {
    const resumeRun = async (queued, expected) => {
      if (!uuid.test(queued.id ?? "") || !uuid.test(queued.linkedIssueId ?? "")) {
        throw new Error("Invalid queued Routine run");
      }
      const path = `issues/${queued.linkedIssueId}`;
      const target = await board(path);
      if (target.id !== queued.linkedIssueId || target.companyId !== companyId
          || target.assigneeAgentId !== agentId || target.originKind !== "routine_execution"
          || target.originId !== routineId || target.originRunId !== queued.id) {
        throw new Error("Unexpected queued Routine issue");
      }
      if (target.status !== "backlog") return;
      const documents = await board(`${path}/documents`);
      if (documents.some((doc) => doc.key === "audit" || doc.key === "audit-status")) return;
      if (await isEligible(expected.pr_number, expected.head_sha)) await board(path, "PATCH", { status: "todo" });
    };
    const rows = await runInboundDiscovery({
      board, companyId, routineId, repoName, listRequestedPrs, prHead, prReviews, reviewer, isEligible, resumeRun,
    });
    await board(`issues/${issue.id}`, "PATCH", { status: "done" });
    return { kind: "discovery", count: rows.length };
  }
  if (run.source !== "manual") throw new Error("Unsupported native Routine source");
  const payload = run.triggerPayload;
  if (payload?.repo !== repoName || !Number.isSafeInteger(payload.pr_number) || payload.pr_number < 1
      || typeof payload.head_sha !== "string" || !sha.test(payload.head_sha)
      || run.idempotencyKey !== `pr-${repoName.toLowerCase()}-${payload.pr_number}-${payload.head_sha}`) {
    throw new Error("Invalid native head key");
  }
  if (!uuid.test(run.createdByRunId ?? "")) throw new Error("Missing scheduled run origin");
  const parentHeartbeat = await board(`heartbeat-runs/${run.createdByRunId}`);
  const parentIssue = await board(`issues/${parentHeartbeat.issueId}`);
  const parentRun = await findRoutineRun(board, routineId, parentIssue.originRunId);
  const operatorParent = parentRun?.source === "manual" && parentRun.triggerPayload?.kind === "discovery"
    && !parentRun.createdByRunId && typeof parentIssue.createdByUserId === "string"
    && parentIssue.createdByUserId.length > 0;
  if (parentHeartbeat.id !== run.createdByRunId || parentHeartbeat.companyId !== companyId
      || parentHeartbeat.agentId !== agentId || parentIssue.id !== parentHeartbeat.issueId
      || parentIssue.companyId !== companyId || parentIssue.originKind !== "routine_execution"
      || parentIssue.originId !== routineId || !parentRun || parentRun.companyId !== companyId
      || parentRun.routineId !== routineId || parentRun.linkedIssueId !== parentIssue.id
      || (parentRun.source !== "schedule" && !operatorParent)) {
    throw new Error("Unexpected scheduled run origin");
  }
  if (!await isEligible(payload.pr_number)) {
    await board(`issues/${issue.id}`, "PATCH", { status: "backlog" });
    return { kind: "audit", status: "ineligible" };
  }
  if (issue.status === "blocked") {
    const documents = await board(`issues/${issue.id}/documents`);
    if (documents.some((doc) => doc.key === "audit-status")) {
      await board(`issues/${issue.id}`, "PATCH", { status: "cancelled" });
      return { kind: "audit", status: "superseded" };
    }
    if (documents.some((doc) => doc.key === "audit")) {
      await board(`issues/${issue.id}`, "PATCH", { status: "backlog" });
      return { kind: "audit", status: "already_published" };
    }
  }
  const status = await runRoutineAudit({
    board, companyId, routineId, runId: run.id, repoName, repoPath, audit, currentHead, isEligible,
  });
  if (status === "stale" || status === "superseded") {
    if (!await isEligible(payload.pr_number)) {
      await board(`issues/${issue.id}`, "PATCH", { status: "backlog" });
      return { kind: "audit", status: "ineligible" };
    }
    await board(`issues/${issue.id}`, "PATCH", { status: "cancelled" });
  } else {
    await board(`issues/${issue.id}`, "PATCH", { status: "backlog" });
  }
  return { kind: "audit", status };
}

async function main() {
  const [routineId, repoName, repoPath, reviewer, ...extra] = process.argv.slice(2);
  const { PAPERCLIP_API_URL: origin, PAPERCLIP_COMPANY_ID: companyId, PAPERCLIP_AGENT_ID: agentId,
    PAPERCLIP_RUN_ID: heartbeatRunId, PAPERCLIP_API_KEY: token, GH_TOKEN: githubToken } = process.env;
  delete process.env.PAPERCLIP_API_KEY;
  delete process.env.PAPERCLIP_AGENT_PRIVATE_KEY;
  const assignees = parseAssignees(process.env.RALPH_REVIEW_ASSIGNEES);
  if (!/^http:\/\/127\.0\.0\.1:[0-9]+$/.test(origin ?? "")
      || ![companyId, routineId, agentId, heartbeatRunId].every((id) => uuid.test(id ?? ""))
      || !repoSlug.test(repoName ?? "") || !repoPath?.startsWith("/") || !login.test(reviewer ?? "")
      || !token || !githubToken || extra.length) {
    throw new Error("Expected native Paperclip identity, scoped GH_TOKEN, Routine, repository, clone, and reviewer");
  }
  const board = async (path, method = "GET", body) => {
    const response = await fetch(`${origin}/api/${path}`, {
      method,
      headers: { authorization: `Bearer ${token}`, origin, "content-type": "application/json",
        "X-Paperclip-Run-Id": heartbeatRunId },
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: "error",
      signal: AbortSignal.timeout(15_000),
    });
    if (!response.ok) throw new Error(`Paperclip ${method} failed (${response.status})`);
    return response.json();
  };
  const exec = promisify(execFile);
  const gh = async (...args) => (await exec("gh", args, { timeout: 30_000 })).stdout;
  const listRequestedPrs = () => listAssignedPrs(gh, repoName, assignees);
  const prHead = async (pr) => JSON.parse(await gh("pr", "view", String(pr), "-R", repoName, "--json", "headRefOid")).headRefOid;
  const isEligible = createPrEligibility({ assignees, readPr: async (pr) => JSON.parse(await gh(
    "pr", "view", String(pr), "-R", repoName, "--json", "state,isDraft,assignees,headRefOid",
  )) });
  const prReviews = async (pr) => JSON.parse(await gh("api", "--paginate", "--slurp",
    `repos/${repoName}/pulls/${pr}/reviews?per_page=100`)).flat();
  let headReader;
  const currentHead = async (pr) => {
    headReader ??= auditCloneHead(repoName, repoPath);
    return (await headReader)(pr);
  };
  const result = await runNativeInbound({ board, companyId, routineId, agentId, heartbeatRunId,
    repoName, repoPath, reviewer, listRequestedPrs, prHead, prReviews, currentHead, audit: auditPr, isEligible });
  console.log(JSON.stringify(result));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
