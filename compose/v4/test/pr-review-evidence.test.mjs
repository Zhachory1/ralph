import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { collectReviewEvidence } from "../pr-review-evidence.mjs";

const repo = "fixture/project";
const head = "a".repeat(40);
const base = "b".repeat(40);
const mergeBase = "c".repeat(40);
const diff = "diff --git a/example.js b/example.js\n--- a/example.js\n+++ b/example.js\n@@ -1 +1 @@\n-old\n+new\n";

function fixture() {
  const calls = [];
  let pr = { state: "OPEN", isDraft: false, assignees: [{ login: "fixture-bot" }],
    headRefOid: head, baseRefOid: base };
  let comparison = { files: [{ filename: "example.js", status: "modified", additions: 1, deletions: 1,
    patch: "@@ -1 +1 @@\n-old\n+new" }] };
  let patch = diff;
  const tree = (paths, mode = "100644") => paths.map((path) => ({ path, mode, sha: head, type: "blob" }));
  const before = tree(["example.js", "other.js", "old.js"]);
  let after = tree(["example.js", "other.js", "new.js"]);
  return {
    calls,
    setPr: (value) => { pr = value; },
    setComparison: (value) => { comparison = value; },
    setPatch: (value) => { patch = value; },
    setMode: () => { after = tree(["example.js"], "100755"); },
    options: {
      repo, number: 7, head, assignees: ["fixture-bot", "fixture-human"],
      readPr: async () => pr,
      gh: async (...args) => {
        calls.push(args);
        if (args[1].includes("/git/trees/")) {
          assert.match(args[1], new RegExp(`/git/trees/(${mergeBase}|${head})\\?recursive=1$`));
          return JSON.stringify({ truncated: false, tree: args[1].includes(mergeBase) ? before : after });
        }
        return args.includes("--header") ? patch : JSON.stringify({ merge_base_commit: { sha: mergeBase }, ...comparison });
      },
    },
  };
}

test("collects the exact base and head diff without executing PR code", async () => {
  const f = fixture();
  assert.deepEqual(await collectReviewEvidence(f.options), { status: "ready", repo, number: 7, head, base, diff });
  assert.deepEqual(f.calls, [
    ["api", `repos/${repo}/compare/${base}...${head}`],
    ["api", "--header", "Accept: application/vnd.github.v3.diff", `repos/${repo}/compare/${base}...${head}`],
    ["api", `repos/${repo}/git/trees/${mergeBase}?recursive=1`],
    ["api", `repos/${repo}/git/trees/${head}?recursive=1`],
  ]);
});

test("does not fetch a diff for a draft or unassigned PR", async () => {
  const f = fixture();
  f.setPr({ state: "OPEN", isDraft: true, assignees: [{ login: "fixture-bot" }],
    headRefOid: head, baseRefOid: base });
  assert.deepEqual(await collectReviewEvidence(f.options), { status: "ineligible" });
  assert.deepEqual(f.calls, []);
});

test("discards a diff if the PR becomes a draft during collection", async () => {
  const f = fixture();
  let reads = 0;
  f.options.readPr = async () => ({ state: "OPEN", isDraft: ++reads > 1,
    assignees: [{ login: "fixture-bot" }], headRefOid: head, baseRefOid: base });
  assert.deepEqual(await collectReviewEvidence(f.options), { status: "ineligible" });
});

test("discards a diff if the PR is unassigned during collection", async () => {
  const f = fixture();
  let reads = 0;
  f.options.readPr = async () => ({ state: "OPEN", isDraft: false,
    assignees: [{ login: ++reads === 1 ? "fixture-bot" : "someone-else" }], headRefOid: head, baseRefOid: base });
  assert.deepEqual(await collectReviewEvidence(f.options), { status: "ineligible" });
});

test("discards a diff if the base changes during collection", async () => {
  const f = fixture();
  let reads = 0;
  f.options.readPr = async () => ({ state: "OPEN", isDraft: false, assignees: [{ login: "fixture-bot" }],
    headRefOid: head, baseRefOid: ++reads === 1 ? base : "c".repeat(40) });
  assert.deepEqual(await collectReviewEvidence(f.options), { status: "stale" });
});

test("discards a diff if the head changes during collection", async () => {
  const f = fixture();
  let reads = 0;
  f.options.readPr = async () => ({ state: "OPEN", isDraft: false, assignees: [{ login: "fixture-bot" }],
    headRefOid: ++reads === 1 ? head : "c".repeat(40), baseRefOid: base });
  assert.deepEqual(await collectReviewEvidence(f.options), { status: "stale" });
});

test("refuses a comparison that may exceed GitHub's file limit", async () => {
  const f = fixture();
  f.setComparison({ files: Array.from({ length: 300 }, () => ({ filename: "file" })) });
  await assert.rejects(collectReviewEvidence(f.options), /file limit/i);
  assert.equal(f.calls.length, 1);
});

test("refuses oversized evidence rather than truncating it", async () => {
  const f = fixture();
  f.setPatch("x".repeat(1_048_577));
  await assert.rejects(collectReviewEvidence(f.options), /size limit/i);
});

test("refuses a comparison with unreviewable file content", async () => {
  const f = fixture();
  f.setComparison({ files: [{ filename: "binary.dat" }] });
  await assert.rejects(collectReviewEvidence(f.options), /unreviewable/i);
  assert.equal(f.calls.length, 1);
});

test("refuses a file patch that omits reported changed lines", async () => {
  const f = fixture();
  f.setComparison({ files: [{ filename: "example.js", additions: 2, deletions: 1,
    patch: "@@ -1 +1 @@\n-old\n+new" }] });
  await assert.rejects(collectReviewEvidence(f.options), /unreviewable/i);
  assert.equal(f.calls.length, 1);
});

test("reports an empty comparison without fetching a diff", async () => {
  const f = fixture();
  f.setComparison({ files: [] });
  assert.deepEqual(await collectReviewEvidence(f.options), { status: "empty" });
  assert.equal(f.calls.length, 1);
});

test("rechecks eligibility before reporting an empty comparison", async () => {
  const f = fixture();
  let reads = 0;
  f.setComparison({ files: [] });
  f.options.readPr = async () => ({ state: "OPEN", isDraft: ++reads > 1,
    assignees: [{ login: "fixture-bot" }], headRefOid: head, baseRefOid: base });
  assert.deepEqual(await collectReviewEvidence(f.options), { status: "ineligible" });
});

test("refuses a diff missing a patch from the comparison", async () => {
  const f = fixture();
  f.setPatch("diff --git a/example.js b/example.js\n");
  await assert.rejects(collectReviewEvidence(f.options), /incomplete.*diff/i);
});

test("accepts identical changes split into different GitHub hunks", async () => {
  const f = fixture();
  const patch = "diff --git a/example.js b/example.js\n--- a/example.js\n+++ b/example.js\n"
    + "@@ -1 +0,0 @@\n-old\n@@ -1,0 +1 @@\n+new\n";
  f.setPatch(patch);
  assert.equal((await collectReviewEvidence(f.options)).diff, patch);
});

test("refuses matching changes attributed to a different file", async () => {
  const f = fixture();
  f.setPatch("diff --git a/wrong.js b/wrong.js\n--- a/wrong.js\n+++ b/wrong.js\n@@ -1 +1 @@\n-old\n+new\n");
  await assert.rejects(collectReviewEvidence(f.options), /incomplete.*diff/i);
});

test("refuses a hunk truncated after its final change", async () => {
  const f = fixture();
  f.setPatch("diff --git a/example.js b/example.js\n--- a/example.js\n+++ b/example.js\n@@ -1,2 +1,2 @@\n-old\n+new\n");
  await assert.rejects(collectReviewEvidence(f.options), /incomplete.*diff/i);
});

test("accepts a complete multi-file diff", async () => {
  const f = fixture();
  f.setComparison({ files: [
    { filename: "example.js", status: "modified", additions: 1, deletions: 1, patch: "@@ -1 +1 @@\n-old\n+new" },
    { filename: "other.js", status: "modified", additions: 1, deletions: 1, patch: "@@ -1 +1 @@\n-before\n+after" },
  ] });
  const other = "diff --git a/other.js b/other.js\n--- a/other.js\n+++ b/other.js\n@@ -1 +1 @@\n-before\n+after\n";
  f.setPatch(diff + other);
  assert.equal((await collectReviewEvidence(f.options)).diff, diff + other);
});

test("refuses a diff with a different file status", async () => {
  const f = fixture();
  f.setComparison({ files: [{ filename: "example.js", status: "added", additions: 1, deletions: 1,
    patch: "@@ -1 +1 @@\n-old\n+new" }] });
  await assert.rejects(collectReviewEvidence(f.options), /incomplete.*diff/i);
});

test("checks both paths of a renamed file", async () => {
  const f = fixture();
  const patch = "diff --git a/old.js b/new.js\nsimilarity index 50%\nrename from old.js\nrename to new.js\n"
    + "--- a/old.js\n+++ b/new.js\n@@ -1 +1 @@\n-old\n+new\n";
  f.setComparison({ files: [{ filename: "new.js", previous_filename: "old.js", status: "renamed",
    additions: 1, deletions: 1, patch: "@@ -1 +1 @@\n-old\n+new" }] });
  f.setPatch(patch);
  assert.equal((await collectReviewEvidence(f.options)).diff, patch);
  f.setComparison({ files: [{ filename: "new.js", previous_filename: "wrong.js", status: "renamed",
    additions: 1, deletions: 1, patch: "@@ -1 +1 @@\n-old\n+new" }] });
  await assert.rejects(collectReviewEvidence(f.options), /incomplete.*diff/i);
});

test("refuses matching changes at different line numbers", async () => {
  const f = fixture();
  f.setPatch(diff.replace("@@ -1 +1 @@", "@@ -20 +20 @@"));
  await assert.rejects(collectReviewEvidence(f.options), /incomplete.*diff/i);
});

test("refuses mismatched missing-final-newline markers", async () => {
  const f = fixture();
  f.setComparison({ files: [{ filename: "example.js", status: "modified", additions: 1, deletions: 1,
    patch: "@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new" }] });
  await assert.rejects(collectReviewEvidence(f.options), /incomplete.*diff/i);
});

test("accepts a diff containing embedded line separators", async () => {
  const f = fixture();
  const patch = "@@ -1 +1 @@\n-old\rvalue\n+new\u2028value";
  f.setComparison({ files: [{ filename: "example.js", status: "modified", additions: 1, deletions: 1, patch }] });
  const raw = `diff --git a/example.js b/example.js\n--- a/example.js\n+++ b/example.js\n${patch}\n`;
  f.setPatch(raw);
  assert.equal((await collectReviewEvidence(f.options)).diff, raw);
});

test("parses paths outside the caller's repository subdirectory", async () => {
  const f = fixture();
  const previous = process.cwd();
  process.chdir(fileURLToPath(new URL("..", import.meta.url)));
  try {
    assert.equal((await collectReviewEvidence(f.options)).diff, diff);
  } finally {
    process.chdir(previous);
  }
});

test("accepts a complete pure rename", async () => {
  const f = fixture();
  const raw = "diff --git a/old.js b/new.js\nsimilarity index 100%\nrename from old.js\nrename to new.js\n";
  f.setComparison({ files: [{ filename: "new.js", previous_filename: "old.js", status: "renamed",
    additions: 0, deletions: 0 }] });
  f.setPatch(raw);
  assert.equal((await collectReviewEvidence(f.options)).diff, raw);
});

test("refuses altered context even when edit positions and content match", async () => {
  const f = fixture();
  f.setComparison({ files: [{ filename: "example.js", status: "modified", additions: 1, deletions: 1,
    patch: "@@ -1,2 +1,2 @@\n-old\n+new\n real context" }] });
  f.setPatch(diff.replace("@@ -1 +1 @@", "@@ -1,2 +1,2 @@") + " changed context\n");
  await assert.rejects(collectReviewEvidence(f.options), /incomplete.*diff/i);
});

test("accepts shorter raw context when all displayed lines are pinned", async () => {
  const f = fixture();
  f.setComparison({ files: [{ filename: "example.js", status: "modified", additions: 1, deletions: 1,
    patch: "@@ -1,2 +1,2 @@\n-old\n+new\n real context" }] });
  assert.equal((await collectReviewEvidence(f.options)).diff, diff);
});

test("accepts an exact mode-only change and refuses an altered mode", async () => {
  const f = fixture();
  const metadata = "diff --git a/example.js b/example.js\nold mode 100644\nnew mode 100755\n";
  f.setMode();
  f.setComparison({ files: [{ filename: "example.js", status: "modified", additions: 0, deletions: 0 }] });
  f.setPatch(metadata);
  assert.equal((await collectReviewEvidence(f.options)).diff, metadata);
  f.setPatch(metadata.replace("new mode 100755", "new mode 120000"));
  await assert.rejects(collectReviewEvidence(f.options), /incomplete.*diff/i);
});

test("refuses a text diff that hides a mode change", async () => {
  const f = fixture();
  f.setMode();
  await assert.rejects(collectReviewEvidence(f.options), /incomplete.*diff/i);
});
