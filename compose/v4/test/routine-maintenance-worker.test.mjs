import assert from "node:assert/strict";
import test from "node:test";
import { MAX_WAKE_ATTEMPTS, createPaperclipDeps, runOutboundMaintenance } from "../routine-maintenance-worker.mjs";

const head = "1".repeat(40);
const issueId = "00000000-0000-4000-8000-000000000009";
const directorUserId = "00000000-0000-4000-8000-0000000000de";
const baseEvent = { issueId, repo: "fixture/canary", pr_number: 9, head_sha: head,
  event_type: "ci_failure", event_id: "check_suite:1", author_login: "a-human" };

function fixture(overrides = {}) {
  const calls = [];
  let state = { processedEventIds: [], wakeCount: 0, escalated: false };
  let revisionId = "rev-0";
  return {
    calls,
    getState: () => state,
    deps: {
      botLogin: "ralph-bot",
      loadTrackedPr: async (id) => {
        calls.push(["loadTrackedPr", id]);
        return { repo: "fixture/canary", pr_number: 9 };
      },
      loadState: async (id) => {
        calls.push(["loadState", id]);
        return { state, revisionId };
      },
      saveState: async (id, base, next) => {
        calls.push(["saveState", id, base, next]);
        if (base !== revisionId) return false;
        state = next;
        revisionId = `${revisionId}+1`;
        return true;
      },
      wake: async (id, detail) => {
        calls.push(["wake", id, detail]);
        return true;
      },
      escalate: async (id, detail) => {
        calls.push(["escalate", id, detail]);
        return true;
      },
      ...overrides,
    },
  };
}

test("takes no action on a bot's own reply", async () => {
  const { calls, deps } = fixture();
  const result = await runOutboundMaintenance({ ...baseEvent, author_login: "RALPH-BOT" }, deps);
  assert.deepEqual(result, { status: "own_reply" });
  assert.deepEqual(calls, []);
});

test("takes no action when the issue is out of this worker's scope", async () => {
  const { calls, deps } = fixture({
    loadTrackedPr: async (id) => { calls.push(["loadTrackedPr", id]); return null; },
  });
  const result = await runOutboundMaintenance(baseEvent, deps);
  assert.deepEqual(result, { status: "out_of_scope" });
  assert.deepEqual(calls, [["loadTrackedPr", issueId]]);
});

test("takes no action when the tracked issue names a different repo or PR", async () => {
  const { deps } = fixture({
    loadTrackedPr: async () => ({ repo: "fixture/canary", pr_number: 4 }),
  });
  const result = await runOutboundMaintenance(baseEvent, deps);
  assert.deepEqual(result, { status: "out_of_scope" });
});

test("wakes the existing worktree for an ordinary CI failure only after the claim commits", async () => {
  const { calls, deps, getState } = fixture();
  const result = await runOutboundMaintenance(baseEvent, deps);
  assert.deepEqual(result, { status: "woke", wakeCount: 1 });
  assert.deepEqual(getState(), { processedEventIds: ["check_suite:1"], wakeCount: 1, escalated: false });
  const order = calls.map(([name]) => name);
  assert.deepEqual(order, ["loadTrackedPr", "loadState", "saveState", "wake"]);
  const [, , , [, wakeIssueId, wakeDetail]] = calls;
  assert.equal(wakeIssueId, issueId);
  assert.deepEqual(wakeDetail, { repo: "fixture/canary", pr: 9, head, type: "ci_failure", id: "check_suite:1" });
});

test("wakes for ordinary review feedback the same as a CI failure", async () => {
  const { deps } = fixture();
  const result = await runOutboundMaintenance({ ...baseEvent, event_type: "review_feedback", event_id: "review:1" }, deps);
  assert.deepEqual(result, { status: "woke", wakeCount: 1 });
});

test("deduplicates a redelivered event instead of waking again", async () => {
  const { calls, deps } = fixture();
  assert.equal((await runOutboundMaintenance(baseEvent, deps)).status, "woke");
  calls.length = 0;
  const result = await runOutboundMaintenance(baseEvent, deps);
  assert.deepEqual(result, { status: "duplicate" });
  assert.deepEqual(calls.filter(([name]) => name === "wake"), []);
});

test("escalates to the Director instead of waking once retries are exhausted at the PDF's cap of two", async () => {
  assert.equal(MAX_WAKE_ATTEMPTS, 2);
  const { deps, getState } = fixture();
  for (let attempt = 1; attempt <= MAX_WAKE_ATTEMPTS; attempt += 1) {
    const result = await runOutboundMaintenance({ ...baseEvent, event_id: `check_suite:${attempt}` }, deps);
    assert.deepEqual(result, { status: "woke", wakeCount: attempt });
  }
  const result = await runOutboundMaintenance({ ...baseEvent, event_id: "check_suite:final" }, deps);
  assert.deepEqual(result, { status: "escalated", reason: "wake_attempts_exhausted" });
  assert.equal(getState().escalated, true);
  assert.equal(getState().wakeCount, MAX_WAKE_ATTEMPTS);
});

test("escalates base drift and merge readiness without ever waking", async () => {
  for (const event_type of ["base_drift", "merge_ready"]) {
    const { calls, deps } = fixture();
    const result = await runOutboundMaintenance({ ...baseEvent, event_type, event_id: `${event_type}:1` }, deps);
    assert.deepEqual(result, { status: "escalated", reason: event_type });
    assert.deepEqual(calls.filter(([name]) => name === "wake"), []);
  }
});

test("does not act again once already escalated and waiting on the Director", async () => {
  const { deps } = fixture();
  await runOutboundMaintenance({ ...baseEvent, event_type: "base_drift", event_id: "base_drift:1" }, deps);
  const result = await runOutboundMaintenance({ ...baseEvent, event_type: "base_drift", event_id: "base_drift:2" }, deps);
  assert.deepEqual(result, { status: "already_escalated" });
});

test("rejects an untrusted event shape before touching the board", async () => {
  const { calls, deps } = fixture();
  await assert.rejects(runOutboundMaintenance({ ...baseEvent, pr_number: 0 }, deps), /trusted outbound PR event/);
  await assert.rejects(runOutboundMaintenance({ ...baseEvent, head_sha: "nope" }, deps), /trusted outbound PR event/);
  await assert.rejects(runOutboundMaintenance({ ...baseEvent, event_type: "merged" }, deps), /trusted outbound PR event/);
  await assert.rejects(runOutboundMaintenance({ ...baseEvent, issueId: "not-a-uuid" }, deps), /trusted outbound PR event/);
  await assert.rejects(runOutboundMaintenance({ ...baseEvent, issueId: "DEADBEEF-0000-4000-8000-000000000009" }, deps),
    /trusted outbound PR event/);
  assert.deepEqual(calls, []);
});

test("never resets retry state on a non-404 board read error, and never performs the effect", async () => {
  const { calls, deps } = fixture({
    loadState: async () => { throw Object.assign(new Error("Paperclip GET failed (500)"), { status: 500 }); },
    wake: async () => { calls.push(["wake"]); return true; },
    saveState: async () => { calls.push(["saveState"]); return true; },
  });
  await assert.rejects(runOutboundMaintenance(baseEvent, deps), /500/);
  assert.deepEqual(calls.filter(([name]) => name === "wake" || name === "saveState"), []);
});

test("claims the event before performing its effect, so a claim failure never wakes", async () => {
  const { calls, deps } = fixture({ saveState: async () => false });
  const result = await runOutboundMaintenance(baseEvent, deps);
  assert.deepEqual(result, { status: "stale_state" });
  assert.deepEqual(calls.filter(([name]) => name === "wake" || name === "escalate"), []);
});

test("reports an ambiguous effect instead of retrying once the claim has already committed", async () => {
  const { deps, getState } = fixture({ wake: async () => false });
  const result = await runOutboundMaintenance(baseEvent, deps);
  assert.deepEqual(result, { status: "effect_ambiguous", action: "wake" });
  // The claim already committed: the event is marked handled even though the effect failed.
  assert.deepEqual(getState().processedEventIds, ["check_suite:1"]);
  const replay = await runOutboundMaintenance(baseEvent, deps);
  assert.deepEqual(replay, { status: "duplicate" }, "fails closed: never retries a committed claim automatically");
});

test("createPaperclipDeps treats only a 404 board read as a missing document", async () => {
  const reads = [];
  const board = async (path) => {
    reads.push(path);
    if (path === `issues/${issueId}/documents/outbound-pr`) {
      throw Object.assign(new Error("not found"), { status: 404 });
    }
    throw Object.assign(new Error("Paperclip GET failed (503)"), { status: 503 });
  };
  const deps = createPaperclipDeps(board, { botLogin: "ralph-bot", directorUserId });
  assert.equal(await deps.loadTrackedPr(issueId), null);
  await assert.rejects(deps.loadState(issueId), /503/);
});

test("createPaperclipDeps accepts Paperclip's actual opaque (non-UUID) user id", () => {
  // Verified against a disposable Paperclip instance: auth/get-session returns a user id
  // like this, not a UUID -- see paperclip-maintenance-canary.py.
  assert.doesNotThrow(() => createPaperclipDeps(async () => {}, { botLogin: "ralph-bot", directorUserId: "pxkyMRTDh1BfyR3NS9BZN6bN6e94IqXE" }));
});

test("createPaperclipDeps rejects a Director id unsafe to interpolate into a URL", () => {
  for (const directorUserId of ["", "../escape", "has space", "a".repeat(129)]) {
    assert.throws(() => createPaperclipDeps(async () => {}, { botLogin: "ralph-bot", directorUserId }), /opaque id/);
  }
});
