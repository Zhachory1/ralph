"""Check the provisioned Comms seat's reply policy."""

import unittest

from test_provision_github_instructions import COMPANY_ID, COMPANY_PATH, OPENCODE, PROVISION, FakeBoard


class ProvisionCommsInstructionsTest(unittest.TestCase):
    def test_comms_validates_routine_chat_and_escalates_uncertain_sends(self):
        board = FakeBoard()
        models = {
            "Council Architecture": "anthropic/fixture",
            "Council Security": "google/fixture",
            "Council Scope": "openai/fixture",
        }

        PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, 500, 100,
                  reconcile=True, judge_models=models)

        comms = next(body for path, body, _method in board.calls
                     if path == f"{COMPANY_PATH}/agents" and body and body["name"] == "Comms")
        instructions = comms["instructionsBundle"]["files"]["AGENTS.md"]
        self.assertIn("Routine Chat replies", instructions)
        self.assertIn("important, questionable, or uncertain", instructions)
        self.assertIn("Director", instructions)
        self.assertIn("Gmail", instructions)
        self.assertIn("sending identity", instructions)


if __name__ == "__main__":
    unittest.main()
