"""Orchestration tests: the board behaves the way the design claims.

These run against a real Kanban board and the real dispatcher, but never
spawn a worker and never call a model: `dispatch --dry-run` reports what
would have happened. So they are fast and free, and still catch the things
that only appear in a live run.

Every test here exists because something broke, or because the design makes
a claim a reader should be able to check. They assert behaviour, not prose.

    docker compose exec -u hermes hermes python3 /ralph/evals/test_orchestration.py
"""

import os
import tempfile
import unittest
import uuid

from board import Board

AUTHOR = "ralph-author"
REVIEWER = "ralph-reviewer"
PR_URL = "https://github.com/example/repo/pull/1"


class BoardTest(unittest.TestCase):
    """Each test gets its own cards and cleans them up."""

    @classmethod
    def setUpClass(cls):
        # A private DB the gateway never ticks: on a listed board the live
        # dispatcher spawned real workers on these cards mid-test.
        cls.tmp = tempfile.TemporaryDirectory()
        cls.board = Board("ralph-evals", db_path=os.path.join(cls.tmp.name, "kanban.db"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.created = []

    def tearDown(self):
        for task_id in self.created:
            self.board.archive(task_id)

    def card(self, title: str = "", **flags) -> str:
        flags.setdefault("assignee", AUTHOR)
        task_id = self.board.create(title or f"eval {uuid.uuid4().hex[:6]}", **flags)
        self.created.append(task_id)
        return task_id


class HumanGateTests(BoardTest):
    """A blocked card is the human gate. Its whole value is that nothing but a
    person moves it."""

    def test_blocked_card_is_never_dispatched(self):
        task = self.card()
        self.board.block(task, "review-required: DD ready")

        for _ in range(3):
            self.assertFalse(self.board.would_spawn(task))
        self.assertEqual("blocked", self.board.status(task))

    def test_unblock_returns_it_to_the_queue(self):
        task = self.card()
        self.board.block(task, "review-required: DD ready")
        self.board.unblock(task)

        self.assertEqual("ready", self.board.status(task))

    def test_block_reason_is_recoverable(self):
        """`list --json` omits the reason, so the notifier reads it from the
        card. If this breaks, alerts silently lose their text."""
        task = self.card()
        self.board.block(task, "review-required: 3 decisions, scope M")

        self.assertTrue(
            any("3 decisions, scope M" in c for c in self.board.comments(task)),
            "block reason did not survive onto the card",
        )


class RespawnGuardTests(BoardTest):
    """A card whose comments hold a pull-request URL is guarded against
    respawn, so a crashed worker cannot open a second PR. The guard lifts on a
    handoff, which is what a human decision has to record."""

    def test_pr_comment_guards_respawn(self):
        task = self.card()
        self.board.comment(task, f"Opened {PR_URL}")

        self.assertEqual("active_pr", self.board.guard_reason(task))

    def test_unblock_alone_does_not_lift_the_guard(self):
        """The bug this suite was written for: the card returns to `ready` and
        then sits there forever, with only a gateway log line to say why."""
        task = self.card()
        self.board.comment(task, f"Opened {PR_URL}")
        self.board.block(task, "review-required: DD ready")
        self.board.unblock(task)

        self.assertEqual("ready", self.board.status(task))
        self.assertEqual("active_pr", self.board.guard_reason(task))

    def test_reopen_review_lifts_the_guard(self):
        task = self.card()
        self.board.request_review(task, "DD ready", reviewer=REVIEWER)
        self.board.comment(task, f"Opened {PR_URL}")
        self.board.reopen_review(task, "Changes requested in GitHub")

        self.assertIsNone(self.board.guard_reason(task))

    def test_reassigning_elsewhere_and_back_lifts_the_guard(self):
        """The approval path: a card handed back to its owner may run again."""
        task = self.card()
        self.board.comment(task, f"Opened {PR_URL}")
        self.board.assign(task, REVIEWER)
        self.board.assign(task, AUTHOR)

        self.assertIsNone(self.board.guard_reason(task))

    def test_noop_reassign_does_not_lift_the_guard(self):
        """Reassigning a card to the profile that already owns it is not a
        handoff, and the dispatcher knows it."""
        task = self.card()
        self.board.comment(task, f"Opened {PR_URL}")
        self.board.assign(task, AUTHOR)

        self.assertEqual("active_pr", self.board.guard_reason(task))


class ReviewGateTests(BoardTest):
    """`review` is the agent reviewer's queue, and separate from the human's."""

    def test_request_review_does_not_block_the_card(self):
        """Review is the agents' queue: the dispatcher claims it right away, so
        the card is `review` or already `running`. Either way it is not
        `blocked`, which is the status a human has to clear."""
        task = self.card()
        self.board.request_review(task, "DD ready")

        self.assertIn(self.board.status(task), {"review", "running"})

    def test_a_reviewed_card_is_not_a_human_gate(self):
        """The first live run put the human gate on `review` and the author
        reviewed its own work for two rounds before anyone noticed. A gate the
        dispatcher can clear is not a gate."""
        task = self.card()
        self.board.request_review(task, "DD ready")

        self.assertNotEqual("blocked", self.board.status(task))

    def test_reviewer_flag_reassigns_the_card(self):
        """Without this the dispatcher hands the card back to its author and
        the agent reviews its own work."""
        task = self.card()
        self.board.request_review(task, "DD ready", reviewer=REVIEWER)

        self.assertEqual(REVIEWER, self.board.show(task)["task"]["assignee"])

    def test_review_summary_survives(self):
        task = self.card()
        self.board.request_review(task, "DD ready: 3 decisions, scope M")

        self.assertIn("3 decisions", self.board.show(task)["latest_summary"])


class CompletionTests(BoardTest):
    """Kanban refuses an evidence-free completion, which puts the handoff
    contract below the prompt layer where a skill rewrite cannot reach it."""

    def test_completion_without_evidence_is_refused(self):
        task = self.card()
        result = self.board.complete(task, check=False)

        self.assertNotEqual(0, result.returncode)
        self.assertNotEqual("done", self.board.status(task))

    def test_completion_with_a_summary_succeeds(self):
        task = self.card()
        self.board.complete(task, summary="Wrote the DD. Assumptions: none.")

        self.assertEqual("done", self.board.status(task))


class DependencyTests(BoardTest):
    """The card graph is what makes a workflow an ordering rather than a pile."""

    def test_child_waits_for_its_parent(self):
        parent = self.card("parent")
        child = self.card("child", parent=parent)

        self.assertEqual("todo", self.board.status(child))

    def test_child_is_promoted_when_the_parent_completes(self):
        parent = self.card("parent")
        child = self.card("child", parent=parent)
        self.board.complete(parent, summary="done")

        self.board.dispatch_plan()
        self.assertEqual("ready", self.board.status(child))

    def test_a_blocked_parent_holds_its_child(self):
        """This is what makes a human gate stop a feature rather than one card."""
        parent = self.card("parent")
        child = self.card("child", parent=parent)
        self.board.block(parent, "review-required: waiting on you")

        self.assertFalse(self.board.would_spawn(child))
        self.assertEqual("todo", self.board.status(child))


if __name__ == "__main__":
    unittest.main(verbosity=2)
