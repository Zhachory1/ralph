import { readFileSync } from "node:fs";
import { validateCouncilVerdict } from "./council-verdict.mjs";
import { runProviderWorker } from "./provider-worker.mjs";

export { WorkerCleanupError as CouncilCleanupError } from "./provider-worker.mjs";

const MODELS = Object.freeze({
  architecture_scale: "anthropic/claude-sonnet-5.5",
  security_blast_radius: "google/gemini-3.8-flash",
  scope_pragmatism: "openai/gpt-6.2-sol",
});
const WORKER = readFileSync(new URL("./council-judge-worker.py", import.meta.url), "utf8");
const MAX_OUTPUT = 1024 * 1024;

export async function invokeCouncilJudge({ judge_role, artifact, rubric }) {
  if (!Object.hasOwn(MODELS, judge_role)) throw new TypeError("Unknown judge role");
  if (typeof artifact !== "string" || !artifact.trim() || typeof rubric !== "string" || !rubric.trim()) {
    throw new TypeError("Expected an artifact and rubric");
  }
  const runId = process.env.RALPH_COUNCIL_RUN_ID;
  const prefix = /^[a-f0-9]{64}$/.test(runId ?? "") ? `ralph-council-judge-${runId}` : "ralph-council-judge";
  const output = await runProviderWorker({ prefix, worker: WORKER, maxOutput: MAX_OUTPUT,
    job: { role: judge_role, model: MODELS[judge_role], artifact, rubric } });
  let verdict;
  try { verdict = JSON.parse(output); } catch { throw new Error("Judge did not return a JSON verdict"); }
  if (verdict?.judge_role !== judge_role) throw new Error("Judge verdict does not match its assigned role");
  validateCouncilVerdict(verdict);
  return verdict;
}
