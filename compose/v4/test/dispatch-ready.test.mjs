import assert from "node:assert/strict";
import test from "node:test";
import { dispatchReadyTicket } from "../dispatch-ready.mjs";

const job = { ticketId: 4, repo: "fixture/project", baseBranch: "main", expectedAuthor: "fixture-bot", repoPath: "/work/project",
  product: "fixture", rfcPath: "/docs/accepted.md", acceptanceCriteria: ["Check formatting"] };
const draft = { repo: job.repo, branch: "ralph/vikunja-4", baseBranch: "main", author: "fixture-bot", isDraft: true,
  isCrossRepository: false, number: 7, headSha: "a".repeat(40) };

test("rechecks Ready eligibility after claiming, before invoking SWE", async () => {
  let reads = 0;
  const outcome = await dispatchReadyTicket(job, {
    readReady: async () => ++reads === 1,
    claim: async () => "lease-fixture",
    findDraft: async () => assert.fail("did not recheck before consulting GitHub"),
    invokeSwe: async () => assert.fail("dispatched an ineligible ticket"),
  });
  assert.equal(outcome.status, "no_longer_ready");
});

test("records an already-open draft instead of invoking SWE again after a crash", async () => {
  let call;
  const outcome = await dispatchReadyTicket(job, {
    readReady: async () => true,
    claim: async () => "lease-fixture",
    findDraft: async (repo, branch) => { assert.equal(repo, job.repo); assert.equal(branch, draft.branch); return draft; },
    invokeSwe: async () => assert.fail("dispatched despite existing draft"),
    record: async (...args) => { call = args; return true; },
  });
  assert.equal(outcome.status, "waiting_merge");
  assert.deepEqual(call, [4, "lease-fixture", job.repo, 7, draft.headSha]);
});

test("ignores SWE self-report and cannot advance on an expired lease", async () => {
  let invoked = false;
  const outcome = await dispatchReadyTicket(job, {
    readReady: async () => true,
    claim: async () => "lease-fixture",
    findDraft: async () => invoked ? draft : null,
    invokeSwe: async (args) => {
      invoked = true;
      assert.equal(args.ticket_id, "vikunja-4");
      assert.equal(args.branch, draft.branch);
      assert.equal(args.rfc_path, job.rfcPath);
      return { pr_url: "https://example.invalid/not-trusted" };
    },
    record: async () => false,
  });
  assert.equal(outcome.status, "stale_lease");
});

test("does not record a draft targeting the wrong base branch", async () => {
  await assert.rejects(dispatchReadyTicket(job, {
    readReady: async () => true,
    claim: async () => "lease-fixture",
    findDraft: async () => ({ ...draft, baseBranch: "different" }),
    invokeSwe: async () => assert.fail("restarted a ticket with an unexpected draft"),
    record: async () => assert.fail("recorded a draft for the wrong base"),
  }), /Expected a same-repository draft/);
});
