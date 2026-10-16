"""Resume cards when a human decides on the pull request they are waiting for.

The other half of the loop: the notifier tells you a design doc is ready,
you approve or request changes in GitHub, and this turns that into an
unblock so the worker picks up where it left off.

Comments carry the feedback; merging or labelling says you are finished.

The two jobs are separate on purpose. Every comment you leave is collected
and handed to the worker, wherever you left it: inline on the diff, at the
top level, or in a review. But comments alone cannot say whether you are
done, since three comments might mean "fix these and resubmit" or "answered,
carry on". Guessing resumes work on unresolved feedback or stalls a card in
silence, so the decision is an explicit act: apply a label, or merge.

Labels also give you a filterable queue in the GitHub UI, which is what
makes them better than a marker comment.

Review states are honoured too, but only work on pull requests somebody
else opened: GitHub refuses both `APPROVED` and `CHANGES_REQUESTED` on your
own pull request, and agent PRs are opened under your account. That is the
same rule that stops an agent approving its own work.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

PR_URL = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
VERDICTS = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes"}
CHANGES_LABEL = "dd-changes"


class GitHub:
    def __init__(self, binary: str = "gh"):
        self.binary = binary

    def _api(self, path: str):
        result = subprocess.run(
            [self.binary, "api", path],
            capture_output=True,
            text=True,
            check=True,
        )
        if not result.stdout.strip():
            return []
        return json.loads(result.stdout)

    def reviews(self, repo: str, number: int):
        return self._api(f"repos/{repo}/pulls/{number}/reviews")

    def diff_comments(self, repo: str, number: int):
        return self._api(f"repos/{repo}/pulls/{number}/comments")

    def issue_comments(self, repo: str, number: int):
        return self._api(f"repos/{repo}/issues/{number}/comments")

    def remove_label(self, repo: str, number: int, label: str):
        subprocess.run(
            [self.binary, "api", "-X", "DELETE", f"repos/{repo}/issues/{number}/labels/{label}"],
            capture_output=True,
            text=True,
            check=False,
        )

    def pull_request(self, repo: str, number: int):
        return self._api(f"repos/{repo}/pulls/{number}")


class Board:
    def __init__(self, binary: str = "hermes"):
        self.binary = binary

    def _run(self, *args: str):
        subprocess.run([self.binary, "kanban", *args], capture_output=True, text=True, check=True)

    def comment(self, board: str, task_id: str, text: str):
        self._run("--board", board, "comment", task_id, text)

    def unblock(self, board: str, task_id: str):
        self._run("--board", board, "unblock", task_id)

    def reopen_review(self, board: str, task_id: str, reason: str):
        self._run("--board", board, "reopen-review", task_id, "--reason", reason)

    def complete(self, board: str, task_id: str, summary: str):
        self._run("--board", board, "complete", task_id, "--summary", summary)

    def pending(self, board: str):
        """Cards waiting on a human.

        Only `blocked`. A card in `review` is waiting on the agent reviewer and
        the dispatcher will pick it up; polling that status would race the
        reviewer and let a human decision land mid-critique.
        """
        result = subprocess.run(
            [self.binary, "kanban", "--board", board, "list", "--status", "blocked", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else []

    def show(self, board: str, task_id: str):
        result = subprocess.run(
            [self.binary, "kanban", "--board", board, "show", task_id, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else {}


def verdict(reviews: list[dict], pull: dict | None = None) -> str | None:
    """The human's decision, or None if they have not made one.

    Merge and labels outrank review states because on a self-authored pull
    request they are the only signals available.
    """
    pull = pull or {}
    if pull.get("merged"):
        return "approved"

    labels = {label.get("name") for label in pull.get("labels") or []}
    if CHANGES_LABEL in labels:
        return "changes"

    decisions = [r for r in reviews if r.get("state") in VERDICTS]
    if not decisions:
        return None
    latest = max(decisions, key=lambda r: r.get("submitted_at") or "")
    return VERDICTS[latest["state"]]


def feedback(reviews: list[dict], diff_comments=(), issue_comments=()) -> str:
    """Every comment the human left, oldest first, wherever they left it.

    Inline comments keep their file and line, because "this is wrong" means
    nothing to a worker that cannot see where you were pointing.
    """
    items = []
    for review in reviews:
        body = (review.get("body") or "").strip()
        if body:
            items.append((review.get("submitted_at") or "", body))
    for comment in diff_comments:
        body = (comment.get("body") or "").strip()
        if body:
            where = comment.get("path") or ""
            line = comment.get("line") or comment.get("original_line")
            label = f"{where}:{line}" if line else where
            items.append((comment.get("created_at") or "", f"[{label}] {body}" if label else body))
    for comment in issue_comments:
        body = (comment.get("body") or "").strip()
        if body:
            items.append((comment.get("created_at") or "", body))

    items.sort(key=lambda pair: pair[0])
    return "\n\n".join(body for _, body in items)


def pull_request_for(detail: dict) -> tuple[str, int] | None:
    haystack = [detail.get("latest_summary") or ""]
    haystack += [(c.get("body") or "") for c in detail.get("comments") or []]
    for text in haystack:
        match = PR_URL.search(text)
        if match:
            return match.group(1), int(match.group(2))
    return None


def apply(board_client, board: str, task_id: str, decision: str, text: str, status: str = "blocked") -> None:
    """Record the decision, then move the card.

    Order matters: resuming re-spawns the worker, so the feedback has to be on
    the card before it wakes up and reads its context.

    Which transition applies depends on where the card waits. A human gate is
    `blocked`, so `unblock` is the normal path. The `review` branches are kept
    for a card a human reaches while the agent reviewer still holds it;
    `unblock` rejects a reviewed card, which is how this was found.
    """
    note = "Approved in GitHub." if decision == "approved" else "Changes requested in GitHub."
    board_client.comment(board, task_id, f"{note} {text}".strip())

    if status == "blocked":
        board_client.unblock(board, task_id)
    elif decision == "changes":
        board_client.reopen_review(board, task_id, text or "Changes requested in GitHub")
    else:
        board_client.complete(board, task_id, f"Approved in GitHub. {text}".strip())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    board_client, github = Board(), GitHub()
    resumed = 0

    for task in board_client.pending(args.board):
        task_id = task["id"]
        link = pull_request_for(board_client.show(args.board, task_id))
        if not link:
            continue

        repo, number = link
        reviews = github.reviews(repo, number)
        decision = verdict(reviews, github.pull_request(repo, number))
        if not decision:
            continue

        text = feedback(
            reviews,
            github.diff_comments(repo, number),
            github.issue_comments(repo, number),
        )
        if args.dry_run:
            print(f"{task_id} <- {decision} on {repo}#{number}")
        else:
            apply(board_client, args.board, task_id, decision, text, task.get("status", "review"))
            # Clear the marker, or the next poll acts on the same decision again.
            github.remove_label(repo, number, CHANGES_LABEL)
        resumed += 1

    print(f"{resumed} card(s) resumed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
