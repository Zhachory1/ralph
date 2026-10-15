"""Tests for the GitHub approval watcher.

The loop these close: a human approves a design-doc PR in GitHub, and the
card waiting on them resumes. The risk is acting on the wrong signal, so
most of these pin what must *not* unblock a card.
"""

import json
import unittest
from unittest import mock

import approvals


def review(state, submitted_at="2026-10-15T10:00:00Z", user="zhach1", body=""):
    return {
        "state": state,
        "submitted_at": submitted_at,
        "user": {"login": user},
        "body": body,
    }


class VerdictTests(unittest.TestCase):
    def test_approved_is_approval(self):
        self.assertEqual("approved", approvals.verdict([review("APPROVED")]))

    def test_changes_requested_is_rework(self):
        self.assertEqual("changes", approvals.verdict([review("CHANGES_REQUESTED")]))

    def test_comment_alone_is_not_a_verdict(self):
        """A human thinking out loud is not a decision. Treating a comment as
        approval would merge work nobody agreed to."""
        self.assertIsNone(approvals.verdict([review("COMMENTED")]))

    def test_no_reviews_is_not_a_verdict(self):
        self.assertIsNone(approvals.verdict([]))

    def test_latest_verdict_wins(self):
        """Approval followed by second thoughts means rework, not approval."""
        reviews = [
            review("APPROVED", "2026-10-15T10:00:00Z"),
            review("CHANGES_REQUESTED", "2026-10-15T11:00:00Z"),
        ]
        self.assertEqual("changes", approvals.verdict(reviews))

    def test_comments_after_a_verdict_do_not_erase_it(self):
        reviews = [
            review("APPROVED", "2026-10-15T10:00:00Z"),
            review("COMMENTED", "2026-10-15T11:00:00Z"),
        ]
        self.assertEqual("approved", approvals.verdict(reviews))

    def test_out_of_order_submissions_are_sorted(self):
        reviews = [
            review("CHANGES_REQUESTED", "2026-10-15T11:00:00Z"),
            review("APPROVED", "2026-10-15T09:00:00Z"),
        ]
        self.assertEqual("changes", approvals.verdict(reviews))


class SelfAuthoredPullRequestTests(unittest.TestCase):
    """GitHub refuses APPROVED and CHANGES_REQUESTED on your own pull request,
    and every agent PR is opened under your account. Merge and labels are the
    only decisions available, so they have to outrank review state."""

    def test_merge_is_approval(self):
        self.assertEqual("approved", approvals.verdict([], {"merged": True}))

    def test_approved_label_is_approval(self):
        pull = {"merged": False, "labels": [{"name": "dd-approved"}]}
        self.assertEqual("approved", approvals.verdict([], pull))

    def test_changes_label_sends_it_back(self):
        pull = {"merged": False, "labels": [{"name": "dd-changes"}]}
        self.assertEqual("changes", approvals.verdict([], pull))

    def test_changes_label_outranks_approved_label(self):
        pull = {"merged": False, "labels": [{"name": "dd-approved"}, {"name": "dd-changes"}]}
        self.assertEqual("changes", approvals.verdict([], pull))

    def test_unrelated_labels_are_not_decisions(self):
        pull = {"merged": False, "labels": [{"name": "agent-authored"}]}
        self.assertIsNone(approvals.verdict([], pull))

    def test_comment_on_own_pr_is_still_not_a_decision(self):
        pull = {"merged": False, "labels": []}
        self.assertIsNone(approvals.verdict([review("COMMENTED")], pull))


class FeedbackTests(unittest.TestCase):
    def test_collects_bodies_from_the_deciding_review(self):
        text = approvals.feedback(
            [review("CHANGES_REQUESTED", body="Add a Non-goals section")]
        )
        self.assertIn("Add a Non-goals section", text)

    def test_empty_when_reviewer_left_no_words(self):
        self.assertEqual("", approvals.feedback([review("CHANGES_REQUESTED")]))


class PullRequestLinkTests(unittest.TestCase):
    def test_finds_pr_url_in_card_summary(self):
        detail = {
            "latest_summary": "DD ready: segment refresh https://github.com/o/r/pull/112",
            "comments": [],
        }
        self.assertEqual(("o/r", 112), approvals.pull_request_for(detail))

    def test_finds_pr_url_in_a_comment(self):
        detail = {
            "latest_summary": None,
            "comments": [{"body": "BLOCKED: review github.com/o/r/pull/7"}],
        }
        self.assertEqual(("o/r", 7), approvals.pull_request_for(detail))

    def test_none_when_card_links_no_pr(self):
        """A card with no PR is waiting on something else. Leave it alone."""
        detail = {"latest_summary": "question: which TTL?", "comments": []}
        self.assertIsNone(approvals.pull_request_for(detail))


class ResolveTests(unittest.TestCase):
    def test_blocked_card_is_unblocked(self):
        board = mock.Mock()
        approvals.apply(board, "b", "t_1", "approved", "ship it", status="blocked")

        board.comment.assert_called_once()
        board.unblock.assert_called_once_with("b", "t_1")

    def test_approved_review_card_completes(self):
        """`unblock` rejects a card in review; it is not blocked. Approval
        finishes the card instead."""
        board = mock.Mock()
        approvals.apply(board, "b", "t_1", "approved", "ship it", status="review")

        board.complete.assert_called_once()
        board.unblock.assert_not_called()

    def test_changes_on_review_card_reopens_it(self):
        board = mock.Mock()
        approvals.apply(board, "b", "t_1", "changes", "Add Non-goals", status="review")

        board.reopen_review.assert_called_once()
        self.assertIn("Add Non-goals", board.reopen_review.call_args[0][2])
        board.complete.assert_not_called()

    def test_feedback_is_recorded_before_the_card_resumes(self):
        """A worker re-spawns on unblock. Comment first or it wakes up without
        the feedback it is supposed to act on."""
        calls = []
        board = mock.Mock()
        board.comment.side_effect = lambda *a: calls.append("comment")
        board.reopen_review.side_effect = lambda *a: calls.append("reopen")

        approvals.apply(board, "b", "t_1", "changes", "fix it", status="review")

        self.assertEqual(["comment", "reopen"], calls)


class CliTests(unittest.TestCase):
    def test_tolerates_empty_output(self):
        with mock.patch.object(approvals.subprocess, "run") as run:
            run.return_value = mock.Mock(stdout="", returncode=0)
            self.assertEqual([], approvals.GitHub().reviews("o/r", 1))

    def test_raises_on_malformed_json(self):
        with mock.patch.object(approvals.subprocess, "run") as run:
            run.return_value = mock.Mock(stdout="not json", returncode=0)
            with self.assertRaises(json.JSONDecodeError):
                approvals.GitHub().reviews("o/r", 1)


if __name__ == "__main__":
    unittest.main()
