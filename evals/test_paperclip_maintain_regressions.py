"""Regressions for maintenance publication and durable native recovery."""

import copy
import json
import unittest

from bootstrap import paperclip_maintain as maintain
from bootstrap.paperclip_client import ProtocolError
from evals import test_paperclip_maintain_workflow as reference


class MaintenanceRegressionTest(unittest.TestCase):
    workflow = reference.WorkflowTest.workflow
    actionable = reference.WorkflowTest.actionable
    next_tick = reference.WorkflowTest.next_tick
    start_writer = reference.WorkflowTest.start_writer
    prepared = reference.WorkflowTest.prepared

    def setUp(self):
        self.native, self.github, self.git = reference.Native(), reference.GitHub(), reference.Git()
        self.git.github = self.github

    def save_slot(self, state):
        key = maintain.slot_key("company", 13, 1)
        doc = self.native.get_document("ledger", key)
        self.native.update_document("ledger", key, json.dumps(state), doc["latestRevisionId"])

    def test_reply_readback_spawn_failure_never_authorizes_duplicate_post(self):
        self.start_writer(material=False)
        self.prepared(material=False)
        self.workflow().apply_effect("push")
        post = self.github.post_reply
        find = self.github.find_reply
        posts = []

        def post_once(payload):
            posts.append(payload)
            return post(payload)

        def failed_readback(payload, comment_id=None):
            if posts:
                raise maintain.DispatchNotStarted("readback did not start")
            return find(payload, comment_id)

        self.github.post_reply = post_once
        self.github.find_reply = failed_readback
        with self.assertRaises(maintain.MaintenanceError):
            self.workflow().apply_effect("reply", "comment-v1", "Fixed")
        reply = next(effect for effect in self.native.slot()["effects"].values()
                     if not effect.get("receipt", {}).get("commit"))
        self.github.replies.clear()
        self.github.find_reply = find
        self.native.finish()
        self.native.tasks["step-2"]["status"] = "in_progress"
        self.native.actor("step-2", "builder", "run-builder-retry")
        try:
            self.workflow().apply_effect("reply", "comment-v1", "Fixed")
        except maintain.MaintenanceError:
            pass
        self.assertNotEqual(reply["status"], "not_sent")
        self.assertEqual(len(posts), 1)

    def test_direct_reply_and_resolve_before_fix_are_not_published(self):
        self.start_writer(material=False)
        self.prepared(material=False)
        for operation in ("reply", "resolve"):
            with self.subTest(operation=operation):
                with self.assertRaises(maintain.MaintenanceError):
                    self.workflow().apply_effect(operation, "comment-v1", "Fixed")
        self.assertEqual(self.github.replies, [])
        self.assertFalse(self.github.resolved)
        self.assertNotIn("push", self.git.calls)

    def test_existing_fix_head_moving_during_confirmation_never_pushes(self):
        self.start_writer(material=False)
        self.workflow().record_result({
            "outcome": "existing", "workspace": "/virtual/writer", "material": False,
            "fixed": ["comment-v1"], "checks": [{"command": "verify", "outcome": "passed"}]})
        read = self.github.read_pr
        calls = []

        def moving_head(repo, number):
            calls.append(1)
            pr = read(repo, number)
            if len(calls) > 1:
                pr["head"]["sha"] = "other-head"
            return pr

        self.github.read_pr = moving_head
        with self.assertRaises(maintain.MaintenanceError):
            self.workflow().apply_effect("push")
        self.assertNotIn("push", self.git.calls)

    def test_forged_plan_slot_key_cannot_authorize_execution(self):
        self.actionable()
        self.workflow().poll()
        plan_doc = self.native.get_document("step-1", "plan")
        plan = json.loads(plan_doc["body"])
        fake_key = maintain.slot_key("company", 99, 1)
        state = self.native.slot()
        plan["slotKey"] = fake_key
        self.native.create_document("ledger", fake_key, json.dumps(state))
        self.native.update_document("step-1", "plan", json.dumps(plan), plan_doc["latestRevisionId"])
        self.native.finish()
        self.native.actor("step-1", "expert", "run-expert")
        with self.assertRaises(maintain.MaintenanceError):
            self.workflow().preflight()
        self.assertEqual(len(self.native.created), 1)

    def test_snapshot_repository_id_mismatch_cannot_dispatch(self):
        self.actionable()
        snapshot = self.github.snapshot

        def replaced_repo(*args, **kwargs):
            evidence = snapshot(*args, **kwargs)
            evidence["pr"]["base"]["repo"]["id"] = 99
            return evidence

        self.github.snapshot = replaced_repo
        result = self.workflow().poll()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(self.native.created, [])

    def test_lost_new_job_phase_ack_recovers_expert_on_next_poll(self):
        self.actionable()
        update = self.native.update_document
        lost = []

        def lose_ack(task, key, body, revision):
            result = update(task, key, body, revision)
            value = json.loads(body)
            if task == "ledger" and (value.get("active") or {}).get("phase") == "expert" and not lost:
                lost.append(True)
                raise ProtocolError("lost committed job acknowledgement")
            return result

        self.native.update_document = lose_ack
        self.workflow().poll()
        self.next_tick()
        self.workflow().poll()
        self.assertTrue(lost)
        self.assertEqual(len(self.native.created), 1)
        self.assertEqual(self.native.created[0]["assigneeAgentId"], "expert")

    def test_lost_writer_transition_ack_recovers_writer_on_next_poll(self):
        self.actionable()
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-1", "expert", "run-expert")
        self.workflow().record_result(
            {"outcome": "plan", "writer": "builder", "instructions": "Fix", "material": False})
        self.native.finish()
        self.native.actor("tick-2", "routine", "run-tick-2")
        update = self.native.update_document
        lost = []

        def lose_ack(task, key, body, revision):
            result = update(task, key, body, revision)
            value = json.loads(body)
            active = value.get("active") or {}
            if task == "ledger" and active.get("phase") == "writer" and not lost:
                lost.append(True)
                raise ProtocolError("lost committed transition acknowledgement")
            return result

        self.native.update_document = lose_ack
        self.workflow().poll()
        self.native.finish()
        self.native.actor("tick-3", "routine", "run-tick-3")
        self.workflow().poll()
        self.assertTrue(lost)
        self.assertEqual(len(self.native.created), 2)
        self.assertEqual(self.native.created[-1]["assigneeAgentId"], "builder")

    def test_lost_prepared_result_ack_recovers_original_revision_and_attachment(self):
        self.start_writer()
        create = self.native.create_document
        saved = []

        def lose_ack(task, key, body):
            result = create(task, key, body)
            if task == "step-2" and key.startswith("result-") and not saved:
                saved.append(copy.deepcopy(result))
                raise ProtocolError("lost committed result acknowledgement")
            return result

        self.native.create_document = lose_ack
        try:
            self.prepared()
        except ProtocolError:
            self.prepared()
        self.assertEqual(len(saved), 1)
        ref = self.native.slot()["active"]["preparedRef"]
        self.assertEqual(ref["revisionId"], saved[0]["latestRevisionId"])
        self.assertEqual(len(self.native.attachments), 1)

    def test_new_preparation_ref_with_same_objects_requires_new_approval(self):
        self.start_writer()
        self.prepared()
        self.native.finish()
        self.native.actor("tick-3", "routine", "run-tick-3")
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-3", "reviewer", "run-reviewer")
        self.workflow().record_result({
            "outcome": "approve", "workspace": "/virtual/reviewer",
            "checks": [{"command": "inspect", "outcome": "passed"}]})
        self.native.finish()
        self.native.actor("tick-4", "routine", "run-tick-4")
        self.workflow().poll()
        state = self.native.slot()
        ref = state["active"]["preparedRef"]
        original = self.native.get_document(ref["taskId"], ref["key"])
        self.native.actor("step-2", "builder", "run-builder")
        replacement = self.native.create_document("step-2", "replacement-preparation", original["body"])
        self.native.finish()
        self.native.actor("tick-4", "routine", "run-tick-4")
        state["active"]["preparedRef"] = {
            "taskId": "step-2", "key": replacement["key"], "revisionId": replacement["latestRevisionId"]}
        self.save_slot(state)
        self.native.finish()
        self.native.actor("step-4", "builder", "run-publisher")
        with self.assertRaises(maintain.MaintenanceError):
            self.workflow().apply_effect("push")
        self.assertNotIn("push", self.git.calls)

    def test_review_does_not_import_bytes_other_than_verified_attachment(self):
        self.start_writer()
        self.prepared()
        self.native.finish()
        self.native.actor("tick-3", "routine", "run-tick-3")
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-3", "reviewer", "run-reviewer")
        download = self.native.download_attachment
        downloads = []
        imported = []

        def changing_download(identifier):
            downloads.append(identifier)
            return download(identifier) if len(downloads) <= 2 else b"unchecked bytes"

        def accept_bundle(workspace, data, prepared):
            imported.append(data)
            return dict(prepared, workspace=workspace)

        self.native.download_attachment = changing_download
        self.git.verify_bundle = accept_bundle
        try:
            self.workflow().prepare_review("/virtual/reviewer")
        except maintain.MaintenanceError:
            pass
        self.assertNotIn(b"unchecked bytes", imported)

    def test_closing_slot_waits_for_todo_task_without_live_run(self):
        self.start_writer(material=False)
        self.prepared(material=False)
        self.workflow().finish()
        self.native.finish()
        self.native.tasks["step-2"]["status"] = "todo"
        self.native.actor("tick-3", "routine", "run-tick-3")
        self.workflow().poll()
        self.assertIsNotNone(self.native.slot()["active"])
