"""Check the repository's pull-request instructions for agents."""

import re
import unittest
from pathlib import Path


class AgentPullRequestPolicyTest(unittest.TestCase):
    def test_pr_policy_covers_creation_size_tests_and_merge_authority(self):
        instructions = re.sub(r"\s+", " ", (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text())
        self.assertIn("Pull requests are cheap", instructions)
        self.assertIn("fewer than 750 changed lines", instructions)
        self.assertIn("include tests", instructions)
        self.assertIn("Never merge unless the user explicitly tells you to", instructions)


if __name__ == "__main__":
    unittest.main()
