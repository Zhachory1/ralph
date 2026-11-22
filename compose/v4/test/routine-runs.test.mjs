import assert from "node:assert/strict";
import test from "node:test";
import { findRoutineRun, routineRunHistory } from "../routine-runs.mjs";

const runs = Array.from({ length: 201 }, (_, index) => ({
  id: `11111111-1111-4111-8111-${String(index).padStart(12, "0")}`,
}));

test("reads every page and finds an older run", async () => {
  const calls = [];
  const board = async (path) => {
    calls.push(path);
    if (path === "routines/fixture/runs?limit=200") return runs.slice(0, 200);
    if (path === `routines/fixture/runs?limit=200&before=${runs[199].id}`) return runs.slice(200);
    throw new Error(`Unexpected path ${path}`);
  };
  assert.deepEqual(await Array.fromAsync(routineRunHistory(board, "fixture")), runs);
  assert.equal((await findRoutineRun(board, "fixture", runs[200].id)).id, runs[200].id);
  assert.equal(calls.length, 4);
});

test("fails closed if an older Paperclip server ignores the cursor", async () => {
  const board = async () => runs.slice(0, 200);
  let seen = 0;
  await assert.rejects(async () => {
    for await (const _run of routineRunHistory(board, "fixture")) seen++;
  }, /cursor/i);
  assert.equal(seen, 200);
});

test("rejects a short page that repeats an earlier cursor", async () => {
  const second = Array.from({ length: 200 }, (_, index) => ({
    id: `22222222-2222-4222-8222-${String(index).padStart(12, "0")}`,
  }));
  const board = async (path) => {
    if (path === "routines/fixture/runs?limit=200") return runs.slice(0, 200);
    if (path.endsWith(`before=${runs[199].id}`)) return second;
    if (path.endsWith(`before=${second[199].id}`)) return [runs[199]];
    throw new Error(`Unexpected path ${path}`);
  };
  await assert.rejects(Array.fromAsync(routineRunHistory(board, "fixture")), /cursor/i);
});
