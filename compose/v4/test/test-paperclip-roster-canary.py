"""Check that roster handoff failure evidence is bounded and safe for public CI."""

import io
import json
import runpy
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path

CANARY = runpy.run_path(str(Path(__file__).with_name("paperclip-roster-canary.py")))


class ReviewEvidenceTests(unittest.TestCase):
    def test_distinguishes_committed_reassignment_receipt_and_unclaimed_run(self):
        sensitive = "must-not-be-printed"
        replies = {
            "issues/fixture/activity?view=recent": [
                {"action": "issue.updated", "createdAt": "2026-11-23T01:00:00.000Z", "details": {
                    "changes": {"assigneeAgentId": {"from": "developer", "to": "reviewer"}},
                    "title": sensitive,
                }},
            ],
            "issues/fixture/diagnostics/wakes": {
                "events": [
                    {"kind": "wake_request", "agentId": "reviewer", "source": "automation"}
                    for _ in range(5)
                ] + [
                    {"kind": "wake_request", "agentId": "developer", "status": "claimed", "claimedAt": "now"},
                    {"kind": "wake_request", "agentId": "reviewer", "status": "deferred_issue_execution",
                     "source": "assignment", "reason": "other", "requestedAt": "2026-11-23T01:00:01.000Z",
                     "runId": sensitive, "error": sensitive},
                    {"kind": "wake_request", "agentId": "reviewer", "status": "completed",
                     "source": "assignment", "reason": "other", "requestedAt": "2026-11-23T00:59:59.000Z",
                     "runId": "different run", "claimedAt": "then"},
                ],
                "truncated": False,
            },
            "issues/fixture/runs?view=recent": [
                {"agentId": "developer", "status": "cancelled", "startedAt": "then",
                 "errorCode": "issue_reassigned", "error": sensitive},
                {"agentId": "reviewer", "status": "queued", "runId": sensitive},
            ],
        }
        output = io.StringIO()
        with redirect_stdout(output):
            CANARY["print_review_handoff_evidence"](
                replies.__getitem__, "issues/fixture",
                {"assigneeAgentId": "reviewer", "status": "in_review",
                 "executionState": {"currentStageType": "review"}, "title": sensitive},
                "developer", "reviewer",
            )
        evidence = json.loads(output.getvalue().removeprefix("Roster review evidence: "))
        self.assertEqual(evidence["assignee"], "reviewer")
        self.assertTrue(evidence["reviewerReassignmentInRecentActivity"])
        self.assertTrue(evidence["inReviewStage"])
        self.assertEqual(
            evidence["reviewerAssignmentWakes"],
            [{"status": "deferred_issue_execution", "atOrAfterReassignment": True, "linked": True,
              "claimed": False, "linkedRecentRunStarted": False},
             {"status": "completed", "atOrAfterReassignment": False, "linked": True, "claimed": True,
              "linkedRecentRunStarted": None}],
        )
        self.assertEqual(evidence["recentReviewerRuns"], [{"status": "queued", "started": False}])
        self.assertTrue(evidence["developerCancellationInRecentRuns"])
        self.assertNotIn(sensitive, output.getvalue())

    def test_unavailable_reads_do_not_report_absence_or_print_errors(self):
        def board(path):
            if path.endswith("/runs?view=recent"):
                return [{"agentId": "reviewer", "status": "unexpected private value", "startedAt": "now"}]
            if path.endswith("/diagnostics/wakes"):
                return {"unexpected": "private data", "truncated": "private"}
            raise subprocess.TimeoutExpired(["private-value"], 1)

        output = io.StringIO()
        with redirect_stdout(output):
            CANARY["print_review_handoff_evidence"](
                board, "issues/fixture", {"status": "blocked", "assigneeAgentId": "developer"}, "developer", "reviewer",
            )
        evidence = json.loads(output.getvalue().removeprefix("Roster review evidence: "))
        self.assertIsNone(evidence["reviewerAssignmentWakes"])
        self.assertIsNone(evidence["reviewerReassignmentInRecentActivity"])
        self.assertIsNone(evidence["wakeHistoryTruncated"])
        self.assertEqual(evidence["recentReviewerRuns"], [{"status": "other", "started": True}])
        self.assertNotIn("private", output.getvalue())


if __name__ == "__main__":
    unittest.main()
