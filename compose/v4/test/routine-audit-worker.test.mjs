import assert from "node:assert/strict";
import test from "node:test";
import { runRoutineAudit } from "../routine-audit-worker.mjs";

const head = "1".repeat(40);
const settings = {
  companyId: "company", routineId: "routine", runId: "run", repoPath: "/repo", repoName: "fixture/canary",
};

function fixture() {
  const run = { id: "run", source: "manual", linkedIssueId: "issue",
    triggerPayload: { repo: "fixture/canary", pr_number: 7, head_sha: head } };
  const documents = [];
  const calls = [];
  return {
    calls,
    options: {
      ...settings,
      isEligible: async () => true,
      audit: async (input) => {
        calls.push(["audit", input]);
        return { tests_passed: true };
      },
      currentHead: async () => head,
      board: async (path, method, body) => {
        if (path === "routines/routine") return { id: "routine", companyId: "company" };
        if (path === "routines/routine/runs?limit=200") return [run];
        if (path === "issues/issue") return {
          id: "issue", companyId: "company", originKind: "routine_execution", originId: "routine", originRunId: "run",
        };
        if (path === "issues/issue/documents") return documents;
        if (path === "issues/issue/documents/audit" && method === "PUT") {
          calls.push(["put", body]);
          documents.push({ key: "audit" });
          return { key: "audit" };
        }
        if (path === "issues/issue/documents/audit-status" && method === "PUT") {
          calls.push(["status", body]);
          documents.push({ key: "audit-status" });
          return { key: "audit-status" };
        }
        throw new Error(`Unexpected board request: ${path}`);
      },
    },
  };
}

test("uses the recorded head and publishes one dossier on serial replay", async () => {
  const { options, calls } = fixture();
  assert.equal(await runRoutineAudit(options), "published");
  assert.equal(await runRoutineAudit(options), "already_published");
  assert.deepEqual(calls[0], ["audit", { repo_path: "/repo", pr_number: 7, head_sha: head }]);
  assert.match(calls[1][1].body, new RegExp(head));
  assert.equal(calls.length, 2);
});

test("withholds a dossier if the head changes while the audit runs", async () => {
  const { options, calls } = fixture();
  const heads = [head, "2".repeat(40)];
  options.currentHead = async () => heads.shift();
  assert.equal(await runRoutineAudit(options), "stale");
  assert.deepEqual(calls.map(([call]) => call), ["audit"]);
});

test("rechecking an outdated dossier marks it superseded once without auditing again", async () => {
  const { options, calls } = fixture();
  assert.equal(await runRoutineAudit(options), "published");
  options.currentHead = async () => "2".repeat(40);
  assert.equal(await runRoutineAudit(options), "superseded");
  assert.equal(await runRoutineAudit(options), "superseded");
  assert.deepEqual(calls.map(([call]) => call), ["audit", "put", "status"]);
  assert.match(calls[2][1].body, /historical/i);
});

test("rejects a linked issue created by another run", async () => {
  const { options, calls } = fixture();
  const board = options.board;
  options.board = async (...args) => {
    const result = await board(...args);
    return args[0] === "issues/issue" ? { ...result, originRunId: "other" } : result;
  };
  await assert.rejects(runRoutineAudit(options), /Unexpected linked Routine issue/);
  assert.deepEqual(calls, []);
});
