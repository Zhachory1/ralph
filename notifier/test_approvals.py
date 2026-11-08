"""Tests for the GitHub approval watcher.

The loop these close: a human approves a design-doc PR in GitHub, and the
card waiting on them resumes. The risk is acting on the wrong signal, so
most of these pin what must *not* unblock a card.
"""

import io
import json
import pathlib
import unittest
from contextlib import redirect_stderr
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


class PullRequestFromAncestorsTests(unittest.TestCase):
    """A gate card is created before the pull request it gates exists, so the
    address is never on the gate card itself. It has to be found on whichever
    ancestor opened the pull request and later completed."""

    def test_finds_pr_on_the_immediate_parent(self):
        board = mock.Mock()
        board.show.return_value = {
            "latest_summary": "Opened https://github.com/o/r/pull/9",
            "comments": [],
        }
        detail = {"parents": ["t_parent"]}
        self.assertEqual(
            ("o/r", 9), approvals.pull_request_from_ancestors(board, "b", detail)
        )
        board.show.assert_called_once_with("b", "t_parent")

    def test_walks_past_a_parent_with_nothing_to_a_grandparent(self):
        board = mock.Mock()
        board.show.side_effect = [
            {"latest_summary": None, "comments": [], "parents": ["t_grandparent"]},
            {"latest_summary": "https://github.com/o/r/pull/4", "comments": []},
        ]
        detail = {"parents": ["t_parent"]}
        self.assertEqual(
            ("o/r", 4), approvals.pull_request_from_ancestors(board, "b", detail)
        )

    def test_none_when_no_ancestor_has_a_pull_request(self):
        board = mock.Mock()
        board.show.return_value = {"latest_summary": None, "comments": [], "parents": []}
        detail = {"parents": ["t_parent"]}
        self.assertIsNone(approvals.pull_request_from_ancestors(board, "b", detail))

    def test_no_parents_resolves_to_none_without_calling_the_board(self):
        board = mock.Mock()
        self.assertIsNone(
            approvals.pull_request_from_ancestors(board, "b", {"parents": []})
        )
        board.show.assert_not_called()

    def test_does_not_revisit_a_parent_reachable_twice(self):
        """Two dependency edges to the same ancestor must not loop forever."""
        board = mock.Mock()
        board.show.return_value = {"latest_summary": None, "comments": [], "parents": ["t_shared"]}
        detail = {"parents": ["t_shared", "t_shared"]}
        approvals.pull_request_from_ancestors(board, "b", detail)
        self.assertEqual(1, board.show.call_count)


class GateAddressPropagationTests(unittest.TestCase):
    """The end-to-end loop: a gate card with no address of its own finds one
    on an ancestor that completed having opened a pull request, and carries
    it from then on -- without a human pasting it by hand."""

    def _github(self, merged=True):
        github = mock.Mock()
        github.reviews.return_value = []
        github.diff_comments.return_value = []
        github.issue_comments.return_value = []
        github.pull_request.return_value = {"merged": merged, "labels": []}
        return github

    def test_address_propagates_from_a_completed_ancestor(self):
        board = mock.Mock()
        board.waiting.return_value = []
        board.pending.return_value = [{"id": "t_gate", "status": "blocked"}]

        def show(_board, task_id):
            if task_id == "t_gate":
                return {"latest_summary": None, "comments": [], "parents": ["t_author"]}
            return {
                "latest_summary": "Opened https://github.com/o/r/pull/9",
                "comments": [],
            }

        board.show.side_effect = show
        github = self._github()

        with mock.patch.object(approvals, "Board", return_value=board), \
             mock.patch.object(approvals, "GitHub", return_value=github):
            approvals.main(["--board", "b"])

        github.reviews.assert_called_once_with("o/r", 9)
        board.comment.assert_any_call("b", "t_gate", "https://github.com/o/r/pull/9")
        board.unblock.assert_called_once_with("b", "t_gate")

    def test_gate_with_nothing_resolvable_is_reported_not_skipped_silently(self):
        board = mock.Mock()
        board.waiting.return_value = []
        board.pending.return_value = [{"id": "t_gate", "status": "blocked"}]
        board.show.return_value = {"latest_summary": None, "comments": [], "parents": []}
        github = self._github()

        with mock.patch.object(approvals, "Board", return_value=board), \
             mock.patch.object(approvals, "GitHub", return_value=github):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                approvals.main(["--board", "b"])

        github.reviews.assert_not_called()
        board.unblock.assert_not_called()
        self.assertIn("t_gate", stderr.getvalue())


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

    def _run(self, pending, events=()):
        github = mock.Mock()
        github.reviews.return_value = []
        github.diff_comments.return_value = []
        github.issue_comments.return_value = []
        github.pull_request.return_value = {"merged": True, "labels": []}
        board = mock.Mock()
        board.waiting.return_value = []
        board.pending.return_value = pending
        board.show.return_value = {
            "latest_summary": "DD https://github.com/o/r/pull/3",
            "comments": [],
            "events": list(events),
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

    def test_a_triage_card_not_parked_by_a_block_loop_is_left_alone(self):
        """#102 review: a card a person put in triage is not ours to resume."""
        board = self._run([{"id": "t_1", "status": "triage"}])
        board.release_triage.assert_not_called()
        board.handoff.assert_not_called()

    def test_a_gate_parked_in_triage_is_released_and_handed_back(self):
        """docs#7: the author re-blocked the gate on a revision, Hermes moved it
        to triage, and the dd-changes label was never picked up."""
        board = self._run([{"id": "t_1", "status": "triage"}],
                          events=[{"kind": "block_loop_detected"}])
        board.release_triage.assert_called_once_with("b", "t_1")
        board.unblock.assert_not_called()
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
        board.waiting.return_value = []
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




class StrandedHandoffTests(unittest.TestCase):
    """#103: the card moved, the handoff failed, and it left the polled statuses."""

    def detail(self, events, note="Changes requested in GitHub. fix it", at=100):
        return {"comments": [{"body": "https://github.com/o/r/pull/3", "created_at": 50},
                             {"body": note, "created_at": at}],
                "events": events}

    def test_a_decision_with_no_handoff_after_it_is_stranded(self):
        self.assertEqual("changes", approvals.stranded(self.detail([{"kind": "unblocked", "created_at": 101}])))

    def test_a_handoff_after_the_decision_is_not_stranded(self):
        self.assertIsNone(approvals.stranded(self.detail(
            [{"kind": "assigned", "created_at": 101, "payload": {"assignee": "ralph-author", "from": None}}])))

    def test_a_handoff_before_the_decision_does_not_count(self):
        self.assertEqual("approved", approvals.stranded(self.detail(
            [{"kind": "assigned", "created_at": 99}], note="Approved in GitHub.")))

    def test_a_card_with_no_decision_is_not_stranded(self):
        self.assertIsNone(approvals.stranded({"comments": [], "events": []}))

    def test_the_poll_redoes_only_the_handoff(self):
        board = mock.Mock()
        board.waiting.return_value = [{"id": "t_1", "status": "ready"}]
        board.pending.return_value = []
        board.show.return_value = self.detail([{"kind": "unblocked", "created_at": 101}])
        with mock.patch.object(approvals, "Board", return_value=board), \
             mock.patch.object(approvals, "GitHub", return_value=mock.Mock()):
            approvals.main(["--board", "b"])
        board.handoff.assert_called_once_with("b", "t_1", "changes", "blocked")
        board.unblock.assert_not_called()
        board.comment.assert_not_called()



class PaginationTests(unittest.TestCase):
    """#59: every page is read, so a verdict or comment past the 30th counts."""

    def run_list(self, stdout):
        github = approvals.GitHub()
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(stdout=stdout)
            items = github.reviews("o/r", 7)
        return run.call_args.args[0], items

    def test_lists_ask_for_every_page(self):
        argv, _ = self.run_list("[[]]")
        self.assertIn("--paginate", argv)
        self.assertIn("--slurp", argv)
        self.assertTrue(argv[-1].endswith("/pulls/7/reviews?per_page=100"))

    def test_pages_are_flattened_in_order(self):
        _, items = self.run_list('[[{"id": 1}, {"id": 2}], [{"id": 3}]]')
        self.assertEqual([1, 2, 3], [i["id"] for i in items])

    def test_a_late_verdict_on_page_two_wins(self):
        _, items = self.run_list(json.dumps([
            [{"state": "APPROVED", "submitted_at": "2026-01-01T00:00:00Z"}] * 30,
            [{"state": "CHANGES_REQUESTED", "submitted_at": "2026-02-01T00:00:00Z"}]]))
        self.assertEqual("changes", approvals.verdict(items))



class ApprovalGateTests(unittest.TestCase):
    """#56: an unassigned gate between design and build is completed on
    approval, so its dependants release; changes requested leave it held."""

    def board(self, assignee=None, title="Approve design: x"):
        board = mock.Mock()
        board.assignee.return_value = assignee
        board.show.return_value = {"task": {"assignee": assignee, "title": title}}
        return board

    def test_approving_an_unassigned_gate_completes_it(self):
        board = self.board()
        approvals.apply(board, "b", "t_gate", "approved", "", status="blocked")
        board.unblock.assert_called_once_with("b", "t_gate")
        board.complete.assert_called_once()
        board.handoff.assert_not_called()

    def test_changes_on_a_gate_leave_it_blocked_and_untouched(self):
        """#110 review: unblocking it on changes stopped a later approval
        from ever being polled."""
        board = self.board()
        approvals.apply(board, "b", "t_gate", "changes", "fix", status="blocked")
        board.complete.assert_not_called()
        board.unblock.assert_not_called()
        board.handoff.assert_not_called()
        board.comment.assert_not_called()

    def test_an_approved_review_on_an_open_pr_does_not_open_the_gate(self):
        """#110 review: the gate opens on a merge, not an APPROVED review."""
        board = mock.Mock()
        board.waiting.return_value = []
        board.pending.return_value = [{"id": "t_gate", "status": "blocked"}]
        board.show.return_value = {
            "task": {"assignee": None, "title": "Approve design: x"},
            "latest_summary": "https://github.com/o/r/pull/3", "comments": [], "events": []}
        github = mock.Mock()
        github.reviews.return_value = [{"state": "APPROVED", "submitted_at": "2026-01-01T00:00:00Z"}]
        github.pull_request.return_value = {"merged": False, "labels": []}
        github.diff_comments.return_value = github.issue_comments.return_value = []
        with mock.patch.object(approvals, "Board", return_value=board), \
             mock.patch.object(approvals, "GitHub", return_value=github):
            approvals.main(["--board", "b"])
        board.complete.assert_not_called()
        board.unblock.assert_not_called()

    def test_approving_a_prd_gate_completes_it(self):
        """#65: the PRD gate works like the design gate."""
        board = self.board(title="Approve PRD: x")
        approvals.apply(board, "b", "t_gate", "approved", "", status="blocked")
        board.complete.assert_called_once()

    def test_approving_a_plan_gate_completes_it(self):
        """#119: the plan gate works like the PRD and design gates."""
        board = self.board(title="Approve plan: x")
        approvals.apply(board, "b", "t_gate", "approved", "", status="blocked")
        board.complete.assert_called_once()

    def test_an_unassigned_card_that_is_not_a_gate_is_not_completed(self):
        """#110 review: only the orchestrator's gate is completed on approval."""
        board = self.board(title="some unassigned card")
        approvals.apply(board, "b", "t_x", "approved", "", status="blocked")
        board.complete.assert_not_called()

    def test_a_gate_left_unblocked_but_not_completed_is_finished_next_poll(self):
        """#110 review: an interrupted gate completion is retried."""
        board = mock.Mock()
        board.waiting.return_value = [{"id": "t_gate", "status": "ready"}]
        board.pending.return_value = []
        board.show.return_value = {
            "task": {"assignee": None, "title": "Approve design: x"},
            "comments": [{"body": "Approved in GitHub.", "created_at": 100}],
            "events": [{"kind": "unblocked", "created_at": 101}]}
        with mock.patch.object(approvals, "Board", return_value=board), \
             mock.patch.object(approvals, "GitHub", return_value=mock.Mock()):
            approvals.main(["--board", "b"])
        board.complete.assert_called_once_with("b", "t_gate", "Approved in GitHub.")
        board.handoff.assert_not_called()

    def test_an_assigned_card_is_handed_back_not_completed(self):
        board = self.board("ralph-author")
        approvals.apply(board, "b", "t_dd", "approved", "", status="blocked")
        board.complete.assert_not_called()
        board.handoff.assert_called_once()



class StaleReviewTests(unittest.TestCase):
    """#64: an old review decision must not replay against a newer head."""

    def review(self, state, sha, at="2026-01-01T00:00:00Z"):
        return {"state": state, "commit_id": sha, "submitted_at": at}

    def test_a_review_on_an_older_head_is_not_a_decision(self):
        pull = {"head": {"sha": "new"}, "labels": []}
        self.assertIsNone(approvals.verdict([self.review("CHANGES_REQUESTED", "old")], pull))

    def test_a_review_on_the_current_head_is_a_decision(self):
        pull = {"head": {"sha": "new"}, "labels": []}
        self.assertEqual("changes", approvals.verdict([self.review("CHANGES_REQUESTED", "new")], pull))

    def test_the_label_and_a_merge_stay_current_whatever_the_head(self):
        self.assertEqual("changes", approvals.verdict(
            [], {"head": {"sha": "new"}, "labels": [{"name": "dd-changes"}]}))
        self.assertEqual("approved", approvals.verdict(
            [self.review("CHANGES_REQUESTED", "old")], {"head": {"sha": "new"}, "merged": True}))


class NotifierOrderTests(unittest.TestCase):
    """#64: decisions are applied before alerts, so a gate just decided is
    not announced as still waiting."""

    def test_approvals_run_before_the_poller(self):
        text = (pathlib.Path(__file__).resolve().parent.parent / "bootstrap" / "run-notifier").read_text()
        self.assertLess(text.index("approvals.py"), text.index("poller.py"))



class SameAuthorReworkTests(unittest.TestCase):
    """#66: Hermes lifts active_pr only on an assign to a different profile,
    so the handoff unassigns and then assigns the same author back."""

    def test_handoff_unassigns_then_assigns_the_same_owner(self):
        board = approvals.Board()
        with mock.patch.object(board, "assignee", return_value="ralph-author"), \
             mock.patch.object(board, "_run") as run:
            board.handoff("b", "t_1", "changes", "blocked")
        self.assertEqual([mock.call("--board", "b", "assign", "t_1", "none"),
                          mock.call("--board", "b", "assign", "t_1", "ralph-author")],
                         run.call_args_list)

    def test_a_retry_after_an_interrupted_handoff_restores_the_owner(self):
        """#114 review: the card was left unassigned; the retry puts the
        author back from the unassign event."""
        board = approvals.Board()
        detail = {"task": {"assignee": None},
                  "events": [{"kind": "assigned", "payload": {"assignee": None, "from": "ralph-author"}}]}
        with mock.patch.object(board, "show", return_value=detail), \
             mock.patch.object(board, "_run") as run:
            board.handoff("b", "t_1", "changes", "blocked")
        self.assertEqual(mock.call("--board", "b", "assign", "t_1", "ralph-author"),
                         run.call_args_list[-1])

    def test_an_unassign_alone_is_not_a_completed_handoff(self):
        """Interrupted between the two assigns: still stranded, so it retries."""
        detail = {"comments": [{"body": "Changes requested in GitHub. x", "created_at": 100}],
                  "events": [{"kind": "assigned", "created_at": 101,
                              "payload": {"assignee": None, "from": "ralph-author"}}]}
        self.assertEqual("changes", approvals.stranded(detail))

if __name__ == "__main__":
    unittest.main()
