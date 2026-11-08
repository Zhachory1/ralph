"""Tests for the gate poller.

These pin the three things the stage 0 spike found the hard way: pending
gates live in two statuses, the blocked reason is absent from `list --json`,
and an alert must be delivered exactly once.
"""

import json
import unittest
from unittest import mock

import poller


def task(task_id, title, status):
    return {"id": task_id, "title": title, "status": status}


def detail(summary=None, comments=(), events=()):
    return {
        "task": {},
        "latest_summary": summary,
        "comments": [{"body": b} for b in comments],
        "events": list(events),
    }


class FakeBoard:
    """Stands in for the hermes CLI."""

    def __init__(self, by_status, details):
        self.by_status = by_status
        self.details = details
        self.list_calls = []
        self.show_calls = []

    def list_tasks(self, board, status):
        self.list_calls.append(status)
        return self.by_status.get(status, [])

    def show_task(self, board, task_id):
        self.show_calls.append(task_id)
        return self.details[task_id]


class PendingGateTests(unittest.TestCase):
    def test_polls_both_blocked_and_review(self):
        """request-review yields `review`, not `blocked`. Watching one status
        silently misses every design-doc gate."""
        board = FakeBoard(
            {
                "blocked": [task("t_1", "Build PR 1", "blocked")],
                "review": [task("t_2", "DD segment refresh", "review")],
            },
            {
                "t_1": detail(comments=["BLOCKED: question: which cache TTL?"]),
                "t_2": detail(summary="DD ready: segment refresh. 3 decisions, scope M."),
            },
        )

        gates = poller.pending_gates(board, "b")

        self.assertEqual({"t_1", "t_2"}, {g.task_id for g in gates})
        self.assertEqual(["blocked", "review"], sorted(board.list_calls))

    def test_reason_comes_from_show_not_list(self):
        """`list --json` omits the blocked reason entirely."""
        board = FakeBoard(
            {"blocked": [task("t_1", "Build PR 1", "blocked")]},
            {"t_1": detail(comments=["BLOCKED: question: which cache TTL?"])},
        )

        (gate,) = poller.pending_gates(board, "b")

        self.assertEqual("question: which cache TTL?", gate.reason)
        self.assertIn("t_1", board.show_calls)

    def test_review_summary_preferred_over_comment(self):
        board = FakeBoard(
            {"review": [task("t_2", "DD", "review")]},
            {"t_2": detail(summary="DD ready: 3 decisions", comments=["BLOCKED: stale"])},
        )

        (gate,) = poller.pending_gates(board, "b")

        self.assertEqual("DD ready: 3 decisions", gate.reason)

    def test_falls_back_to_event_reason(self):
        board = FakeBoard(
            {"blocked": [task("t_1", "Build", "blocked")]},
            {"t_1": detail(events=[{"kind": "blocked", "payload": {"reason": "needs input"}}])},
        )

        (gate,) = poller.pending_gates(board, "b")

        self.assertEqual("needs input", gate.reason)

    def test_latest_blocked_comment_wins(self):
        board = FakeBoard(
            {"blocked": [task("t_1", "Build", "blocked")]},
            {"t_1": detail(comments=["BLOCKED: first", "unrelated", "BLOCKED: second"])},
        )

        (gate,) = poller.pending_gates(board, "b")

        self.assertEqual("second", gate.reason)

    def test_title_used_when_no_reason_recorded(self):
        board = FakeBoard(
            {"blocked": [task("t_1", "Build PR 1", "blocked")]},
            {"t_1": detail()},
        )

        (gate,) = poller.pending_gates(board, "b")

        self.assertEqual("Build PR 1", gate.reason)


class DedupeTests(unittest.TestCase):
    def test_announces_once_per_gate(self):
        seen = {}
        gate = poller.Gate("t_1", "DD", "review", "DD ready")

        first = poller.unannounced([gate], seen)
        poller.record([gate], seen)
        second = poller.unannounced([gate], seen)

        self.assertEqual([gate], first)
        self.assertEqual([], second)

    def test_rereports_when_reason_changes(self):
        """A second round of review is a new thing to say, not an echo."""
        seen = {}
        poller.record([poller.Gate("t_1", "DD", "review", "v1 ready")], seen)

        again = poller.unannounced([poller.Gate("t_1", "DD", "review", "v2 ready")], seen)

        self.assertEqual(1, len(again))

    def test_forgets_resolved_gates(self):
        """Without pruning, the state file grows forever and a re-blocked card
        stays silent."""
        seen = {"t_1": "old", "t_2": "other"}

        poller.prune(seen, [poller.Gate("t_2", "DD", "review", "other")])

        self.assertEqual({"t_2"}, set(seen))


class RenderTests(unittest.TestCase):
    def test_alert_stands_alone(self):
        gate = poller.Gate("t_2", "DD segment refresh", "review", "DD ready: 3 decisions, scope M")

        text = poller.render(gate)

        self.assertIn("DD ready: 3 decisions, scope M", text)
        self.assertIn("t_2", text)

    def test_digest_leads_with_count(self):
        gates = [
            poller.Gate("t_1", "A", "review", "DD ready"),
            poller.Gate("t_2", "B", "blocked", "question: TTL?"),
        ]

        text = poller.render_digest(gates)

        self.assertTrue(text.startswith("2 waiting on you"), text)


class CliParsingTests(unittest.TestCase):
    def test_tolerates_empty_output(self):
        with mock.patch.object(poller.subprocess, "run") as run:
            run.return_value = mock.Mock(stdout="", returncode=0)
            self.assertEqual([], poller.HermesBoard().list_tasks("b", "blocked"))

    def test_raises_on_malformed_json(self):
        with mock.patch.object(poller.subprocess, "run") as run:
            run.return_value = mock.Mock(stdout="not json", returncode=0)
            with self.assertRaises(json.JSONDecodeError):
                poller.HermesBoard().list_tasks("b", "blocked")




class PartialDeliveryTests(unittest.TestCase):
    """#60: when the webhook fails part-way, gates already posted this tick
    are recorded, and only the undelivered ones are retried."""

    def test_only_undelivered_gates_retry(self):
        import tempfile, pathlib, urllib.error
        gates = [poller.Gate(f"t_{i}", "DD", "blocked", f"reason {i}") for i in range(3)]
        board = mock.Mock()
        with tempfile.TemporaryDirectory() as d:
            state = pathlib.Path(d) / "state.json"
            posted = []

            def post(_url, text):
                if len(posted) == 2:
                    raise urllib.error.URLError("down")
                posted.append(text)

            with mock.patch.object(poller, "pending_gates", return_value=gates), \
                 mock.patch.object(poller, "HermesBoard", return_value=board), \
                 mock.patch.object(poller, "post", side_effect=post), \
                 mock.patch.dict("os.environ", {"GCHAT_WEBHOOK_URL": "http://x"}):
                self.assertEqual(1, poller.main(["--board", "b", "--state", str(state)]))
                seen = json.loads(state.read_text())
                self.assertEqual({"t_0", "t_1"}, set(seen))

    def test_a_failed_tick_still_prunes_resolved_gates(self):
        """#112 review: without pruning, a gate that resolved and reopened
        with the same reason would never be announced again."""
        import tempfile, pathlib, urllib.error
        with tempfile.TemporaryDirectory() as d:
            state = pathlib.Path(d) / "state.json"
            state.write_text(json.dumps({"t_gone": "old reason"}))
            gates = [poller.Gate("t_new", "DD", "blocked", "r")]
            with mock.patch.object(poller, "pending_gates", return_value=gates), \
                 mock.patch.object(poller, "HermesBoard", return_value=mock.Mock()), \
                 mock.patch.object(poller, "post", side_effect=urllib.error.URLError("down")), \
                 mock.patch.dict("os.environ", {"GCHAT_WEBHOOK_URL": "http://x"}):
                self.assertEqual(1, poller.main(["--board", "b", "--state", str(state)]))
            self.assertNotIn("t_gone", json.loads(state.read_text()))

                posted.clear()
                retried = []
                with mock.patch.object(poller, "post", side_effect=lambda _u, t: retried.append(t)):
                    self.assertEqual(0, poller.main(["--board", "b", "--state", str(state)]))
                self.assertEqual(1, len(retried))
                self.assertIn("reason 2", retried[0])

if __name__ == "__main__":
    unittest.main()
