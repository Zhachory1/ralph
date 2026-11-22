// Traverse Paperclip Routine history without silently dropping older runs.
const pageSize = 200;
const uuid = /^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;

export async function* routineRunHistory(board, routineId) {
  let before;
  const cursors = new Set();
  for (;;) {
    const path = `routines/${routineId}/runs?limit=${pageSize}`
      + (before ? `&before=${before}` : "");
    const runs = await board(path);
    if (!Array.isArray(runs) || runs.length > pageSize
        || runs.some((run) => cursors.has(run.id))) {
      throw new Error("Paperclip Routine history cursor did not advance");
    }
    const next = runs.at(-1)?.id;
    if (runs.length === pageSize && (!uuid.test(next ?? "") || cursors.has(next))) {
      throw new Error("Invalid Paperclip Routine history cursor");
    }
    yield* runs;
    if (runs.length < pageSize) return;
    cursors.add(next);
    before = next;
  }
}

export async function findRoutineRun(board, routineId, runId) {
  for await (const run of routineRunHistory(board, routineId)) {
    if (run.id === runId) return run;
  }
  return null;
}
