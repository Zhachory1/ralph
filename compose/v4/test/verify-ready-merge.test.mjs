import assert from "node:assert/strict";
import test from "node:test";
import { completeReadyTicket, verifyReadyMerge } from "../verify-ready-merge.mjs";

const expected = {
  ticketId: 101,
  repo: "fixture/project",
  prNumber: 7,
  headSha: "a".repeat(40),
  baseBranch: "main",
  expectedAuthor: "fixture-bot",
  directors: ["fixture-director"],
};
const merged = {
  number: 7, state: "closed", merged: true, merged_at: "2026-11-18T01:00:00Z", merge_commit_sha: "b".repeat(40),
  user: { login: "fixture-bot" }, merged_by: { login: "fixture-director", type: "User" },
  head: { ref: "ralph/vikunja-101", sha: expected.headSha, repo: { full_name: expected.repo } },
  base: { ref: "main", repo: { full_name: expected.repo } },
};

test("only a confirmed Director merge of the pinned pull request produces a receipt", () => {
  assert.deepEqual(verifyReadyMerge(expected, merged), {
    status: "verified", headSha: expected.headSha, mergeCommitSha: merged.merge_commit_sha,
    mergedBy: "fixture-director", mergedAt: merged.merged_at,
  });
});

test("open and closed-unmerged PRs never prove completion", () => {
  assert.equal(verifyReadyMerge(expected, { ...merged, state: "open", merged: false }).status, "waiting_merge");
  assert.equal(verifyReadyMerge(expected, { ...merged, merged: false }).status, "closed_unmerged");
});

test("a changed head does not inherit permission to reach Done", () => {
  const newer = { ...merged, head: { ...merged.head, sha: "c".repeat(40) } };
  assert.equal(verifyReadyMerge(expected, newer).status, "stale_head");
});

test("denies the automation identity and an unexpected merge actor", () => {
  const own = { ...merged, merged_by: { login: expected.expectedAuthor, type: "User" } };
  assert.equal(verifyReadyMerge({ ...expected, directors: [...expected.directors, expected.expectedAuthor] }, own).status, "unauthorized_actor");
  assert.equal(verifyReadyMerge(expected, { ...merged, merged_by: { login: "fixture-other", type: "User" } }).status, "unauthorized_actor");
});

test("denies evidence from the wrong branch, repository or PR number", () => {
  assert.equal(verifyReadyMerge(expected, { ...merged, number: 9 }).status, "wrong_pr");
  const wrongBase = { ...merged, base: { ...merged.base, ref: "release" } };
  assert.equal(verifyReadyMerge(expected, wrongBase).status, "wrong_pr");
  const fork = { ...merged, head: { ...merged.head, repo: { full_name: "fixture/fork" } } };
  assert.equal(verifyReadyMerge(expected, fork).status, "wrong_pr");
});

test("refuses merge evidence without a valid merge commit and timestamp", () => {
  assert.equal(verifyReadyMerge(expected, { ...merged, merge_commit_sha: null }).status, "incomplete_evidence");
  assert.equal(verifyReadyMerge(expected, { ...merged, merged_at: "not-a-date" }).status, "incomplete_evidence");
});

test("records proof before moving the board and acknowledges only a confirmed move", async () => {
  const calls = [];
  const result = await completeReadyTicket(expected, {
    readPull: async () => merged,
    recordMerge: async () => { calls.push("record"); return true; },
    moveBoardDone: async () => { calls.push("board"); return true; },
    recordDone: async (id, sha) => {
      calls.push("ack");
      assert.equal(id, expected.ticketId);
      assert.equal(sha, merged.merge_commit_sha);
      return true;
    },
  });
  assert.equal(result.status, "done");
  assert.deepEqual(calls, ["record", "board", "ack"]);
});

test("does not touch the board when the merge receipt is rejected", async () => {
  const result = await completeReadyTicket(expected, {
    readPull: async () => merged,
    recordMerge: async () => false,
    moveBoardDone: async () => assert.fail("board moved without receipt"),
    recordDone: async () => assert.fail("Done recorded without receipt"),
  });
  assert.equal(result.status, "receipt_rejected");
});

test("leaves an accepted receipt retryable if the ticket has not reached the Director gate", async () => {
  const result = await completeReadyTicket(expected, {
    readPull: async () => merged,
    recordMerge: async () => true,
    moveBoardDone: async () => false,
    recordDone: async () => assert.fail("acknowledged a ticket that did not move"),
  });
  assert.equal(result.status, "not_at_director_gate");
});

test("does not report Done if the acknowledgement fails after a confirmed move", async () => {
  const result = await completeReadyTicket(expected, {
    readPull: async () => merged,
    recordMerge: async () => true,
    moveBoardDone: async () => true,
    recordDone: async () => false,
  });
  assert.equal(result.status, "board_ack_failed");
});
