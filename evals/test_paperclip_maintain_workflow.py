"""Exercise native maintenance jobs with real protocol boundaries and no services."""

import copy
import hashlib
import json
import unittest

from bootstrap import paperclip_maintain as maintain
from bootstrap.paperclip_client import DocumentConflict, DocumentNotFound, ProtocolError


class Native:
    def __init__(self):
        self.config = {
            "companyId": "company", "projectId": "project", "routineAgentId": "routine",
            "expertAgentId": "expert", "builderAgentId": "builder", "sreAgentId": "sre",
            "reviewerAgentId": "reviewer", "ledgerIssueId": "ledger",
            "authorLogin": "author", "authorAccountId": 7,
            "publisherLogin": "publisher", "publisherAccountId": 9, "repositories": [],
        }
        self.tasks = {"ledger": {"id": "ledger", "companyId": "company", "projectId": "project",
                                 "assigneeAgentId": None, "assigneeUserId": None}}
        self.documents, self.revisions, self.runs = {}, {}, {}
        self.created, self.updates = [], []
        self.lose_create = False
        self.hide_tasks = False
        self.attachments = {}
        self.interactions = {}
        self.serial = 0
        self.actor("tick", "routine", "run-tick")

    def actor(self, task, agent, run):
        self.task, self.agent, self.run = task, agent, run
        self.tasks.setdefault(task, {"id": task, "companyId": "company", "projectId": "project",
                                     "assigneeAgentId": agent, "assigneeUserId": None,
                                     "status": "in_progress", "parentId": None})
        self.runs[run] = {"id": run, "agentId": agent, "companyId": "company",
                          "issueId": task, "status": "running"}

    def finish(self):
        self.runs[self.run]["status"] = "succeeded"
        self.tasks[self.task]["status"] = "done"

    def resolve_context(self, company, agent, task=None):
        if (company, agent) != ("company", self.agent) or task not in (None, self.task):
            raise ProtocolError("Wrong actor")
        return {"companyId": company, "agentId": agent, "taskId": self.task, "runId": self.run}

    def get_me(self):
        return {"id": self.agent, "companyId": "company",
                "adapterType": "process" if self.agent == "routine" else "opencode_local"}

    def get_issue(self, task):
        return copy.deepcopy(self.tasks[task])

    def get_run(self, run):
        return copy.deepcopy(self.runs[run])

    def checkout_issue(self, task, agent):
        issue = self.tasks[task]
        if issue["status"] not in ("todo", "in_progress") or issue["assigneeAgentId"] != agent:
            raise ProtocolError("Checkout held")
        lock = issue.get("checkoutRunId")
        if lock and lock != self.run and self.runs[lock]["status"] in ("queued", "running"):
            raise ProtocolError("Another execution owns checkout")
        issue.update(checkoutRunId=self.run, executionRunId=self.run, status="in_progress")
        return self.get_issue(task)

    def create_interaction(self, task, body):
        identifier = "question-" + str(len(self.interactions) + 1)
        result = dict(body, id=identifier, issueId=task, companyId="company", status="pending",
                      createdByAgentId=self.agent, sourceRunId=self.run, effectiveResolverPolicy="human_only")
        self.interactions[identifier] = result
        return copy.deepcopy(result)

    def list_interactions(self, task):
        return [copy.deepcopy(value) for value in self.interactions.values() if value["issueId"] == task]

    def live_runs(self, task):
        return [{"id": run["id"], "agentId": run["agentId"], "status": run["status"]}
                for run in self.runs.values()
                if run["issueId"] == task and run["status"] in ("queued", "running")]

    def get_document(self, task, key):
        if (task, key) not in self.documents:
            raise DocumentNotFound("Missing")
        return copy.deepcopy(self.documents[task, key])

    def list_documents(self, task):
        return [copy.deepcopy(value) for (issue, key), value in self.documents.items() if issue == task]

    def get_revision(self, task, key, revision):
        result = copy.deepcopy(self.revisions[revision])
        result["latestRevisionId"] = self.documents[task, key]["latestRevisionId"]
        result["isLatest"] = result["latestRevisionId"] == revision
        return result

    def _write(self, task, key, body):
        self.serial += 1
        revision = "revision-" + str(self.serial)
        document = {"key": key, "body": body, "latestRevisionId": revision}
        self.documents[task, key] = document
        self.revisions[revision] = dict(
            document, id=revision, issueId=task, companyId="company",
            createdByAgentId=self.agent, createdByUserId=None, createdByRunId=self.run)
        return copy.deepcopy(document)

    def create_document(self, task, key, body):
        if (task, key) in self.documents:
            raise DocumentConflict("Exists")
        return self._write(task, key, body)

    def update_document(self, task, key, body, revision):
        if self.documents[task, key]["latestRevisionId"] != revision:
            raise DocumentConflict("Stale")
        return self._write(task, key, body)

    def create_issue(self, company, body):
        task = "step-" + str(len(self.created) + 1)
        self.created.append(copy.deepcopy(body))
        self.tasks[task] = dict(body, id=task, companyId=company, assigneeUserId=None)
        self.create_document(task, "plan", body["initialPlan"])
        if self.lose_create:
            self.lose_create = False
            raise ProtocolError("Lost response")
        return self.get_issue(task)

    def list_issues(self, company, project, after_id=None, limit=100):
        if self.hide_tasks:
            return []
        rows = [value for key, value in sorted(self.tasks.items())
                if key.startswith("step-") and (after_id is None or key > after_id)]
        return copy.deepcopy(rows[:limit])

    def update_issue(self, task, body):
        self.updates.append((task, copy.deepcopy(body)))
        self.tasks[task].update(body)
        return self.get_issue(task)

    def upload_attachment(self, company, task, filename, data):
        identifier = "attachment-" + str(len(self.attachments) + 1)
        metadata = {"id": identifier, "companyId": company, "issueId": task,
                    "createdByAgentId": self.agent, "createdByUserId": None,
                    "originatingRunId": self.run, "sha256": hashlib.sha256(data).hexdigest(),
                    "byteSize": len(data)}
        self.attachments[identifier] = (metadata, data)
        return copy.deepcopy(metadata)

    def list_attachments(self, task):
        return [copy.deepcopy(meta) for meta, data in self.attachments.values() if meta["issueId"] == task]

    def download_attachment(self, identifier):
        return self.attachments[identifier][1]

    def slot(self):
        key = maintain.slot_key("company", 13, 1)
        return json.loads(self.documents["ledger", key]["body"])


class GitHub:
    def __init__(self):
        self.pr = {
            "number": 1, "state": "open", "user": {"id": 7, "login": "author"},
            "base": {"repo": {"id": 13, "full_name": "owner/repo"}, "sha": "b", "ref": "main"},
            "head": {"repo": {"id": 13, "full_name": "owner/repo"}, "sha": "h", "ref": "feature"},
        }
        self.evidence = {"pr": self.pr, "feedback": [], "items": {}, "failed": [],
                         "pending": True, "conflict": False}
        self.replies = []
        self.resolved = False
        self.lose_reply = False
        self.reject_reply = False

    def identity(self):
        return {"id": 9, "login": "publisher"}

    def open_prs(self, author):
        return [copy.deepcopy(self.pr)]

    def read_pr(self, repo, number):
        return copy.deepcopy(self.pr)

    def snapshot(self, repo, number, confirmed_receipt_ids=()):
        return copy.deepcopy(self.evidence)

    def contains(self, repo, ancestor, descendant):
        return ancestor == descendant

    def thread(self, repo, number, identifier):
        return {"id": identifier, "isResolved": self.resolved,
                "resolvedBy": {"login": "publisher", "databaseId": 9} if self.resolved else None,
                "comments": {"nodes": [{"databaseId": 41, "id": "comment", "body": "Fix",
                                       "updatedAt": "before"}]}}

    def find_reply(self, payload, comment_id=None):
        return [copy.deepcopy(reply) for reply in self.replies if payload["marker"] in reply["body"]
                and (comment_id is None or reply["id"] == comment_id)]

    def post_reply(self, payload):
        if self.reject_reply:
            raise maintain.MaintenanceError("Response uncertain")
        reply = {"id": 100 + len(self.replies), "body": payload["body"],
                 "user": {"id": 9, "login": "publisher"}, "html_url": "https://github.com/owner/repo/reply"}
        if payload["kind"] == "thread":
            reply.update(in_reply_to_id=41, pull_request_url="https://api.github.com/repos/owner/repo/pulls/1")
        else:
            reply["issue_url"] = "https://api.github.com/repos/owner/repo/issues/1"
        self.replies.append(reply)
        if self.lose_reply:
            self.lose_reply = False
            raise maintain.MaintenanceError("Lost response")
        return copy.deepcopy(reply)

    def resolve_thread(self, payload):
        self.resolved = True
        return self.thread(payload["repo"], payload["number"], payload["threadId"])


class Git:
    def __init__(self):
        self.calls = []

    def inspect(self, workspace, pinned, commit=None):
        self.calls.append("inspect")
        if commit not in (None, "candidate"):
            raise maintain.MaintenanceError("Candidate drift")
        return {"workspace": workspace, "commit": "candidate", "tree": "tree",
                "head": pinned["head"]["sha"], "base": pinned["base"]["sha"],
                "headRef": pinned["head"]["ref"], "baseRef": pinned["base"]["ref"], "repo": "owner/repo"}

    def bundle(self, workspace, commit, head):
        self.calls.append("bundle")
        return b"prepared bundle"

    def verify_bundle(self, workspace, data, prepared):
        self.calls.append("verify")
        if data != b"prepared bundle":
            raise maintain.MaintenanceError("Corrupt bundle")
        return dict(prepared, workspace=workspace)

    def inspect_existing(self, workspace, pinned):
        return dict(self.inspect(workspace, pinned), commit=pinned["head"]["sha"])

    def push(self, workspace, prepared):
        self.calls.append("push")
        self.github.pr["head"]["sha"] = prepared["commit"]


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.native, self.github = Native(), GitHub()
        self.git = Git()
        self.git.github = self.github

    def workflow(self):
        return maintain.MaintenanceWorkflow(self.native, self.github, self.native.config, git=self.git)

    def actionable(self):
        self.github.evidence.update(feedback=["comment-v1"], pending=False,
                                    items={"comment-v1": {
                                        "kind": "thread", "threadId": "thread", "rootCommentId": 41,
                                        "comment": {"id": "comment", "databaseId": 41,
                                                    "body": "Fix", "updatedAt": "before"}}})

    def next_tick(self):
        self.native.finish()
        self.native.actor("tick-2", "routine", "run-tick-2")

    def test_clean_poll_settles_native_execution_and_starts_no_model_task(self):
        result = self.workflow().poll()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(self.native.created, [])
        self.assertEqual(self.native.tasks["tick"]["status"], "done")

    def test_actionable_poll_creates_one_bounded_expert_step(self):
        self.actionable()
        self.workflow().poll()
        self.assertEqual(len(self.native.created), 1)
        self.assertEqual(self.native.created[0]["assigneeAgentId"], "expert")
        self.assertIsNone(self.native.created[0]["parentId"])
        self.assertEqual(self.native.slot()["active"]["phase"], "expert")

    def test_repeated_poll_does_not_duplicate_admitted_step(self):
        self.actionable()
        self.workflow().poll()
        self.next_tick()
        self.workflow().poll()
        self.assertEqual(len(self.native.created), 1)

    def test_lost_creation_is_recovered_by_authenticated_plan_not_recreated(self):
        self.actionable()
        self.native.lose_create = True
        self.workflow().poll()
        self.next_tick()
        self.workflow().poll()
        self.assertEqual(len(self.native.created), 1)
        task = self.native.slot()["active"]["steps"][0]["taskId"]
        self.assertEqual(task, "step-1")
        self.assertEqual(self.native.tasks[task]["status"], "todo")

    def test_missing_recovery_match_never_authorizes_recreation(self):
        self.actionable()
        self.native.lose_create = True
        self.workflow().poll()
        self.next_tick()
        self.native.hide_tasks = True
        self.workflow().poll()
        self.assertEqual(len(self.native.created), 1)
        self.assertIsNone(self.native.slot()["active"]["steps"][0]["taskId"])

    def test_successful_expert_plan_selects_exactly_one_writer(self):
        self.actionable()
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-1", "expert", "run-expert")
        self.workflow().record_result(
            {"outcome": "plan", "writer": "builder", "instructions": "Fix the item", "material": False})
        self.native.finish()
        self.native.actor("tick-2", "routine", "run-tick-2")
        self.workflow().poll()
        self.assertEqual(len(self.native.created), 2)
        self.assertEqual(self.native.created[1]["assigneeAgentId"], "builder")

    def test_plan_from_wrong_native_actor_is_refused(self):
        self.actionable()
        self.workflow().poll()
        self.native.finish()
        self.native.actor("unrelated", "expert", "wrong-run")
        with self.assertRaises(maintain.MaintenanceError):
            self.workflow().record_result(
                {"outcome": "plan", "writer": "builder", "instructions": "Fix", "material": False})

    def test_unresolved_red_ci_cannot_be_marked_no_action(self):
        self.github.evidence.update(failed=["build"], pending=False)
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-1", "expert", "run-expert")
        with self.assertRaises(maintain.MaintenanceError):
            self.workflow().record_result({"outcome": "noop", "handled": []})

    def start_writer(self, material=True):
        self.actionable()
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-1", "expert", "run-expert")
        self.workflow().record_result(
            {"outcome": "plan", "writer": "builder", "instructions": "Fix", "material": material})
        self.native.finish()
        self.native.actor("tick-2", "routine", "run-tick-2")
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-2", "builder", "run-builder")

    def prepared(self, material=True):
        return self.workflow().record_result({
            "outcome": "prepared", "workspace": "/virtual/writer", "commit": "candidate",
            "material": material, "fixed": ["comment-v1"],
            "checks": [{"command": "test", "outcome": "passed"}],
        })

    def test_material_preparation_requires_pinned_bundle_review_before_push_phase(self):
        self.start_writer()
        self.prepared()
        self.native.finish()
        self.native.actor("tick-3", "routine", "run-tick-3")
        self.workflow().poll()
        self.assertEqual(self.native.created[-1]["assigneeAgentId"], "reviewer")
        self.assertEqual(self.native.slot()["active"]["phase"], "review")
        self.assertIn("bundle", self.git.calls)

    def test_corrupt_prepared_attachment_cannot_start_review(self):
        self.start_writer()
        self.prepared()
        identifier = next(iter(self.native.attachments))
        meta, _ = self.native.attachments[identifier]
        self.native.attachments[identifier] = (meta, b"corrupt")
        self.native.finish()
        self.native.actor("tick-3", "routine", "run-tick-3")
        result = self.workflow().poll()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(self.native.created), 2)

    def test_independent_review_verifies_bundle_in_own_workspace(self):
        self.start_writer()
        self.prepared()
        self.native.finish()
        self.native.actor("tick-3", "routine", "run-tick-3")
        self.workflow().poll()
        self.native.finish()
        self.native.actor("step-3", "reviewer", "run-reviewer")
        self.workflow().prepare_review("/virtual/reviewer")
        self.workflow().record_result(
            {"outcome": "approve", "workspace": "/virtual/reviewer",
             "checks": [{"command": "inspect", "outcome": "passed"}]})
        self.assertIn("verify", self.git.calls)
        self.native.finish()
        self.native.actor("tick-4", "routine", "run-tick-4")
        self.workflow().poll()
        self.assertEqual(self.native.created[-1]["assigneeAgentId"], "builder")
        self.assertEqual(self.native.slot()["active"]["phase"], "effects")

    def test_nonmaterial_preparation_does_not_start_a_reviewer(self):
        self.start_writer(material=False)
        result = self.prepared(material=False)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(self.native.created), 2)
        self.assertNotIn("bundle", self.git.calls)

    def test_normal_finish_pushes_replies_resolves_and_later_poll_settles(self):
        self.start_writer(material=False)
        self.prepared(material=False)
        self.workflow().finish()
        self.assertEqual(self.git.calls.count("push"), 1)
        self.assertEqual(len(self.github.replies), 1)
        self.assertTrue(self.github.resolved)
        self.native.finish()
        self.native.actor("tick-3", "routine", "run-tick-3")
        self.workflow().poll()
        self.assertIsNone(self.native.slot()["active"])
        self.assertIn("comment-v1", self.native.slot()["handled"])

    def test_lost_reply_is_read_back_without_second_post(self):
        self.start_writer(material=False)
        self.prepared(material=False)
        self.github.lose_reply = True
        self.workflow().finish()
        self.assertEqual(len(self.github.replies), 1)
        self.assertTrue(self.github.resolved)

    def test_unknown_reply_without_remote_evidence_never_retries_or_starts_model(self):
        self.start_writer(material=False)
        self.prepared(material=False)
        self.github.reject_reply = True
        with self.assertRaises(maintain.MaintenanceError):
            self.workflow().finish()
        self.native.finish()
        self.native.actor("tick-3", "routine", "run-tick-3")
        self.workflow().poll()
        self.assertEqual(len(self.native.created), 2)
        self.assertEqual(self.github.replies, [])
        self.assertIsNotNone(self.native.slot()["active"])

    def test_closed_write_admission_rejects_every_effect_before_io(self):
        self.start_writer(material=False)
        self.prepared(material=False)
        state = self.native.slot()
        state["active"].update(writesOpen=False, phase="closing")
        key = maintain.slot_key("company", 13, 1)
        doc = self.native.documents["ledger", key]
        self.native.update_document("ledger", key, json.dumps(state), doc["latestRevisionId"])
        with self.assertRaises(maintain.MaintenanceError):
            self.workflow().apply_effect("push")
        self.assertNotIn("push", self.git.calls)

    def test_existing_fix_needs_no_new_commit_or_push(self):
        self.start_writer(material=False)
        self.workflow().record_result({
            "outcome": "existing", "workspace": "/virtual/writer", "material": False,
            "fixed": ["comment-v1"], "checks": [{"command": "verify", "outcome": "passed"}]})
        self.workflow().finish()
        self.assertNotIn("push", self.git.calls)
        self.assertTrue(self.github.resolved)
