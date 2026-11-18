import assert from "node:assert/strict";
import test from "node:test";
import { councilDecision } from "../council-verdict.mjs";

const roles = ["architecture_scale", "security_blast_radius", "scope_pragmatism"];
const issue = { severity: "major", location: "Section 2", issue: "Missing boundary", recommendation: "Add a boundary" };
const panel = () => roles.map((judge_role) => ({
  judge_role, verdict: "PASS", confidence: 0.88, blocking_issues: [], non_blocking_notes: [],
}));

test("unanimous PASS advances even after two revisions", () => {
  const verdicts = panel();
  verdicts[0].non_blocking_notes = ["Consider a metric"];
  assert.equal(councilDecision(verdicts, 2), "advance");
});

test("REVISE requests revision until two revisions have been completed", () => {
  const verdicts = panel();
  verdicts[1] = { ...verdicts[1], verdict: "REVISE", blocking_issues: [issue] };
  assert.equal(councilDecision(verdicts, 0), "revise");
  assert.equal(councilDecision(verdicts, 1), "revise");
  assert.equal(councilDecision(verdicts, 2), "escalate");
});

test("any BLOCK escalates regardless of remaining revisions", () => {
  const verdicts = panel();
  verdicts[0] = { ...verdicts[0], verdict: "REVISE", blocking_issues: [issue] };
  verdicts[2] = { ...verdicts[2], verdict: "BLOCK", blocking_issues: [issue] };
  assert.equal(councilDecision(verdicts, 0), "escalate");
});

test("rejects missing, duplicate, or unknown judge roles", () => {
  assert.throws(() => councilDecision(panel().slice(0, 2), 0));
  assert.throws(() => councilDecision([panel()[0], panel()[0], panel()[2]], 0));
  const sparse = panel();
  delete sparse[1];
  assert.throws(() => councilDecision(sparse, 0));
  const verdicts = panel();
  verdicts[0].judge_role = "unknown";
  assert.throws(() => councilDecision(verdicts, 0));
});

test("rejects malformed and contradictory verdicts instead of advancing", () => {
  for (const invalid of [
    { verdict: "PASS", blocking_issues: [issue] },
    { verdict: "REVISE", blocking_issues: [] },
    { verdict: "INVALID" }, { confidence: -0.1 }, { confidence: NaN }, { confidence: 1.1 },
    { non_blocking_notes: [""] }, { blocking_issues: [{ ...issue, severity: "minor" }] },
    { verdict: "REVISE", blocking_issues: Array(1) }, { non_blocking_notes: Array(1) },
    { unexpected: true },
  ]) {
    const verdicts = panel();
    verdicts[0] = { ...verdicts[0], ...invalid };
    assert.throws(() => councilDecision(verdicts, 0));
  }
});

test("rejects invalid revision counts", () => {
  for (const count of [-1, 3, 0.5, "0", null]) assert.throws(() => councilDecision(panel(), count));
});
