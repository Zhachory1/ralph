import { COUNCIL_ROLES, MAX_COUNCIL_REVISIONS, councilDecision } from "./council-verdict.mjs";

// invokeJudge must isolate and time-limit each execution and return one parsed verdict for its role.
export async function runCouncilPanel({ artifact, rubric, revisions }, invokeJudge) {
  if (typeof artifact !== "string" || !artifact.trim() || typeof rubric !== "string" || !rubric.trim()
      || !Number.isInteger(revisions) || revisions < 0 || revisions > MAX_COUNCIL_REVISIONS
      || typeof invokeJudge !== "function") {
    throw new TypeError("Expected an artifact, rubric, 0–2 completed revisions, and a judge executor");
  }
  const results = await Promise.allSettled(COUNCIL_ROLES.map(async (judge_role) => {
    const result = await invokeJudge({ judge_role, artifact, rubric });
    if (result?.judge_role !== judge_role) throw new TypeError("Judge verdict does not match its assigned role");
    return result;
  }));
  const failures = results.filter((result) => result.status === "rejected").map((result) => result.reason);
  if (failures.length === 1) throw failures[0];
  if (failures.length > 1) {
    throw new AggregateError(failures, `Multiple Council judges failed: ${failures[0]?.message ?? "unknown error"}`);
  }
  const verdicts = results.map((result) => result.value);
  return { decision: councilDecision(verdicts, revisions), verdicts };
}
