// Discover eligible PR heads across each configured GitHub account's accessible repositories.
import { createPrEligibility, parseAssignees } from "./github-pr-eligibility.mjs";

const repoSlug = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9_.-]{1,100}$/;
const repoUrl = /^https:\/\/api\.github\.com\/repos\/([A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9_.-]{1,100})$/i;
const searchLimit = 1000;

function timestamp(second) {
  return new Date(second * 1000).toISOString().replace(".000Z", "Z");
}

function createdSecond(row) {
  const second = Date.parse(row?.created_at) / 1000;
  if (!Number.isSafeInteger(second) || timestamp(second) !== row.created_at) {
    throw new Error("Incomplete GitHub search: invalid creation time");
  }
  return second;
}

async function readAssignedSearch(gh, viewer, assignee) {
  const query = `is:pr is:open draft:false assignee:${assignee}`;
  let budget = 64;
  async function readPages(start, end) {
    if (budget-- === 0) throw new Error("Incomplete GitHub search: partition limit");
    const qualifier = start === undefined ? "" : ` created:${timestamp(start)}..${timestamp(end)}`;
    const pages = JSON.parse(await gh(viewer, "api", "--method", "GET", "--paginate", "--slurp", "search/issues",
      "-f", `q=${query}${qualifier}`, "-F", "per_page=100"));
    if (!Array.isArray(pages) || pages.length === 0) throw new Error("Incomplete GitHub search");
    const total = pages[0]?.total_count;
    if (!Number.isSafeInteger(total) || total < 0 || pages.some((page) => page?.incomplete_results !== false
        || page.total_count !== total || !Array.isArray(page.items) || page.items.length > 100)) {
      throw new Error("Incomplete GitHub search");
    }
    const rows = pages.flatMap((page) => page.items);
    if (rows.length !== Math.min(total, searchLimit) || (start !== undefined && rows.some((row) => {
      const created = createdSecond(row);
      return created < start || created > end;
    }))) throw new Error("Incomplete GitHub search");
    return { total, rows };
  }

  const root = await readPages();
  if (root.total < searchLimit) return root.rows;

  async function boundary(order) {
    const page = JSON.parse(await gh(viewer, "api", "--method", "GET", "search/issues",
      "-f", `q=${query}`, "-f", "sort=created", "-f", `order=${order}`, "-F", "per_page=1"));
    if (page?.incomplete_results !== false || page.total_count !== root.total
        || !Array.isArray(page.items) || page.items.length !== 1) {
      throw new Error("Incomplete GitHub search: could not verify creation bounds");
    }
    return createdSecond(page.items[0]);
  }
  async function split(start, end, { total, rows }) {
    if (start >= end) throw new Error("GitHub search reached its result limit in a single second");
    const sample = rows.map(createdSecond).sort((a, b) => a - b);
    if (sample[0] < start || sample.at(-1) > end) throw new Error("Incomplete GitHub search: creation bounds drift");
    // Split near the observed results rather than paging through years of empty time.
    const mid = Math.min(sample[Math.floor(sample.length / 2)], end - 1);
    const left = await readPages(start, mid);
    const right = await readPages(mid + 1, end);
    if (left.total + right.total !== total) throw new Error("Incomplete GitHub search: partition count drift");
    return [
      ...(left.total < searchLimit ? left.rows : await split(start, mid, left)),
      ...(right.total < searchLimit ? right.rows : await split(mid + 1, end, right)),
    ];
  }
  return split(await boundary("asc"), await boundary("desc"), root);
}

export async function discoverReviewQueue({ accounts, gh }) {
  const identities = parseAssignees(Array.isArray(accounts) ? accounts.join(",") : "");
  if (identities.length < 2 || typeof gh !== "function") throw new Error("Configure GitHub readers for both accounts");
  for (const viewer of identities) {
    const actual = await gh(viewer, "api", "user", "--jq", ".login");
    if (typeof actual !== "string" || actual.trim().toLowerCase() !== viewer) {
      throw new Error("GitHub account credential mismatch");
    }
  }
  const candidates = new Map();
  for (const viewer of identities) {
    for (const assignee of identities) {
      const rows = await readAssignedSearch(gh, viewer, assignee);
      const seen = new Set();
      for (const row of rows) {
        const repo = typeof row?.repository_url === "string" ? row.repository_url.match(repoUrl)?.[1] : null;
        if (!repoSlug.test(repo ?? "") || !Number.isSafeInteger(row?.number) || row.number < 1
            || !row.pull_request || typeof row.pull_request !== "object") {
          throw new Error("Invalid GitHub search result");
        }
        const key = `${repo.toLowerCase()}#${row.number}`;
        if (seen.has(key)) throw new Error("Incomplete GitHub search");
        seen.add(key);
        if (!candidates.has(key)) candidates.set(key, { repo, number: row.number, viewers: new Set() });
        candidates.get(key).viewers.add(viewer);
      }
    }
  }
  const queue = { ours: [], incoming: [] };
  for (const candidate of candidates.values()) {
    let pr;
    for (const viewer of candidate.viewers) {
      try {
        pr = JSON.parse(await gh(viewer, "pr", "view", String(candidate.number), "-R", candidate.repo,
          "--json", "state,isDraft,assignees,headRefOid,author,title"));
        break;
      } catch {
        // A different configured account may retain read access.
      }
    }
    if (!pr) throw new Error("GitHub PR detail unavailable");
    const isEligible = createPrEligibility({ assignees: identities, readPr: async () => pr });
    if (!await isEligible(candidate.number)) continue;
    if (typeof pr.author?.login !== "string" || !pr.author.login || typeof pr.title !== "string") {
      throw new Error("Invalid GitHub PR detail");
    }
    const lane = identities.includes(pr.author.login.toLowerCase()) ? "ours" : "incoming";
    queue[lane].push({ repo: candidate.repo, number: candidate.number, head: pr.headRefOid, title: pr.title });
  }
  return queue;
}
