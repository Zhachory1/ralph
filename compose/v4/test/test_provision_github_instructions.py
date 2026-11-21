"""Focused checks for the provisioned SWE GitHub guidance."""

import copy
import runpy
import unittest
from pathlib import Path


PROVISIONER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "provision-paperclip.py"))
PROVISION = PROVISIONER["provision"]
SEATS = PROVISIONER["SEATS"]
WORKFLOW_SEATS = PROVISIONER["WORKFLOW_SEATS"]
COMPANY_ID = "00000000-0000-4000-8000-000000000001"
COMPANY_PATH = f"companies/{COMPANY_ID}"
OPENCODE = {"model": "fixture/small"}


class FakeBoard:
    def __init__(self, existing=None):
        self.existing = copy.deepcopy(existing or [])
        self.calls = []
        self.created = 0

    def __call__(self, path, body=None, method=None):
        self.calls.append((path, body, method))
        if path == f"{COMPANY_PATH}/agents" and body is None:
            return self.existing
        if path == f"{COMPANY_PATH}/budgets/overview":
            return {"policies": []}
        if path.endswith("/budgets/policies") or path.endswith("/permissions"):
            return {}
        if path == f"{COMPANY_PATH}/agents" and body is not None:
            self.created += 1
            return {"id": f"new-seat-{self.created}"}
        raise AssertionError(f"Unexpected board request: {method} {path}")


def existing_roster():
    ids = {name: f"existing-{index}" for index, (name, *_rest) in enumerate(SEATS, start=1)}
    roster = []
    for name, role, adapter, _duty in SEATS:
        roster.append({
            "id": ids[name],
            "name": name,
            "role": role,
            "adapterType": adapter,
            "adapterConfig": OPENCODE if adapter == "opencode_local" else {"hermesCommand": "/opt/hermes/bin/hermes"},
            "reportsTo": None if name == "Orchestrator" else ids["Orchestrator"],
            "permissions": {"canCreateAgents": False},
            "runtimeConfig": {"heartbeat": {"enabled": False}},
            "instructionsBundle": {"files": {"AGENTS.md": f"Existing {name} instructions"}},
        })
    return roster


class ProvisionGithubInstructionsTest(unittest.TestCase):
    def test_new_swe_instructions_use_an_existing_gh_token(self):
        board = FakeBoard()

        PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, 500, 100)

        swe = next(body for path, body, _method in board.calls
                   if path == f"{COMPANY_PATH}/agents" and body and body["name"] == "SWE")
        instructions = swe["instructionsBundle"]["files"]["AGENTS.md"]
        self.assertIn("When GH_TOKEN is already configured for your seat", instructions)
        self.assertIn("use it with git and gh in the terminal", instructions)
        self.assertIn("instead of requesting a separate GitHub connection", instructions)

    def test_reconciliation_does_not_write_existing_seat_instructions(self):
        existing = existing_roster()
        original_instructions = {
            seat["id"]: seat["instructionsBundle"] for seat in existing
        }
        board = FakeBoard(existing)
        judge_models = {
            "Council Architecture": "anthropic/fixture",
            "Council Security": "google/fixture",
            "Council Scope": "openai/fixture",
        }

        PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, 500, 100,
                  reconcile=True, judge_models=judge_models)

        self.assertEqual(
            original_instructions,
            {seat["id"]: seat["instructionsBundle"] for seat in board.existing},
        )
        existing_ids = set(original_instructions)
        existing_seat_writes = [
            path for path, body, _method in board.calls
            if body is not None and any(path.startswith(f"agents/{seat_id}/") or path == f"agents/{seat_id}"
                                        for seat_id in existing_ids)
        ]
        self.assertEqual([], existing_seat_writes)


if __name__ == "__main__":
    unittest.main()
