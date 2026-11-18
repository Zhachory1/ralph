// Draft RFC text without tools, checkout, or publication credentials.
import { readFileSync } from "node:fs";
import { runProviderWorker } from "./provider-worker.mjs";

const worker = readFileSync(new URL("./author-worker.py", import.meta.url), "utf8");
const maxDraftBytes = 16 * 1024;
// A 20 KiB total leaves room for worst-case JSON escaping under Linux's per-argument limit.
const maxContextBytes = 4 * 1024;
const text = (value) => typeof value === "string" && value.trim();

export async function draftRfc(job) {
  if (!job || Object.keys(job).some((key) => !["brief", "model", "currentDraft", "feedback"].includes(key))) {
    throw new TypeError("Expected an RFC draft request");
  }
  const { brief, model, currentDraft, feedback } = job;
  const revision = currentDraft !== undefined || feedback !== undefined;
  if (!text(brief) || typeof model !== "string" || model.length > 128 || !/^[\w.-]+\/[\w.-]+$/.test(model)
      || (revision && (!text(currentDraft) || !text(feedback) || Buffer.byteLength(currentDraft) > maxDraftBytes))
      || Buffer.byteLength(brief) + Buffer.byteLength(feedback ?? "") > maxContextBytes) {
    throw new TypeError("Expected a brief and provider/model; revisions need both current draft and feedback");
  }
  const output = await runProviderWorker({ prefix: "ralph-author", worker, maxOutput: maxDraftBytes,
    job: { brief, model, ...(revision && { currentDraft, feedback }) } });
  if (!output.trim()) throw new Error("Author returned an empty RFC");
  return output;
}
