import { findVikunjaBucket, requestVikunja } from "./ready-ticket.mjs";

export async function moveVikunjaDone(options) {
  const { projectId, viewId, ticketId, directorBucketId, doneBucketId } = options;
  if (![directorBucketId, doneBucketId].every((id) => Number.isSafeInteger(id) && id > 0)
      || directorBucketId === doneBucketId) throw new Error("Expected distinct Director and Done buckets");
  const bucket = await findVikunjaBucket(options);
  if (bucket !== directorBucketId && bucket !== doneBucketId) return false;
  const viewPath = `/projects/${projectId}/views/${viewId}`;
  const { data: view } = await requestVikunja(options, viewPath);
  if (view?.id !== viewId || view.project_id !== projectId || view.view_kind !== "kanban" || view.done_bucket_id !== doneBucketId) {
    throw new Error("Vikunja view is not configured for the expected Done bucket");
  }
  if (bucket === directorBucketId) {
    if (await findVikunjaBucket(options) !== directorBucketId) return false;
    await requestVikunja(options, `${viewPath}/buckets/${doneBucketId}/tasks`, { task_id: ticketId, bucket_id: doneBucketId });
  }
  const { data: task } = await requestVikunja(options, `/tasks/${ticketId}`);
  return task?.id === ticketId && task.project_id === projectId && task.done === true
    && (await findVikunjaBucket(options)) === doneBucketId;
}
