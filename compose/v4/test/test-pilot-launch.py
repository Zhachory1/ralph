"""Check that launcher safety gates are enforced even in optimized Python."""

import subprocess
import runpy
import sys
import unittest
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[1] / "pilot-launch.py"


class LaunchTest(unittest.TestCase):
    def test_finish_records_done_only_after_verification(self):
        finish = runpy.run_path(str(LAUNCHER))["finish_issue"]
        issue = {"id": "child", "identifier": "SBX-2", "status": "in_progress",
                 "parentId": "intake", "companyId": "sandbox", "assigneeAgentId": "developer"}
        assignment = {"agentId": "developer", "invocationSource": "assignment", "status": "succeeded"}
        calls = []

        def board(path, body=None, method=None):
            calls.append((path, method))
            if path == "issues/child/runs":
                return [assignment]
            if method == "PATCH":
                issue["status"] = body["status"]
            return dict(issue)

        def verify(identifier):
            calls.append(("verify", identifier))
            raise ValueError("test failed")

        with self.assertRaisesRegex(ValueError, "test failed"):
            finish(board, "sandbox", "intake", "developer", issue, verify)
        self.assertNotIn(("issues/child", "PATCH"), calls)

        finish(board, "sandbox", "intake", "developer", issue,
               lambda identifier: calls.append(("verified", identifier)))
        self.assertEqual(calls[-3:], [("verified", "SBX-2"), ("issues/child", None), ("issues/child", "PATCH")])
        self.assertEqual(issue["status"], "done")

    def test_unexpected_preview_is_rejected_under_optimization(self):
        result = subprocess.run([sys.executable, "-O", "-c", """
import runpy, sys
preflight = runpy.run_path(sys.argv[1])["preflight"]
preflight.__globals__["cmd"] = lambda *args, **kwargs: (
    '[{"State": {"Running": true}, "Config": {"Image": "unapproved-preview"}}]'
)
preflight({})
""", str(LAUNCHER)], text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.strip(), "Expected the running pinned Paperclip preview")


if __name__ == "__main__":
    unittest.main()
