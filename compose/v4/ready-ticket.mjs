const MAX_BOARD_PAGES = 100;

export async function requestVikunja({ origin, token }, path, payload) {
  if (!token) throw new Error("Vikunja requires a scoped credential");
  const response = await fetch(new URL(`/api/v1${path}`, origin), {
    method: payload ? "POST" : "GET",
    body: payload && JSON.stringify(payload),
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json", "Content-Type": "application/json" },
    redirect: "error",
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`Vikunja request failed (${response.status})`);
  return { data: await response.json(), headers: response.headers };
}

export async function findVikunjaBucket(options) {
  const { projectId, viewId, ticketId } = options;
  if (![projectId, viewId, ticketId].every((id) => Number.isSafeInteger(id) && id > 0)) {
    throw new Error("Vikunja requires positive project, view, and ticket IDs");
  }
  for (let page = 1; page <= MAX_BOARD_PAGES; page++) {
    const { data, headers } = await requestVikunja(options, `/projects/${projectId}/views/${viewId}/tasks?page=${page}&per_page=50`);
    if (!Array.isArray(data)) throw new Error("Expected a Vikunja Kanban bucket list");
    for (const bucket of data) {
      const tasks = bucket.tasks ?? [];
      if (!Array.isArray(tasks)) throw new Error("Expected a Vikunja task list");
      if (tasks.some((task) => task.id === ticketId)) return bucket.id;
    }
    const totalPages = Number(headers.get("x-pagination-total-pages") ?? 1);
    if (!Number.isSafeInteger(totalPages) || totalPages < 0 || totalPages > MAX_BOARD_PAGES) {
      throw new Error("Invalid or oversized Vikunja board pagination");
    }
    if (page >= totalPages) return null;
  }
  return null;
}

export async function readReadyTicket(options) {
  const { projectId, readyBucketId, ticketId } = options;
  if (!Number.isSafeInteger(readyBucketId) || readyBucketId < 1) throw new Error("Expected a positive Ready bucket ID");
  if (await findVikunjaBucket(options) !== readyBucketId) return null;
  const { data: task } = await requestVikunja(options, `/tasks/${ticketId}`);
  if (task?.id !== ticketId || task.project_id !== projectId || task.done !== false) return null;
  const blockers = task.related_tasks?.blocked ?? [];
  if (!Array.isArray(blockers)) throw new Error("Expected a Vikunja blocked relation list");
  return blockers.every((blocker) => blocker?.done === true) ? task : null;
}
