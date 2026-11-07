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
import os
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

    def _list(self, path: str):
        """Every item of a GitHub list endpoint, not its first page of 30.

        A busy design-doc PR passes 30 reviews or comments, and GitHub returns
        oldest first: the first page alone missed the latest verdict and the
        newest feedback (#59). `--slurp` wraps all pages in one array."""
        result = subprocess.run(
            [self.binary, "api", "--paginate", "--slurp", f"{path}?per_page=100"],
            capture_output=True,
            text=True,
            check=True,
        )
        if not result.stdout.strip():
            return []
        return [item for page in json.loads(result.stdout) for item in page]

    def reviews(self, repo: str, number: int):
        return self._list(f"repos/{repo}/pulls/{number}/reviews")

    def diff_comments(self, repo: str, number: int):
        return self._list(f"repos/{repo}/pulls/{number}/comments")

    def issue_comments(self, repo: str, number: int):
        return self._list(f"repos/{repo}/issues/{number}/comments")

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

    def _argv(self, args):
        """Reach the board through the hermes container when not inside it.

        Kanban is a SQLite file owned by the hermes service, and the CLI that
        speaks to it ships in a 4.5GB image. Carrying that image just to run a
        few commands doubled the stack's disk for no gain, so the notifier is
        a small image that asks the hermes container instead.
        """
        if os.environ.get("RALPH_HERMES_CONTAINER"):
            return ["docker", "exec", "-e", f"HERMES_HOME={os.environ.get('HERMES_HOME', '')}",
                    os.environ["RALPH_HERMES_CONTAINER"], self.binary, *args]
        return [self.binary, *args]

    def _run(self, *args: str):
        subprocess.run(self._argv(["kanban", *args]), capture_output=True, text=True, check=True)

    def comment(self, board: str, task_id: str, text: str):
        self._run("--board", board, "comment", task_id, text)

    def unblock(self, board: str, task_id: str):
        self._run("--board", board, "unblock", task_id)

    def release_triage(self, board: str, task_id: str):
        """Move a gate card Hermes parked in `triage` back to `todo`.

        Hermes routes a card to triage when it is blocked for the same reason
        twice (BLOCK_RECURRENCE_LIMIT). A design-doc gate is exactly that: the
        author blocks it for approval on every revision. Triage has no
        `unblock`, and `kanban specify` would rewrite the card with a model, so
        this calls Hermes' own state change with the card left as it is.
        """
        script = ("import sys\n"
                  "from hermes_cli import kanban_db as kb, kanban_db_connect as kbc\n"
                  "with kbc.connect_closing(board=sys.argv[1]) as conn:\n"
                  "    sys.exit(0 if kb.specify_triage_task(conn, sys.argv[2], author='ralph-notifier') else 1)\n")
        argv = ["python3", "-c", script, board, task_id]
        if os.environ.get("RALPH_HERMES_CONTAINER"):
            argv = ["docker", "exec", "-u", "hermes", os.environ["RALPH_HERMES_CONTAINER"],
                    "/opt/hermes/.venv/bin/python3", "-c", script, board, task_id]
        subprocess.run(argv, capture_output=True, text=True, check=True)

    def handoff(self, board: str, task_id: str, decision: str, status: str = "blocked"):
        """Record that a human handed the card back, lifting the active_pr guard.

        `reopen-review` is the honest verb for rework: it says the reviewed
        artifact needs another pass. An approval that leaves the card running
        is a reassignment back to its owner.
        """
        if decision == "changes" and status == "review":
            self._run("--board", board, "reopen-review", task_id,
                      "--reason", "Changes requested in GitHub")
            return
        owner = self.assignee(board, task_id)
        if owner:
            self._run("--board", board, "assign", task_id, owner)

    def assignee(self, board: str, task_id: str):
        return (self.show(board, task_id).get("task") or {}).get("assignee")

    def reopen_review(self, board: str, task_id: str, reason: str):
        self._run("--board", board, "reopen-review", task_id, "--reason", reason)

    def complete(self, board: str, task_id: str, summary: str):
        self._run("--board", board, "complete", task_id, "--summary", summary)

    def pending(self, board: str):
        """Cards waiting on a human.

        `blocked`, and `triage`, where Hermes parks a card blocked for the same
        reason twice -- every revised design doc's gate (docs#7). A card in
        `review` is waiting on the agent reviewer and the dispatcher will pick
        it up; polling that status would race the reviewer.
        """
        cards = []
        for status in ("blocked", "triage"):
            result = subprocess.run(
                self._argv(["kanban", "--board", board, "list", "--status", status, "--json"]),
                capture_output=True,
                text=True,
                check=True,
            )
            cards += json.loads(result.stdout) if result.stdout.strip() else []
        return cards

    def waiting(self, board: str):
        """Cards in `ready` or `todo`: where a resumed gate lands."""
        cards = []
        for status in ("ready", "todo"):
            result = subprocess.run(
                self._argv(["kanban", "--board", board, "list", "--status", status, "--json"]),
                capture_output=True, text=True, check=True,
            )
            cards += json.loads(result.stdout) if result.stdout.strip() else []
        return cards

    def show(self, board: str, task_id: str):
        result = subprocess.run(
            self._argv(["kanban", "--board", board, "show", task_id, "--json"]),
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else {}


def parked_by_block_loop(detail: dict) -> bool:
    """True when the card's latest status event is Hermes' block-loop routing."""
    for event in reversed(detail.get("events") or []):
        kind = event.get("kind")
        if kind == "block_loop_detected":
            return True
        if kind in ("blocked", "unblocked", "promoted", "specified", "completed", "created"):
            return False
    return False


DECISION_NOTES = ("Approved in GitHub.", "Changes requested in GitHub.")
GATE_TITLE = "Approve design:"


def is_approval_gate(detail: dict) -> bool:
    """The orchestrator's design approval gate (#56): titled `Approve design:`
    and assigned to nobody. Other unassigned cards are not this notifier's to
    complete (#110 review)."""
    task = detail.get("task") or {}
    return not task.get("assignee") and (task.get("title") or "").startswith(GATE_TITLE)


def stranded(detail: dict) -> str | None:
    """The decision of a gate this notifier resumed but never handed off.

    `apply` comments the decision, moves the card (unblock or triage release),
    then hands it off with an `assigned` event, which lifts Hermes' active_pr
    guard. If the move succeeded and the handoff failed, the card left the
    polled statuses and sits undispatchable (#103). Returns "approved" or
    "changes" when the newest decision comment has no handoff after it.
    """
    notes = [c for c in detail.get("comments") or []
             if (c.get("body") or "").startswith(DECISION_NOTES)]
    if not notes:
        return None
    last = max(notes, key=lambda c: c.get("created_at") or 0)
    after = last.get("created_at") or 0
    if any(e.get("kind") in ("assigned", "changes_requested", "review_reopened")
           and (e.get("created_at") or 0) > after for e in detail.get("events") or []):
        return None
    return "approved" if last["body"].startswith(DECISION_NOTES[0]) else "changes"


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


def is_bot(comment: dict) -> bool:
    """CI bots comment on pull requests too, at length.

    A real repository answers a design-doc review with a security scan, a
    coverage table, and a build summary. Handing those to the worker as
    "the feedback" buries the two sentences a person actually wrote, and the
    worker revises against a robot. GitHub types the author, so use that
    rather than guessing from names.
    """
    return ((comment.get("user") or {}).get("type") or "").lower() == "bot"


def feedback(reviews: list[dict], diff_comments=(), issue_comments=()) -> str:
    """Every comment the human left, oldest first, wherever they left it.

    Inline comments keep their file and line, because "this is wrong" means
    nothing to a worker that cannot see where you were pointing. Bot
    comments are dropped: see is_bot.
    """
    reviews = [r for r in reviews if not is_bot(r)]
    diff_comments = [c for c in diff_comments if not is_bot(c)]
    issue_comments = [c for c in issue_comments if not is_bot(c)]
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


def pull_request_from_ancestors(board_client, board: str, detail: dict, _seen=None) -> tuple[str, int] | None:
    """Find the address on the ancestor that actually opened it.

    A gate card is created at planning time, before the pull request it gates
    exists, so the address is never on the gate card itself. It lands on
    whichever ancestor later completed having opened one. Walk `parents` --
    a list of task ids, per `show --json` -- one level at a time, until one
    resolves or the chain runs out. A `seen` set guards against revisiting a
    parent reachable by more than one path.
    """
    seen = _seen if _seen is not None else set()
    for parent_id in detail.get("parents") or []:
        if parent_id in seen:
            continue
        seen.add(parent_id)
        parent_detail = board_client.show(board, parent_id)
        link = pull_request_for(parent_detail) or pull_request_from_ancestors(
            board_client, board, parent_detail, seen
        )
        if link:
            return link
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

    if decision == "approved" and status in ("blocked", "triage") \
            and is_approval_gate(board_client.show(board, task_id)):
        # An approval gate (#56): an unassigned card between a design and its
        # build. No worker runs it, so approving it is completing it, which
        # releases the cards that depend on it.
        if status == "triage":
            board_client.release_triage(board, task_id)
        else:
            board_client.unblock(board, task_id)
        board_client.complete(board, task_id, f"Approved in GitHub. {text}".strip())
    elif status == "triage":
        board_client.release_triage(board, task_id)
        board_client.handoff(board, task_id, decision, "blocked")
    elif status == "blocked":
        board_client.unblock(board, task_id)
        # Unblocking is not enough on its own. The dispatcher refuses to
        # respawn a card whose recent comments contain a pull-request URL --
        # the `active_pr` respawn guard, which exists so a crashed worker does
        # not open a second PR for the same work. It lifts only on a handoff
        # event recorded after that comment, and a human decision is exactly
        # such a handoff. Without this the card sits in `ready` forever while
        # the dispatcher logs "0 workers spawned".
        board_client.handoff(board, task_id, decision, status)
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

    # A gate resumed on an earlier poll whose handoff failed (#103): redo the
    # handoff only. The card already moved and the decision is on it.
    for task in board_client.waiting(args.board):
        detail = board_client.show(args.board, task["id"])
        decision = stranded(detail)
        if decision is None:
            continue
        # An approval gate the notifier unblocked but failed to complete
        # (#110 review): finish it, so the build it holds is released.
        gate = decision == "approved" and is_approval_gate(detail)
        if args.dry_run:
            print(f"{task['id']} <- {'complete gate' if gate else 're-handoff'} ({decision})")
        elif gate:
            board_client.complete(args.board, task["id"], "Approved in GitHub.")
        else:
            board_client.handoff(args.board, task["id"], decision, "blocked")
        resumed += 1

    for task in board_client.pending(args.board):
        task_id = task["id"]
        detail = board_client.show(args.board, task_id)
        # Only a gate Hermes parked for a repeated block; a card a person or
        # another flow put in triage is not this notifier's to resume.
        if task.get("status") == "triage" and not parked_by_block_loop(detail):
            continue
        link = pull_request_for(detail)
        if not link:
            link = pull_request_from_ancestors(board_client, args.board, detail)
            if link:
                # The gate card is created before the pull request exists, so
                # there is nobody to paste the address by hand. Write it down
                # here instead, the same act a human would have done, so the
                # card carries its own address from now on.
                repo, number = link
                board_client.comment(args.board, task_id, f"https://github.com/{repo}/pull/{number}")
        if not link:
            # The failure this issue is about is silence, not an error: skip
            # this card as before, but say so instead of skipping quietly.
            print(f"{task_id}: blocked with no pull request to approve", file=sys.stderr)
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
