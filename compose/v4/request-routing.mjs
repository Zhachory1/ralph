// Decide whether a Hermes proposal may advance; never dispatch or resolve a human gate.
import { createHash } from "node:crypto";
import { councilDecision, MAX_COUNCIL_REVISIONS } from "./council-verdict.mjs";

// Only proposal is model output. The host supplies policy and evidence scoped to this request.
// directorApprovedSha256 is from a verified Paperclip approval requested after this Council PASS.
export function decideRequestRoute(proposal, {
  allowed, initiative, consequential, registry, council, revisions, directorApprovedSha256,
} = {}) {
  if (allowed !== true || typeof initiative !== "boolean" || typeof consequential !== "boolean") return "denied";
  if (proposal?.kind === "registered" && !initiative) {
    const validate = registry instanceof Map && typeof proposal.workflow === "string"
      ? registry.get(proposal.workflow) : null;
    if (typeof validate !== "function") return "clarify";
    try {
      if (validate(proposal.inputs) !== true) return "clarify";
    } catch {
      return "clarify";
    }
    return consequential ? "human" : "advance";
  }

  if (proposal?.kind !== "novel" || !["coding", "non-coding"].includes(proposal.domain)
      || typeof proposal.artifact !== "string" || !proposal.artifact.trim()
      || !proposal.artifact.isWellFormed()) return "clarify";
  if (!Number.isInteger(revisions) || revisions < 0 || revisions > MAX_COUNCIL_REVISIONS) return "escalate";
  if (council === undefined) return "review";

  const digest = createHash("sha256").update(proposal.artifact).digest("hex");
  if (council?.status !== "completed" || council.artifactSha256 !== digest) return "escalate";
  try {
    const decision = councilDecision(council.result?.verdicts, revisions);
    if (decision !== "advance") return decision;
  } catch {
    return "escalate";
  }

  if (initiative && directorApprovedSha256 !== digest) return "director";
  return consequential ? "human" : "advance";
}
