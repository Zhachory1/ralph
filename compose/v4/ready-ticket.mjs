const MAX_BOARD_PAGES = 100;

export async function readReadyTicket({ origin, token, projectId, viewId, readyBucketId, ticketId }) {
  if (![projectId, viewId, readyBucketId, ticketId].every((id) => Number.isSafeInteger(id) && id > 0) || !token) {
    throw new Error("Vikunja eligibility requires a credential and positive board and ticket IDs");
  }
  const read = async (path) => {
    const response = await fetch(new URL(`/api/v1${path}`, origin), {
      headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
      redirect: "error",
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error(`Vikunja eligibility lookup failed (${response.status})`);
    return { data: await response.json(), headers: response.headers };
  };

  for (let page = 1; page <= MAX_BOARD_PAGES; page++) {
    const { data, headers } = await read(`/projects/${projectId}/views/${viewId}/tasks?page=${page}&per_page=50`);
    if (!Array.isArray(data)) throw new Error("Expected a Vikunja Kanban bucket list");
    const tasks = data.find((bucket) => bucket.id === readyBucketId)?.tasks ?? [];
    if (!Array.isArray(tasks)) throw new Error("Expected a Vikunja Ready task list");
    if (tasks.some((task) => task.id === ticketId)) {
      const { data: task } = await read(`/tasks/${ticketId}`);
      if (task?.id !== ticketId || task.project_id !== projectId || task.done !== false) return null;
      const blockers = task.related_tasks?.blocked ?? [];
      if (!Array.isArray(blockers)) throw new Error("Expected a Vikunja blocked relation list");
      return blockers.every((blocker) => blocker?.done === true) ? task : null;
    }
    const totalPages = Number(headers.get("x-pagination-total-pages") ?? 1);
    if (!Number.isSafeInteger(totalPages) || totalPages < 0 || totalPages > MAX_BOARD_PAGES) {
      throw new Error("Invalid or oversized Vikunja board pagination");
    }
    if (page >= totalPages) return null;
  }
  return null;
}
