"""Regressions for interrupted maintenance and publication boundary checks."""

import copy
import json
import unittest

from bootstrap import paperclip_maintain as maintain
from bootstrap.paperclip_client import ProtocolError
from evals import test_paperclip_maintain_holds as holds
from evals import test_paperclip_maintain_workflow as reference


class RecoveryTest(unittest.TestCase):
    workflow = reference.WorkflowTest.workflow
    actionable = reference.WorkflowTest.actionable
    start_writer = reference.WorkflowTest.start_writer
    prepared = reference.WorkflowTest.prepared
    start_expert = holds.HoldTest.start_expert
    start_reviewer = holds.HoldTest.start_reviewer
    block = holds.HoldTest.block
    checker = holds.HoldTest.checker
    native_questions = holds.HoldTest.native_questions
    answer = holds.HoldTest.answer

    def setUp(self):
        self.native, self.github, self.git = reference.Native(), reference.GitHub(), reference.Git()
        self.git.github = self.github

    def lose_document_ack(self, prefix):
        create = self.native.create_document
        saved = []

        def lose(task, key, body):
            document = create(task, key, body)
            if task == "step-2" and key.startswith(prefix) and not saved:
                saved.append(copy.deepcopy(document))
                raise ProtocolError("Lost committed document acknowledgement")
            return document

        self.native.create_document = lose
        return saved

    def test_drift_after_writer_checkout_cancels_stale_execution_and_releases_slot(self):
        self.start_writer(material=False)
        self.workflow().preflight()
        self.github.pr["base"]["sha"] = "new-base"
        self.checker()
        self.workflow().poll()
        self.assertEqual(self.native.tasks["step-2"]["status"], "cancelled")
        self.assertIsNone(self.native.slot()["active"])
        self.assertEqual(len(self.native.created), 2)

    def test_lost_input_ack_reuses_the_claimed_execution_document(self):
        self.actionable()
        create = self.native.create_document
        captures = []

        def lose(task, key, body):
            document = create(task, key, body)
            if key.startswith("input-"):
                captures.append((task, key, document["latestRevisionId"]))
                if len(captures) == 1:
                    raise ProtocolError("Lost input acknowledgement")
            return document

        self.native.create_document = lose
        self.workflow().poll()
        self.checker()
        self.workflow().poll()
        self.assertEqual(len(captures), 1)
        ref = self.native.slot()["active"]["inputRef"]
        self.assertEqual((ref["taskId"], ref["key"], ref["revisionId"]), captures[0])
        self.assertEqual(len(self.native.created), 1)

    def test_global_github_read_failure_settles_tick_without_starting_models(self):
        self.github.open_prs = lambda author: (_ for _ in ()).throw(
            maintain.MaintenanceError("Managed GitHub read failed"))
        result = self.workflow().poll()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(self.native.tasks["tick"]["status"], "done")
        self.assertEqual(self.native.created, [])
        self.assertTrue(result["errors"])

    def test_obsolete_capture_does_not_start_expert_when_ci_turns_green(self):
        self.github.evidence.update(failed=["build"], pending=False)
        create = self.native.create_document
        lost = []

        def lose(task, key, body):
            result = create(task, key, body)
            if key.startswith("input-") and not lost:
                lost.append(True)
                raise ProtocolError("Lost captured CI input")
            return result

        self.native.create_document = lose
        self.workflow().poll()
        self.github.evidence["failed"] = []
        self.checker()
        self.workflow().poll()
        self.assertEqual(self.native.created, [])
        self.assertIsNone(self.native.slot()["active"])

    def test_stale_unknown_task_creation_is_recovered_and_cancelled_not_recreated(self):
        self.actionable()
        self.native.lose_create = True
        self.workflow().poll()
        self.github.pr["head"]["sha"] = "new-head"
        self.checker()
        self.workflow().poll()
        self.assertEqual(len(self.native.created), 1)
        self.assertEqual(self.native.tasks["step-1"]["status"], "cancelled")
        self.assertIsNone(self.native.slot()["active"])

    def test_human_decision_survives_stale_capture_before_expert_dispatch(self):
        self.answer()
        self.checker("tick-answered")
        self.workflow().poll()
        create = self.native.create_document
        lost = []

        def lose(task, key, body):
            result = create(task, key, body)
            if key.startswith("input-") and not lost:
                lost.append(True)
                raise ProtocolError("Lost new input")
            return result

        self.native.create_document = lose
        self.checker("tick-capture")
        self.workflow().poll()
        self.github.pr["base"]["sha"] = "advanced-base"
        self.checker("tick-obsolete")
        self.workflow().poll()
        self.assertIsNone(self.native.slot()["active"])
        self.checker("tick-fresh")
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-2", "expert", "run-fresh-expert")
        self.assertIn("Use the scoped repair", json.dumps(self.workflow().preflight()["input"]["humanDecision"]))

    def test_failed_material_publisher_settles_durable_exact_completion(self):
        self.start_reviewer()
        self.workflow().record_result({
            "outcome": "approve", "workspace": "/virtual/reviewer",
            "checks": [{"command": "inspect", "outcome": "passed"}]})
        self.native.finish()
        self.native.actor("tick-4", "routine", "run-tick-4")
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-4", "builder", "run-publisher")
        create = self.native.create_document

        def lose(task, key, body):
            result = create(task, key, body)
            if key.startswith("completion-"):
                raise ProtocolError("Lost completion")
            return result

        self.native.create_document = lose
        with self.assertRaises(ProtocolError):
            self.workflow().finish()
        self.native.runs["run-publisher"]["status"] = "failed"
        self.native.actor("tick-5", "routine", "run-tick-5")
        self.workflow().poll()
        self.assertIsNone(self.native.slot()["active"])
        self.assertEqual(self.git.calls.count("push"), 1)
        self.assertEqual(len(self.github.replies), 1)

    def test_deferred_reply_with_proved_non_delivery_can_settle(self):
        self.start_writer(material=False)
        self.prepared(material=False)
        workflow = self.workflow()
        payload = workflow._payload
        calls = []

        def changed_after_claim(operation, *args):
            if operation == "reply":
                calls.append(True)
                if len(calls) == 2:
                    raise maintain.FeedbackChanged("Feedback edited after admission")
            return payload(operation, *args)

        workflow._payload = changed_after_claim
        workflow.finish()
        self.checker()
        self.workflow().poll()
        self.assertIsNone(self.native.slot()["active"])
        self.assertNotIn("comment-v1", self.native.slot()["handled"])
        self.assertEqual(self.github.replies, [])

    def test_human_decision_survives_cancelled_unactivated_expert(self):
        self.answer()
        self.checker("tick-answered")
        self.workflow().poll()
        self.native.lose_create = True
        self.checker("tick-create")
        self.workflow().poll()
        self.github.pr["base"]["sha"] = "new-base"
        self.checker("tick-obsolete")
        self.workflow().poll()
        self.assertEqual(self.native.tasks["step-2"]["status"], "cancelled")
        self.checker("tick-fresh")
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-3", "expert", "run-fresh-expert")
        self.assertIn("Use the scoped repair", json.dumps(self.workflow().preflight()["input"]["humanDecision"]))

    def test_lost_prepared_ack_recovers_original_result_without_another_writer(self):
        self.start_writer()
        saved = self.lose_document_ack("result-")
        try:
            self.prepared()
        except ProtocolError:
            pass
        self.assertEqual(len(saved), 1)
        self.assertEqual(self.native.tasks["step-2"]["status"], "in_progress")
        self.checker()
        result = self.workflow().poll()
        self.assertEqual(result["status"], "complete")
        job = self.native.slot()["active"]
        self.assertEqual(job["preparedRef"]["revisionId"], saved[0]["latestRevisionId"])
        self.assertEqual(job["phase"], "review")
        self.assertEqual([item["assigneeAgentId"] for item in self.native.created],
                         ["expert", "builder", "reviewer"])

    def test_lost_block_ack_recovers_hold_and_original_blocker(self):
        self.start_writer(material=False)
        saved = self.lose_document_ack("result-")
        try:
            self.block()
        except ProtocolError:
            pass
        self.assertEqual(len(saved), 1)
        self.assertEqual(self.native.tasks["step-2"]["status"], "in_progress")
        self.checker()
        self.native_questions()
        result = self.workflow().poll()
        self.assertEqual(result["status"], "complete")
        job = self.native.slot()["active"]
        self.assertEqual(job["phase"], "held")
        self.assertFalse(job["writesOpen"])
        self.assertEqual(job["blockerRef"]["revisionId"], saved[0]["latestRevisionId"])
        self.assertEqual(len(self.native.created), 2)

    def test_lost_completion_ack_settles_without_republishing(self):
        self.start_writer(material=False)
        self.prepared(material=False)
        saved = self.lose_document_ack("completion-")
        resolve = self.github.resolve_thread
        resolutions = []

        def record_resolution(payload):
            resolutions.append(payload)
            return resolve(payload)

        self.github.resolve_thread = record_resolution
        try:
            self.workflow().finish()
        except ProtocolError:
            pass
        self.assertEqual(len(saved), 1)
        self.assertEqual(self.native.tasks["step-2"]["status"], "in_progress")
        self.assertEqual(self.git.calls.count("push"), 1)
        self.assertEqual(len(self.github.replies), 1)
        self.assertTrue(self.github.resolved)
        self.checker()
        result = self.workflow().poll()
        self.assertEqual(result["status"], "complete")
        self.assertIsNone(self.native.slot()["active"])
        self.assertIn("comment-v1", self.native.slot()["handled"])
        self.assertEqual(self.git.calls.count("push"), 1)
        self.assertEqual(len(self.github.replies), 1)
        self.assertEqual(len(resolutions), 1)
        self.assertEqual(len(self.native.created), 2)

    def test_lost_final_issue_patch_after_close_settles_without_republishing(self):
        self.start_writer(material=False)
        self.prepared(material=False)
        update = self.native.update_issue
        lost = []
        resolve = self.github.resolve_thread
        resolutions = []

        def record_resolution(payload):
            resolutions.append(payload)
            return resolve(payload)

        self.github.resolve_thread = record_resolution

        def lose(task, body):
            if task == "step-2" and body.get("status") == "done" and not lost:
                lost.append(True)
                raise ProtocolError("Lost final issue patch")
            return update(task, body)

        self.native.update_issue = lose
        try:
            self.workflow().finish()
        except ProtocolError:
            pass
        self.assertEqual(lost, [True])
        self.assertEqual(self.native.tasks["step-2"]["status"], "in_progress")
        self.checker()
        result = self.workflow().poll()
        self.assertEqual(result["status"], "complete")
        self.assertIsNone(self.native.slot()["active"])
        self.assertIn("comment-v1", self.native.slot()["handled"])
        self.assertEqual(self.git.calls.count("push"), 1)
        self.assertEqual(len(self.github.replies), 1)
        self.assertEqual(len(resolutions), 1)
        self.assertEqual(len(self.native.created), 2)

    def test_lost_committed_final_issue_patch_does_not_republish(self):
        self.start_writer(material=False)
        self.prepared(material=False)
        update = self.native.update_issue
        lost = []

        def lose(task, body):
            result = update(task, body)
            if task == "step-2" and body.get("status") == "done" and not lost:
                lost.append(True)
                raise ProtocolError("Lost committed final issue acknowledgement")
            return result

        self.native.update_issue = lose
        try:
            self.workflow().finish()
        except ProtocolError:
            pass
        self.assertEqual(lost, [True])
        self.checker()
        self.assertEqual(self.workflow().poll()["status"], "complete")
        self.assertIsNone(self.native.slot()["active"])
        self.assertEqual(self.git.calls.count("push"), 1)
        self.assertEqual(len(self.github.replies), 1)
        self.assertTrue(self.github.resolved)
        self.assertEqual(len(self.native.created), 2)

    def test_approval_refuses_wrong_head_in_review_workspace(self):
        self.start_reviewer()
        workflow = self.workflow()
        workflow.prepare_review("/virtual/reviewer")
        inspect = self.git.inspect

        def wrong_head(workspace, pinned, commit=None):
            if workspace == "/virtual/reviewer":
                raise maintain.MaintenanceError("Review workspace has wrong HEAD")
            return inspect(workspace, pinned, commit)

        self.git.inspect = wrong_head
        with self.assertRaises(maintain.MaintenanceError):
            workflow.record_result({"outcome": "approve", "workspace": "/virtual/reviewer",
                                    "checks": [{"command": "inspect", "outcome": "passed"}]})

    def test_retargeted_snapshot_cannot_publish_reply_or_resolve(self):
        self.start_writer(material=False)
        self.prepared(material=False)
        workflow = self.workflow()
        workflow.apply_effect("push")
        snapshot = self.github.snapshot

        def retargeted(*args, **kwargs):
            evidence = snapshot(*args, **kwargs)
            evidence["pr"]["base"]["ref"] = "other-base"
            return evidence

        self.github.snapshot = retargeted
        for operation in ("reply", "resolve"):
            with self.subTest(operation=operation):
                with self.assertRaises(maintain.MaintenanceError):
                    workflow.apply_effect(operation, "comment-v1", "Fixed")
        self.assertEqual(self.github.replies, [])
        self.assertFalse(self.github.resolved)

    def test_fresh_expert_receives_human_decision_and_native_question_reference(self):
        self.answer()
        question_id = next(iter(self.native.interactions))
        self.checker("tick-answered")
        self.workflow().poll()
        self.checker("tick-fresh")
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-2", "expert", "run-fresh-expert")
        decision = self.workflow().preflight()["input"]["humanDecision"]
        self.assertIn("Use the scoped repair", json.dumps(decision))
        self.assertIn(question_id, json.dumps(decision))


if __name__ == "__main__":
    unittest.main()
