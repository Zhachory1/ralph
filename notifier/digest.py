"""The morning digest: what is waiting on you, and what the board did overnight.

Three lines, not a report. The operational numbers the design puts on a daily
cadence — gate waits, work in flight, cards that failed — because those are the
ones you act on the same day. The weekly trend report is a different job.

It leads with what is blocked on you, because that is the only part only you
can clear. A digest that opens with throughput is a digest you stop reading.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import poller


def age(seconds: float) -> str:
    """Rounded, and never more precise than it is useful.

    "blocked 3d" is actionable; "blocked 3d 4h 12m" is the same fact with more
    to read.
    """
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def oldest_first(gates: list[poller.Gate], detail: dict) -> list[tuple[poller.Gate, float]]:
    now = time.time()
    aged = [(g, now - float(detail.get(g.task_id) or now)) for g in gates]
    aged.sort(key=lambda pair: pair[1], reverse=True)
    return aged


def render(gates: list[tuple[poller.Gate, float]], counts: dict) -> str:
    """A digest with nothing waiting still says so: silence is ambiguous."""
    waiting = len(gates)
    running = counts.get("running", 0)
    ready = counts.get("ready", 0)

    if waiting == 0:
        head = "Nothing waiting on you."
    else:
        head = f"{waiting} waiting on you" + (f", oldest {age(gates[0][1])}" if gates else "")

    lines = [head]
    for gate, seconds in gates[:5]:
        lines.append(f"• {age(seconds):>4}  {gate.reason[:90]}")
    if waiting > 5:
        lines.append(f"• and {waiting - 5} more")

    lines.append(f"{running} running, {ready} queued")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    webhook = os.environ.get("GCHAT_WEBHOOK_URL")
    if not webhook and not args.dry_run:
        print("GCHAT_WEBHOOK_URL is not set", file=sys.stderr)
        return 2

    board = poller.HermesBoard()
    gates = poller.pending_gates(board, args.board)

    # When a card entered its current state, so a gate can carry its age. The
    # board's list does not say, so it comes from the card like the reason does.
    since: dict[str, float] = {}
    for gate in gates:
        detail = board.show_task(args.board, gate.task_id)
        events = detail.get("events") or []
        blocked = [e for e in events if "block" in str(e.get("kind", ""))]
        if blocked:
            since[gate.task_id] = blocked[-1].get("created_at") or time.time()

    stats = board.stats(args.board)
    counts = stats.get("by_status", {})
    text = render(oldest_first(gates, since), counts)

    if args.dry_run:
        print(text)
        return 0
    poller.post(webhook, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
