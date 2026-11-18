import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { once } from "node:events";
import test from "node:test";
import { createReadyTickServer } from "../ready-tick-server.mjs";

test("requires a bearer and prevents overlapping Ready passes", async (t) => {
  const token = randomBytes(32).toString("hex");
  let finish;
  let runs = 0;
  const pending = new Promise((resolve) => { finish = resolve; });
  const server = createReadyTickServer(token, async () => { runs++; await pending; }).listen(0, "127.0.0.1");
  t.after(() => { finish(); return new Promise((resolve) => server.close(resolve)); });
  await once(server, "listening");
  const url = `http://127.0.0.1:${server.address().port}/tick`;
  const headers = { Authorization: `Bearer ${token}` };
  assert.equal((await fetch(url, { method: "POST" })).status, 401);
  assert.equal((await fetch(url, { method: "POST", headers: { Authorization: `Bearer ${"0".repeat(64)}` } })).status, 401);
  assert.equal((await fetch(url, { method: "POST", headers: { ...headers, Origin: "https://fixture.invalid" } })).status, 403);
  assert.equal((await fetch(url, { method: "POST", headers, body: "{}" })).status, 400);
  const accepted = await fetch(url, { method: "POST", headers });
  assert.equal(accepted.status, 202);
  assert.deepEqual(await accepted.json(), { status: "accepted" });
  const busy = await fetch(url, { method: "POST", headers });
  assert.equal(busy.status, 202);
  assert.deepEqual(await busy.json(), { status: "busy" });
  assert.equal(runs, 1);
});
