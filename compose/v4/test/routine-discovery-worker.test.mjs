import assert from "node:assert/strict";
import test from "node:test";
import { runInboundDiscovery } from "../routine-discovery-worker.mjs";

const settings = { companyId: "company", routineId: "routine", repoName: "fixture/canary" };

function fixture() {
  const calls = [];
  const runs = new Map(); // idempotencyKey -> run, mirrors Paperclip's own dedup
  let nextRunId = 1;
  return {
    calls,
    options: {
      ...settings,
      isEligible: async () => true,
      board: async (path, method, body) => {
        if (path === "routines/routine") {
          return { id: "routine", companyId: "company", concurrencyPolicy: "always_enqueue" };
        }
        if (path === "routines/routine/run" && method === "POST") {
          calls.push(["run", body]);
          if (!runs.has(body.idempotencyKey)) {
            runs.set(body.idempotencyKey, { id: `run-${nextRunId++}`, linkedIssueId: `issue-${nextRunId}` });
          }
          return runs.get(body.idempotencyKey);
        }
        throw new Error(`Unexpected board request: ${path}`);
      },
    },
  };
}

const head1 = "1".repeat(40);
const head2 = "2".repeat(40);

test("triggers one idempotent Routine run per discovered PR head", async () => {
  const { options, calls } = fixture();
  options.listRequestedPrs = async () => [7, 9];
  options.prHead = async (pr) => (pr === 7 ? head1 : head2);
  const triggered = await runInboundDiscovery(options);
  assert.deepEqual(triggered.map((row) => row.pr_number), [7, 9]);
  assert.equal(triggered[0].head_sha, head1);
  assert.deepEqual(calls.map((call) => call[1].idempotencyKey),
    [`pr-fixture/canary-7-${head1}`, `pr-fixture/canary-9-${head2}`]);
});

test("replaying the same PR head reuses the same Routine run (native idempotency)", async () => {
  const { options, calls } = fixture();
  options.listRequestedPrs = async () => [7];
  options.prHead = async () => head1;
  const first = await runInboundDiscovery(options);
  const second = await runInboundDiscovery(options);
  assert.equal(first[0].run_id, second[0].run_id);
  assert.equal(first[0].linked_issue_id, second[0].linked_issue_id);
  assert.equal(calls.length, 2);
  assert.equal(calls[0][1].idempotencyKey, calls[1][1].idempotencyKey);
});

test("an audit tick hands the linked run to the auditor and reports its result", async () => {
  const { options } = fixture();
  options.listRequestedPrs = async () => [7];
  options.prHead = async () => head1;
  const audited = [];
  options.auditRun = async (runId, expected) => {
    audited.push([runId, expected]);
    return "published";
  };
  const [row] = await runInboundDiscovery(options);
  assert.deepEqual(audited, [[row.run_id, { pr_number: 7, head_sha: head1 }]]);
  assert.equal(row.audit_status, "published");
});

test("skips a PR whose head cannot be read without failing the others", async () => {
  const { options, calls } = fixture();
  options.listRequestedPrs = async () => [7, 9];
  options.prHead = async (pr) => {
    if (pr === 7) throw new Error("gh pr view failed");
    return head2;
  };
  const triggered = await runInboundDiscovery(options);
  assert.deepEqual(triggered.map((row) => row.pr_number), [9]);
  assert.equal(calls.length, 1);
});

test("skips a PR reporting a malformed head SHA", async () => {
  const { options, calls } = fixture();
  options.listRequestedPrs = async () => [7];
  options.prHead = async () => "not-a-sha";
  const triggered = await runInboundDiscovery(options);
  assert.deepEqual(triggered, []);
  assert.equal(calls.length, 0);
});

test("refuses a potentially truncated review search", async () => {
  const { options, calls } = fixture();
  options.listRequestedPrs = async () => Array.from({ length: 1000 }, (_, index) => index + 1);
  options.prHead = async () => head1;
  await assert.rejects(runInboundDiscovery(options), /search.*limit/i);
  assert.deepEqual(calls, []);
});

test("rejects a Routine from another company", async () => {
  const { options, calls } = fixture();
  const board = options.board;
  options.board = async (...args) =>
    args[0] === "routines/routine" ? { id: "routine", companyId: "other" } : board(...args);
  options.listRequestedPrs = async () => [7];
  options.prHead = async () => head1;
  await assert.rejects(runInboundDiscovery(options), /Unexpected Routine company/);
  assert.equal(calls.length, 0);
});

test("reconciles a published exact-head dossier even after the review request disappears", async () => {
  const { options, calls } = fixture();
  const originalBoard = options.board;
  const issueId = "38a76334-96d1-4746-97b1-7cfadfc068a7";
  const path = `issues/${issueId}`;
  const run = { id: "old-run", source: "manual", linkedIssueId: issueId,
    triggerPayload: { repo: settings.repoName, pr_number: 7, head_sha: head1 } };
  const issue = { id: issueId, companyId: "company", originKind: "routine_execution",
    originId: "routine", originRunId: run.id, status: "todo" };
  const newer = Array.from({ length: 200 }, (_, index) => ({
    id: `11111111-1111-4111-8111-${String(index).padStart(12, "0")}`, source: "schedule",
  }));
  options.board = async (route, method, body) => {
    if (route === "routines/routine/runs?limit=200") return newer;
    if (route === `routines/routine/runs?limit=200&before=${newer[199].id}`) return [run];
    if (route === `${path}/documents`) return [{ key: "audit" }];
    if (route !== path) return originalBoard(route, method, body);
    if (method === "PATCH") {
      calls.push(["close", body.status]);
      issue.status = body.status;
    }
    return issue;
  };
  options.listRequestedPrs = async () => [];
  options.prHead = async () => head1;
  const review = { id: 42, user: { login: "director", type: "User" }, state: "APPROVED",
    commit_id: head1, submitted_at: "2026-11-19T00:00:00Z" };
  options.reviewer = "Director";
  options.prReviews = async () => [
    { ...review, state: "PENDING" },
    { ...review, commit_id: head2 },
    { ...review, user: { login: "other", type: "User" } },
    { ...review, user: { login: "director", type: "Bot" } },
  ];
  await runInboundDiscovery(options);
  assert.deepEqual(calls, []);
  options.prReviews = async () => [review];
  await runInboundDiscovery(options);
  await runInboundDiscovery(options);
  assert.deepEqual(calls, [["close", "done"]]);
});

test("a changed head supersedes its old dossier instead of closing it for an old review", async () => {
  const { options, calls } = fixture();
  const originalBoard = options.board;
  const issueId = "38a76334-96d1-4746-97b1-7cfadfc068a7";
  const path = `issues/${issueId}`;
  const run = { id: "old-run", source: "manual", linkedIssueId: issueId,
    triggerPayload: { repo: settings.repoName, pr_number: 7, head_sha: head1 } };
  const issue = { id: issueId, companyId: "company", originKind: "routine_execution",
    originId: "routine", originRunId: run.id, status: "backlog" };
  options.board = async (route, method, body) => {
    if (route === "routines/routine/runs?limit=200") return [run];
    if (route === `${path}/documents`) return [{ key: "audit" }];
    if (route === `${path}/documents/audit-status` && method === "PUT") {
      calls.push(["superseded", body]);
      return {};
    }
    if (route === path && method === "PATCH") {
      calls.push(["close", body.status]);
      return { ...issue, ...body };
    }
    if (route === path) return issue;
    return originalBoard(route, method, body);
  };
  options.listRequestedPrs = async () => [];
  options.prHead = async () => head2;
  options.reviewer = "Director";
  options.prReviews = async () => { throw new Error("a stale dossier must not be closed by review"); };
  await runInboundDiscovery(options);
  assert.equal(calls[0][0], "superseded");
  assert.match(calls[0][1].body, new RegExp(head2));
  assert.deepEqual(calls[1], ["close", "cancelled"]);
});

test("a head that changes while reading review is superseded, not closed", async () => {
  const { options, calls } = fixture();
  const originalBoard = options.board;
  const issueId = "38a76334-96d1-4746-97b1-7cfadfc068a7";
  const path = `issues/${issueId}`;
  const run = { id: "old-run", source: "manual", linkedIssueId: issueId,
    triggerPayload: { repo: settings.repoName, pr_number: 7, head_sha: head1 } };
  const issue = { id: issueId, companyId: "company", originKind: "routine_execution",
    originId: "routine", originRunId: run.id, status: "backlog" };
  options.board = async (route, method, body) => {
    if (route === "routines/routine/runs?limit=200") return [run];
    if (route === `${path}/documents`) return [{ key: "audit" }];
    if (route === `${path}/documents/audit-status` && method === "PUT") {
      calls.push(["superseded", body]);
      return {};
    }
    if (route === path && method === "PATCH") {
      calls.push(["close", body.status]);
      return { ...issue, ...body };
    }
    if (route === path) return issue;
    return originalBoard(route, method, body);
  };
  let reads = 0;
  options.listRequestedPrs = async () => [];
  options.prHead = async () => (++reads === 1 ? head1 : head2);
  options.reviewer = "Director";
  options.prReviews = async () => [{ user: { type: "User", login: "Director" }, state: "APPROVED",
    commit_id: head1, submitted_at: "2026-01-01T00:00:00Z" }];
  await runInboundDiscovery(options);
  assert.equal(calls[0][0], "superseded");
  assert.deepEqual(calls[1], ["close", "cancelled"]);
});

test("a PR becoming ineligible while reading its review is not closed", async () => {
  const { options, calls } = fixture();
  const originalBoard = options.board;
  const issueId = "38a76334-96d1-4746-97b1-7cfadfc068a7";
  const path = `issues/${issueId}`;
  const run = { id: "old-run", source: "manual", linkedIssueId: issueId,
    triggerPayload: { repo: settings.repoName, pr_number: 7, head_sha: head1 } };
  const issue = { id: issueId, companyId: "company", originKind: "routine_execution",
    originId: "routine", originRunId: run.id, status: "backlog" };
  options.board = async (route, method, body) => {
    if (route === "routines/routine/runs?limit=200") return [run];
    if (route === `${path}/documents`) return [{ key: "audit" }];
    if (route === path && method === "PATCH") {
      calls.push(["close", body.status]);
      return { ...issue, ...body };
    }
    if (route === path) return issue;
    return originalBoard(route, method, body);
  };
  let checks = 0;
  options.listRequestedPrs = async () => [];
  options.prHead = async () => head1;
  options.isEligible = async () => ++checks === 1;
  options.reviewer = "Director";
  options.prReviews = async () => [{ user: { type: "User", login: "Director" }, state: "APPROVED",
    commit_id: head1, submitted_at: "2026-01-01T00:00:00Z" }];
  await runInboundDiscovery(options);
  assert.deepEqual(calls, []);
});

test("a PR becoming ineligible during supersession gets no issue changes", async () => {
  const { options, calls } = fixture();
  const originalBoard = options.board;
  const issueId = "38a76334-96d1-4746-97b1-7cfadfc068a7";
  const path = `issues/${issueId}`;
  const run = { id: "old-run", source: "manual", linkedIssueId: issueId,
    triggerPayload: { repo: settings.repoName, pr_number: 7, head_sha: head1 } };
  const issue = { id: issueId, companyId: "company", originKind: "routine_execution",
    originId: "routine", originRunId: run.id, status: "backlog" };
  options.board = async (route, method, body) => {
    if (route === "routines/routine/runs?limit=200") return [run];
    if (route === `${path}/documents`) return [{ key: "audit" }];
    if (route === `${path}/documents/audit-status` && method === "PUT") calls.push(["superseded", body]);
    else if (route === path && method === "PATCH") calls.push(["close", body.status]);
    else if (route === path) return issue;
    else return originalBoard(route, method, body);
    return {};
  };
  let checks = 0;
  options.listRequestedPrs = async () => [];
  options.prHead = async () => head2;
  options.isEligible = async () => ++checks === 1;
  options.reviewer = "Director";
  options.prReviews = async () => [];
  await runInboundDiscovery(options);
  assert.deepEqual(calls, []);
});
