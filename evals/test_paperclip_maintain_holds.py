"""Exercise human holds without native services or GitHub traffic."""

import copy
import json
import unittest
from unittest.mock import patch

from bootstrap import paperclip_maintain as maintain
from bootstrap.paperclip_client import ProtocolError
from evals import test_paperclip_maintain_workflow as fakes


class HoldTest(unittest.TestCase):
    workflow = fakes.WorkflowTest.workflow
    actionable = fakes.WorkflowTest.actionable
    start_writer = fakes.WorkflowTest.start_writer
    prepared = fakes.WorkflowTest.prepared

    def setUp(self):
        self.native, self.github, self.git = fakes.Native(), fakes.GitHub(), fakes.Git()
        self.git.github = self.github

    def start_expert(self):
        self.actionable()
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-1", "expert", "run-expert")

    def start_reviewer(self):
        self.start_writer()
        self.prepared()
        self.native.finish()
        self.native.actor("tick-3", "routine", "run-tick-3")
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-3", "reviewer", "run-reviewer")

    def block(self):
        return self.workflow().record_result(
            {"outcome": "block", "reason": "Need a decision", "unblock": "Choose the safe option"})

    def checker(self, name="tick-next"):
        self.native.runs[self.native.run]["status"] = "succeeded"
        self.native.actor(name, "routine", "run-" + name)

    def native_questions(self, lose_first=False):
        create = self.native.create_interaction
        lost = [lose_first]

        def idempotent(task, body):
            key = body["idempotencyKey"]
            matches = [item for item in self.native.interactions.values()
                       if item["idempotencyKey"] == key]
            result = matches[0] if matches else create(task, body)
            self.native.tasks[task]["status"] = "in_review"
            if lost[0]:
                lost[0] = False
                raise ProtocolError("Lost create response")
            return result

        self.native.create_interaction = idempotent

    def test_block_result_closes_writes_without_finishing_current_step(self):
        for start in ("start_expert", "start_writer", "start_reviewer"):
            with self.subTest(start=start):
                self.setUp()
                getattr(self, start)()
                task = self.native.task
                workflow = self.workflow()
                with patch.object(self.github, "read_pr", side_effect=maintain.MaintenanceError("offline")):
                    workflow.record_result(
                        {"outcome": "block", "reason": "Need a decision", "unblock": "Choose the safe option"})
                job = self.native.slot()["active"]
                self.assertEqual(job["phase"], "held")
                self.assertIs(job["writesOpen"], False)
                self.assertNotEqual(self.native.tasks[task]["status"], "done")
                self.assertFalse(any(issue == task and body.get("status") == "done"
                                     for issue, body in self.native.updates))

    def test_checker_creates_one_human_question_before_closing_stale_pr(self):
        self.start_expert()
        self.block()
        self.checker()
        self.native_questions()
        self.github.pr["state"] = "closed"
        self.github.open_prs = lambda author: []
        self.workflow().poll()
        self.assertEqual(len(self.native.interactions), 1)
        question = next(iter(self.native.interactions.values()))
        self.assertEqual(question["issueId"], "step-1")
        self.assertEqual(question["kind"], "ask_user_questions")
        self.assertEqual(question["effectiveResolverPolicy"], "human_only")
        requested = {key: value for key, value in question.items() if key != "effectiveResolverPolicy"}
        self.assertIn("human_only", json.dumps(requested))
        self.assertIn("wake_assignee", json.dumps(question))
        payload = json.dumps(question["payload"])
        self.assertIn("Need a decision", payload)
        self.assertIn("Choose the safe option", payload)
        self.assertTrue(question["idempotencyKey"])
        self.assertEqual(self.native.slot()["active"]["phase"], "held")
        self.assertNotEqual(self.native.tasks["step-1"]["status"], "done")

    def test_unknown_question_creation_is_reconciled_without_duplicate(self):
        self.start_expert()
        self.block()
        self.checker()
        self.native_questions(lose_first=True)
        self.workflow().poll()
        self.checker("tick-again")
        self.workflow().poll()
        self.assertEqual(len(self.native.interactions), 1)
        self.assertEqual(self.native.slot()["active"]["phase"], "held")
        self.assertFalse(self.native.slot()["active"]["writesOpen"])

    def test_pending_question_on_own_task_prevents_checkout_and_external_io(self):
        self.start_expert()
        self.native.create_interaction("step-1", {
            "kind": "ask_user_questions", "idempotencyKey": "human-question",
            "payload": {}, "resolverPolicy": "human_only"})
        with patch.object(self.native, "checkout_issue", wraps=self.native.checkout_issue) as checkout, \
                patch.object(self.github, "identity", wraps=self.github.identity) as identity:
            with self.assertRaises(maintain.MaintenanceError):
                self.workflow()
        checkout.assert_not_called()
        identity.assert_not_called()
        self.assertEqual(self.git.calls, [])

    def test_pending_question_on_done_ancestor_prevents_child_checkout(self):
        self.start_writer()
        self.assertEqual(self.native.tasks["step-1"]["status"], "done")
        self.native.create_interaction("step-1", {
            "kind": "ask_user_questions", "idempotencyKey": "ancestor-question",
            "payload": {}, "resolverPolicy": "human_only"})
        with patch.object(self.native, "checkout_issue", wraps=self.native.checkout_issue) as checkout, \
                patch.object(self.github, "identity", wraps=self.github.identity) as identity:
            with self.assertRaises(maintain.MaintenanceError):
                self.workflow()
        checkout.assert_not_called()
        identity.assert_not_called()
        self.assertEqual(self.git.calls, [])

    def test_done_ancestor_without_question_does_not_hold_child(self):
        self.start_writer()
        self.assertEqual(self.native.tasks["step-1"]["status"], "done")
        self.assertEqual(self.workflow().preflight()["step"], "writer")

    def test_agent_answer_cannot_release_a_human_hold(self):
        self.start_expert()
        self.block()
        self.checker()
        self.native_questions()
        self.workflow().poll()
        question = next(iter(self.native.interactions.values()))
        question.update(status="answered", resolvedByUserId=None, resolvedByAgentId="expert",
                        result={"version": 1, "answers": [
                             {"questionId": "unblock", "optionIds": [], "otherText": "Proceed"}]})
        self.checker("tick-answered")
        self.workflow().poll()
        self.assertEqual(self.native.slot()["active"]["phase"], "held")
        self.assertFalse(self.native.slot()["active"]["writesOpen"])
        self.assertEqual(len(self.native.created), 1)

    def answer(self):
        self.start_expert()
        self.block()
        self.checker()
        self.native_questions()
        self.workflow().poll()
        question = next(iter(self.native.interactions.values()))
        question.update(status="answered", resolvedByUserId="human", resolvedByAgentId=None,
                        result={"version": 1, "answers": [
                            {"questionId": "unblock", "optionIds": [], "otherText": "Use the scoped repair"}]})

    def test_native_human_answer_releases_only_for_a_fresh_job(self):
        self.answer()
        self.checker("tick-answered")
        self.workflow().poll()
        self.assertIsNone(self.native.slot()["active"])
        self.assertEqual(self.native.slot()["handled"], [])
        self.checker("tick-fresh")
        self.workflow().poll()
        self.assertEqual(len(self.native.created), 2)
        self.assertEqual(self.native.created[-1]["assigneeAgentId"], "expert")

    def test_human_answer_does_not_release_a_live_execution(self):
        self.answer()
        self.native.runs["run-expert"]["status"] = "running"
        self.checker("tick-answered")
        self.workflow().poll()
        self.assertEqual(self.native.slot()["active"]["phase"], "held")

    def test_another_pending_question_still_blocks_settlement(self):
        self.answer()
        self.native.create_interaction("step-1", {
            "kind": "request_confirmation", "idempotencyKey": "another-human-gate", "payload": {}})
        self.checker("tick-answered")
        self.workflow().poll()
        self.assertEqual(self.native.slot()["active"]["phase"], "held")

    def test_clarification_is_one_round_with_the_same_writer(self):
        self.start_writer(material=False)
        self.workflow().record_result({"outcome": "clarify", "question": "Which requirement?"})
        self.native.finish()
        self.native.actor("tick-3", "routine", "run-tick-3")
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-3", "expert", "run-answer")
        self.workflow().record_result({"outcome": "answer", "instructions": "Keep the approved scope"})
        self.native.finish()
        self.native.actor("tick-4", "routine", "run-tick-4")
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-4", "builder", "run-writer-again")
        with self.assertRaises(maintain.MaintenanceError):
            self.workflow().record_result({"outcome": "clarify", "question": "One more?"})
        self.assertEqual(self.native.slot()["active"]["clarifications"], 1)
        self.assertEqual(self.native.slot()["active"]["writer"], "builder")
        self.assertEqual(self.native.created[-1]["assigneeAgentId"], "builder")

    def reject_review(self):
        return self.workflow().record_result({
            "outcome": "changes", "workspace": "/virtual/reviewer",
            "instructions": "Repair the incorrect branch",
            "checks": [{"command": "inspect", "outcome": "passed"}]})

    def test_second_review_rejection_is_a_human_hold_not_another_repair(self):
        self.start_reviewer()
        self.reject_review()
        self.native.finish()
        self.native.actor("tick-4", "routine", "run-tick-4")
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-4", "builder", "run-repair")
        self.prepared()
        self.native.finish()
        self.native.actor("tick-5", "routine", "run-tick-5")
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-5", "reviewer", "run-review-again")
        self.reject_review()
        self.assertNotEqual(self.native.tasks["step-5"]["status"], "done")
        self.checker("tick-6")
        self.workflow().poll()
        self.assertEqual(len(self.native.created), 5)
        self.assertEqual(self.native.slot()["active"]["phase"], "held")
        self.assertEqual(len(self.native.interactions), 1)

    def test_unrelated_clean_pr_does_not_start_model_while_first_is_held(self):
        self.start_expert()
        self.block()
        self.checker()
        self.native_questions()
        second = copy.deepcopy(self.github.pr)
        second["number"] = 2
        self.github.open_prs = lambda author: [copy.deepcopy(self.github.pr), copy.deepcopy(second)]
        original_snapshot = self.github.snapshot

        def snapshot(repo, number, confirmed_receipt_ids=()):
            if number == 1:
                return original_snapshot(repo, number, confirmed_receipt_ids)
            return {"pr": copy.deepcopy(second), "feedback": [], "items": {}, "failed": [],
                    "pending": False, "conflict": False}

        self.github.snapshot = snapshot
        self.workflow().poll()
        self.assertEqual(len(self.native.created), 1)
        self.assertEqual(self.native.slot()["active"]["phase"], "held")


if __name__ == "__main__":
    unittest.main()
