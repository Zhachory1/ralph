"""Reacting to review feedback on a pull request you authored.

No network for the digest/identity logic -- those are pure functions tested
against hand-built snapshots. The round-label logic (prior_rounds) mocks
the two subprocess boundaries (gh, hermes). There is no cap here by direct
instruction -- the one cap in this design is on review, not maintenance.
The circuit breaker holds a runaway (5+ rounds in 24h) for a human; it does
not cap throughput. The real end-to-end path -- feedback read
from a live PR, a real card created, a real maintainer round that pushed a
commit and posted a comment -- was proven live against Zhachory1/ralph#23;
see the PR body for that record, including the commit it produced and the
review it was responding to.

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


class ApprovalSupersedesTests(unittest.TestCase):
    """Only each reviewer's latest review is feedback, and an approval is none."""

    def _snapshot(self, reviews):
        def fake_gh(*args):
            if args[:2] == ("api", "graphql"):
                return mock.Mock(returncode=0, stdout=json.dumps(
                    {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}))
            if args[0] == "api":
                return mock.Mock(returncode=0, stdout=json.dumps(reviews))
            return mock.Mock(returncode=1, stdout="")
        with mock.patch.object(maintain_pr, "gh", side_effect=fake_gh):
            return maintain_pr.feedback_snapshot("a/b", 1, "author")

    def r(self, i, state, who="reviewer"):
        return {"id": i, "user": {"login": who}, "state": state, "body": f"review {i}"}

    def test_an_approved_pr_has_no_feedback(self):
        """The live case: canary#6, changes requested, fixed, then approved."""
        snap = self._snapshot([self.r(1, "CHANGES_REQUESTED"), self.r(2, "APPROVED")])
        self.assertEqual("empty", maintain_pr.feedback_digest(snap))

    def test_a_later_change_request_is_feedback(self):
        snap = self._snapshot([self.r(1, "APPROVED"), self.r(2, "CHANGES_REQUESTED")])
        self.assertEqual([2], [x["id"] for x in snap["reviews"]])

    def test_one_reviewers_approval_does_not_hide_anothers_request(self):
        snap = self._snapshot([self.r(1, "CHANGES_REQUESTED", "a"), self.r(2, "APPROVED", "b")])
        self.assertEqual([1], [x["id"] for x in snap["reviews"]])

    def test_a_self_approval_posted_as_a_comment_is_not_feedback(self):
        """The live case: kns#3, the fleet's APPROVE verdict submitted as
        COMMENTED because the PR was self-authored."""
        r = self.r(1, "COMMENTED")
        r["body"] = "**Verdict: APPROVE** (submitted as COMMENT -- self-authored PR)"
        self.assertEqual("empty", maintain_pr.feedback_digest(self._snapshot([r])))

    def test_a_dismissed_review_is_not_feedback(self):
        """The live case: canary#5 and #6, a duplicate approval dismissed. A
        dismissed review asks for nothing, so it must not start a round."""
        snap = self._snapshot([self.r(1, "DISMISSED")])
        self.assertEqual("empty", maintain_pr.feedback_digest(snap))

    def test_a_dismissed_approval_does_not_hide_an_earlier_request(self):
        snap = self._snapshot([self.r(1, "CHANGES_REQUESTED"), self.r(2, "DISMISSED")])
        self.assertEqual([1], [x["id"] for x in snap["reviews"]])

    def test_a_comment_review_is_still_feedback(self):
        snap = self._snapshot([self.r(1, "COMMENTED")])
        self.assertEqual([1], [x["id"] for x in snap["reviews"]])


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


class NoCapTests(unittest.TestCase):
    """By direct instruction: maintenance has no per-PR cap and no daily
    cap. The one cap in this design is on review. These assert the absence
    stays absent, not that a count stays under some number."""

    def test_no_round_cap_constant_exists(self):
        self.assertFalse(hasattr(maintain_pr, "MAX_ROUNDS_PER_PR"))

    def test_no_daily_cap_state_functions_exist(self):
        self.assertFalse(hasattr(maintain_pr, "load_daily"))
        self.assertFalse(hasattr(maintain_pr, "save_daily"))

    def test_main_has_no_daily_cap_argument(self):
        import subprocess
        help_text = subprocess.run(["python3", str(SCRIPT), "--help"],
                                   capture_output=True, text=True).stdout
        self.assertNotIn("--daily-cap", help_text)


class RoundLabelTests(unittest.TestCase):
    """prior_rounds labels the round for a human reading the board, and feeds
    the circuit breaker. It is never a throughput cap."""

    def test_counts_matching_cards_only(self):
        cards = [
            {"body": "repo: owner/repo\nnumber: 1\nother: x\n"},
            {"body": "repo: owner/repo\nnumber: 1\nother: y\n"},
            {"body": "repo: owner/repo\nnumber: 2\nother: z\n"},  # different PR
        ]
        with mock.patch.object(maintain_pr, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=0, stdout=json.dumps(cards))
            count = len(maintain_pr.prior_rounds("queues", "owner/repo", 1))
        self.assertEqual(2, count)

    def test_a_hermes_failure_returns_none_not_a_fabricated_count(self):
        """No cap means no 'fail closed at the cap' -- a read failure just
        means the label is imprecise, nothing is blocked by it."""
        with mock.patch.object(maintain_pr, "hermes") as hermes_mock:
            hermes_mock.return_value = mock.Mock(returncode=1, stdout="")
            count = maintain_pr.prior_rounds("queues", "owner/repo", 1)
        self.assertIsNone(count)


class CircuitBreakerTests(unittest.TestCase):
    """5+ rounds on one PR in 24h is a runaway (usually CI failures that change
    every run), not a reviewer. The next round is created blocked."""

    NOW = 1_800_000_000

    def cards(self, *ages_hours):
        return [{"created_at": self.NOW - h * 3600, "body": ""} for h in ages_hours]

    def test_four_recent_rounds_do_not_trip(self):
        self.assertFalse(maintain_pr.breaker_tripped(self.cards(1, 2, 3, 4), self.NOW))

    def test_five_recent_rounds_trip(self):
        self.assertTrue(maintain_pr.breaker_tripped(self.cards(1, 2, 3, 4, 5), self.NOW))

    def test_old_rounds_do_not_count(self):
        self.assertFalse(maintain_pr.breaker_tripped(self.cards(1, 2, 30, 40, 50, 60), self.NOW))

    def test_an_unreadable_board_does_not_trip(self):
        self.assertFalse(maintain_pr.breaker_tripped(None, self.NOW))

    def run_main(self, prior, op_seen=False):
        """main() with GitHub and the board stubbed; returns the hermes calls."""
        import sys
        snapshot = {"reviews": [{"id": 9, "body": "fix it"}], "threads": [], "checks": []}
        op = maintain_pr.operation_id("o/r", 1, maintain_pr.feedback_digest(snapshot))
        cards = [{"created_at": c["created_at"], "body": f"repo: o/r\nnumber: 1\n"
                  + (f"operation_id: {op}\n" if op_seen and i == 0 else "")}
                 for i, c in enumerate(prior)]
        calls = []

        def fake_hermes(*args):
            calls.append(args)
            if "list" in args:
                return mock.Mock(returncode=0, stdout=json.dumps(cards))
            if "create" in args:
                return mock.Mock(returncode=0, stdout=json.dumps({"id": "t_new"}))
            return mock.Mock(returncode=0, stdout="", stderr="")

        gh_view = mock.Mock(returncode=0, stdout=json.dumps(
            {"headRefOid": "a" * 40, "headRefName": "b"}))
        with mock.patch.object(sys, "argv", ["maintain-pr", "--once", "--repo", "o/r",
                                             "--pr", "1", "--login", "me"]), \
                mock.patch.object(maintain_pr, "feedback_snapshot", return_value=snapshot), \
                mock.patch.object(maintain_pr, "gh", return_value=gh_view), \
                mock.patch.object(maintain_pr, "hermes", side_effect=fake_hermes), \
                mock.patch.object(maintain_pr, "notify") as notify, \
                mock.patch.object(maintain_pr.time, "time", return_value=self.NOW):
            self.assertEqual(0, maintain_pr.main())
        # hermes("kanban", "--board", board, VERB, ...)
        return [c[3] for c in calls if len(c) > 3], notify

    def test_a_tripped_breaker_blocks_the_new_card_and_alerts_once(self):
        verbs, notify = self.run_main(self.cards(1, 2, 3, 4, 5))
        self.assertIn("create", verbs)
        self.assertIn("block", verbs)
        notify.assert_called_once()

    def test_below_the_threshold_the_card_runs(self):
        verbs, notify = self.run_main(self.cards(1, 2))
        self.assertNotIn("block", verbs)
        notify.assert_not_called()

    def test_a_rerun_of_existing_feedback_is_never_held_again(self):
        """The same digest returns the same card every pass; re-blocking and
        re-alerting it every 15 minutes would be the runaway this prevents."""
        verbs, notify = self.run_main(self.cards(1, 2, 3, 4, 5), op_seen=True)
        self.assertNotIn("block", verbs)
        notify.assert_not_called()


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
