import assert from "node:assert/strict";
import test from "node:test";
import { createPrEligibility, parseAssignees } from "../github-pr-eligibility.mjs";

const head = "a".repeat(40);
const base = { state: "OPEN", isDraft: false, headRefOid: head, assignees: [{ login: "fixture-bot" }] };

test("accepts an open ready PR assigned to either configured account", async () => {
  const isEligible = createPrEligibility({
    assignees: ["fixture-human", "fixture-bot"], readPr: async () => base,
  });
  assert.equal(await isEligible(7, head), true);
});

test("rejects draft, closed, unassigned, and changed-head PRs", async () => {
  for (const patch of [
    { isDraft: true },
    { state: "CLOSED" },
    { assignees: [{ login: "someone-else" }] },
    { headRefOid: "b".repeat(40) },
  ]) {
    const isEligible = createPrEligibility({
      assignees: ["fixture-human", "fixture-bot"], readPr: async () => ({ ...base, ...patch }),
    });
    assert.equal(await isEligible(7, head), false);
  }
});

test("missing GitHub fields fail closed", async () => {
  const isEligible = createPrEligibility({
    assignees: ["fixture-bot"], readPr: async () => ({ ...base, isDraft: undefined }),
  });
  await assert.rejects(isEligible(7, head), /eligibility/i);
});

test("requires explicit valid configured assignees", () => {
  assert.deepEqual(parseAssignees("Fixture-Human,fixture-bot"), ["fixture-human", "fixture-bot"]);
  assert.throws(() => parseAssignees(""), /assignees/i);
  assert.throws(() => parseAssignees("fixture-bot,invalid/name"), /assignees/i);
});
