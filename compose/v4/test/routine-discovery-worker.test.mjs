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
      board: async (path, method, body) => {
        if (path === "routines/routine") return { id: "routine", companyId: "company" };
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
  assert.deepEqual(calls.map((call) => call[1].idempotencyKey), [`pr-7-${head1}`, `pr-9-${head2}`]);
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
