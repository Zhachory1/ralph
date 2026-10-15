"""Resume cards when a human decides on the pull request they are waiting for.

The other half of the loop: the notifier tells you a design doc is ready,
you approve or request changes in GitHub, and this turns that into an
unblock so the worker picks up where it left off.

A decision is one of three signals, in priority order: the pull request was
merged, an approval label was applied, or a review state was submitted. A
plain comment is someone thinking out loud, and treating it as a decision
would advance work nobody agreed to.

Review states are listed last because they usually cannot be used. Agent
pull requests are opened under your own account, and GitHub refuses both
`APPROVED` and `CHANGES_REQUESTED` on your own pull request, leaving
`COMMENTED` as the only state you can submit. So merging the design-doc
pull request, or labelling it, is the approval act in practice. Review
states are still honoured for pull requests opened by somebody else.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

PR_URL = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
VERDICTS = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes"}
APPROVED_LABEL = "dd-approved"
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
        result = subprocess.run(
            [self.binary, "kanban", "--board", board, "list", "--status", "review", "--json"],
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
    if APPROVED_LABEL in labels:
        return "approved"

    decisions = [r for r in reviews if r.get("state") in VERDICTS]
    if not decisions:
        return None
    latest = max(decisions, key=lambda r: r.get("submitted_at") or "")
    return VERDICTS[latest["state"]]


def feedback(reviews: list[dict]) -> str:
    decisions = [r for r in reviews if r.get("state") in VERDICTS]
    if not decisions:
        return ""
    latest = max(decisions, key=lambda r: r.get("submitted_at") or "")
    return (latest.get("body") or "").strip()


def pull_request_for(detail: dict) -> tuple[str, int] | None:
    haystack = [detail.get("latest_summary") or ""]
    haystack += [(c.get("body") or "") for c in detail.get("comments") or []]
    for text in haystack:
        match = PR_URL.search(text)
        if match:
            return match.group(1), int(match.group(2))
    return None


def apply(board_client, board: str, task_id: str, decision: str, text: str, status: str = "review") -> None:
    """Record the decision, then move the card.

    Order matters: resuming re-spawns the worker, so the feedback has to be on
    the card before it wakes up and reads its context.

    Which transition applies depends on where the card is waiting. `unblock`
    only accepts a blocked card, and a card in review needs `reopen-review` to
    go back to its author or `complete` to finish. Calling the wrong one
    fails, which is how this was found.
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

        text = feedback(reviews)
        if args.dry_run:
            print(f"{task_id} <- {decision} on {repo}#{number}")
        else:
            apply(board_client, args.board, task_id, decision, text, task.get("status", "review"))
        resumed += 1

    print(f"{resumed} card(s) resumed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
