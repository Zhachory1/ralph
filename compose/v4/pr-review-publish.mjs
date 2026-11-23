// Publish a completed synthesis once, after rechecking the current PR.
import { createPrEligibility } from "./github-pr-eligibility.mjs";

const titlePattern = /^PR review ([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)#([0-9]+) @ ([a-f0-9]{40})$/;
const descriptionPattern = /^Review ([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)#([0-9]+) at head ([a-f0-9]{40}) against base ([a-f0-9]{40})\.\n/;

export async function publishReview({ task, board, gh, post, companyId, managerId, synthesisId, publisher, accounts }) {
  const title = typeof task?.title === "string" && task.title.match(titlePattern);
  const description = typeof task?.description === "string" && task.description.match(descriptionPattern);
  if (!title || !description || title[1].toLowerCase() !== description[1].toLowerCase()
      || title[2] !== description[2] || title[3] !== description[3] || task.companyId !== companyId
      || task.assigneeAgentId !== managerId || task.status !== "done") {
    throw new Error("Invalid completed review task");
  }
  const [, repo, numberText, head] = title;
  const number = Number(numberText);
  const base = description[4];
  if (!Number.isSafeInteger(number) || number < 1) throw new Error("Invalid review PR number");
  const login = (await gh(publisher, "api", "user", "--jq", ".login")).trim();
  if (login.toLowerCase() !== publisher.toLowerCase()) throw new Error("Incorrect review publishing identity");
  const marker = `<!-- ai-pr-automation head=${head} -->`;
  const alreadyPublished = async () => {
    const pages = JSON.parse(await gh(publisher, "api", "--paginate", "--slurp",
      `repos/${repo}/pulls/${number}/reviews?per_page=100`));
    if (!Array.isArray(pages) || pages.some((page) => !Array.isArray(page))) throw new Error("Cannot read reviews");
    return pages.flat().some((review) => review.user?.login?.toLowerCase() === publisher.toLowerCase()
      && review.state !== "PENDING" && review.commit_id === head && review.body?.includes(marker));
  };
  if (await alreadyPublished()) return "reviewed";
  const readPr = async () => JSON.parse(await gh(publisher, "pr", "view", String(number), "-R", repo,
    "--json", "state,isDraft,assignees,headRefOid,baseRefOid"));
  const currentStatus = async () => {
    const current = await readPr();
    if (!await createPrEligibility({ assignees: accounts, readPr: async () => current })(number)) return "ineligible";
    return current.headRefOid === head && current.baseRefOid === base ? null : "stale";
  };
  const status = await currentStatus();
  if (status) return status;
  const query = new URLSearchParams({ parentId: task.id, assigneeAgentId: synthesisId, status: "done",
    includeAgentCreatedTasks: "true", limit: "100" });
  const children = await board(`companies/${companyId}/issues?${query}`);
  if (!Array.isArray(children) || children.length !== 1 || children[0].companyId !== companyId
      || children[0].parentId !== task.id || children[0].assigneeAgentId !== synthesisId
      || children[0].status !== "done") return "not_ready";
  const comments = await board(`issues/${children[0].id}/comments?order=desc`);
  if (!Array.isArray(comments)) throw new Error("Cannot read synthesis comments");
  const review = comments.find((comment) => comment.authorAgentId === synthesisId && typeof comment.body === "string"
    && comment.body.trim());
  if (!review) return "not_ready";
  const path = `issues/${task.id}/documents/github-review-publication`;
  try {
    await board(path);
    return "reconcile";
  } catch (error) {
    if (error?.status !== 404) throw error;
  }
  const lastStatus = await currentStatus();
  if (lastStatus) return lastStatus;
  const receipt = await board(path, "PUT", { format: "markdown", body: JSON.stringify({ state: "posting", head }) });
  const finalStatus = await currentStatus();
  if (finalStatus) {
    await board(path, "PUT", {
      format: "markdown", body: JSON.stringify({ state: finalStatus, head }), baseRevisionId: receipt.latestRevisionId,
    });
    return finalStatus;
  }
  try {
    const response = await post({ repo, number, head, body: `Automated review by Ralph\n\n${review.body.trim()}\n\n${marker}` });
    if (response?.state !== "COMMENTED" || response.commit_id !== head) {
      if (!await alreadyPublished()) return "reconcile";
    }
  } catch {
    if (!await alreadyPublished()) return "reconcile";
  }
  await board(path, "PUT", {
    format: "markdown", body: JSON.stringify({ state: "posted", head }), baseRevisionId: receipt.latestRevisionId,
  });
  return "published";
}
