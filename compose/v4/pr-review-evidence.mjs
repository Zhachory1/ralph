// Collect a read-only diff for one eligible, exact-head PR.
import { spawnSync } from "node:child_process";
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
  const metadataOnly = (file) => !file.patch && file.additions === 0 && file.deletions === 0;
  if (comparison.files.some((file) => {
    if (typeof file?.filename !== "string" || !file.filename
        || !Number.isSafeInteger(file.additions) || file.additions < 0
        || !Number.isSafeInteger(file.deletions) || file.deletions < 0) return true;
    if (metadataOnly(file)) return !["renamed", "modified"].includes(file.status);
    if (typeof file.patch !== "string" || !file.patch) return true;
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
  const stats = (reverse) => {
    const result = spawnSync("git", ["apply", "--numstat", "-z", ...(reverse ? ["--reverse"] : []), "-"],
      { input: diff, encoding: "utf8", cwd: "/", timeout: 30_000,
        env: { PATH: process.env.PATH, GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: "/dev/null" } });
    if (result.status !== 0) throw new Error("Incomplete GitHub diff");
    return result.stdout.split("\0").filter(Boolean).map((row) => {
      const match = row.match(/^(\d+)\t(\d+)\t([\s\S]+)$/);
      if (!match) throw new Error("Incomplete GitHub diff");
      return { additions: Number(match[1]), deletions: Number(match[2]), path: match[3] };
    });
  };
  const forward = stats(false);
  const reverse = stats(true).reverse();
  const sections = [];
  for (const line of diff.split("\n")) {
    if (line.startsWith("diff --git ")) sections.push([]);
    if (sections.length) sections.at(-1).push(line);
  }
  if (sections.length !== comparison.files.length || forward.length !== sections.length
      || reverse.length !== sections.length) throw new Error("Incomplete GitHub diff");
  const mergeBase = comparison.merge_base_commit?.sha;
  if (typeof mergeBase !== "string" || !sha.test(mergeBase)) throw new Error("Could not verify comparison merge base");
  const tree = async (ref) => {
    const result = JSON.parse(await gh("api", `repos/${repo}/git/trees/${ref}?recursive=1`));
    if (result?.truncated !== false || !Array.isArray(result.tree)) throw new Error("Incomplete GitHub diff tree");
    return new Map(result.tree.filter((entry) => entry.type !== "tree").map((entry) => [entry.path, entry]));
  };
  const [before, after] = await Promise.all([tree(mergeBase), tree(head)]);
  const changes = (lines) => {
    const edits = [];
    let oldLine;
    let newLine;
    let last;
    for (const line of lines) {
      const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (hunk) {
        oldLine = Number(hunk[1]);
        newLine = Number(hunk[2]);
        last = null;
      } else if (oldLine !== undefined && line.startsWith("-")) {
        last = ["-", oldLine++, line.slice(1), false];
        edits.push(last);
      } else if (newLine !== undefined && line.startsWith("+")) {
        last = ["+", newLine++, line.slice(1), false];
        edits.push(last);
      } else if (oldLine !== undefined && line.startsWith(" ")) {
        last = [" ", oldLine++, newLine++, line.slice(1), false];
        edits.push(last);
      } else if (line === "\\ No newline at end of file" && last) {
        last[3] = true;
      }
    }
    return edits;
  };
  const matchesPatch = (section, patch) => {
    const actual = changes(section).map((row) => JSON.stringify(row));
    let next = 0;
    // GitHub can render fewer unchanged lines in the raw diff than in its JSON patch.
    for (const row of changes(patch.split("\n"))) {
      if (JSON.stringify(row) === actual[next]) next += 1;
      else if (row[0] !== " ") return false;
    }
    return next === actual.length;
  };
  for (const [index, file] of comparison.files.entries()) {
    const section = sections[index];
    const start = section.findIndex((line) => line.startsWith("@@ "));
    const has = (prefix) => section.some((line) => line.startsWith(prefix));
    const status = has("new file mode ") ? "added" : has("deleted file mode ") ? "removed"
      : has("rename from ") ? "renamed" : has("copy from ") ? "copied" : "modified";
    const from = before.get(file.previous_filename ?? file.filename);
    const to = after.get(file.filename);
    const header = start < 0 ? section : section.slice(0, start);
    const modes = header.filter((line) => /^(?:old mode|new mode|new file mode|deleted file mode) /.test(line));
    const expectedModes = status === "added" ? [`new file mode ${to?.mode}`]
      : status === "removed" ? [`deleted file mode ${from?.mode}`] : from?.mode === to?.mode ? []
        : [`old mode ${from?.mode}`, `new mode ${to?.mode}`];
    const indexMode = header.find((line) => line.startsWith("index "))?.split(" ")[2];
    if (Boolean(from?.mode) !== (status !== "added") || Boolean(to?.mode) !== (status !== "removed")
        || JSON.stringify(modes) !== JSON.stringify(expectedModes)
        || (indexMode && (indexMode !== from?.mode || indexMode !== to?.mode))
        || (metadataOnly(file) ? start >= 0 || !sha.test(from?.sha ?? "") || from.sha !== to?.sha
          || (status !== "renamed" && from.mode === to.mode) : start < 0)
        || forward[index].path !== file.filename
        || reverse[index].path !== (file.previous_filename ?? file.filename)
        || forward[index].additions !== file.additions || forward[index].deletions !== file.deletions
        || (["added", "removed", "renamed", "copied", "modified"].includes(file.status) && file.status !== status)
        || (!metadataOnly(file) && !matchesPatch(section.slice(start), file.patch))) {
      throw new Error("Incomplete GitHub diff");
    }
  }
  const status = await currentStatus();
  if (status) return { status };
  return { status: "ready", repo, number, head, base, diff };
}
