import assert from "node:assert/strict";
import test from "node:test";
import { discoverReviewQueue } from "../pr-review-queue.mjs";

const accounts = ["fixture-human", "fixture-bot"];
const heads = { 1: "a".repeat(40), 2: "b".repeat(40), 3: "c".repeat(40), 4: "d".repeat(40) };
const candidate = (repo, number) => ({
  number, repository_url: `https://api.github.com/repos/${repo}`, pull_request: { url: "https://api.github.com/fixture" },
});

function fixture() {
  const searches = [];
  const reads = [];
  const metadata = { incomplete: false };
  const rows = {
    "fixture-human:fixture-human": [candidate("fixture/ours", 1), candidate("fixture/external", 2)],
    "fixture-human:fixture-bot": [candidate("fixture/ours", 1), candidate("fixture/other", 3)],
    "fixture-bot:fixture-human": [candidate("FIXTURE/OURS", 1)],
    "fixture-bot:fixture-bot": [candidate("fixture/other", 3), candidate("fixture/other", 4)],
  };
  const prs = {
    "fixture/ours#1": { state: "OPEN", isDraft: false, headRefOid: heads[1],
      assignees: [{ login: "fixture-bot" }], author: { login: "fixture-bot" }, title: "Our change" },
    "fixture/external#2": { state: "OPEN", isDraft: false, headRefOid: heads[2],
      assignees: [{ login: "fixture-human" }], author: { login: "outside" }, title: "Incoming change" },
    "fixture/other#3": { state: "OPEN", isDraft: true, headRefOid: heads[3],
      assignees: [{ login: "fixture-bot" }], author: { login: "outside" }, title: "Draft" },
    "fixture/other#4": { state: "OPEN", isDraft: false, headRefOid: heads[4],
      assignees: [{ login: "outside" }], author: { login: "outside" }, title: "Unassigned" },
  };
  const gh = async (viewer, ...args) => {
    if (args[0] === "api" && args[1] === "user") return `${viewer}\n`;
    if (args[0] === "api") {
      const query = args.find((arg) => arg.startsWith("q="));
      const assignee = query.match(/assignee:([a-z0-9-]+)/)[1];
      searches.push({ viewer, assignee, args });
      const items = rows[`${viewer}:${assignee}`] ?? [];
      const pages = [];
      for (let offset = 0; offset < Math.max(items.length, 1); offset += 100) {
        pages.push({ total_count: items.length, incomplete_results: metadata.incomplete, items: items.slice(offset, offset + 100) });
      }
      return JSON.stringify(pages);
    }
    if (args[0] === "pr" && args[1] === "view") {
      const number = Number(args[2]);
      const repo = args[args.indexOf("-R") + 1].toLowerCase();
      reads.push({ viewer, repo, number });
      return JSON.stringify(prs[`${repo}#${number}`]);
    }
    throw new Error("Unexpected synthetic GitHub command");
  };
  return { accounts, gh, searches, reads, rows, prs, metadata };
}

test("deduplicates both accounts across repos and separates our PRs from incoming", async () => {
  const f = fixture();
  assert.deepEqual(await discoverReviewQueue(f), {
    ours: [{ repo: "fixture/ours", number: 1, head: heads[1], title: "Our change" }],
    incoming: [{ repo: "fixture/external", number: 2, head: heads[2], title: "Incoming change" }],
  });
  assert.equal(f.searches.length, 4);
  assert.equal(f.reads.length, 4);
  assert.ok(f.searches.every(({ args }) => args.some((arg) => arg.includes("draft:false"))));
  assert.ok(f.searches.every(({ args }) => !args.some((arg) => arg.includes("review-requested"))));
});

test("an inaccessible PR makes discovery fail instead of returning an incomplete queue", async () => {
  const f = fixture();
  const original = f.gh;
  f.gh = async (viewer, ...args) => {
    if (args[0] === "pr") throw new Error("fixture access denied");
    return original(viewer, ...args);
  };
  await assert.rejects(discoverReviewQueue(f), /detail unavailable/i);
});

test("a full GitHub search fails rather than silently truncating the queue", async () => {
  const f = fixture();
  f.rows["fixture-human:fixture-human"] = Array.from({ length: 1000 }, (_, index) => candidate("fixture/ours", index + 1));
  await assert.rejects(discoverReviewQueue(f), /search.*limit/i);
  assert.deepEqual(f.reads, []);
});

test("reads multiple search pages before creating the snapshot", async () => {
  const f = fixture();
  for (const key of Object.keys(f.rows)) f.rows[key] = [];
  f.rows["fixture-human:fixture-human"] = Array.from({ length: 101 }, (_, index) => candidate("fixture/many", index + 1));
  for (let number = 1; number <= 101; number++) {
    f.prs[`fixture/many#${number}`] = { state: "OPEN", isDraft: false, headRefOid: heads[1],
      assignees: [{ login: "fixture-human" }], author: { login: "outside" }, title: "Incoming" };
  }
  const queue = await discoverReviewQueue(f);
  assert.equal(queue.incoming.length, 101);
  assert.equal(f.reads.length, 101);
});

test("a failed account search rejects the whole snapshot", async () => {
  const f = fixture();
  const original = f.gh;
  f.gh = async (viewer, ...args) => {
    if (viewer === "fixture-bot" && args[0] === "api" && args[1] !== "user") throw new Error("fixture account unavailable");
    return original(viewer, ...args);
  };
  await assert.rejects(discoverReviewQueue(f), /account unavailable/);
  assert.deepEqual(f.reads, []);
});

test("a credential for the wrong GitHub account fails before reading PRs", async () => {
  const f = fixture();
  const original = f.gh;
  f.gh = async (viewer, ...args) => {
    if (viewer === "fixture-bot" && args[0] === "api" && args[1] === "user") return "fixture-human\n";
    return original(viewer, ...args);
  };
  await assert.rejects(discoverReviewQueue(f), /credential.*mismatch/i);
  assert.deepEqual(f.reads, []);
});

test("discovery requires both configured GitHub accounts", async () => {
  const f = fixture();
  f.accounts = ["fixture-human"];
  await assert.rejects(discoverReviewQueue(f), /both.*accounts/i);
  assert.deepEqual(f.searches, []);
});

test("uses the other authorized viewer if the first loses access", async () => {
  const f = fixture();
  const original = f.gh;
  f.gh = async (viewer, ...args) => {
    if (viewer === "fixture-human" && args[0] === "pr" && args[2] === "1") throw new Error("access lost");
    return original(viewer, ...args);
  };
  const queue = await discoverReviewQueue(f);
  assert.equal(queue.ours[0].number, 1);
  assert.ok(f.reads.some((read) => read.viewer === "fixture-bot" && read.number === 1));
});

test("missing author attribution does not silently classify a PR", async () => {
  const f = fixture();
  f.prs["fixture/ours#1"].author = null;
  await assert.rejects(discoverReviewQueue(f), /invalid github pr detail/i);
});

test("an incomplete GitHub search fails before returning a queue", async () => {
  const f = fixture();
  f.metadata.incomplete = true;
  await assert.rejects(discoverReviewQueue(f), /incomplete.*search/i);
  assert.deepEqual(f.reads, []);
});

test("a missing search result fails closed", async () => {
  const missing = fixture();
  const original = missing.gh;
  missing.gh = async (viewer, ...args) => {
    const output = await original(viewer, ...args);
    if (args[0] !== "api" || args[1] === "user") return output;
    const pages = JSON.parse(output);
    pages[0].total_count += 1;
    return JSON.stringify(pages);
  };
  await assert.rejects(discoverReviewQueue(missing), /incomplete.*search/i);
});

test("a repeated search result fails closed", async () => {
  const repeated = fixture();
  repeated.rows["fixture-human:fixture-human"].push(candidate("fixture/ours", 1));
  await assert.rejects(discoverReviewQueue(repeated), /incomplete.*search/i);
});

test("a PR closed after search cannot enter the queue", async () => {
  const closed = fixture();
  closed.prs["fixture/ours#1"].state = "CLOSED";
  assert.deepEqual((await discoverReviewQueue(closed)).ours, []);
});

test("an untrusted repository URL is rejected", async () => {
  const invalid = fixture();
  invalid.rows["fixture-human:fixture-human"][0].repository_url = "https://evil.example/repos/fixture/ours";
  await assert.rejects(discoverReviewQueue(invalid), /invalid github search result/i);
});
