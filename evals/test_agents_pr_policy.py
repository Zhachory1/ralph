"""Check the repository's pull-request instructions for agents."""

import re
import unittest
from pathlib import Path


class AgentPullRequestPolicyTest(unittest.TestCase):
    def test_pr_policy_covers_creation_size_tests_and_merge_authority(self):
        instructions = (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text()
        policy = re.search(r"(?ms)^- \*\*Pull requests are cheap\.\*\*(.*?)(?=^- |\Z)", instructions)
        self.assertIsNotNone(policy, "The pull-request policy bullet is missing")
        text = re.sub(r"\s+", " ", policy.group(1))
        self.assertIn("Open focused PRs for review", text)
        self.assertIn("fewer than 750 changed lines", text)
        self.assertIn("include tests", text)
        self.assertIn("Never merge unless the user explicitly tells you to", text)
        self.assertIn("Use protected branches and scoped credentials", text)


if __name__ == "__main__":
    unittest.main()
