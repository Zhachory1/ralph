"""Exercise maintenance admission and safety without native or GitHub calls."""

import json
import subprocess
import unittest

from bootstrap import paperclip_maintain as maintain


class MaintenancePolicyTest(unittest.TestCase):
    def setUp(self):
        self.config = {
            "companyId": "company", "projectId": "project", "routineAgentId": "routine",
            "expertAgentId": "expert", "builderAgentId": "builder", "sreAgentId": "sre",
            "reviewerAgentId": "reviewer", "ledgerIssueId": "ledger",
            "authorLogin": "author", "authorAccountId": 7,
            "publisherLogin": "publisher", "publisherAccountId": 9, "repositories": ["owner/repo"],
        }
        self.pr = {
            "number": 1, "state": "open", "user": {"login": "Author", "id": 7},
            "base": {"repo": {"id": 13, "full_name": "owner/repo"}, "sha": "base", "ref": "main"},
            "head": {"repo": {"id": 13, "full_name": "owner/repo"}, "sha": "head", "ref": "branch"},
        }

    def test_eligible_requires_author_login_and_numeric_id(self):
        self.assertTrue(maintain.eligible(self.config, self.pr))
        self.pr["user"]["id"] = 8
        self.assertFalse(maintain.eligible(self.config, self.pr))

    def test_slot_key_is_stable_across_head_changes(self):
        key = maintain.slot_key("company", 13, 1)
        self.assertEqual(key, maintain.slot_key("company", 13, 1))
        self.assertNotEqual(key, maintain.slot_key("company", 14, 1))
        self.assertLessEqual(len(key), 64)

    def test_new_failed_check_attempt_is_not_suppressed_by_old_hold(self):
        first = {"id": 1, "name": "build", "status": "completed", "conclusion": "failure",
                 "head_sha": "head", "started_at": "then", "completed_at": "now", "app": {"id": 1}}
        second = dict(first, id=2)
        old, _ = maintain.failed_checks([first], [])
        new, _ = maintain.failed_checks([first, second], [])
        self.assertNotEqual(old, new)
        evidence = {"feedback": [], "failed": new, "pending": False, "conflict": False}
        self.assertTrue(maintain.needs_job(evidence, {"handled": [], "holds": old}))

    def test_new_failed_status_attempt_changes_failure_version(self):
        first = {"id": 1, "context": "test", "state": "failure", "created_at": "then"}
        second = dict(first, id=2)
        self.assertNotEqual(maintain.failed_checks([], [first])[0],
                            maintain.failed_checks([], [first, second])[0])

    def test_updated_failed_check_changes_failure_version(self):
        first = {"id": 1, "name": "build", "status": "completed", "conclusion": "failure",
                 "head_sha": "head1", "completed_at": "then", "app": {"id": 1}}
        second = dict(first, head_sha="head2", completed_at="now")
        self.assertNotEqual(maintain.failed_checks([first], [])[0],
                            maintain.failed_checks([second], [])[0])

    def test_comment_summary_and_resolved_thread_new_comment_are_work(self):
        feedback = maintain.feedback_versions(
            [{"id": 31, "state": "COMMENTED", "body": "Please fix", "submitted_at": "time"}],
            [{"id": "thread", "isResolved": True, "comments": {
                "nodes": [{"id": "comment", "body": "New issue", "updatedAt": "later"}]}}],
            [],
        )
        self.assertEqual(len(feedback), 2)

    def test_only_confirmed_receipt_suppresses_own_comment(self):
        thread = {"id": "thread", "isResolved": False, "comments": {"nodes": [
            {"id": "comment", "body": "Please fix", "updatedAt": "time"},
        ]}}
        self.assertEqual(len(maintain.feedback_versions([], [thread], [])), 1)
        self.assertEqual(maintain.feedback_versions([], [thread], ["comment"]), [])

    def test_failure_is_not_handled_by_unchanged_input(self):
        evidence = {"feedback": [], "failed": ["build"], "conflict": False, "pending": False}
        self.assertTrue(maintain.needs_job(evidence, {"handled": [], "holds": []}))
        self.assertFalse(maintain.needs_job(evidence, {"handled": [], "holds": ["build"]}))

    def test_pending_only_and_handled_feedback_start_no_job(self):
        evidence = {"feedback": ["feedback-v1"], "failed": [], "conflict": False, "pending": True}
        self.assertFalse(maintain.needs_job(evidence, {"handled": ["feedback-v1"], "holds": []}))

    def test_latest_check_attempt_and_commit_status_win(self):
        checks = [
            {"id": 1, "name": "build", "status": "completed", "conclusion": "failure",
             "started_at": "2026-01-01T00:00:00Z", "app": {"id": 1}},
            {"id": 2, "name": "build", "status": "completed", "conclusion": "success",
             "started_at": "2026-01-02T00:00:00Z", "app": {"id": 1}},
        ]
        statuses = [{"id": 1, "context": "test", "state": "failure"},
                    {"id": 2, "context": "test", "state": "success"}]
        self.assertEqual(maintain.failed_checks(checks, statuses), ([], False))

    def test_latest_queued_check_with_no_start_is_pending_not_failure(self):
        checks = [
            {"id": 1, "name": "build", "status": "completed", "conclusion": "failure",
             "started_at": "2026-01-01T00:00:00Z", "app": {"id": 1}},
            {"id": 2, "name": "build", "status": "queued", "conclusion": None,
             "started_at": None, "app": {"id": 1}},
        ]
        self.assertEqual(maintain.failed_checks(checks, []), ([], True))

    def test_routine_is_paused_and_schedule_disabled(self):
        request = maintain.routine_requests(self.config, "/tmp/example.json")
        self.assertEqual(request["routine"]["body"]["status"], "paused")
        self.assertEqual(request["routine"]["body"]["concurrencyPolicy"], "skip_if_active")
        self.assertFalse(request["trigger"]["body"]["enabled"])

    def test_config_rejects_secret_and_duplicate_fields(self):
        with self.assertRaises(maintain.MaintenanceError):
            maintain.validate_config(dict(self.config, token="placeholder"))
        with self.assertRaises(maintain.MaintenanceError):
            json.loads('{"companyId":"a","companyId":"b"}', object_pairs_hook=maintain.unique_object)

    def test_routine_identity_cannot_be_an_llm_role(self):
        for role in ("expertAgentId", "builderAgentId", "sreAgentId", "reviewerAgentId"):
            with self.subTest(role=role), self.assertRaises(maintain.MaintenanceError):
                maintain.validate_config(dict(self.config, **{role: "routine"}))


class GitHubReadTest(unittest.TestCase):
    def test_launcher_is_required_and_ambient_tokens_cannot_authorize(self):
        with self.assertRaises(maintain.MaintenanceError):
            maintain.GitHubCLI(env={"GH_TOKEN": "ambient"})

    def test_identity_uses_managed_launcher_and_masks_failure(self):
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 1, "", "secret from auth")

        gh = maintain.GitHubCLI(runner=runner, env={
            "PAPERCLIP_GITHUB_BROKER_URL": "https://broker.example",
            "PAPERCLIP_GITHUB_BROKER_TOKEN": "placeholder",
            "PAPERCLIP_GITHUB_LAUNCHER_DIR": "/launcher",
            "GH_TOKEN": "ambient",
        })
        with self.assertRaises(maintain.MaintenanceError) as failure:
            gh.identity()
        self.assertNotIn("secret", str(failure.exception))
        self.assertEqual(calls[0][0], ["/launcher/gh", "api", "--hostname", "github.com", "user"])
        self.assertNotIn("GH_TOKEN", calls[0][1]["env"])
        self.assertGreater(calls[0][1]["timeout"], 0)

    def test_search_fails_closed_when_provider_reports_incomplete(self):
        gh = object.__new__(maintain.GitHubCLI)
        gh._rest = lambda *args: {"total_count": 1, "incomplete_results": True, "items": []}
        with self.assertRaises(maintain.MaintenanceError):
            gh.open_prs("author")

    def test_search_rejects_api_cap_even_with_exact_count(self):
        gh = object.__new__(maintain.GitHubCLI)
        calls = []

        def search(path):
            calls.append(path)
            first = (len(calls) - 1) * 100 + 1
            items = [{"number": number, "html_url": f"https://github.com/owner/repo/pull/{number}"}
                     for number in range(first, first + 100)]
            return {"total_count": 1000, "incomplete_results": False, "items": items}

        def unexpected_read(repo, number):
            self.fail("Capped search must not fetch pull requests")

        gh._rest = search
        gh.read_pr = unexpected_read
        with self.assertRaises(maintain.MaintenanceError) as failure:
            gh.open_prs("author")
        self.assertEqual(len(calls), 1)
        self.assertIn("result window", str(failure.exception))

    def test_search_rejects_count_above_result_window(self):
        gh = object.__new__(maintain.GitHubCLI)
        gh._rest = lambda path: {
            "total_count": 1001, "incomplete_results": False,
            "items": [{"number": 1, "html_url": "https://github.com/owner/repo/pull/1"}],
        }
        with self.assertRaisesRegex(maintain.MaintenanceError, "result window"):
            gh.open_prs("author")

    def test_launcher_unavailable_diagnostic_rejects_successful_public_result(self):
        def runner(args, **kwargs):
            return subprocess.CompletedProcess(
                args, 0, '{"id":1,"login":"author"}',
                "Paperclip: GitHub access unavailable. Continuing without GitHub credentials.",
            )

        gh = maintain.GitHubCLI(runner=runner, env={
            "PAPERCLIP_GITHUB_BROKER_URL": "https://broker.example",
            "PAPERCLIP_GITHUB_BROKER_TOKEN": "placeholder",
            "PAPERCLIP_GITHUB_LAUNCHER_DIR": "/launcher",
        })
        with self.assertRaises(maintain.MaintenanceError):
            gh.identity()

    def test_snapshot_preserves_resolved_comments_and_pending_mergeability(self):
        gh = object.__new__(maintain.GitHubCLI)
        gh.read_pr = lambda repo, number: {
            "number": number, "head": {"sha": "head"}, "mergeable": None,
        }
        gh._pages = lambda endpoint: (
            [{"id": 7, "state": "COMMENTED", "body": "Review", "submitted_at": "now"}]
            if endpoint.endswith("/reviews") else []
        )
        gh._threads = lambda repo, number: [{
            "id": "thread", "isResolved": True,
            "comments": {"nodes": [{"id": "comment", "body": "Fix", "updatedAt": "now"}]},
        }]
        gh._checks = lambda repo, sha: ([], [])
        result = gh.snapshot("owner/repo", 1)
        self.assertEqual(len(result["feedback"]), 2)
        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(result["pending"])
        self.assertFalse(result["conflict"])

    def test_confirmed_numeric_comment_receipt_suppresses_graphql_comment(self):
        thread = {"id": "thread", "comments": {"nodes": [{
            "id": "node", "databaseId": 42, "body": "receipt", "updatedAt": "now",
        }]}}
        self.assertEqual(maintain.feedback_versions([], [thread], [42]), [])

    def test_snapshot_refuses_head_drift(self):
        gh = object.__new__(maintain.GitHubCLI)
        responses = iter([{"head": {"sha": "old"}, "mergeable": True},
                          {"head": {"sha": "new"}, "mergeable": True}])
        gh.read_pr = lambda repo, number: next(responses)
        gh._pages = lambda endpoint: []
        gh._threads = lambda repo, number: []
        gh._checks = lambda repo, sha: ([], [])
        with self.assertRaises(maintain.MaintenanceError):
            gh.snapshot("owner/repo", 1)

    def test_nested_thread_comments_are_fully_paginated(self):
        gh = object.__new__(maintain.GitHubCLI)

        def graphql(query, variables):
            if "reviewThreads" in query:
                return {"repository": {"pullRequest": {"reviewThreads": {
                    "nodes": [{"id": "thread", "isResolved": True}],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }}}}
            second = variables["cursor"] == "next"
            return {"node": {"comments": {
                "nodes": [{"id": "two" if second else "one", "body": "feedback", "updatedAt": "now"}],
                "pageInfo": {"hasNextPage": not second, "endCursor": None if second else "next"},
            }}}

        gh._graphql = graphql
        threads = gh._threads("owner/repo", 1)
        self.assertEqual([node["id"] for node in threads[0]["comments"]["nodes"]], ["one", "two"])

    def test_graphql_repeated_cursor_is_rejected(self):
        gh = object.__new__(maintain.GitHubCLI)
        gh._graphql = lambda query, variables: {
            "nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "same"},
        }
        with self.assertRaises(maintain.MaintenanceError):
            gh._connection("query", {}, lambda result: result)

    def test_review_ids_do_not_collide_with_confirmed_comment_ids(self):
        reviews = [{"id": 42, "state": "COMMENTED", "body": "Fix", "submitted_at": "now"}]
        self.assertEqual(len(maintain.feedback_versions(reviews, [], [42])), 1)


class IntakeTest(unittest.TestCase):
    def test_intake_rejects_non_process_actor(self):
        from evals.test_paperclip_maintain_workflow import GitHub, Native
        client = Native()
        client.get_me = lambda: {"id": "routine", "companyId": "company", "adapterType": "claude_local"}
        with self.assertRaises(maintain.MaintenanceError):
            maintain.MaintenanceWorkflow(client, GitHub(), client.config)

    def test_poll_returns_evidence_without_creating_tasks(self):
        from evals.test_paperclip_maintain_workflow import GitHub, Native
        client = Native()
        result = maintain.MaintenanceWorkflow(client, GitHub(), client.config).poll()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(client.created, [])
        self.assertEqual(client.slot()["repoId"], 13)


if __name__ == "__main__":
    unittest.main()
