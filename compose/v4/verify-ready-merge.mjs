function sameLogin(actual, expected) {
  return typeof actual === "string" && typeof expected === "string" && actual.toLowerCase() === expected.toLowerCase();
}

export function verifyReadyMerge(expected, pr) {
  if (!pr || pr.number !== expected.prNumber || pr.head?.ref !== `ralph/vikunja-${expected.ticketId}`
      || !sameLogin(pr.head.repo?.full_name, expected.repo) || !sameLogin(pr.base?.repo?.full_name, expected.repo)
      || pr.base?.ref !== expected.baseBranch || !sameLogin(pr.user?.login, expected.expectedAuthor)) {
    return { status: "wrong_pr" };
  }
  if (pr.head.sha !== expected.headSha || !/^[a-f0-9]{40}$/.test(pr.head.sha)) return { status: "stale_head" };
  if (pr.merged !== true) return { status: pr.state === "closed" ? "closed_unmerged" : "waiting_merge" };
  const actor = pr.merged_by;
  if (actor?.type !== "User" || sameLogin(actor.login, expected.expectedAuthor)
      || !expected.directors.some((login) => sameLogin(actor.login, login))) {
    return { status: "unauthorized_actor" };
  }
  if (pr.state !== "closed" || !/^[a-f0-9]{40}$/.test(pr.merge_commit_sha) || !Number.isFinite(Date.parse(pr.merged_at))) {
    return { status: "incomplete_evidence" };
  }
  return { status: "verified", headSha: pr.head.sha, mergeCommitSha: pr.merge_commit_sha,
    mergedBy: actor.login, mergedAt: pr.merged_at };
}

export async function completeReadyTicket(expected, { readPull, recordMerge, moveBoardDone, recordDone }) {
  const pull = await readPull(expected.repo, expected.prNumber);
  const proof = verifyReadyMerge(expected, pull);
  if (proof.status !== "verified") return proof;
  if (!(await recordMerge(expected, proof))) return { status: "receipt_rejected" };
  if (!(await moveBoardDone(expected.ticketId))) return { status: "not_at_director_gate" };
  if (!(await recordDone(expected.ticketId, proof.mergeCommitSha))) return { status: "board_ack_failed" };
  return { status: "done", mergeCommitSha: proof.mergeCommitSha };
}
