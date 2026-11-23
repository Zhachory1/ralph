"""Check scheduled PR maintenance dispatch without GitHub or Paperclip writes."""

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

path = Path(__file__).resolve().parents[1] / "pr-maintenance-dispatch.py"
spec = importlib.util.spec_from_file_location("pr_maintenance_dispatch", path)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

HEAD = "a" * 40
BASE = "b" * 40


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.issues = []
        self.pr = {"repo": "example/project", "number": 7, "author": "human",
                   "head": HEAD, "base": BASE, "mergeable": "MERGEABLE"}
        self.snapshot = {"reviews": [], "threads": [], "checks": []}

    def board(self, path, method="GET", body=None):
        if method == "POST":
            issue = {"id": f"task-{len(self.issues)}", "companyId": "company", **body}
            self.issues.append(issue)
            return issue
        query = parse_qs(path.split("?", 1)[1])
        status = query.get("status", [""])[0]
        term = query.get("q", [""])[0]
        return [issue for issue in self.issues
                if issue["status"] in status.split(",") and term in issue["title"]]

    def scan(self):
        return worker.dispatch_maintenance(
            board=self.board, company_id="company", maintainer_id="maintainer",
            accounts=["human", "bot"], prs=[self.pr],
            read_feedback=lambda _pr: self.snapshot,
        )

    def test_new_review_creates_opencode_task(self):
        self.snapshot["reviews"] = [{"id": 9, "author": "reviewer", "body": "Fix this", "bot": False}]
        self.assertEqual(self.scan()[0]["status"], "dispatched")
        self.assertEqual(self.issues[0]["assigneeAgentId"], "maintainer")
        self.assertIn("example/project#7", self.issues[0]["description"])

    def test_failed_ci_creates_task(self):
        self.snapshot["checks"] = [{"name": "tests"}]
        self.assertEqual(self.scan()[0]["status"], "dispatched")

    def test_merge_conflict_creates_task(self):
        self.pr["mergeable"] = "CONFLICTING"
        self.assertEqual(self.scan()[0]["status"], "dispatched")

    def test_no_feedback_creates_no_task(self):
        self.assertEqual(self.scan()[0]["status"], "clean")
        self.assertEqual(self.issues, [])

    def test_same_feedback_does_not_start_another_session(self):
        self.snapshot["checks"] = [{"name": "tests"}]
        self.scan()
        self.issues[0]["status"] = "done"
        self.assertEqual(self.scan()[0]["status"], "existing")
        self.assertEqual(len(self.issues), 1)

    def test_new_feedback_waits_for_active_session(self):
        self.snapshot["checks"] = [{"name": "tests"}]
        self.scan()
        self.snapshot["reviews"] = [{"id": 10, "author": "reviewer", "body": "Another issue", "bot": False}]
        self.assertEqual(self.scan()[0]["status"], "active")
        self.assertEqual(len(self.issues), 1)

    def test_new_review_on_same_head_starts_another_session(self):
        self.snapshot["reviews"] = [{"id": 9, "author": "reviewer", "body": "First", "bot": False}]
        self.scan()
        self.issues[0]["status"] = "done"
        self.snapshot["reviews"] = [{"id": 10, "author": "reviewer", "body": "Second", "bot": False}]
        self.assertEqual(self.scan()[0]["status"], "dispatched")
        self.assertEqual(len(self.issues), 2)

    def test_new_bot_review_with_the_same_text_starts_another_session(self):
        self.snapshot["reviews"] = [{"id": 9, "author": "reviewer[bot]", "body": "Check this", "bot": True}]
        self.scan()
        self.issues[0]["status"] = "done"
        self.snapshot["reviews"][0]["id"] = 10
        self.assertEqual(self.scan()[0]["status"], "dispatched")

    def test_other_authors_are_not_maintained(self):
        self.pr["author"] = "someone-else"
        self.snapshot["checks"] = [{"name": "tests"}]
        self.assertEqual(self.scan(), [])
        self.assertEqual(self.issues, [])

    def test_both_configured_authors_are_in_scope(self):
        self.pr["author"] = "bot"
        self.snapshot["checks"] = [{"name": "tests"}]
        self.assertEqual(self.scan()[0]["status"], "dispatched")

    def test_dispatches_a_burst_without_a_per_scan_cap(self):
        prs = [{**self.pr, "number": number} for number in range(1, 101)]
        snapshot = {"reviews": [], "threads": [], "checks": [{"name": "tests"}]}
        result = worker.dispatch_maintenance(
            board=self.board, company_id="company", maintainer_id="maintainer",
            accounts=["human", "bot"], prs=prs, read_feedback=lambda _: snapshot,
        )
        self.assertEqual(len([row for row in result if row["status"] == "dispatched"]), 100)


class DiscoveryTests(unittest.TestCase):
    def test_discovers_prs_for_both_accounts(self):
        def gh(account, *args):
            if args[:2] == ("api", "user"):
                data = account
            elif "search/issues" in args:
                number = 1 if account == "human" else 2
                data = worker.json.dumps([{"total_count": 1, "incomplete_results": False,
                                           "items": [{"repository_url": "https://api.github.com/repos/example/project",
                                                      "number": number}]}])
            else:
                data = worker.json.dumps({"state": "OPEN", "author": {"login": account},
                                          "headRefOid": HEAD, "baseRefOid": BASE, "mergeable": "MERGEABLE"})
            return SimpleNamespace(returncode=0, stdout=data)

        prs = worker.discover_prs(["human", "bot"], gh)
        self.assertEqual([(pr["author"], pr["number"]) for pr in prs], [("human", 1), ("bot", 2)])

    def test_unreadable_pr_does_not_block_other_prs(self):
        def gh(account, *args):
            if args[:2] == ("api", "user"):
                return SimpleNamespace(returncode=0, stdout=account)
            if "search/issues" in args:
                data = [{"total_count": 2, "incomplete_results": False, "items": [
                    {"repository_url": "https://api.github.com/repos/example/project", "number": 1},
                    {"repository_url": "https://api.github.com/repos/example/project", "number": 2},
                ]}]
                return SimpleNamespace(returncode=0, stdout=worker.json.dumps(data))
            if args[2] == "1":
                return SimpleNamespace(returncode=1, stdout="")
            data = {"state": "OPEN", "author": {"login": account}, "headRefOid": HEAD,
                    "baseRefOid": BASE, "mergeable": "MERGEABLE"}
            return SimpleNamespace(returncode=0, stdout=worker.json.dumps(data))

        prs = worker.discover_prs(["human"], gh)
        self.assertIn("error", prs[0])
        self.assertEqual(prs[1]["number"], 2)


class TickTests(unittest.TestCase):
    def test_scan_creates_a_task_and_completes_its_own_task(self):
        fixture = DispatchTests()
        fixture.setUp()
        fixture.snapshot["checks"] = [{"name": "tests"}]
        updates = []

        def board(path, method="GET", body=None):
            if path == "heartbeat-runs/run":
                return {"id": "run", "companyId": "company", "agentId": "scanner", "issueId": "scan"}
            if path == "issues/scan":
                updates.append(body)
                return {"id": "scan", "status": body["status"]}
            return fixture.board(path, method, body)

        result = worker.run_tick(board=board, company_id="company", agent_id="scanner", run_id="run",
                                 maintainer_id="maintainer", accounts=["human", "bot"],
                                 discover=lambda: [fixture.pr], read_feedback=lambda _: fixture.snapshot)
        self.assertEqual(result, {"created": 1, "failed": 0})
        self.assertEqual(updates[0]["status"], "done")

    def test_failed_discovery_finishes_the_scan_task_for_the_next_tick(self):
        updates = []

        def board(path, method="GET", body=None):
            if path == "heartbeat-runs/run":
                return {"id": "run", "companyId": "company", "agentId": "scanner", "issueId": "scan"}
            updates.append((path, method, body))
            return {"id": "scan", "status": body["status"]}

        def discover():
            raise ValueError("GitHub unavailable")

        with self.assertRaisesRegex(ValueError, "GitHub unavailable"):
            worker.run_tick(board=board, company_id="company", agent_id="scanner", run_id="run",
                            maintainer_id="maintainer", accounts=["human", "bot"],
                            discover=discover, read_feedback=lambda _: None)
        self.assertEqual(updates[0][2]["status"], "done")
        self.assertIn("GitHub unavailable", updates[0][2]["comment"])


if __name__ == "__main__":
    unittest.main()
