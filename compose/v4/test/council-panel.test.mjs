import assert from "node:assert/strict";
import test from "node:test";
import { runCouncilPanel } from "../council-panel.mjs";

const roles = ["architecture_scale", "security_blast_radius", "scope_pragmatism"];
const job = { artifact: "Fixture RFC", rubric: "Fixture rubric v1", revisions: 0 };
const verdict = (judge_role) => ({
  judge_role, verdict: "PASS", confidence: 0.9, blocking_issues: [], non_blocking_notes: [],
});

test("starts all role calls before awaiting results and returns every verdict", async () => {
  const calls = [], pending = [];
  const result = runCouncilPanel(job, (input) => {
    calls.push(input);
    return new Promise((resolve) => pending.push(() => resolve(verdict(input.judge_role))));
  });
  assert.deepEqual(calls.map((call) => call.judge_role), roles);
  for (const call of calls) assert.deepEqual(call, { judge_role: call.judge_role, artifact: job.artifact, rubric: job.rubric });
  for (const complete of pending) complete();
  assert.deepEqual(await result, { decision: "advance", verdicts: roles.map(verdict) });
});

test("returns the existing verdict contract's capped decision and full findings", async () => {
  const issue = { severity: "major", location: "Section 1", issue: "Missing boundary", recommendation: "Add boundary" };
  const result = await runCouncilPanel({ ...job, revisions: 2 }, ({ judge_role }) => ({
    ...verdict(judge_role), ...(judge_role === roles[0] ? { verdict: "REVISE", blocking_issues: [issue] } : {}),
  }));
  assert.equal(result.decision, "escalate");
  assert.deepEqual(result.verdicts[0].blocking_issues, [issue]);
});

test("a single BLOCK escalates the panel", async () => {
  const issue = { severity: "critical", location: "Section 1", issue: "Unsafe access", recommendation: "Restrict access" };
  const result = await runCouncilPanel(job, ({ judge_role }) => ({
    ...verdict(judge_role), ...(judge_role === roles[1] ? { verdict: "BLOCK", blocking_issues: [issue] } : {}),
  }));
  assert.equal(result.decision, "escalate");
});

test("rejects a verdict submitted for a different role, even if the panel has all roles", async () => {
  await assert.rejects(runCouncilPanel(job, ({ judge_role }) => verdict(roles[(roles.indexOf(judge_role) + 1) % 3])), /role/i);
});

test("rejects missing, malformed, duplicate, or failed judge responses", async () => {
  await assert.rejects(runCouncilPanel(job, ({ judge_role }) => judge_role === roles[1] ? undefined : verdict(judge_role)));
  await assert.rejects(runCouncilPanel(job, ({ judge_role }) => ({ ...verdict(judge_role), confidence: 2 })));
  await assert.rejects(runCouncilPanel(job, () => verdict(roles[0])));
  await assert.rejects(runCouncilPanel(job, ({ judge_role }) => {
    if (judge_role === roles[1]) throw new Error("judge unavailable");
    return verdict(judge_role);
  }), /judge unavailable/);
  await assert.rejects(runCouncilPanel(job, ({ judge_role }) => judge_role === roles[1]
    ? Promise.reject(new Error("judge timed out")) : verdict(judge_role)), /judge timed out/);
});

test("waits for every in-flight judge to settle before reporting a panel failure", async () => {
  const pending = [];
  let settled = false;
  const panel = runCouncilPanel(job, ({ judge_role }) => {
    if (judge_role === roles[0]) throw new Error("judge unavailable");
    return new Promise((resolve) => pending.push(() => resolve(verdict(judge_role))));
  });
  panel.catch(() => { settled = true; });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(settled, false);
  pending.forEach((finish) => finish());
  await assert.rejects(panel, /judge unavailable/);
});

test("preserves every failure when multiple judges fail", async () => {
  await assert.rejects(runCouncilPanel(job, ({ judge_role }) => {
    if (judge_role === roles[0]) throw new Error("judge failed");
    if (judge_role === roles[1]) throw new Error("cleanup unconfirmed");
    return verdict(judge_role);
  }), (error) => error instanceof AggregateError
    && error.errors.some((failure) => failure.message === "cleanup unconfirmed"));
});

test("rejects invalid input before invoking judges", async () => {
  for (const bad of [{ artifact: "" }, { artifact: null }, { rubric: " " }, { revisions: -1 }, { revisions: 3 }]) {
    await assert.rejects(runCouncilPanel({ ...job, ...bad }, () => assert.fail("invalid job dispatched")));
  }
});
