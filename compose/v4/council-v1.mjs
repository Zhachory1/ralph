import { readFileSync } from "node:fs";
import { invokeCouncilJudge } from "./council-judge.mjs";
import { runCouncilPanel } from "./council-panel.mjs";

export async function runV1Council({ artifact, revisions }) {
  const rubric = readFileSync(new URL("../../profiles/council/rubric-v1.md", import.meta.url), "utf8");
  const result = await runCouncilPanel({ artifact, rubric, revisions }, invokeCouncilJudge);
  return { rubricVersion: "v1", ...result };
}
