"""Reacting to review feedback on a pull request you authored.

No network for the digest/identity logic -- those are pure functions tested
against hand-built snapshots. Round-cap and daily-cap logic mock the two
subprocess boundaries (gh, hermes). The real end-to-end path -- feedback
read from a live PR, a real card created, a real maintainer round that
pushed a commit and posted a comment -- was proven live against
Zhachory1/ralph#23; see the PR body for that record, including the commit
it produced and the review it was responding to.

    python3 evals/test_maintain_pr.py
"""

import importlib.machinery
import importlib.util
import json
import pathlib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "maintain-pr"

_loader = importlib.machinery.SourceFileLoader("maintain_pr", str(SCRIPT))
_spec = importlib.util.spec_from_loader("maintain_pr", _loader)
maintain_pr = importlib.util.module_from_spec(_spec)
_loader.exec_module(maintain_pr)


class DigestTests(unittest.TestCase):
    """The fleet's own algorithm, kept byte-identical: hash each review body,
    each thread comment body, each failing check, sort, hash the list."""

    def test_empty_snapshot_is_the_empty_sentinel(self):
        snapshot = {"reviews": [], "threads": [], "checks": []}
        self.assertEqual("empty", maintain_pr.feedback_digest(snapshot))

    def test_same_content_same_digest(self):
        snapshot = {"reviews": [{"id": 1, "body": "fix this"}], "threads": [], "checks": []}
        d1 = maintain_pr.feedback_digest(snapshot)
        d2 = maintain_pr.feedback_digest(snapshot)
        self.assertEqual(d1, d2)
        self.assertNotEqual("empty", d1)

    def test_different_content_different_digest(self):
        a = maintain_pr.feedback_digest({"reviews": [{"id": 1, "body": "fix this"}],
                                         "threads": [], "checks": []})
        b = maintain_pr.feedback_digest({"reviews": [{"id": 1, "body": "fix that"}],
                                         "threads": [], "checks": []})
        self.assertNotEqual(a, b)

    def test_review_order_does_not_change_the_digest(self):
        """The digest sorts before hashing, so two snapshots with the same
        content in a different order are the same operation."""
        snap1 = {"reviews": [{"id": 1, "body": "a"}, {"id": 2, "body": "b"}],
                "threads": [], "checks": []}
        snap2 = {"reviews": [{"id": 2, "body": "b"}, {"id": 1, "body": "a"}],
                "threads": [], "checks": []}
        self.assertEqual(maintain_pr.feedback_digest(snap1), maintain_pr.feedback_digest(snap2))

    def test_threads_and_checks_both_contribute(self):
        base = maintain_pr.feedback_digest({"reviews": [], "threads": [], "checks": []})
        with_thread = maintain_pr.feedback_digest({
            "reviews": [], "checks": [],
            "threads": [{"id": "t1", "comments": [{"id": "c1", "body": "x"}]}]})
        with_check = maintain_pr.feedback_digest({
            "reviews": [], "threads": [],
            "checks": [{"name": "ci", "bucket": "fail", "state": "FAILURE", "link": "x"}]})
        self.assertNotEqual(base, with_thread)
        self.assertNotEqual(base, with_check)
        self.assertNotEqual(with_thread, with_check)


class OperationIdTests(unittest.TestCase):
    def test_deterministic(self):
        a = maintain_pr.operation_id("owner/repo", 1, "digest123")
        b = maintain_pr.operation_id("owner/repo", 1, "digest123")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("maintain-"))

    def test_different_pr_different_id(self):
        a = maintain_pr.operation_id("owner/repo", 1, "digest123")
        b = maintain_pr.operation_id("owner/repo", 2, "digest123")
        self.assertNotEqual(a, b)

    def test_different_digest_is_a_new_operation(self):
        """A new round of feedback on the same PR is a different operation --
        this is the whole reason the key is not the head SHA."""
        a = maintain_pr.operation_id("owner/repo", 1, "digest-round-1")
        b = maintain_pr.operation_id("owner/repo", 1, "digest-round-2")
        self.assertNotEqual(a, b)


class DailyCapTests(unittest.TestCase):
    def test_fresh_state_starts_at_zero(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            state = maintain_pr.load_daily(pathlib.Path(d))
            self.assertEqual(0, state["count"])

    def test_a_stale_date_resets_the_count(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            work_root = pathlib.Path(d)
            maintain_pr.save_daily(work_root, {"date": "2020-01-01", "count": 9})
            state = maintain_pr.load_daily(work_root)
            self.assertEqual(0, state["count"])

    def test_same_day_count_persists(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            work_root = pathlib.Path(d)
            maintain_pr.save_daily(work_root, {"date": maintain_pr.today(), "count": 3})
            state = maintain_pr.load_daily(work_root)
            self.assertEqual(3, state["count"])


class RoundCapTests(unittest.TestCase):
    """Three rounds per pull request -- the fleet's cap, kept, so an agent
    and a reviewer cannot trade rounds indefinitely."""

    def test_existing_rounds_counts_matching_cards_only(self):
        cards = [
            {"body": "repo: owner/repo\nnumber: 1\nother: x\n"},
            {"body": "repo: owner/repo\nnumber: 1\nother: y\n"},
            {"body": "repo: owner/repo\nnumber: 2\nother: z\n"},  # different PR
        ]
        with mock.patch.object(maintain_pr, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=0, stdout=json.dumps(cards))
            count = maintain_pr.existing_rounds("queues", "owner/repo", 1)
        self.assertEqual(2, count)

    def test_a_hermes_failure_fails_closed_at_the_cap(self):
        """Cannot verify the round count -> assume capped, never assume zero."""
        with mock.patch.object(maintain_pr, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=1, stdout="")
            count = maintain_pr.existing_rounds("queues", "owner/repo", 1)
        self.assertEqual(maintain_pr.MAX_ROUNDS_PER_PR, count)


class HermesArgvTests(unittest.TestCase):
    """Same docker-exec bridge and the same HERMES_HOME fix as
    bootstrap/publish-review and bootstrap/discover-reviews."""

    def test_runs_locally_without_a_container(self):
        import os
        old = os.environ.pop("RALPH_HERMES_CONTAINER", None)
        try:
            with mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="")
                maintain_pr.hermes("kanban", "show")
                self.assertEqual(["hermes", "kanban", "show"], run.call_args.args[0])
        finally:
            if old is not None:
                os.environ["RALPH_HERMES_CONTAINER"] = old

    def test_does_not_forward_an_unset_home(self):
        import os
        old_container = os.environ.get("RALPH_HERMES_CONTAINER")
        old_home = os.environ.pop("HERMES_HOME", None)
        os.environ["RALPH_HERMES_CONTAINER"] = "ralph-hermes-1"
        try:
            with mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="")
                maintain_pr.hermes("kanban", "show")
                argv = run.call_args.args[0]
                self.assertNotIn("-e", argv)
        finally:
            if old_container is not None:
                os.environ["RALPH_HERMES_CONTAINER"] = old_container
            else:
                os.environ.pop("RALPH_HERMES_CONTAINER", None)
            if old_home is not None:
                os.environ["HERMES_HOME"] = old_home


if __name__ == "__main__":
    unittest.main(verbosity=2)
