import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createServer } from "node:http";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);
const companyId = "22222222-2222-2222-2222-222222222222";
const routineId = "11111111-1111-1111-1111-111111111111";
const runId = "33333333-3333-3333-3333-333333333333";
const botLogin = "ralph-bot";
const directorUserId = "director-fixture-token";
const worker = fileURLToPath(new URL("../routine-maintenance-worker.mjs", import.meta.url));

// Valid in every field the worker checks, so refusal below is provably about the run's
// non-manual source, not an incidental payload or company mismatch.
const triggerPayload = {
  issueId: "44444444-4444-4444-4444-444444444444", repo: "fixture/outbound", pr_number: 9,
  head_sha: "a".repeat(40), event_type: "ci_failure", event_id: "check_suite:1", author_login: "a-human",
};

// One CLI boundary test: the real worker, run as a child process against a loopback-only
// local HTTP fixture standing in for Paperclip. No Docker, no live credentials, no writes.
test("the CLI refuses a non-manual Routine run, with a matching company and no side effects", async () => {
  const requests = [];
  const server = createServer((req, res) => {
    requests.push(`${req.method} ${req.url}`);
    if (req.url === `/api/routines/${routineId}` && req.method === "GET") {
      res.end(JSON.stringify({ id: routineId, companyId }));
      return;
    }
    if (req.url === `/api/routines/${routineId}/runs` && req.method === "GET") {
      res.end(JSON.stringify([
        { id: runId, companyId, routineId, source: "scheduled", triggerPayload },
      ]));
      return;
    }
    // Any other path (comments, documents, interactions, ...) is a side effect this run must
    // never reach; respond and still record it above so the assertion below can catch it.
    res.statusCode = 404;
    res.end("{}");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const origin = `http://127.0.0.1:${server.address().port}`;
    const result = await run("node", [worker, origin, companyId, routineId, runId, botLogin, directorUserId], {
      env: { ...process.env, PAPERCLIP_API_KEY: "fixture-token" },
    }).catch((error) => error);

    assert.notEqual(result.code, 0, "a non-manual run must not exit cleanly");
    assert.equal(result.stdout, "", "no result is printed for a refused run");
    assert.match(result.stderr, /Expected a manually triggered Routine run with a trigger payload/);
    assert.doesNotMatch(result.stderr, /Unexpected Routine company/, "the company matched; refusal must be about source");
    assert.deepEqual(requests, [`GET /api/routines/${routineId}`, `GET /api/routines/${routineId}/runs`],
      "no comment, document, or interaction request may be attempted for a refused run");
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
