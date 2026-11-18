import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer } from "node:http";
import test from "node:test";
import { moveVikunjaDone } from "../move-done.mjs";

test("only moves a ticket from the Director bucket and confirms completion idempotently", async (t) => {
  let bucket = 5;
  let doneBucketId = 8;
  let writes = 0;
  const server = createServer((req, res) => {
    const path = new URL(req.url, "http://fixture.invalid").pathname;
    res.setHeader("Content-Type", "application/json");
    if (req.method === "POST") {
      assert.equal(path, "/api/v1/projects/2/views/3/buckets/8/tasks");
      writes++;
      bucket = 8;
      res.end("{}");
    } else if (path === "/api/v1/projects/2/views/3") {
      res.end(JSON.stringify({ id: 3, project_id: 2, view_kind: "kanban", done_bucket_id: doneBucketId }));
    } else if (path.endsWith("/views/3/tasks")) {
      res.end(JSON.stringify([{ id: bucket, tasks: [{ id: 4 }] }]));
    } else if (path === "/api/v1/tasks/4") {
      res.end(JSON.stringify({ id: 4, project_id: 2, done: bucket === doneBucketId }));
    } else {
      res.statusCode = 404;
      res.end("{}");
    }
  }).listen(0, "127.0.0.1");
  t.after(() => server.close());
  await once(server, "listening");
  const options = { origin: `http://127.0.0.1:${server.address().port}`, token: "synthetic",
    projectId: 2, viewId: 3, directorBucketId: 7, doneBucketId: 8, ticketId: 4 };
  assert.equal(await moveVikunjaDone(options), false);
  bucket = 7;
  doneBucketId = 9;
  await assert.rejects(moveVikunjaDone(options), /not configured for the expected Done bucket/);
  assert.equal(writes, 0);
  doneBucketId = 8;
  assert.equal(await moveVikunjaDone(options), true);
  assert.equal(await moveVikunjaDone(options), true);
  assert.equal(writes, 1);
});
