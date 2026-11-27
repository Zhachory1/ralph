"""Exercise fail-closed review policy and native provenance."""

import json
import io
import hashlib
import shlex
from contextlib import redirect_stdout
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from bootstrap.paperclip_client import DocumentNotFound, ProtocolError
from bootstrap.paperclip_review import (
    ReviewError, allowed_pr, publication_key, validate_config, validate_ledger_issue,
    validate_publisher_identity, validate_result_revision, validate_run,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "compose" / "pr-review.example.json"


class NativeFake:
    def __init__(self):
        self.tasks = {"tick": {"id": "tick", "companyId": "company", "projectId": "project",
                               "parentId": None, "assigneeAgentId": "routine", "status": "in_progress"},
                      "ledger": {"id": "ledger", "companyId": "company", "projectId": "project",
                                 "assigneeAgentId": None, "assigneeUserId": None}}
        self.documents = {}
        self.runs = {}
        self.reviews = []
        self.calls = []
        self.invocations = []
        self.head = "h"
        self.base = "b"
        self.assignees = ["publisher"]
        self.lose_post = False
        self.lose_reservation = False

    def actor(self, task, agent, run):
        self.task, self.agent, self.run = task, agent, run
        self.runs[run] = {"id": run, "companyId": "company", "agentId": agent,
                          "issueId": task, "status": "running"}

    def get_me(self):
        return {"id": self.agent, "companyId": "company"}

    def get_issue(self, task):
        return self.tasks[task].copy()

    def get_run(self, run):
        return self.runs[run].copy()

    def open_gateway(self, task, project):
        pass

    def tool_name(self, connection, name):
        return name

    def call_tool(self, connection, name, parameters, idempotency_key):
        self.calls.append((name, parameters, idempotency_key))
        if name == "get_me":
            return "read", {"id": 9, "login": "publisher"}
        if name == "search_pull_requests":
            return "read", {"total_count": 1, "incomplete_results": False,
                            "items": [{"number": 7, "html_url": "https://github.com/o/r/pull/7"}]}
        if name == "search_repositories":
            return "read", {"total_count": 1, "incomplete_results": False,
                            "items": [{"id": 5, "full_name": "o/r"}]}
        if name == "pull_request_read" and parameters["method"] == "get":
            return "read", {"number": 7, "state": "open", "draft": False,
                            "user": {"login": "author"}, "assignees": self.assignees,
                            "head": {"sha": self.head, "ref": "feature"},
                            "base": {"sha": self.base, "ref": "main"}}
        if name == "pull_request_read" and parameters["method"] == "get_reviews":
            return "read", list(self.reviews)
        if name == "pull_request_review_write":
            self.invocations.append({"id": "inv", "companyId": "company",
                                     "idempotencyKey": idempotency_key, "agentId": self.agent,
                                     "issueId": self.task, "runId": self.run,
                                     "connectionId": connection, "toolName": name,
                                     "status": "succeeded", "startedAt": "now"})
            self.reviews.append({"id": 31, "state": "APPROVED" if parameters["event"] == "APPROVE"
                                 else "COMMENTED", "user": {"id": 9, "login": "publisher"},
                                 "commit_id": parameters["commitID"], "body": parameters["body"]})
            if self.lose_post:
                raise ProtocolError("outcome unknown")
            return "inv", {"message": "pull request review submitted successfully"}
        raise AssertionError(name)

    def get_decisions(self, company, run):
        return {"runId": run, "decisions": [{"invocation": inv} for inv in self.invocations]}

    def get_document(self, task, key):
        if (task, key) not in self.documents:
            raise DocumentNotFound("missing")
        return self.documents[task, key].copy()

    def get_revision(self, task, key, revision):
        doc = self.documents[task, key]
        return {"id": revision, "issueId": task, "key": key, "companyId": "company",
                "isLatest": revision == doc["latestRevisionId"], "latestRevisionId": doc["latestRevisionId"],
                "createdByAgentId": doc["agent"], "createdByUserId": None,
                "createdByRunId": doc["run"], "body": doc["body"]}

    def create_document(self, task, key, body):
        if (task, key) in self.documents:
            raise ProtocolError("conflict")
        doc = {"key": key, "body": body, "latestRevisionId": "rev-" + str(len(self.documents)),
               "agent": self.agent, "run": self.run}
        self.documents[task, key] = doc
        if self.lose_reservation and key.startswith("send-"):
            raise ProtocolError("lost acknowledgement")
        return doc.copy()

    def update_document(self, task, key, body, revision):
        doc = self.documents[task, key]
        if doc["latestRevisionId"] != revision:
            raise ProtocolError("conflict")
        doc.update(body=body, latestRevisionId=revision + "-next", agent=self.agent, run=self.run)
        return doc.copy()

    def create_issue(self, company, body):
        task = "task-" + str(len(self.tasks))
        self.tasks[task] = {"id": task, "companyId": company, "projectId": body["projectId"],
                            "parentId": body["parentId"], "assigneeAgentId": body["assigneeAgentId"],
                            "status": "todo", "assigneeUserId": None, "blockedByIssueIds": []}
        self.create_document(task, "plan", body["initialPlan"])
        return self.tasks[task].copy()

    def update_issue(self, task, body):
        self.tasks[task].update(body)
        return self.tasks[task].copy()


def enable_two_prs(client):
    call = client.call_tool
    def two_prs(connection, name, params, key):
        if name == "search_pull_requests":
            return "read", {"total_count": 2, "incomplete_results": False, "items": [
                {"number": 7, "html_url": "https://github.com/o/r/pull/7"},
                {"number": 8, "html_url": "https://github.com/o/r/pull/8"}]}
        invocation, value = call(connection, name, params, key)
        if name == "pull_request_read" and params["method"] == "get" and params["pullNumber"] == 8:
            value = dict(value, number=8)
        return invocation, value
    client.call_tool = two_prs


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.config = {"companyId": "company", "projectId": "project", "routineAgentId": "routine",
                       "expertAgentId": "expert", "reviewerAgentId": "reviewer",
                       "gatewayConnectionId": "connection", "ledgerIssueId": "ledger",
                       "assignmentLogin": "publisher", "publisherLogin": "publisher",
                       "publisherAccountId": 9, "authors": ["author"], "repoFilter": [],
                       "reviewHistoryUnfiltered": True}
        self.client = NativeFake()

    def workflow(self, task, agent, run):
        from bootstrap.paperclip_review import ReviewWorkflow
        self.client.actor(task, agent, run)
        return ReviewWorkflow(self.client, self.config, {
            "companyId": "company", "agentId": agent, "runId": run, "taskId": task,
        }, "/private/config.json")

    def prepared(self):
        expert = self.workflow("tick", "routine", "tick-run").discover()[0]
        self.client.runs["tick-run"]["status"] = "succeeded"
        review = self.workflow(expert, "expert", "delegate-run").delegate()
        self.client.runs["delegate-run"]["status"] = "succeeded"
        reviewer = self.workflow(review, "reviewer", "review-run")
        reviewer.preflight()
        reviewer.record_result({"verdict": "APPROVE", "coverage": "diff", "reviewedHead": "h", "reviewedBase": "b",
                                "findings": [], "checks": [{"command": "test", "outcome": "passed"}]})
        self.client.tasks[review]["status"] = "done"
        self.client.runs["review-run"]["status"] = "succeeded"
        return expert

    def test_discover_delegate_and_publish_one_submitted_summary(self):
        expert = self.prepared()
        result = self.workflow(expert, "expert", "publish-run").publish()
        self.assertEqual(result["status"], "submitted")
        writes = [call for call in self.client.calls if call[0] == "pull_request_review_write"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][1]["method"], "create")
        self.assertEqual(writes[0][1]["event"], "APPROVE")
        self.assertEqual(writes[0][1]["commitID"], "h")
        self.assertIn("#pullrequestreview-31", result["url"])
        self.assertEqual(self.workflow(expert, "expert", "restart-run").publish()["status"], "submitted")
        self.assertEqual(len([call for call in self.client.calls if call[0] == "pull_request_review_write"]), 1)

    def test_lost_post_reconciles_remote_and_native_audit_without_resend(self):
        expert = self.prepared()
        self.client.lose_post = True
        self.assertEqual(self.workflow(expert, "expert", "publish-run").publish()["status"], "submitted")
        self.assertEqual(len([call for call in self.client.calls if call[0] == "pull_request_review_write"]), 1)

    def test_lost_reservation_ack_never_posts(self):
        expert = self.prepared()
        self.client.lose_reservation = True
        self.assertEqual(self.workflow(expert, "expert", "publish-run").publish()["status"],
                         "submission_unknown")
        self.assertEqual(self.workflow(expert, "expert", "restart-run").publish()["status"],
                         "submission_unknown")
        self.assertFalse([call for call in self.client.calls if call[0] == "pull_request_review_write"])

    def test_missing_remote_or_native_audit_never_causes_resend(self):
        expert = self.prepared()
        self.client.lose_post = True
        self.client.get_decisions = lambda company, run: {"runId": run, "decisions": []}
        self.assertEqual(self.workflow(expert, "expert", "publish-run").publish()["status"],
                         "submission_unknown")
        self.client.reviews.clear()
        self.assertEqual(self.workflow(expert, "expert", "restart-run").publish()["status"],
                         "submission_unknown")
        self.assertEqual(len([call for call in self.client.calls if call[0] == "pull_request_review_write"]), 1)

    def test_unqualified_history_and_unfinished_reviewer_never_write(self):
        expert = self.prepared()
        self.config["reviewHistoryUnfiltered"] = False
        with self.assertRaises(ReviewError):
            self.workflow(expert, "expert", "publish-run").publish()
        self.config["reviewHistoryUnfiltered"] = True
        self.client.runs["review-run"]["status"] = "running"
        self.assertEqual(self.workflow(expert, "expert", "publish-run-2").publish()["status"], "pending")
        self.assertFalse([call for call in self.client.calls if call[0] == "pull_request_review_write"])

    def test_manual_review_and_publisher_pending_stop_before_reservation(self):
        expert = self.prepared()
        self.client.reviews.append({"id": 1, "state": "APPROVED",
                                    "user": {"id": 9, "login": "publisher"},
                                    "commit_id": "h", "body": "manual"})
        self.assertEqual(self.workflow(expert, "expert", "publish-run").publish()["status"], "existing_review")
        self.client.reviews[0]["state"] = "PENDING"
        self.assertEqual(self.workflow(expert, "expert", "publish-run-2").publish()["status"], "conflict")
        self.assertFalse([key for task, key in self.client.documents if key.startswith("send-")])

    def test_head_drift_stops_before_reservation(self):
        expert = self.prepared()
        self.client.head = "new"
        with self.assertRaises(ReviewError):
            self.workflow(expert, "expert", "publish-run").publish()
        self.assertFalse([key for task, key in self.client.documents if key.startswith("send-")])

    def test_base_drift_after_reservation_never_posts(self):
        expert = self.prepared()
        create = self.client.create_document
        def reserve(task, key, body):
            result = create(task, key, body)
            if key.startswith("send-"):
                self.client.base = "new"
            return result
        self.client.create_document = reserve
        self.assertEqual(self.workflow(expert, "expert", "publish-run").publish()["status"],
                         "submission_unknown")
        self.assertFalse([call for call in self.client.calls if call[0] == "pull_request_review_write"])

    def test_result_revision_change_after_reservation_never_posts(self):
        expert = self.prepared()
        create = self.client.create_document
        def reserve(task, key, body):
            result = create(task, key, body)
            if key.startswith("send-"):
                review_task = next(task for task, name in self.client.documents if name == "review-result")
                self.client.documents[review_task, "review-result"]["latestRevisionId"] += "-changed"
            return result
        self.client.create_document = reserve
        self.assertEqual(self.workflow(expert, "expert", "publish-run").publish()["status"],
                         "submission_unknown")
        self.assertFalse([call for call in self.client.calls if call[0] == "pull_request_review_write"])

    def test_repeated_discovery_does_not_create_duplicate_task(self):
        first = self.workflow("tick", "routine", "tick-run").discover()
        second = self.workflow("tick", "routine", "tick-run-2").discover()
        self.assertEqual(first, second)
        self.assertEqual(len(self.client.tasks), 3)

    def test_message_only_response_is_not_a_receipt(self):
        expert = self.prepared()
        call = self.client.call_tool
        def without_remote(connection, name, parameters, key):
            response = call(connection, name, parameters, key)
            if name == "pull_request_review_write":
                self.client.reviews.clear()
            return response
        self.client.call_tool = without_remote
        self.assertEqual(self.workflow(expert, "expert", "publish-run").publish()["status"],
                         "submission_unknown")

    def test_cli_discover_runs_using_native_environment_and_plan(self):
        from bootstrap.paperclip_review import main
        self.client.actor("tick", "routine", "tick-run")
        env = {"PAPERCLIP_COMPANY_ID": "company", "PAPERCLIP_AGENT_ID": "routine",
               "PAPERCLIP_RUN_ID": "tick-run", "PAPERCLIP_TASK_ID": "tick",
               "PAPERCLIP_API_URL": "https://example.invalid", "PAPERCLIP_API_KEY": "placeholder"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(self.config))
            stdout = io.StringIO()
            with patch.dict(os.environ, env, clear=True), patch(
                    "bootstrap.paperclip_review.PaperclipClient", return_value=self.client), redirect_stdout(stdout):
                self.assertEqual(main(["discover", "--config", str(path)]), 0)
            expert = json.loads(stdout.getvalue())[0]
            plan = json.loads(self.client.get_document(expert, "plan")["body"])
            self.assertEqual(plan["kind"], "expert")
            self.assertIn("delegate --config", plan["instructions"])

    def test_self_approval_uses_numeric_author_identity(self):
        from bootstrap.paperclip_review import render_summary
        result = {"verdict": "APPROVE", "coverage": "diff", "findings": [], "reviewedHead": "h", "reviewedBase": "b",
                  "checks": [{"command": "test", "outcome": "passed"}]}
        pr = {"repo": "o/r", "number": 7, "head": "h", "base": "b",
              "author": "old-login", "authorId": 9}
        self.assertEqual(render_summary(result, pr, "publisher", 9)[0], "COMMENT")

    def test_final_metadata_is_checked_after_history_before_send(self):
        for change in ("base", "assignment"):
            with self.subTest(change=change):
                self.setUp()
                expert = self.prepared()
                original = self.client.call_tool
                scans = []
                def history_race(connection, name, params, key):
                    value = original(connection, name, params, key)
                    if name == "pull_request_read" and params["method"] == "get_reviews":
                        scans.append(1)
                        if len(scans) == 2:
                            if change == "base":
                                self.client.base = "new"
                            else:
                                self.client.assignees = []
                    return value
                self.client.call_tool = history_race
                self.assertEqual(self.workflow(expert, "expert", "publish-run").publish()["status"],
                                 "submission_unknown")
                self.assertFalse([c for c in self.client.calls if c[0] == "pull_request_review_write"])

    def test_current_run_is_rechecked_after_final_history_scan(self):
        expert = self.prepared()
        original = self.client.call_tool
        scans = []
        def stop_run(connection, name, params, key):
            value = original(connection, name, params, key)
            if name == "pull_request_read" and params["method"] == "get_reviews":
                scans.append(1)
                if len(scans) == 2:
                    self.client.runs["publish-run"]["status"] = "failed"
            return value
        self.client.call_tool = stop_run
        self.assertEqual(self.workflow(expert, "expert", "publish-run").publish()["status"],
                         "submission_unknown")
        self.assertFalse([c for c in self.client.calls if c[0] == "pull_request_review_write"])

    def test_reviewer_reports_exact_reviewed_commits_before_result_save(self):
        expert = self.workflow("tick", "routine", "tick-run").discover()[0]
        reviewer_id = self.workflow(expert, "expert", "delegate-run").delegate()
        reviewer = self.workflow(reviewer_id, "reviewer", "review-run")
        plan = json.loads(self.client.get_document(reviewer_id, "plan")["body"])
        self.assertIn("reviewedHead", plan["instructions"])
        self.assertIn("reviewedBase", plan["instructions"])
        self.assertIn("GitHub", plan["instructions"])
        self.assertIn("not moving branches", plan["instructions"])
        result = {"verdict": "COMMENT", "coverage": "diff", "findings": [],
                  "checks": [{"command": "test", "outcome": "passed"}]}
        for pins in ({}, {"reviewedHead": "wrong", "reviewedBase": "b"},
                     {"reviewedHead": "h", "reviewedBase": "wrong"}):
            with self.subTest(pins=pins), self.assertRaises(ReviewError):
                reviewer.record_result(dict(result, **pins))
        self.assertNotIn((reviewer_id, "review-result"), self.client.documents)

    def test_exact_receipt_ignores_later_manual_or_pending(self):
        for state in ("COMMENTED", "PENDING"):
            with self.subTest(state=state):
                self.setUp()
                expert = self.prepared()
                decisions = self.client.get_decisions
                self.client.get_decisions = lambda company, run: {"runId": run, "decisions": []}
                self.client.lose_post = True
                self.assertEqual(self.workflow(expert, "expert", "publish-run").publish()["status"],
                                 "submission_unknown")
                self.client.get_decisions = decisions
                self.client.reviews.append({"id": 32, "state": state,
                                            "user": {"id": 9, "login": "publisher"},
                                            "commit_id": "h", "body": "manual"})
                self.assertEqual(self.workflow(expert, "expert", "restart-run").publish()["status"], "submitted")
                self.assertEqual(len(self.client.reviews), 2)
                self.assertEqual(len([c for c in self.client.calls if c[0] == "pull_request_review_write"]), 1)

    def test_duplicate_exact_receipts_are_unknown(self):
        expert = self.prepared()
        decisions = self.client.get_decisions
        self.client.get_decisions = lambda company, run: {"runId": run, "decisions": []}
        self.workflow(expert, "expert", "publish-run").publish()
        self.client.get_decisions = decisions
        self.client.reviews.append(dict(self.client.reviews[0], id=32))
        self.assertEqual(self.workflow(expert, "expert", "restart-run").publish()["status"],
                         "submission_unknown")

    def test_unknown_first_dispatch_does_not_starve_second(self):
        from bootstrap.paperclip_review import _document_key, fetch_pr
        enable_two_prs(self.client)
        tick = self.workflow("tick", "routine", "tick-run")
        pr = fetch_pr(self.client, "connection", "o/r", 7)
        brief = tick._brief("expert", pr, tick._route(pr["repo"]))
        self.client.create_document("ledger", _document_key("expert", brief["analysisKey"]), json.dumps({
            "key": brief["analysisKey"], "kind": "expert", "ownerTaskId": "tick", "nonce": "unknown"}))
        outcome = tick.discover()
        self.assertEqual(outcome["status"], "partial")
        self.assertEqual(outcome["blocked"][0]["number"], 7)
        self.assertEqual(outcome["blocked"][0]["head"], "h")
        self.assertEqual(outcome["blocked"][0]["key"], brief["key"])
        self.assertEqual(len(outcome["dispatched"]), 1)
        repeat = self.workflow("tick", "routine", "tick-run-2").discover()
        self.assertEqual(repeat["dispatched"], outcome["dispatched"])
        self.assertEqual(len(self.client.tasks), 3)

    def test_lost_task_creation_ack_blocks_only_that_pr_and_is_not_retried(self):
        enable_two_prs(self.client)
        create = self.client.create_issue
        attempts = []
        def lose_first(company, body):
            attempts.append(body["title"])
            issue = create(company, body)
            if body["title"].endswith("#7"):
                raise ProtocolError("lost acknowledgement")
            return issue
        self.client.create_issue = lose_first
        outcome = self.workflow("tick", "routine", "tick-run").discover()
        self.assertEqual(outcome["status"], "partial")
        self.assertEqual([row["number"] for row in outcome["blocked"]], [7])
        self.assertEqual(len(outcome["dispatched"]), 1)
        self.workflow("tick", "routine", "tick-run-2").discover()
        self.assertEqual(attempts.count("Review o/r#7"), 1)

    def test_assignment_race_does_not_starve_other_pr(self):
        enable_two_prs(self.client)
        call = self.client.call_tool
        reads = []
        def race(connection, name, params, key):
            invocation, value = call(connection, name, params, key)
            if name == "pull_request_read" and params["method"] == "get" and params["pullNumber"] == 7:
                reads.append(1)
                if len(reads) > 1:
                    value = dict(value, assignees=[])
            return invocation, value
        self.client.call_tool = race
        outcome = self.workflow("tick", "routine", "tick-run").discover()
        self.assertEqual(outcome["status"], "partial")
        self.assertEqual(outcome["blocked"][0]["error"], "metadata changed")
        self.assertEqual(len(outcome["dispatched"]), 1)

    def test_explicit_permission_denial_stops_discovery(self):
        from bootstrap.paperclip_client import PermissionDenied
        enable_two_prs(self.client)
        create = self.client.create_document
        def deny(task, key, body):
            if key.startswith("expert-"):
                raise PermissionDenied("denied")
            return create(task, key, body)
        self.client.create_document = deny
        with self.assertRaises(PermissionDenied):
            self.workflow("tick", "routine", "tick-run").discover()
        self.assertEqual(len(self.client.tasks), 2)

    def test_history_page_cap_blocks_only_that_pr(self):
        enable_two_prs(self.client)
        call = self.client.call_tool
        def overfull(connection, name, params, key):
            if name == "pull_request_read" and params["method"] == "get_reviews" and params["pullNumber"] == 7:
                return "read", [{}] * 100
            return call(connection, name, params, key)
        self.client.call_tool = overfull
        outcome = self.workflow("tick", "routine", "tick-run").discover()
        self.assertEqual(outcome["status"], "partial")
        self.assertEqual([item["number"] for item in outcome["blocked"]], [7])
        self.assertEqual(len(outcome["dispatched"]), 1)

    def test_unrelated_assignee_addition_reuses_existing_task(self):
        first = self.workflow("tick", "routine", "tick-run").discover()
        self.client.assignees = ["publisher", "other"]
        second = self.workflow("tick", "routine", "tick-run-2").discover()
        self.assertEqual(second, first)
        self.assertEqual(len(self.client.tasks), 3)

    def test_changed_saved_plan_blocks_only_that_pr(self):
        expert = self.workflow("tick", "routine", "tick-run").discover()[0]
        document = self.client.documents[expert, "plan"]
        plan = json.loads(document["body"])
        plan["pr"]["head"] = "wrong"
        document["body"] = json.dumps(plan)
        enable_two_prs(self.client)
        outcome = self.workflow("tick", "routine", "tick-run-2").discover()
        self.assertEqual(outcome["status"], "partial")
        self.assertEqual([item["number"] for item in outcome["blocked"]], [7])
        self.assertEqual(len(outcome["dispatched"]), 1)
        self.assertEqual(json.loads(document["body"])["pr"]["head"], "wrong")

    def test_own_pending_review_blocks_only_that_pr(self):
        enable_two_prs(self.client)
        call = self.client.call_tool
        def pending(connection, name, params, key):
            if name == "pull_request_read" and params["method"] == "get_reviews" and params["pullNumber"] == 7:
                return "read", [{"id": 1, "state": "PENDING",
                                 "user": {"id": 9, "login": "publisher"}, "body": ""}]
            return call(connection, name, params, key)
        self.client.call_tool = pending
        outcome = self.workflow("tick", "routine", "tick-run").discover()
        self.assertEqual(outcome["status"], "partial")
        self.assertEqual(outcome["blocked"][0]["number"], 7)
        self.assertEqual(outcome["blocked"][0]["error"], "review conflict")
        self.assertEqual(len(outcome["dispatched"]), 1)

    def test_incomplete_repository_search_remains_global_failure(self):
        enable_two_prs(self.client)
        call = self.client.call_tool
        searches = []
        def incomplete(connection, name, params, key):
            if name == "search_repositories":
                searches.append(1)
                if len(searches) == 2:
                    return "read", {"total_count": 1, "incomplete_results": True, "items": []}
            return call(connection, name, params, key)
        self.client.call_tool = incomplete
        with self.assertRaises(ReviewError):
            self.workflow("tick", "routine", "tick-run").discover()
        self.assertEqual(len(self.client.tasks), 2)

    def test_explicit_history_permission_denial_remains_global_failure(self):
        from bootstrap.paperclip_client import PermissionDenied
        enable_two_prs(self.client)
        call = self.client.call_tool
        def denied(connection, name, params, key):
            if name == "pull_request_read" and params["method"] == "get_reviews":
                raise PermissionDenied("denied")
            return call(connection, name, params, key)
        self.client.call_tool = denied
        with self.assertRaises(PermissionDenied):
            self.workflow("tick", "routine", "tick-run").discover()
        self.assertEqual(len(self.client.tasks), 2)

    def test_one_pull_request_metadata_failure_does_not_starve_another(self):
        enable_two_prs(self.client)
        call = self.client.call_tool
        def missing(connection, name, params, key):
            if name == "pull_request_read" and params["method"] == "get" and params["pullNumber"] == 7:
                raise ProtocolError("read failed")
            return call(connection, name, params, key)
        self.client.call_tool = missing
        outcome = self.workflow("tick", "routine", "tick-run").discover()
        self.assertEqual(outcome["status"], "partial")
        self.assertEqual(outcome["blocked"][0]["number"], 7)
        self.assertEqual(len(outcome["dispatched"]), 1)

    def test_routine_finishes_scan_and_uses_separate_followup_for_blocked_keys(self):
        from bootstrap.paperclip_review import routine_requests
        description = routine_requests(self.config, "/private/config.json")["routine"]["body"]["description"]
        self.assertIn("separate", description)
        self.assertIn("finish", description.casefold())
        self.assertIn("not a child or blocker", description)

    def test_wrong_reported_commit_is_rejected_at_publication(self):
        expert = self.prepared()
        result = next(doc for (task, key), doc in self.client.documents.items() if key == "review-result")
        body = json.loads(result["body"])
        body["result"]["reviewedHead"] = "other"
        result["body"] = json.dumps(body)
        with self.assertRaises(ReviewError):
            self.workflow(expert, "expert", "publish-run").publish()
        self.assertFalse([c for c in self.client.calls if c[0] == "pull_request_review_write"])

    def test_failed_creator_keeps_authenticated_plan_and_bad_binding_refuses(self):
        expert = self.workflow("tick", "routine", "tick-run").discover()[0]
        self.client.runs["tick-run"]["status"] = "failed"
        worker = self.workflow(expert, "expert", "delegate-run")
        self.client.runs["tick-run"]["issueId"] = "wrong"
        with self.assertRaises(ReviewError):
            worker.delegate()
        self.client.runs["tick-run"]["issueId"] = "tick"
        review = worker.delegate()
        self.client.runs["delegate-run"]["status"] = "failed"
        self.assertEqual(self.workflow(review, "reviewer", "review-run").preflight()["head"], "h")

    def test_plan_writer_mismatch_is_rejected(self):
        expert = self.workflow("tick", "routine", "tick-run").discover()[0]
        self.client.documents[expert, "plan"]["agent"] = "other"
        with self.assertRaises(ReviewError):
            self.workflow(expert, "expert", "delegate-run").delegate()

    def test_delegate_preserves_other_blockers(self):
        expert = self.workflow("tick", "routine", "tick-run").discover()[0]
        self.client.tasks[expert]["blockedByIssueIds"] = ["other"]
        review = self.workflow(expert, "expert", "delegate-run").delegate()
        self.assertEqual(self.client.tasks[expert]["blockedByIssueIds"], ["other", review])

    def test_delegate_preserves_expanded_blocker_relations(self):
        expert = self.workflow("tick", "routine", "tick-run").discover()[0]
        self.client.tasks[expert]["blockedBy"] = [{"id": "other"}]
        review = self.workflow(expert, "expert", "delegate-run").delegate()
        self.assertEqual(self.client.tasks[expert]["blockedByIssueIds"], ["other", review])

    def test_delegate_rejects_malformed_blockers_before_creating_child(self):
        expert = self.workflow("tick", "routine", "tick-run").discover()[0]
        self.client.tasks[expert]["blockedByIssueIds"] = None
        with self.assertRaises(ReviewError):
            self.workflow(expert, "expert", "delegate-run").delegate()
        self.assertEqual(len(self.client.tasks), 3)

    def test_review_saved_plan_is_verified_before_dispatch_ack(self):
        create = self.client.create_issue
        def corrupt(company, body):
            task = create(company, body)
            self.client.documents[task["id"], "plan"]["body"] = "{}"
            return task
        self.client.create_issue = corrupt
        outcome = self.workflow("tick", "routine", "tick-run").discover()
        self.assertEqual(outcome["status"], "partial")
        self.assertEqual(outcome["dispatched"], [])
        self.assertEqual(outcome["blocked"][0]["number"], 7)

    def test_repeated_discovery_refuses_modified_saved_plan(self):
        expert = self.workflow("tick", "routine", "tick-run").discover()[0]
        self.client.documents[expert, "plan"]["body"] = "{}"
        outcome = self.workflow("tick", "routine", "tick-run-2").discover()
        self.assertEqual(outcome["status"], "partial")
        self.assertEqual(outcome["dispatched"], [])
        self.assertEqual(self.client.documents[expert, "plan"]["body"], "{}")

    def test_reconcile_after_result_missing_or_changed_without_post(self):
        expert = self.prepared()
        self.client.lose_post = True
        decisions = self.client.get_decisions
        self.client.get_decisions = lambda company, run: {"runId": run, "decisions": []}
        self.assertEqual(self.workflow(expert, "expert", "publish-run").publish()["status"],
                         "submission_unknown")
        self.client.get_decisions = decisions
        result_key = next(key for key in self.client.documents if key[1] == "review-result")
        del self.client.documents[result_key]
        self.assertEqual(self.workflow(expert, "expert", "restart-run").publish()["status"],
                         "submitted_stale")
        self.assertEqual(len([c for c in self.client.calls if c[0] == "pull_request_review_write"]), 1)

    def test_reconcile_after_result_changed_without_extra_post(self):
        expert = self.prepared()
        decisions = self.client.get_decisions
        self.client.get_decisions = lambda company, run: {"runId": run, "decisions": []}
        self.assertEqual(self.workflow(expert, "expert", "publish-run").publish()["status"],
                         "submission_unknown")
        self.client.get_decisions = decisions
        result_key = next(key for key in self.client.documents if key[1] == "review-result")
        self.client.documents[result_key]["latestRevisionId"] += "-changed"
        self.assertEqual(self.workflow(expert, "expert", "restart-run").publish()["status"],
                         "submitted_stale")
        self.assertEqual(len([c for c in self.client.calls if c[0] == "pull_request_review_write"]), 1)

    def test_malformed_saved_intent_is_never_reconciled_or_resent(self):
        expert = self.prepared()
        self.client.lose_reservation = True
        self.workflow(expert, "expert", "publish-run").publish()
        doc = next(doc for (task, key), doc in self.client.documents.items() if key.startswith("send-"))
        intent = json.loads(doc["body"])
        intent["payload"]["event"] = "REQUEST_CHANGES"
        doc["body"] = json.dumps(intent)
        self.assertEqual(self.workflow(expert, "expert", "restart-run").publish()["status"],
                         "submission_unknown")
        self.assertFalse([c for c in self.client.calls if c[0] == "pull_request_review_write"])

    def test_full_payload_hash_and_receipt_record_revision(self):
        expert = self.prepared()
        self.workflow(expert, "expert", "publish-run").publish()
        send = next(doc for (task, key), doc in self.client.documents.items() if key.startswith("send-"))
        receipt = next(doc for (task, key), doc in self.client.documents.items() if key.startswith("receipt-"))
        intent, saved = json.loads(send["body"]), json.loads(receipt["body"])
        encoded = json.dumps(intent["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        self.assertEqual(intent["payloadHash"], hashlib.sha256(encoded.encode()).hexdigest())
        self.assertEqual(saved["resultRevisionId"], intent["resultRevisionId"])

    def test_started_failed_and_timed_out_send_records_reconcile(self):
        for status in ("failed", "timed_out", "executing"):
            with self.subTest(status=status):
                self.setUp()
                expert = self.prepared()
                self.client.lose_post = True
                decisions = self.client.get_decisions
                def changed(company, run):
                    response = decisions(company, run)
                    for item in response["decisions"]:
                        item["invocation"]["status"] = status
                    return response
                self.client.get_decisions = changed
                self.assertEqual(self.workflow(expert, "expert", "publish-run").publish()["status"], "submitted")

    def test_nonstarted_or_denied_audit_cannot_prove_delivery(self):
        for status, started in (("authorized", "now"), ("denied", "now"),
                                ("awaiting_approval", "now"), ("failed", None)):
            with self.subTest(status=status, started=started):
                self.setUp()
                expert = self.prepared()
                decisions = self.client.get_decisions
                def changed(company, run):
                    response = decisions(company, run)
                    for item in response["decisions"]:
                        item["invocation"]["status"] = status
                        item["invocation"]["startedAt"] = started
                    return response
                self.client.get_decisions = changed
                self.assertEqual(self.workflow(expert, "expert", "publish-run").publish()["status"],
                                 "submission_unknown")
                self.assertFalse([key for task, key in self.client.documents if key.startswith("receipt-")])

    def test_remote_confirmation_records_receipt_when_base_drifts_after_post(self):
        expert = self.prepared()
        call = self.client.call_tool
        def change_after_post(connection, name, parameters, key):
            result = call(connection, name, parameters, key)
            if name == "pull_request_review_write":
                self.client.base = "new"
            return result
        self.client.call_tool = change_after_post
        self.assertEqual(self.workflow(expert, "expert", "publish-run").publish()["status"], "submitted_stale")
        self.assertTrue([key for task, key in self.client.documents if key.startswith("receipt-")])

    def test_reviewed_head_prevents_new_analysis_without_intent(self):
        self.client.reviews.append({"id": 1, "state": "COMMENTED",
                                    "user": {"id": 9, "login": "publisher"},
                                    "commit_id": "h", "body": "manual"})
        self.assertEqual(self.workflow("tick", "routine", "tick-run").discover(), [])

    def test_new_base_creates_fresh_analysis_but_existing_send_blocks_it(self):
        first = self.workflow("tick", "routine", "tick-run").discover()[0]
        self.client.base = "new"
        tick = self.workflow("tick", "routine", "tick-run-2")
        second = tick.discover()[0]
        self.assertNotEqual(first, second)
        self.assertEqual(tick.discover(), [second])
        with self.assertRaises(ReviewError):
            self.workflow(first, "expert", "old-expert-run").delegate()
        self.client.actor("tick", "routine", "tick-run-3")
        from bootstrap.paperclip_review import _document_key
        plan = json.loads(self.client.get_document(second, "plan")["body"])
        self.client.create_document("ledger", _document_key("send", plan["key"]), "{}")
        self.client.base = "newer"
        self.assertEqual(self.workflow("tick", "routine", "tick-run-4").discover(), [])

    def test_publish_noncompletion_cli_exits_nonzero_and_prints_status(self):
        from bootstrap.paperclip_review import main
        expert = self.workflow("tick", "routine", "tick-run").discover()[0]
        self.workflow(expert, "expert", "delegate-run").delegate()
        self.client.actor(expert, "expert", "publish-run")
        env = {"PAPERCLIP_COMPANY_ID": "company", "PAPERCLIP_AGENT_ID": "expert",
               "PAPERCLIP_RUN_ID": "publish-run", "PAPERCLIP_TASK_ID": expert,
               "PAPERCLIP_API_URL": "https://example.invalid", "PAPERCLIP_API_KEY": "placeholder"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(self.config))
            out = io.StringIO()
            with patch.dict(os.environ, env, clear=True), patch(
                    "bootstrap.paperclip_review.PaperclipClient", return_value=self.client), redirect_stdout(out):
                code = main(["publish", "--config", str(path)])
            self.assertNotEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())["status"], "pending")

    def test_publish_cli_distinguishes_completion_from_other_states(self):
        from bootstrap.paperclip_review import main
        env = {"PAPERCLIP_COMPANY_ID": "company", "PAPERCLIP_AGENT_ID": "expert",
               "PAPERCLIP_RUN_ID": "run", "PAPERCLIP_TASK_ID": "task",
               "PAPERCLIP_API_URL": "https://example.invalid", "PAPERCLIP_API_KEY": "placeholder"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(self.config))
            for status in ("submitted", "existing_review", "pending", "submission_unknown",
                           "conflict", "submitted_stale"):
                with self.subTest(status=status):
                    out = io.StringIO()
                    worker = unittest.mock.Mock()
                    worker.publish.return_value = {"status": status}
                    with patch.dict(os.environ, env, clear=True), patch(
                            "bootstrap.paperclip_review.PaperclipClient"), patch(
                            "bootstrap.paperclip_review.ReviewWorkflow", return_value=worker), redirect_stdout(out):
                        code = main(["publish", "--config", str(path)])
                    self.assertEqual(code == 0, status in ("submitted", "existing_review"))
                    self.assertEqual(json.loads(out.getvalue())["status"], status)

    def test_partial_discovery_cli_prints_partial_and_exits_nonzero(self):
        from bootstrap.paperclip_review import main
        env = {"PAPERCLIP_COMPANY_ID": "company", "PAPERCLIP_AGENT_ID": "routine",
               "PAPERCLIP_RUN_ID": "run", "PAPERCLIP_TASK_ID": "task",
               "PAPERCLIP_API_URL": "https://example.invalid", "PAPERCLIP_API_KEY": "placeholder"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(self.config))
            out = io.StringIO()
            worker = unittest.mock.Mock()
            worker.discover.return_value = {"status": "partial", "dispatched": [], "blocked": []}
            with patch.dict(os.environ, env, clear=True), patch(
                    "bootstrap.paperclip_review.PaperclipClient"), patch(
                    "bootstrap.paperclip_review.ReviewWorkflow", return_value=worker), redirect_stdout(out):
                code = main(["discover", "--config", str(path)])
            self.assertNotEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())["status"], "partial")

    def test_direct_entrypoint_and_generated_commands_work_outside_repo(self):
        script = ROOT / "bootstrap" / "paperclip_review.py"
        with tempfile.TemporaryDirectory() as directory:
            help_result = subprocess.run([sys.executable, str(script), "--help"], cwd=directory,
                                         capture_output=True, text=True)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            result = subprocess.run([sys.executable, str(script), "routine", "--config", str(EXAMPLE)],
                                    cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            description = json.loads(result.stdout)["routine"]["body"]["description"]
            command = shlex.split(description.split("`")[1])
            self.assertEqual(command[:2], [sys.executable, str(script)])
            self.assertEqual(command[-1], str(EXAMPLE))
        expert = self.workflow("tick", "routine", "tick-run").discover()[0]
        plan = json.loads(self.client.get_document(expert, "plan")["body"])
        command = shlex.split(plan["instructions"].split("`")[1])
        self.assertEqual(command[:2], [sys.executable, str(script)])
        self.assertIn("submission_unknown", plan["instructions"])

class ReviewTest(unittest.TestCase):
    def test_provider_locator_uses_only_known_provider_paths(self):
        from bootstrap.paperclip_review import repository_from_search_item
        self.assertEqual(repository_from_search_item({
            "html_url": "https://github.com/Owner/Repo/pull/12", "number": 12,
        }), "Owner/Repo")
        with self.assertRaises(ReviewError):
            repository_from_search_item({
                "html_url": "https://evil.invalid/Owner/Repo/pull/12", "number": 12,
            })
        with self.assertRaises(ReviewError):
            repository_from_search_item({
                "html_url": "https://github.com/owner/repo%20in%3Apublic/pull/12", "number": 12,
            })

    def test_review_summary_requires_evidence_and_blocks_approve(self):
        from bootstrap.paperclip_review import render_summary
        pr = {"repo": "owner/repo", "number": 12, "head": "head", "base": "base",
              "author": "author"}
        result = {"verdict": "APPROVE", "coverage": "reviewed diff",
                  "reviewedHead": "head", "reviewedBase": "base",
                  "findings": [{"classification": "blocking", "path": "src/a.py", "line": 4,
                                "evidence": "broken", "correction": "repair"}],
                  "checks": [{"command": "python -m unittest", "outcome": "passed"}]}
        with self.assertRaises(ReviewError):
            render_summary(result, pr, "publisher")
        result["verdict"] = "COMMENT"
        event, body = render_summary(result, pr, "publisher")
        self.assertEqual(event, "COMMENT")
        self.assertIn("Blocking findings", body)
        self.assertIn("Non-blocking suggestions", body)
        self.assertIn("Checks", body)
        self.assertIn("src/a.py:4", body)
        self.assertIn("python -m unittest", body)

    def test_unqualified_review_history_rejects_non_boolean(self):
        config = json.loads(EXAMPLE.read_text())
        config["reviewHistoryUnfiltered"] = "true"
        with self.assertRaises(ReviewError):
            validate_config(config)

    def test_search_pagination_refuses_incomplete_results(self):
        from bootstrap.paperclip_review import search_pages
        class Client:
            def call_tool(self, connection, name, params, key):
                return "inv", {"total_count": 1, "incomplete_results": True, "items": []}
        with self.assertRaises(ReviewError):
            search_pages(Client(), "connection", "search_pull_requests", "query")

    def test_review_pagination_uses_fresh_keys_and_refuses_cap(self):
        from bootstrap.paperclip_review import review_pages
        class Client:
            def __init__(self):
                self.keys = []
            def call_tool(self, connection, name, params, key):
                self.keys.append(key)
                return "inv", [{}] * 100
        client = Client()
        with self.assertRaises(ReviewError):
            review_pages(client, "connection", "owner", "repo", 1, max_pages=2)
        self.assertEqual(len(set(client.keys)), 2)

    def test_self_approval_becomes_comment_and_request_changes_is_invalid(self):
        from bootstrap.paperclip_review import render_summary
        pr = {"repo": "owner/repo", "number": 1, "head": "h", "base": "b",
              "author": "Publisher"}
        result = {"verdict": "APPROVE", "coverage": "diff", "findings": [], "reviewedHead": "h", "reviewedBase": "b",
                  "checks": [{"command": "test", "outcome": "not run: unavailable"}]}
        self.assertEqual(render_summary(result, pr, "publisher")[0], "COMMENT")
        result["verdict"] = "REQUEST_CHANGES"
        with self.assertRaises(ReviewError):
            render_summary(result, pr, "publisher")

    def test_normalize_pr_requires_assignment_and_exact_repository_id(self):
        from bootstrap.paperclip_review import fetch_pr
        class Client:
            def call_tool(self, connection, name, params, key):
                if name == "search_repositories":
                    return "i", {"total_count": 2, "incomplete_results": False, "items": [
                        {"id": 5, "full_name": "OWNER/repo"},
                        {"id": 6, "full_name": "owner/other"},
                    ]}
                return "i", {"number": 7, "state": "open", "draft": False,
                             "user": {"login": "author"}, "assignees": ["assignee"],
                             "head": {"sha": "h", "ref": "feature"},
                             "base": {"sha": "b", "ref": "main"}}
        pr = fetch_pr(Client(), "connection", "owner/repo", 7)
        self.assertEqual((pr["repoId"], pr["head"], pr["base"]), (5, "h", "b"))

    def test_manual_review_and_foreign_pending_are_not_receipts(self):
        from bootstrap.paperclip_review import classify_reviews
        manual = {"id": 2, "state": "APPROVED", "user": {"id": 9, "login": "bot"},
                  "commit_id": "h", "body": "manual"}
        self.assertEqual(classify_reviews([manual], 9, "bot", "h", "marker"), "manual")
        pending = {"id": 3, "state": "PENDING", "user": {"id": 9, "login": "bot"},
                   "commit_id": "h", "body": ""}
        self.assertEqual(classify_reviews([pending], 9, "bot", "h", "marker"), "conflict")

    def test_other_account_pending_does_not_conflict(self):
        from bootstrap.paperclip_review import classify_reviews
        review = {"id": 3, "state": "PENDING", "user": {"id": 7, "login": "other"},
                  "commit_id": "h", "body": ""}
        self.assertIsNone(classify_reviews([review], 9, "bot", "h", "marker"))

    def test_unknown_publisher_review_state_fails_closed(self):
        from bootstrap.paperclip_review import classify_reviews
        review = {"id": 3, "state": "NEWSTATE", "user": {"id": 9, "login": "bot"},
                  "commit_id": "h", "body": ""}
        with self.assertRaises(ReviewError):
            classify_reviews([review], 9, "bot", "h", "marker")

    def test_preflight_rejects_head_or_base_drift_and_policy_change(self):
        from bootstrap.paperclip_review import validate_pinned_pr
        policy = {"authors": ["author"], "repoFilter": [], "assignmentLogin": "assignee"}
        pr = {"repo": "owner/repo", "repoId": 1, "number": 1, "head": "h", "base": "b",
              "headRef": "feature", "baseRef": "main", "author": "author",
              "state": "open", "draft": False, "assignees": ["assignee"]}
        validate_pinned_pr(policy, pr, dict(pr))
        with self.assertRaises(ReviewError):
            validate_pinned_pr(policy, pr, dict(pr, base="new"))
        with self.assertRaises(ReviewError):
            validate_pinned_pr(policy, pr, dict(pr, author="other"))
        with self.assertRaises(ReviewError):
            validate_pinned_pr(policy, dict(pr, authorId=8), dict(pr, authorId=9))

    def test_review_history_refuses_missing_publisher_commit_or_wrong_id(self):
        from bootstrap.paperclip_review import classify_reviews
        review = {"id": 1, "state": "COMMENTED", "user": {"id": 9, "login": "bot"},
                  "body": "review"}
        with self.assertRaises(ReviewError):
            classify_reviews([review], 9, "bot", "h", "marker")
        review["commit_id"] = "h"
        review["user"]["login"] = "different"
        with self.assertRaises(ReviewError):
            classify_reviews([review], 9, "bot", "h", "marker")

    def test_not_run_checks_require_reason(self):
        from bootstrap.paperclip_review import render_summary
        result = {"verdict": "COMMENT", "coverage": "diff", "findings": [], "reviewedHead": "h", "reviewedBase": "b",
                  "checks": [{"command": "test", "outcome": "not run"}]}
        pr = {"repo": "owner/repo", "number": 1, "head": "h", "base": "b", "author": "author"}
        with self.assertRaises(ReviewError):
            render_summary(result, pr, "publisher")
        result["checks"][0]["reason"] = "dependencies unavailable"
        self.assertIn("dependencies unavailable", render_summary(result, pr, "publisher")[1])

    def test_finding_reference_must_be_repository_relative(self):
        from bootstrap.paperclip_review import render_summary
        result = {"verdict": "COMMENT", "coverage": "diff", "reviewedHead": "h", "reviewedBase": "b",
                  "checks": [{"command": "test", "outcome": "passed"}],
                  "findings": [{"classification": "blocking", "path": "../secret", "line": 1,
                                "evidence": "broken", "correction": "fix"}]}
        pr = {"repo": "owner/repo", "number": 1, "head": "h", "base": "b", "author": "author"}
        with self.assertRaises(ReviewError):
            render_summary(result, pr, "publisher")

    def test_same_agent_route_is_rejected_including_overrides(self):
        config = json.loads(EXAMPLE.read_text())
        config["repoOverrides"] = {"owner/repo": {"reviewerAgentId": config["expertAgentId"]}}
        with self.assertRaises(ReviewError):
            validate_config(config)

    def test_repository_search_fails_when_exact_name_is_not_unique(self):
        from bootstrap.paperclip_review import fetch_pr
        class Client:
            def call_tool(self, connection, name, params, key):
                return "i", {"total_count": 2, "incomplete_results": False, "items": [
                    {"id": 1, "full_name": "owner/repo"},
                    {"id": 2, "full_name": "OWNER/REPO"}]}
        with self.assertRaises(ReviewError):
            fetch_pr(Client(), "connection", "owner/repo", 1)

    def test_empty_repo_filter_allows_repos_but_empty_author_list_denies(self):
        pr = {"repo": "owner/repo", "author": "author", "state": "open",
              "draft": False, "assignees": ["assignee"]}
        self.assertTrue(allowed_pr({"authors": ["author"], "repoFilter": [],
                                    "assignmentLogin": "assignee"}, pr))
        self.assertFalse(allowed_pr({"authors": [], "repoFilter": [],
                                     "assignmentLogin": "assignee"}, pr))

    def test_invalid_filter_is_not_treated_as_allow_all(self):
        with self.assertRaises(ReviewError):
            allowed_pr({"authors": ["author"], "repoFilter": None,
                        "assignmentLogin": "assignee"}, {})

    def test_filter_and_assignment_use_metadata_not_ci_or_review_request(self):
        policy = {"authors": ["author"], "repoFilter": ["owner/repo"],
                  "assignmentLogin": "assignee"}
        pr = {"repo": "owner/repo", "author": "author", "state": "open",
              "draft": False, "assignees": ["assignee"]}
        self.assertTrue(allowed_pr(policy, pr))
        pr["repo"] = "owner/other"
        self.assertFalse(allowed_pr(policy, pr))

    def test_run_binding_prefers_native_issue_and_checks_status(self):
        run = {"id": "run", "companyId": "company", "agentId": "agent",
               "status": "succeeded", "nativeIssueId": "wrong",
               "issueId": "task"}
        with self.assertRaises(ReviewError):
            validate_run(run, "run", "company", "agent", "task", "succeeded")

    def test_result_requires_latest_revision_and_server_writer_run(self):
        task = {"id": "review", "companyId": "company", "parentId": "expert",
                "assigneeAgentId": "reviewer", "status": "done"}
        revision = {"id": "rev", "issueId": "review", "key": "result",
                    "companyId": "company", "isLatest": True, "latestRevisionId": "rev",
                    "createdByAgentId": "reviewer", "createdByUserId": None,
                    "createdByRunId": "run"}
        run = {"id": "run", "companyId": "company", "agentId": "reviewer",
               "issueId": "review", "status": "succeeded"}
        validate_result_revision(task, revision, run, "company", "expert", "reviewer",
                                 "result", "rev")
        revision["createdByAgentId"] = "other"
        with self.assertRaises(ReviewError):
            validate_result_revision(task, revision, run, "company", "expert", "reviewer",
                                     "result", "rev")

    def test_publication_key_ignores_base_and_changes_with_head(self):
        first = publication_key("company", "repo", 7, "head-1", 42)
        self.assertEqual(first, publication_key("company", "repo", 7, "head-1", 42))
        self.assertNotEqual(first, publication_key("company", "repo", 7, "head-2", 42))
        self.assertLessEqual(len(first), 64)

    def test_publication_key_accepts_numeric_repository_id(self):
        self.assertEqual(publication_key("company", 99, 7, "head", 42),
                         publication_key("company", "99", 7, "head", 42))

    def test_publisher_identity_requires_numeric_id_and_login(self):
        validate_publisher_identity({"id": 42, "login": "publisher"}, 42, "publisher")
        with self.assertRaises(ReviewError):
            validate_publisher_identity({"id": 43, "login": "publisher"}, 42, "publisher")

    def test_ledger_requires_company_project_and_no_assignee(self):
        issue = {"companyId": "company", "projectId": "project",
                 "assigneeAgentId": None, "assigneeUserId": None}
        validate_ledger_issue(issue, "company", "project")
        issue["assigneeUserId"] = "someone"
        with self.assertRaises(ReviewError):
            validate_ledger_issue(issue, "company", "project")

    def test_config_example_denies_all_and_validates(self):
        config = json.loads(EXAMPLE.read_text())
        validate_config(config)
        self.assertEqual(config["authors"], [])
        self.assertEqual(config["repoFilter"], [])
        self.assertTrue(config["expertAgentId"])
        self.assertTrue(config["reviewerAgentId"])

    def test_malformed_config_and_secret_field_fail_closed(self):
        config = json.loads(EXAMPLE.read_text())
        config["repoFilter"] = None
        with self.assertRaises(ReviewError):
            validate_config(config)
        config["repoFilter"] = []
        config["apiKey"] = "not-a-real-secret"
        with self.assertRaises(ReviewError):
            validate_config(config)

    def test_malformed_repo_override_is_rejected(self):
        config = json.loads(EXAMPLE.read_text())
        config["repoOverrides"] = {"owner/repo": {"reviewerAgentId": None}}
        with self.assertRaises(ReviewError):
            validate_config(config)

    def test_cli_rejects_duplicate_config_keys_without_echoing_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text('{"authors": [], "authors": ["placeholder"]}')
            result = subprocess.run(
                [sys.executable, "-B", "-m", "bootstrap.paperclip_review", "routine",
                 "--config", str(config)],
                capture_output=True, text=True, cwd=ROOT, check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("placeholder", result.stdout + result.stderr)

    def test_routine_cli_prints_paused_requests_without_running_them(self):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "bootstrap.paperclip_review", "routine", "--config", str(EXAMPLE)],
            capture_output=True, text=True, cwd=ROOT, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        requests = json.loads(result.stdout)
        routine = requests["routine"]
        self.assertEqual(routine["method"], "POST")
        self.assertEqual(routine["body"]["status"], "paused")
        self.assertEqual(routine["body"]["concurrencyPolicy"], "coalesce_if_active")
        self.assertEqual(routine["body"]["catchUpPolicy"], "skip_missed")
        self.assertEqual(routine["body"]["activityGatePolicy"], "always")
        self.assertEqual(routine["body"]["activityGateScope"], "company")
        trigger = requests["trigger"]
        self.assertEqual(trigger["method"], "POST")
        self.assertEqual(trigger["body"],
                         {"kind": "schedule", "cronExpression": "*/3 * * * *",
                          "timezone": "UTC", "enabled": False})


if __name__ == "__main__":
    unittest.main()
