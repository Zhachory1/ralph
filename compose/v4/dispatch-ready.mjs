export async function dispatchReadyTicket(job, { readReady, claim, findDraft, invokeSwe, record }) {
  const { ticketId, repo, baseBranch, expectedAuthor, repoPath, product, rfcPath, acceptanceCriteria } = job;
  if (!Number.isSafeInteger(ticketId) || ticketId < 1 || !/^[\w.-]+\/[\w.-]+$/.test(repo)
      || !baseBranch || !expectedAuthor) {
    throw new Error("Expected a trusted repository, base branch, author, and positive Vikunja ticket ID");
  }
  if (!(await readReady(ticketId))) return { status: "not_ready" };
  const lease = await claim(ticketId);
  if (!lease) return { status: "already_claimed" };
  if (!(await readReady(ticketId))) return { status: "no_longer_ready" };

  const ticket = `vikunja-${ticketId}`;
  const branch = `ralph/${ticket}`;
  let draft = await findDraft(repo, branch);
  if (!draft) {
    let workerError;
    try {
      await invokeSwe({ ticket_id: ticket, product, repo_path: repoPath, branch,
        rfc_path: rfcPath, acceptance_criteria: acceptanceCriteria });
    } catch (error) {
      workerError = error;
    }
    draft = await findDraft(repo, branch);
    if (!draft && workerError) throw workerError;
  }
  if (!draft) return { status: "no_draft" };
  if (draft.repo !== repo || draft.branch !== branch || draft.baseBranch !== baseBranch
      || draft.author !== expectedAuthor
      || draft.isDraft !== true || draft.isCrossRepository !== false
      || !Number.isSafeInteger(draft.number) || draft.number < 1 || !/^[a-f0-9]{40}$/.test(draft.headSha)) {
    throw new Error("Expected a same-repository draft for the ticket's exact branch and head");
  }
  if (!(await record(ticketId, lease, repo, draft.number, draft.headSha))) return { status: "stale_lease" };
  return { status: "waiting_merge", repo, prNumber: draft.number, headSha: draft.headSha };
}
