import assert from "node:assert/strict";
import test from "node:test";
import { publishReview } from "../pr-review-publish.mjs";

const head = "a".repeat(40);
const base = "b".repeat(40);

function fixture() {
  let document = null;
  let review = null;
  let posts = 0;
  let stale = false;
  let staleWhenMarked = false;
  let ambiguous = false;
  const task = { id: "manager-task", companyId: "company", assigneeAgentId: "manager", status: "done",
    title: `PR review fixture/repo#1 @ ${head}`,
    description: `Review fixture/repo#1 at head ${head} against base ${base}.\n` };
  const board = async (path, method, body) => {
    if (path.startsWith("companies/company/issues?")) return [{
      id: "synthesis-task", companyId: "company", parentId: task.id, assigneeAgentId: "synthesis", status: "done",
    }];
    if (path === "issues/synthesis-task/comments?order=desc") return [{ authorAgentId: "synthesis", body: "Review findings." }];
    if (path.endsWith("/documents/github-review-publication")) {
      if (method === "PUT") {
        document = { body: body.body, latestRevisionId: "revision" };
        if (staleWhenMarked && JSON.parse(body.body).state === "posting") stale = true;
        return document;
      }
      if (!document) throw Object.assign(new Error("Not found"), { status: 404 });
      return document;
    }
    throw new Error(`Unexpected board call ${path}`);
  };
  const gh = async (_viewer, ...args) => {
    if (args[0] === "pr") return JSON.stringify({
      state: "OPEN", isDraft: false, assignees: [{ login: "fixture-human" }],
      headRefOid: stale ? "c".repeat(40) : head, baseRefOid: base,
    });
    if (args[0] === "api" && args[1] === "user") return "fixture-human";
    if (args[0] === "api") return JSON.stringify([review ? [review] : []]);
    throw new Error("Unexpected GitHub call");
  };
  const post = async (input) => {
    posts += 1;
    if (ambiguous) throw new Error("Connection lost");
    review = { user: { login: "fixture-human" }, state: "COMMENTED", commit_id: head, body: input.body };
    return review;
  };
  return { args: { task, board, gh, post, companyId: "company", managerId: "manager", synthesisId: "synthesis",
    publisher: "fixture-human", accounts: ["fixture-human", "fixture-bot"] },
    setStale() { stale = true; }, setStaleWhenMarked() { staleWhenMarked = true; },
    setAmbiguous() { ambiguous = true; }, posts() { return posts; } };
}

test("publishes the synthesis as a clearly automated exact-head review", async () => {
  const f = fixture();
  assert.equal(await publishReview(f.args), "published");
  assert.equal(f.posts(), 1);
  assert.equal(await publishReview(f.args), "reviewed");
  assert.equal(f.posts(), 1);
});

test("does not publish a stale head", async () => {
  const f = fixture();
  f.setStale();
  assert.equal(await publishReview(f.args), "stale");
  assert.equal(f.posts(), 0);
});

test("does not retry an ambiguous GitHub write", async () => {
  const f = fixture();
  f.setAmbiguous();
  assert.equal(await publishReview(f.args), "reconcile");
  assert.equal(await publishReview(f.args), "reconcile");
  assert.equal(f.posts(), 1);
});

test("rechecks the head immediately before publishing", async () => {
  const f = fixture();
  f.setStaleWhenMarked();
  assert.equal(await publishReview(f.args), "stale");
  assert.equal(f.posts(), 0);
});
