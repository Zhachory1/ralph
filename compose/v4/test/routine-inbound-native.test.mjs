import assert from "node:assert/strict";
import test from "node:test";
import { runNativeInbound } from "../routine-inbound-native.mjs";

const companyId = "11111111-1111-4111-8111-111111111111";
const agentId = "22222222-2222-4222-8222-222222222222";
const routineId = "33333333-3333-4333-8333-333333333333";
const heartbeatRunId = "44444444-4444-4444-8444-444444444444";
const routineRunId = "55555555-5555-4555-8555-555555555555";
const issueId = "66666666-6666-4666-8666-666666666666";
const parentHeartbeatId = "77777777-7777-4777-8777-777777777777";
const parentIssueId = "88888888-8888-4888-8888-888888888888";
const parentRoutineRunId = "99999999-9999-4999-8999-999999999999";
const repoName = "fixture/canary";
const head = "a".repeat(40);

function fixture(source = "schedule") {
  const writes = [];
  const documents = [];
  const queuedIssues = new Map();
  const issue = {
    id: issueId, companyId, assigneeAgentId: agentId, status: "in_progress",
    originKind: "routine_execution", originId: routineId, originRunId: routineRunId,
  };
  const routine = { id: routineId, companyId, assigneeAgentId: agentId, status: "active",
    concurrencyPolicy: "always_enqueue" };
  const agent = { id: agentId, companyId, runtimeConfig: { heartbeat: { maxConcurrentRuns: 1 } } };
  const run = { id: routineRunId, companyId, routineId, linkedIssueId: issueId, source,
    ...(source === "manual" ? { createdByRunId: parentHeartbeatId,
      idempotencyKey: `pr-${repoName}-7-${head}` } : {}),
    triggerPayload: source === "manual" ? { repo: repoName, pr_number: 7, head_sha: head } : null };
  const parentIssue = { id: parentIssueId, companyId, originKind: "routine_execution",
    originId: routineId, originRunId: parentRoutineRunId };
  const parentRun = { id: parentRoutineRunId, companyId, routineId, linkedIssueId: parentIssueId, source: "schedule" };
  const board = async (path, method = "GET", body) => {
    if (path === "agents/me") return agent;
    if (path === `heartbeat-runs/${heartbeatRunId}`) return { id: heartbeatRunId, companyId, agentId, issueId, status: "running" };
    if (path === `heartbeat-runs/${parentHeartbeatId}`) return { id: parentHeartbeatId, companyId, agentId, issueId: parentIssueId };
    if (path === `routines/${routineId}`) return routine;
    if (path === `routines/${routineId}/runs?limit=200`) return source === "manual" ? [run, parentRun] : [run];
    if (path === `routines/${routineId}/run` && method === "POST") {
      writes.push([path, method, body]);
      const suffix = String(writes.length).padStart(12, "0");
      const id = `aaaaaaaa-aaaa-4aaa-8aaa-${suffix}`;
      const linkedIssueId = `bbbbbbbb-bbbb-4bbb-8bbb-${suffix}`;
      queuedIssues.set(linkedIssueId, { id: linkedIssueId, companyId, assigneeAgentId: agentId,
        status: "todo", originKind: "routine_execution", originId: routineId, originRunId: id });
      return { id, linkedIssueId };
    }
    if (path.startsWith("issues/") && method === "GET" && queuedIssues.has(path.slice(7))) {
      return queuedIssues.get(path.slice(7));
    }
    if (path === `issues/${parentIssueId}` && method === "GET") return parentIssue;
    if (path === `issues/${issueId}/documents` && method === "GET") return documents;
    if (path === `issues/${issueId}/documents/audit` && method === "PUT") {
      writes.push([path, method, body]);
      documents.push({ key: "audit" });
      return {};
    }
    if (path === `issues/${issueId}` && method === "GET") return issue;
    if (path === `issues/${issueId}` && method === "PATCH") {
      writes.push([path, method, body]);
      return { ...issue, ...body };
    }
    throw new Error(`Unexpected board request: ${method} ${path}`);
  };
  return {
    writes, documents, issue, routine, agent, run, parentIssue, parentRun,
    options: {
      board, companyId, routineId, agentId, heartbeatRunId, repoName, repoPath: "/fixture",
      listRequestedPrs: async () => [7, 8], prHead: async () => head,
      currentHead: async () => head, audit: async () => ({ tests_passed: true }),
      isEligible: async () => true,
    },
  };
}

test("a scheduled native run queues each exact head and completes its own tick", async () => {
  const { options, writes } = fixture();
  const result = await runNativeInbound(options);
  assert.equal(result.kind, "discovery");
  const queued = writes.filter(([path]) => path === `routines/${routineId}/run`);
  assert.deepEqual(queued.map(([, , body]) => body.idempotencyKey),
    [`pr-fixture/canary-7-${head}`, `pr-fixture/canary-8-${head}`]);
  assert.deepEqual(writes.at(-1), [`issues/${issueId}`, "PATCH", { status: "done" }]);
});

test("a scheduled tick does not queue an ineligible PR", async () => {
  const f = fixture();
  f.options.isEligible = async () => false;
  assert.deepEqual(await runNativeInbound(f.options), { kind: "discovery", count: 0 });
  assert.deepEqual(f.writes, [[`issues/${issueId}`, "PATCH", { status: "done" }]]);
});

test("an eligible parked audit is resumed without a new Routine run", async () => {
  const f = fixture();
  const parkedIssueId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const parkedRunId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  const original = f.options.board;
  const parked = { id: parkedIssueId, companyId, assigneeAgentId: agentId, status: "backlog",
    originKind: "routine_execution", originId: routineId, originRunId: parkedRunId };
  f.options.listRequestedPrs = async () => [7];
  f.options.board = async (path, method = "GET", body) => {
    if (path === `routines/${routineId}/run` && method === "POST") {
      f.writes.push([path, method, body]);
      return { id: parkedRunId, linkedIssueId: parkedIssueId };
    }
    if (path === `issues/${parkedIssueId}` && method === "GET") return parked;
    if (path === `issues/${parkedIssueId}/documents` && method === "GET") return [];
    if (path === `issues/${parkedIssueId}` && method === "PATCH") {
      f.writes.push([path, method, body]);
      return { ...parked, ...body };
    }
    return original(path, method, body);
  };
  await runNativeInbound(f.options);
  assert.deepEqual(f.writes.slice(1), [
    [`issues/${parkedIssueId}`, "PATCH", { status: "todo" }],
    [`issues/${issueId}`, "PATCH", { status: "done" }],
  ]);
});

test("a parked audit is not resumed if eligibility changes after discovery", async () => {
  const f = fixture();
  const parkedIssueId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const parkedRunId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  const original = f.options.board;
  const parked = { id: parkedIssueId, companyId, assigneeAgentId: agentId, status: "backlog",
    originKind: "routine_execution", originId: routineId, originRunId: parkedRunId };
  let checks = 0;
  f.options.listRequestedPrs = async () => [7];
  f.options.isEligible = async () => ++checks === 1;
  f.options.board = async (path, method = "GET", body) => {
    if (path === `routines/${routineId}/run` && method === "POST") {
      f.writes.push([path, method, body]);
      return { id: parkedRunId, linkedIssueId: parkedIssueId };
    }
    if (path === `issues/${parkedIssueId}` && method === "GET") return parked;
    if (path === `issues/${parkedIssueId}/documents` && method === "GET") return [];
    if (path === `issues/${parkedIssueId}` && method === "PATCH") {
      f.writes.push([path, method, body]);
      return { ...parked, ...body };
    }
    return original(path, method, body);
  };
  await runNativeInbound(f.options);
  assert.deepEqual(f.writes.slice(1), [[`issues/${issueId}`, "PATCH", { status: "done" }]]);
});

test("native review requires an eligibility check", async () => {
  const f = fixture();
  delete f.options.isEligible;
  await assert.rejects(runNativeInbound(f.options), /eligibility.*required/i);
  assert.deepEqual(f.writes, []);
});

test("an operator can trigger one manual discovery tick", async () => {
  const f = fixture("manual");
  f.run.createdByRunId = null;
  f.run.triggerPayload = { kind: "discovery" };
  f.issue.createdByUserId = "fixture-director";
  const result = await runNativeInbound(f.options);
  assert.equal(result.kind, "discovery");
  assert.equal(f.writes.filter(([path]) => path === `routines/${routineId}/run`).length, 2);
});

test("a native head run writes its dossier and waits for human review", async () => {
  const { options, writes } = fixture("manual");
  const result = await runNativeInbound(options);
  assert.deepEqual(result, { kind: "audit", status: "published" });
  assert.equal(writes[0][0], `issues/${issueId}/documents/audit`);
  assert.deepEqual(writes[1], [`issues/${issueId}`, "PATCH", { status: "backlog" }]);
});

test("a stale head is cancelled without creating a dossier", async () => {
  const f = fixture("manual");
  f.options.currentHead = async () => "b".repeat(40);
  assert.deepEqual(await runNativeInbound(f.options), { kind: "audit", status: "stale" });
  assert.deepEqual(f.writes, [[`issues/${issueId}`, "PATCH", { status: "cancelled" }]]);
});

test("a stale task becoming ineligible is parked rather than cancelled", async () => {
  const f = fixture("manual");
  let checks = 0;
  f.options.isEligible = async () => ++checks <= 2;
  f.options.currentHead = async () => "b".repeat(40);
  assert.deepEqual(await runNativeInbound(f.options), { kind: "audit", status: "ineligible" });
  assert.deepEqual(f.writes, [[`issues/${issueId}`, "PATCH", { status: "backlog" }]]);
});

test("an audit failure does not complete or publish the issue", async () => {
  const f = fixture("manual");
  f.options.audit = async () => { throw new Error("fixture audit failed"); };
  await assert.rejects(runNativeInbound(f.options), /fixture audit failed/);
  assert.deepEqual(f.writes, []);
});

test("an ineligible PR is parked without starting its audit", async () => {
  const f = fixture("manual");
  f.options.isEligible = async () => false;
  f.options.audit = async () => { throw new Error("audit must not start"); };
  assert.deepEqual(await runNativeInbound(f.options), { kind: "audit", status: "ineligible" });
  assert.deepEqual(f.writes, [[`issues/${issueId}`, "PATCH", { status: "backlog" }]]);
});

test("a PR becoming ineligible during audit gets no dossier", async () => {
  const f = fixture("manual");
  let checks = 0;
  let audited = false;
  f.options.isEligible = async () => ++checks <= 2;
  f.options.audit = async () => { audited = true; return { tests_passed: true }; };
  assert.deepEqual(await runNativeInbound(f.options), { kind: "audit", status: "ineligible" });
  assert.equal(audited, true);
  assert.deepEqual(f.writes, [[`issues/${issueId}`, "PATCH", { status: "backlog" }]]);
});

test("an eligibility lookup failure does not publish or complete work", async () => {
  const f = fixture("manual");
  f.options.isEligible = async () => { throw new Error("GitHub unavailable"); };
  await assert.rejects(runNativeInbound(f.options), /GitHub unavailable/);
  assert.deepEqual(f.writes, []);
});

test("a head run created by an operator-triggered tick is accepted", async () => {
  const f = fixture("manual");
  f.parentRun.source = "manual";
  f.parentRun.triggerPayload = { kind: "discovery" };
  f.parentIssue.createdByUserId = "fixture-director";
  assert.deepEqual(await runNativeInbound(f.options), { kind: "audit", status: "published" });
});

test("a native audit finds its own run and parent beyond recent history", async () => {
  const f = fixture("manual");
  const original = f.options.board;
  const newer = Array.from({ length: 200 }, (_, index) => ({
    id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(index).padStart(12, "0")}`, source: "schedule",
  }));
  f.options.board = async (path, ...args) => {
    if (path === `routines/${routineId}/runs?limit=200`) return newer;
    if (path === `routines/${routineId}/runs?limit=200&before=${newer[199].id}`) return [f.run, f.parentRun];
    return original(path, ...args);
  };
  assert.deepEqual(await runNativeInbound(f.options), { kind: "audit", status: "published" });
  assert.equal(f.writes[0][0], `issues/${issueId}/documents/audit`);
});

test("a native run refuses a different Routine, owner, or concurrency limit before work", async () => {
  for (const change of [
    ({ issue }) => { issue.originId = "other"; },
    ({ routine }) => { routine.assigneeAgentId = "other"; },
    ({ agent }) => { agent.runtimeConfig.heartbeat.maxConcurrentRuns = 2; },
  ]) {
    const f = fixture();
    change(f);
    await assert.rejects(runNativeInbound(f.options), /native.*(context|Routine|concurrent)/i);
    assert.deepEqual(f.writes, []);
  }
});

test("a native run refuses an unsupported Routine source", async () => {
  const { options, writes } = fixture("api");
  await assert.rejects(runNativeInbound(options), /Routine source/);
  assert.deepEqual(writes, []);
});

test("a manual head run must come from a scheduled run and use the exact head key", async () => {
  for (const change of [
    ({ run }) => { run.createdByRunId = null; },
    ({ run }) => { run.idempotencyKey = "different"; },
  ]) {
    const f = fixture("manual");
    change(f);
    await assert.rejects(runNativeInbound(f.options), /scheduled.*origin|head key/i);
    assert.deepEqual(f.writes, []);
  }
});

test("a blocked task without a dossier retries rather than pretending to wait", async () => {
  const f = fixture("manual");
  f.issue.status = "blocked";
  assert.deepEqual(await runNativeInbound(f.options), { kind: "audit", status: "published" });
  assert.equal(f.writes[0][0], `issues/${issueId}/documents/audit`);
});

test("a blocked task with a dossier is parked without another audit", async () => {
  const f = fixture("manual");
  f.issue.status = "blocked";
  f.documents.push({ key: "audit" });
  f.options.audit = async () => { throw new Error("unexpected audit"); };
  assert.deepEqual(await runNativeInbound(f.options), { kind: "audit", status: "already_published" });
  assert.deepEqual(f.writes, [[`issues/${issueId}`, "PATCH", { status: "backlog" }]]);
});

test("a blocked superseded dossier is cancelled without another audit", async () => {
  const f = fixture("manual");
  f.issue.status = "blocked";
  f.documents.push({ key: "audit" }, { key: "audit-status" });
  f.options.audit = async () => { throw new Error("unexpected audit"); };
  assert.deepEqual(await runNativeInbound(f.options), { kind: "audit", status: "superseded" });
  assert.deepEqual(f.writes, [[`issues/${issueId}`, "PATCH", { status: "cancelled" }]]);
});

test("a terminal task cannot enqueue or audit work again", async () => {
  const f = fixture();
  f.issue.status = "done";
  assert.deepEqual(await runNativeInbound(f.options), { kind: "terminal" });
  assert.deepEqual(f.writes, []);
});

test("a scheduled native tick reconciles only the named human's exact-head review", async () => {
  const f = fixture();
  const auditedIssueId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const auditedRunId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  const audited = { id: auditedIssueId, companyId, originKind: "routine_execution",
    originId: routineId, originRunId: auditedRunId, status: "backlog", assigneeAgentId: null };
  const auditRun = { id: auditedRunId, source: "manual", linkedIssueId: auditedIssueId,
    triggerPayload: { repo: repoName, pr_number: 7, head_sha: head } };
  const original = f.options.board;
  f.options.board = async (path, method = "GET", body) => {
    if (path === `routines/${routineId}/runs?limit=200`) return [f.run, auditRun];
    if (path === `issues/${auditedIssueId}/documents`) return [{ key: "audit" }];
    if (path === `issues/${auditedIssueId}` && method === "GET") return audited;
    if (path === `issues/${auditedIssueId}` && method === "PATCH") {
      f.writes.push([path, method, body]);
      return { ...audited, ...body };
    }
    return original(path, method, body);
  };
  f.options.listRequestedPrs = async () => [];
  f.options.prHead = async () => head;
  f.options.reviewer = "Director";
  f.options.prReviews = async () => [
    { user: { type: "User", login: "Director" }, state: "COMMENTED", commit_id: "b".repeat(40),
      submitted_at: "2026-01-01T00:00:00Z" },
  ];
  await runNativeInbound(f.options);
  assert.deepEqual(f.writes, [[`issues/${issueId}`, "PATCH", { status: "done" }]]);
  f.writes.length = 0;
  f.options.prReviews = async () => [
    { user: { type: "User", login: "Director" }, state: "COMMENTED", commit_id: head,
      submitted_at: "2026-01-01T00:00:00Z" },
  ];
  await runNativeInbound(f.options);
  assert.deepEqual(f.writes, [
    [`issues/${auditedIssueId}`, "PATCH", { status: "done" }],
    [`issues/${issueId}`, "PATCH", { status: "done" }],
  ]);
});
