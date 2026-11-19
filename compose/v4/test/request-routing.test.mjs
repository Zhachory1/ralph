import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { COUNCIL_ROLES } from "../council-verdict.mjs";
import { decideRequestRoute } from "../request-routing.mjs";

const artifact = "Run the offline tests and report the result.\n";
const digest = createHash("sha256").update(artifact).digest("hex");
const gate = { allowed: true, initiative: false, consequential: false };
const proposal = { kind: "novel", domain: "coding", artifact };
const registered = { kind: "registered", workflow: "triage", inputs: { issue: 237 } };
const registry = new Map([["triage", (inputs) => Number.isInteger(inputs?.issue) && inputs.issue > 0]]);
const issue = { severity: "major", location: "Plan", issue: "Missing check", recommendation: "Add a test" };

function review(verdict = "PASS") {
  return {
    status: "completed", artifactSha256: digest,
    result: { verdicts: COUNCIL_ROLES.map((judge_role, index) => ({
      judge_role,
      verdict: index === 0 ? verdict : "PASS",
      confidence: 0.9,
      blocking_issues: index === 0 && verdict !== "PASS" ? [issue] : [],
      non_blocking_notes: [],
    })) },
  };
}

function decide(candidate, overrides = {}) {
  return decideRequestRoute(candidate, { ...gate, registry, revisions: 0, ...overrides });
}

test("registered workflow advances only with a known name and validated inputs", () => {
  assert.equal(decide(registered), "advance");
  assert.equal(decide({ ...registered, inputs: {} }), "clarify");
  assert.equal(decide({ ...registered, workflow: "constructor" }), "clarify");
  assert.equal(decide(registered, { registry: {} }), "clarify");
  const throwing = new Map([["triage", () => { throw new Error("invalid inputs"); }]]);
  assert.equal(decide(registered, { registry: throwing }), "clarify");
});

test("ambiguous requests ask for clarification instead of reviewing or dispatching a guess", () => {
  assert.equal(decide({ kind: "clarify" }, { council: review() }), "clarify");
  assert.equal(decide({ kind: "novel", domain: "other", artifact }), "clarify");
  assert.equal(decide({ ...proposal, artifact: "\uD800" }), "clarify");
});

test("in-policy coding and non-coding plans advance only after unanimous exact-text PASS", () => {
  for (const domain of ["coding", "non-coding"]) {
    assert.equal(decide({ ...proposal, domain }), "review");
    assert.equal(decide({ ...proposal, domain }, { council: review() }), "advance");
    assert.equal(decide({ ...proposal, domain, artifact: artifact.trim() }, { council: review() }), "escalate");
  }
});

test("denied requests and missing policy do not advance, regardless of Council", () => {
  assert.equal(decide(proposal, { allowed: false, council: review() }), "denied");
  assert.equal(decide(registered, { allowed: false }), "denied");
  assert.equal(decide(registered, { consequential: undefined }), "denied");
});

test("unavailable or invalid Council cannot advance, including a forged summary", () => {
  const valid = review();
  for (const council of [
    null, { ...valid, status: "failed" }, { ...valid, artifactSha256: undefined },
    { ...valid, result: { decision: "advance", verdicts: valid.result.verdicts.slice(1) } },
    { ...review("BLOCK"), result: { ...review("BLOCK").result, decision: "advance" } },
  ]) {
    assert.equal(decide(proposal, { council }), "escalate");
  }
});

test("REVISE cannot advance and escalates after two completed revisions", () => {
  for (const revisions of [0, 1]) {
    assert.equal(decide(proposal, { council: review("REVISE"), revisions }), "revise");
  }
  assert.equal(decide(proposal, { council: review("REVISE"), revisions: 2 }), "escalate");
  assert.equal(decide(proposal, { council: review(), revisions: 3 }), "escalate");
  assert.equal(decide(proposal, { council: review(), revisions: 2 }), "advance");
});

test("initiative requires both Council PASS and matching Director approval", () => {
  const initiative = { initiative: true };
  assert.equal(decide(registered, initiative), "clarify");
  assert.equal(decide(proposal, { ...initiative, directorApprovedSha256: digest }), "review");
  assert.equal(decide(proposal, { ...initiative, council: review() }), "director");
  assert.equal(decide(proposal, { ...initiative, council: review(), directorApprovedSha256: "stale" }), "director");
  assert.equal(decide(proposal, { ...initiative, council: review(), directorApprovedSha256: digest }), "advance");
});

test("Director and Council clearance cannot clear separate consequential gates", () => {
  assert.equal(decide(registered, { consequential: true }), "human");
  assert.equal(decide(proposal, { consequential: true, council: review(), directorApprovedSha256: digest }), "human");
  assert.equal(decide(proposal, {
    initiative: true, consequential: true, council: review(), directorApprovedSha256: digest,
  }), "human");
});
