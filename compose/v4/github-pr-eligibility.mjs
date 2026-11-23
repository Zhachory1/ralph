// Verify the current GitHub PR state before admitting review work.
const login = /^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$/;
const sha = /^[a-f0-9]{40}$/;

export function parseAssignees(value) {
  const names = typeof value === "string" ? value.split(",").map((name) => name.trim().toLowerCase()) : [];
  if (!names.length || names.some((name) => !login.test(name))) {
    throw new Error("Configure valid GitHub review assignees");
  }
  return [...new Set(names)];
}

export function createPrEligibility({ assignees, readPr }) {
  const configured = parseAssignees(Array.isArray(assignees) ? assignees.join(",") : "");
  if (typeof readPr !== "function") throw new Error("Configure a GitHub eligibility reader");
  return async (number, expectedHead) => {
    const pr = await readPr(number);
    if (!pr || !["OPEN", "CLOSED", "MERGED"].includes(pr.state) || typeof pr.isDraft !== "boolean"
        || typeof pr.headRefOid !== "string" || !sha.test(pr.headRefOid)
        || !Array.isArray(pr.assignees) || pr.assignees.some((person) => typeof person?.login !== "string")) {
      throw new Error("Could not verify GitHub PR eligibility");
    }
    return pr.state === "OPEN" && pr.isDraft === false
      && (!expectedHead || pr.headRefOid === expectedHead)
      && pr.assignees.some((person) => configured.includes(person.login.toLowerCase()));
  };
}

export async function listAssignedPrs(gh, repoName, assignees) {
  const numbers = new Set();
  const configured = parseAssignees(Array.isArray(assignees) ? assignees.join(",") : "");
  for (const assignee of configured) {
    const rows = JSON.parse(await gh("search", "prs", "--assignee", assignee, "--state", "open",
      "--draft=false", "-R", repoName, "--json", "number", "--limit", "1000"));
    if (!Array.isArray(rows) || rows.length >= 1000) throw new Error("Review search reached its result limit");
    for (const row of rows) {
      if (!Number.isSafeInteger(row?.number) || row.number < 1) throw new Error("Invalid GitHub search result");
      numbers.add(row.number);
    }
  }
  return [...numbers];
}
