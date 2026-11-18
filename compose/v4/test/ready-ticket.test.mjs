import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import test from "node:test";
import { readReadyTicket } from "../ready-ticket.mjs";

test("checks exact board membership, paginated results, and unresolved blockers", async (t) => {
  const task = { id: 4, project_id: 2, done: false, related_tasks: { blocked: [{ id: 9, done: false }] } };
  let inReady = true;
  const calls = [];
  const server = createServer((req, res) => {
    const url = new URL(req.url, "http://fixture.invalid");
    calls.push(url.pathname);
    res.setHeader("Content-Type", "application/json");
    if (url.pathname === "/api/v1/projects/2/views/3/tasks") {
      res.setHeader("x-pagination-total-pages", "2");
      const match = url.searchParams.get("page") === "2";
      res.end(JSON.stringify([{ id: 5, tasks: match && inReady ? [{ id: 4 }] : [] }]));
    } else if (url.pathname === "/api/v1/tasks/4") {
      res.end(JSON.stringify(task));
    } else {
      res.statusCode = 404;
      res.end("{}");
    }
  }).listen(0, "127.0.0.1");
  t.after(() => server.close());
  await once(server, "listening");
  const options = { origin: `http://127.0.0.1:${server.address().port}`, token: "synthetic-fixture",
    projectId: 2, viewId: 3, readyBucketId: 5, ticketId: 4 };
  assert.equal(await readReadyTicket(options), null);
  task.related_tasks.blocked[0].done = true;
  assert.deepEqual(await readReadyTicket(options), task);
  inReady = false;
  calls.length = 0;
  assert.equal(await readReadyTicket(options), null);
  assert.ok(!calls.includes("/api/v1/tasks/4"));
});
