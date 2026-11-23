import assert from "node:assert/strict";
import test from "node:test";
import { configuredAccountName, githubEnvironment, runReviewDispatch, runReviewTick } from "../pr-review-dispatch.mjs";

const head = "a".repeat(40);
const base = "b".repeat(40);
const patch = "@@ -1 +1 @@\n-old\n+new";
const accounts = ["fixture-human", "fixture-bot"];

test("preserves the configured GitHub account case for credential lookup", () => {
  assert.equal(configuredAccountName(["Fixture-Human", "fixture-bot"], "fixture-human"), "Fixture-Human");
});

test("does not pass Paperclip or provider credentials to GitHub commands", () => {
  assert.deepEqual(githubEnvironment({ PATH: "/bin", HOME: "/home/fixture", GH_TOKEN: "fixture",
    PAPERCLIP_API_KEY: "fixture", PAPERCLIP_AGENT_PRIVATE_KEY: "fixture", OPENAI_API_KEY: "fixture" }),
  { PATH: "/bin", HOME: "/home/fixture" });
});

function fixture(count = 1) {
  const calls = [];
  let reads = 0;
  let stale = false;
  let changedHead = false;
  let published = false;
  const active = [];
  const completed = [];
  let scanUpdate;
  const gh = async (viewer, ...args) => {
    if (args[0] === "api" && args[1] === "user") return viewer;
    if (args.includes("search/issues")) {
      const query = args.find((arg) => arg.startsWith("q="));
      const found = viewer === "fixture-human" && query.includes("assignee:fixture-human");
      return JSON.stringify([{ total_count: found ? count : 0, incomplete_results: false, items: found ?
        Array.from({ length: count }, (_, i) => ({
          number: i + 1, repository_url: "https://api.github.com/repos/fixture/repo", pull_request: {},
        })) : [] }]);
    }
    if (args[0] === "pr") {
      reads += 1;
      return JSON.stringify({ state: "OPEN", isDraft: false, assignees: [{ login: "fixture-human" }],
        headRefOid: changedHead || (stale && reads >= 4) ? "c".repeat(40) : head, baseRefOid: base,
        author: { login: "outside" }, title: "Fixture change", body: "Fixture description" });
    }
    if (args.includes("--header")) return `diff --git a/file b/file\n${patch}\n`;
    if (args.some((arg) => arg.includes("/reviews?"))) return JSON.stringify([published ? [{
      user: { login: "fixture-human" }, state: "COMMENTED", commit_id: head,
      body: `<!-- ai-pr-automation head=${head} -->`,
    }] : []]);
    if (args[0] === "api") return JSON.stringify({ files: [
      { filename: "file", additions: 1, deletions: 1, patch },
    ] });
    throw new Error("Unexpected GitHub call");
  };
  const board = async (path, method, body) => {
    if (path === "heartbeat-runs/scan-run") {
      return { id: "scan-run", companyId: "company", agentId: "scanner", issueId: "scan" };
    }
    if (path.startsWith("companies/company/issues?")) {
      return new URLSearchParams(path.split("?")[1]).get("status") === "done" ? completed : active;
    }
    if (path === "companies/company/issues") {
      calls.push({ method, body });
      const issue = { id: `issue-${calls.length}`, companyId: "company", ...body };
      active.push(issue);
      return issue;
    }
    if (path === "issues/scan" && method === "PATCH") {
      scanUpdate = body;
      return { id: "scan", status: body.status };
    }
    throw new Error(`Unexpected board call ${path}`);
  };
  return { args: { board, companyId: "company", managerId: "manager", synthesisId: "synthesis", accounts, gh,
    publisher: "fixture-human" },
    calls, active, completed, setStale() { stale = true; }, setChangedHead() { changedHead = true; },
    setPublished() { published = true; }, getScanUpdate() { return scanUpdate; } };
}

test("creates a manager task containing exact-head evidence", async () => {
  const f = fixture();
  const result = await runReviewDispatch(f.args);

  assert.equal(result.incoming[0].status, "dispatched");
  assert.equal(result.ours.length, 0);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].method, "POST");
  assert.equal(f.calls[0].body.idempotencyKey, `review:fixture/repo#1@${head}`);
  assert.equal(f.calls[0].body.assigneeAgentId, "manager");
  assert.equal(f.calls[0].body.visibility, "open");
  assert.equal(f.calls[0].body.parentId, null);
  assert.match(f.calls[0].body.description, /diff --git a\/file b\/file/);
  assert.match(f.calls[0].body.description, /Fixture description/);
  assert.match(f.calls[0].body.description, /block this task on synthesis/i);
  assert.match(f.calls[0].body.description, /synthesis.*publish/i);
  assert.match(f.calls[0].body.description, /ai-pr-automation head=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/);
  assert.match(f.calls[0].body.description, /do not retry an ambiguous post/i);
});

test("does not dispatch when the PR head changes before handoff", async () => {
  const f = fixture();
  f.setStale();

  const result = await runReviewDispatch(f.args);
  assert.equal(result.incoming[0].status, "stale");
  assert.equal(f.calls.length, 0);
});

test("reuses an active task instead of dispatching the same head again", async () => {
  const f = fixture();
  await runReviewDispatch(f.args);
  const result = await runReviewDispatch(f.args);
  assert.equal(result.incoming[0].status, "active");
  assert.equal(f.calls.length, 1);
});

test("rejects a task receipt for a different PR head", async () => {
  const f = fixture();
  const original = f.args.board;
  f.args.board = async (...args) => {
    const result = await original(...args);
    if (args[0] === "companies/company/issues") result.title = "Wrong task";
    return result;
  };

  await assert.rejects(runReviewDispatch(f.args), /task.*receipt/i);
});

test("enqueues 100 PRs without a per-scan cap", async () => {
  const f = fixture(100);
  const result = await runReviewDispatch(f.args);
  assert.equal(result.incoming.length, 100);
  assert.equal(f.calls.length, 100);
});

test("waits for an active graph before starting a new head", async () => {
  const f = fixture();
  await runReviewDispatch(f.args);
  f.setChangedHead();
  const result = await runReviewDispatch(f.args);
  assert.equal(result.incoming[0].status, "waiting");
  assert.equal(f.calls.length, 1);
});

test("does not create a task for a head with a published automated review", async () => {
  const f = fixture();
  f.setPublished();
  const result = await runReviewDispatch(f.args);
  assert.equal(result.incoming[0].status, "reviewed");
  assert.equal(f.calls.length, 0);
});

test("the scanner completes its own Routine task after handing off reviews", async () => {
  const f = fixture();
  await runReviewTick({ ...f.args, runId: "scan-run", agentId: "scanner" });
  assert.equal(f.getScanUpdate().status, "done");
  assert.match(f.getScanUpdate().comment, /1.*created/i);
});

test("does not publish completed reviews from the scanner", async () => {
  const f = fixture();
  f.completed.push({ id: "completed", title: `PR review fixture/repo#1 @ ${head}` });
  const result = await runReviewDispatch(f.args);
  assert.equal(result.incoming[0].status, "existing");
});
