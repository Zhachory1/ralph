"""Poll a Kanban board for gates waiting on a human, and announce them once.

Shaped by three findings from the stage 0 capability spike
(docs/spikes/hermes-capabilities.md), each of which would otherwise cause
silent failure:

1. `request-review` moves a task to `review`, not `blocked`. Polling one
   status misses every design-doc gate.
2. `list --json` carries no blocked reason. The reason lives in the review
   summary, a `BLOCKED:` comment, or a `blocked` event, all of which come
   back from `show --json`. So this is two calls per tick, not one.
3. Announcing is not idempotent on its own, so a state file records what has
   already been said, keyed by reason rather than task id, so a second round
   of review is reported and an unchanged gate is not.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PENDING_STATUSES = ("blocked", "review")
BLOCKED_COMMENT_PREFIX = "BLOCKED:"


@dataclass(frozen=True)
class Gate:
    task_id: str
    title: str
    status: str
    reason: str


class HermesBoard:
    """Thin wrapper over the hermes CLI."""

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

    def _json(self, *args: str):
        result = subprocess.run(
            self._argv(["kanban", *args, "--json"]),
            capture_output=True,
            text=True,
            check=True,
        )
        if not result.stdout.strip():
            return []
        return json.loads(result.stdout)

    def list_tasks(self, board: str, status: str):
        return self._json("--board", board, "list", "--status", status)

    def stats(self, board: str) -> dict:
        return self._json("--board", board, "stats") or {}

    def show_task(self, board: str, task_id: str):
        return self._json("--board", board, "show", task_id)


def _reason_from(detail: dict, fallback: str) -> str:
    """Recover why a task is waiting.

    Preference order matters: a review summary is written for a human to read,
    whereas a comment or event is whatever the worker happened to record.
    """
    summary = detail.get("latest_summary")
    if summary:
        return summary.strip()

    for comment in reversed(detail.get("comments") or []):
        body = (comment.get("body") or "").strip()
        if body.startswith(BLOCKED_COMMENT_PREFIX):
            return body[len(BLOCKED_COMMENT_PREFIX):].strip()

    for event in reversed(detail.get("events") or []):
        if event.get("kind") == "blocked":
            reason = (event.get("payload") or {}).get("reason")
            if reason:
                return reason.strip()

    return fallback


def pending_gates(board_client, board: str) -> list[Gate]:
    gates = []
    for status in PENDING_STATUSES:
        for task in board_client.list_tasks(board, status):
            detail = board_client.show_task(board, task["id"])
            gates.append(
                Gate(
                    task_id=task["id"],
                    title=task.get("title", ""),
                    status=status,
                    reason=_reason_from(detail, task.get("title", "")),
                )
            )
    return gates


def unannounced(gates: list[Gate], seen: dict) -> list[Gate]:
    return [g for g in gates if seen.get(g.task_id) != g.reason]


def record(gates: list[Gate], seen: dict) -> None:
    for gate in gates:
        seen[gate.task_id] = gate.reason


def prune(seen: dict, gates: list[Gate]) -> None:
    """Forget gates that are no longer pending.

    Without this the state file grows forever, and a card that is blocked,
    resolved, then blocked again for the same reason would stay silent.
    """
    live = {g.task_id for g in gates}
    for task_id in list(seen):
        if task_id not in live:
            del seen[task_id]


def render(gate: Gate) -> str:
    return f"{gate.reason}\n  {gate.status} · {gate.task_id}"


def render_digest(gates: list[Gate]) -> str:
    lines = [f"{len(gates)} waiting on you"]
    lines += [f"• {g.reason}  ({g.status} · {g.task_id})" for g in gates]
    return "\n".join(lines)


def post(webhook: str, text: str) -> None:
    request = urllib.request.Request(
        webhook,
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_state(path: Path, seen: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen, indent=1, sort_keys=True))
    tmp.replace(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--digest", action="store_true", help="Post one summary of everything pending")
    parser.add_argument("--dry-run", action="store_true", help="Print instead of posting")
    args = parser.parse_args(argv)

    webhook = os.environ.get("GCHAT_WEBHOOK_URL")
    if not webhook and not args.dry_run:
        print("GCHAT_WEBHOOK_URL is not set", file=sys.stderr)
        return 2

    gates = pending_gates(HermesBoard(), args.board)
    seen = load_state(args.state)

    if args.digest:
        messages = [render_digest(gates)] if gates else []
    else:
        messages = [render(g) for g in unannounced(gates, seen)]

    for text in messages:
        if args.dry_run:
            print(text)
        else:
            try:
                post(webhook, text)
            except urllib.error.URLError as error:
                # Don't record an announcement that never arrived.
                print(f"delivery failed: {error}", file=sys.stderr)
                return 1

    if not args.digest:
        record(gates, seen)
    prune(seen, gates)
    save_state(args.state, seen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
