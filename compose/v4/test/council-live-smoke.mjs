import { runV1Council } from "../council-v1.mjs";

if (process.argv.length !== 3 || process.argv[2] !== "--live") {
  console.error("Usage: node council-live-smoke.mjs --live (runs one paid panel on a synthetic artifact).");
  process.exitCode = 2;
} else {
  try {
    const result = await runV1Council({
      artifact: "Synthetic RFC: A single-user local to-do list stores items in memory. "
        + "Users can add and remove items. Data resets on restart. No accounts, network, "
        + "personal data, persistence, or external systems. Test add, remove, and restart.",
      revisions: 0,
    });
    console.log(JSON.stringify({
      rubricVersion: result.rubricVersion,
      decision: result.decision,
      verdicts: result.verdicts.map(({ judge_role, verdict }) => ({ judge_role, verdict })),
    }));
  } catch (error) {
    console.error(`Council smoke failed: ${error.message}`);
    process.exitCode = 1;
  }
}
