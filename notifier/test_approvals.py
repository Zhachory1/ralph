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
        """Comments carry feedback, not the decision to act on it. Three
        comments might mean "fix these" or "answered, carry on"; the label
        says which."""
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

    def test_changes_label_sends_it_back(self):
        pull = {"merged": False, "labels": [{"name": "dd-changes"}]}
        self.assertEqual("changes", approvals.verdict([], pull))

    def test_unrelated_labels_are_not_decisions(self):
        pull = {"merged": False, "labels": [{"name": "agent-authored"}]}
        self.assertIsNone(approvals.verdict([], pull))

    def test_comment_on_own_pr_is_still_not_a_decision(self):
        pull = {"merged": False, "labels": []}
        self.assertIsNone(approvals.verdict([review("COMMENTED")], pull))


class FeedbackTests(unittest.TestCase):
    """Feedback is gathered from every surface, because a human comments
    wherever is convenient and a worker that reads only one misses the rest."""

    def test_collects_review_bodies(self):
        text = approvals.feedback([review("COMMENTED", body="Add a Non-goals section")])
        self.assertIn("Add a Non-goals section", text)

    def test_collects_top_level_comments(self):
        text = approvals.feedback([], [], [{"created_at": "t1", "body": "What is the TTL?"}])
        self.assertIn("What is the TTL?", text)

    def test_inline_comments_keep_their_location(self):
        """"This is wrong" means nothing without the line it points at."""
        text = approvals.feedback(
            [], [{"created_at": "t1", "path": "dd.md", "line": 12, "body": "unclear"}], []
        )
        self.assertIn("dd.md:12", text)
        self.assertIn("unclear", text)

    def test_orders_oldest_first_across_surfaces(self):
        text = approvals.feedback(
            [review("COMMENTED", submitted_at="2026-10-15T12:00:00Z", body="third")],
            [{"created_at": "2026-10-15T10:00:00Z", "path": "a.md", "body": "first"}],
            [{"created_at": "2026-10-15T11:00:00Z", "body": "second"}],
        )
        self.assertLess(text.index("first"), text.index("second"))
        self.assertLess(text.index("second"), text.index("third"))

    def test_skips_empty_bodies(self):
        """Approving with no words is common and should not yield blank noise."""
        self.assertEqual("", approvals.feedback([review("APPROVED")], [], [{"body": "  "}]))


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


class HumanGateTests(unittest.TestCase):
    """A human gate is a blocked card. `review` belongs to the agent reviewer,
    and polling it would race the dispatcher for the same card."""

    def _run(self, pending):
        github = mock.Mock()
        github.reviews.return_value = []
        github.diff_comments.return_value = []
        github.issue_comments.return_value = []
        github.pull_request.return_value = {"merged": True, "labels": []}
        board = mock.Mock()
        board.pending.return_value = pending
        board.show.return_value = {
            "latest_summary": "DD https://github.com/o/r/pull/3",
            "comments": [],
        }
        with mock.patch.object(approvals, "Board", return_value=board), \
             mock.patch.object(approvals, "GitHub", return_value=github):
            approvals.main(["--board", "b"])
        return board

    def test_blocked_card_is_resumed_with_unblock(self):
        board = self._run([{"id": "t_1", "status": "blocked"}])
        board.unblock.assert_called_once_with("b", "t_1")

    def test_handoff_on_a_blocked_card_does_not_reopen_review(self):
        """reopen-review rejects a card that is not in a review run, which
        is every card a human gate blocked."""
        board = mock.Mock()
        approvals.apply(board, "b", "t_1", "changes", "fix it", status="blocked")
        board.handoff.assert_called_once_with("b", "t_1", "changes", "blocked")

    def test_unblock_is_followed_by_a_handoff(self):
        """The dispatcher will not respawn a card whose recent comments hold a
        pull-request URL unless a handoff event follows. Unblocking alone
        leaves it in `ready` forever."""
        board = self._run([{"id": "t_1", "status": "blocked"}])
        board.handoff.assert_called_once()

    def test_only_blocked_is_polled(self):
        board = self._run([])
        board.pending.assert_called_once_with("b")


class BotFilterTests(unittest.TestCase):
    """A real repository answers a review with a security scan and a coverage
    table. Those are not the feedback, and a worker handed them revises
    against a robot instead of the person who asked."""

    def bot(self, body):
        return {"created_at": "t1", "body": body, "user": {"login": "x[bot]", "type": "Bot"}}

    def human(self, body):
        return {"created_at": "t2", "body": body, "user": {"login": "z", "type": "User"}}

    def test_bot_comments_are_dropped(self):
        text = approvals.feedback([], [], [self.bot("Security Scan Complete")])
        self.assertEqual("", text)

    def test_human_comments_survive_alongside_bots(self):
        text = approvals.feedback(
            [], [], [self.bot("Coverage 98%"), self.human("Confirmed")])
        self.assertIn("Confirmed", text)
        self.assertNotIn("Coverage", text)

    def test_bot_reviews_are_dropped(self):
        text = approvals.feedback([
            {"state": "COMMENTED", "submitted_at": "t1", "body": "bot said",
             "user": {"login": "ci[bot]", "type": "Bot"}}])
        self.assertEqual("", text)


class LabelClearingTests(unittest.TestCase):
    def test_label_is_removed_after_acting(self):
        """A label that stays applied is a decision the next poll makes again,
        reopening a card the author already revised."""
        github = mock.Mock()
        github.reviews.return_value = []
        github.diff_comments.return_value = []
        github.issue_comments.return_value = []
        github.pull_request.return_value = {"merged": False, "labels": [{"name": "dd-changes"}]}

        board = mock.Mock()
        board.pending.return_value = [{"id": "t_1", "status": "review"}]
        board.show.return_value = {
            "latest_summary": "DD https://github.com/o/r/pull/3",
            "comments": [],
        }

        with mock.patch.object(approvals, "Board", return_value=board), \
             mock.patch.object(approvals, "GitHub", return_value=github):
            approvals.main(["--board", "b"])

        removed = {call.args[2] for call in github.remove_label.call_args_list}
        self.assertIn("dd-changes", removed)


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
