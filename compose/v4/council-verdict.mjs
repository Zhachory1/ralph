// Validate the standalone host adapter's structured response; agent handoffs use comments.
export const COUNCIL_ROLES = Object.freeze(["architecture_scale", "security_blast_radius", "scope_pragmatism"]);
export const MAX_COUNCIL_REVISIONS = 2;
const VERDICT_KEYS = ["judge_role", "verdict", "confidence", "blocking_issues", "non_blocking_notes"];
const ISSUE_KEYS = ["severity", "location", "issue", "recommendation"];

function hasKeys(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function nonempty(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function validVerdict(value) {
  return hasKeys(value, VERDICT_KEYS) && COUNCIL_ROLES.includes(value.judge_role)
    && ["PASS", "REVISE", "BLOCK"].includes(value.verdict)
    && typeof value.confidence === "number" && Number.isFinite(value.confidence)
    && value.confidence >= 0 && value.confidence <= 1
    && Array.isArray(value.blocking_issues)
    && Array.from(value.blocking_issues).every((issue) => hasKeys(issue, ISSUE_KEYS)
      && ["critical", "major"].includes(issue.severity)
      && nonempty(issue.location) && nonempty(issue.issue) && nonempty(issue.recommendation))
    && (value.verdict === "PASS" ? value.blocking_issues.length === 0 : value.blocking_issues.length > 0)
    && Array.isArray(value.non_blocking_notes) && Array.from(value.non_blocking_notes).every(nonempty);
}

export function validateCouncilVerdict(verdict) {
  if (!validVerdict(verdict)) throw new TypeError("Invalid Council judge verdict");
}

// revisions is the number of completed author revision cycles, not the review round.
export function councilDecision(verdicts, revisions) {
  if (!Number.isInteger(revisions) || revisions < 0 || revisions > MAX_COUNCIL_REVISIONS
      || !Array.isArray(verdicts) || verdicts.length !== COUNCIL_ROLES.length
      || !Array.from(verdicts).every(validVerdict)
      || !COUNCIL_ROLES.every((role) => verdicts.some((v) => v.judge_role === role))) {
    throw new TypeError("Expected three distinct valid judge verdicts and 0–2 completed revisions");
  }
  if (verdicts.some((v) => v.verdict === "BLOCK")) return "escalate";
  if (verdicts.some((v) => v.verdict === "REVISE")) return revisions < MAX_COUNCIL_REVISIONS ? "revise" : "escalate";
  return "advance";
}
