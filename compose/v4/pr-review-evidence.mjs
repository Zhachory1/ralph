// Collect a read-only diff for one eligible, exact-head PR.
import { createPrEligibility } from "./github-pr-eligibility.mjs";

const repoSlug = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9_.-]{1,100}$/;
const sha = /^[a-f0-9]{40}$/;
const maxFiles = 300;
const maxDiffBytes = 1_048_576;

export async function collectReviewEvidence({ repo, number, head, assignees, readPr, gh }) {
  if (!repoSlug.test(repo ?? "") || !Number.isSafeInteger(number) || number < 1
      || !sha.test(head ?? "") || typeof readPr !== "function" || typeof gh !== "function") {
    throw new Error("Invalid PR review evidence request");
  }
  const eligible = (pr, expectedHead) => createPrEligibility({ assignees, readPr: async () => pr })(number, expectedHead);
  const first = await readPr(repo, number);
  if (!await eligible(first, head)) return { status: "ineligible" };
  const base = first.baseRefOid;
  if (typeof base !== "string" || !sha.test(base)) throw new Error("Could not verify PR base");
  const currentStatus = async () => {
    const last = await readPr(repo, number);
    if (!await eligible(last)) return "ineligible";
    if (last.headRefOid !== head || last.baseRefOid !== base) return "stale";
    return null;
  };
  const path = `repos/${repo}/compare/${base}...${head}`;
  const comparison = JSON.parse(await gh("api", path));
  if (!Array.isArray(comparison?.files)) throw new Error("Could not verify comparison files");
  if (comparison.files.length >= maxFiles) throw new Error("GitHub comparison reached its file limit");
  if (comparison.files.length === 0) return { status: await currentStatus() ?? "empty" };
  if (comparison.files.some((file) => {
    if (typeof file?.filename !== "string" || !file.filename || typeof file.patch !== "string" || !file.patch
        || !Number.isSafeInteger(file.additions) || file.additions < 0
        || !Number.isSafeInteger(file.deletions) || file.deletions < 0) return true;
    const lines = file.patch.split("\n");
    return lines.filter((line) => line.startsWith("+")).length !== file.additions
      || lines.filter((line) => line.startsWith("-")).length !== file.deletions;
  })) {
    throw new Error("GitHub comparison contains unreviewable file content");
  }
  const diff = await gh("api", "--header", "Accept: application/vnd.github.v3.diff", path);
  if (typeof diff !== "string" || Buffer.byteLength(diff) === 0 || Buffer.byteLength(diff) > maxDiffBytes) {
    throw new Error("PR diff is empty or exceeds the size limit");
  }
  const changes = (text) => text.match(/^[+-].*$/gm) ?? [];
  const expected = comparison.files.flatMap((file) => changes(file.patch));
  const actual = diff.split(/^diff --git /m).slice(1).flatMap((file) => {
    const start = file.indexOf("\n@@ ");
    return start < 0 ? [] : changes(file.slice(start));
  });
  if (JSON.stringify(actual) !== JSON.stringify(expected)) throw new Error("Incomplete GitHub diff");
  const status = await currentStatus();
  if (status) return { status };
  return { status: "ready", repo, number, head, base, diff };
}
