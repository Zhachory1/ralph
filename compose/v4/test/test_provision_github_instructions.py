"""Focused checks for the provisioned SWE GitHub guidance."""

import copy
import io
import runpy
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch


PROVISIONER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "provision-paperclip.py"))
PROVISION = PROVISIONER["provision"]
SEATS = PROVISIONER["SEATS"]
WORKFLOW_SEATS = PROVISIONER["WORKFLOW_SEATS"]
COMPANY_ID = "00000000-0000-4000-8000-000000000001"
COMPANY_PATH = f"companies/{COMPANY_ID}"
OPENCODE = {"model": "fixture/small"}


class FakeBoard:
    def __init__(self, existing=None, policies=None):
        self.existing = copy.deepcopy(existing or [])
        self.policies = copy.deepcopy(policies or [])
        self.calls = []
        self.created = 0

    def __call__(self, path, body=None, method=None):
        self.calls.append((path, body, method))
        if path == f"{COMPANY_PATH}/agents" and body is None:
            return self.existing
        if path == f"{COMPANY_PATH}/budgets/overview":
            return {"policies": self.policies}
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
    def test_opencode_roster_can_use_advisory_council_without_spend_caps(self):
        board = FakeBoard()
        config = {"model": "fixture/normal", "dangerouslySkipPermissions": False}
        models = {"Reviewer": "fixture/high", "Comms": "fixture/throughput"}
        judges = {name: "fixture/high" for name in (
            "Council Architecture", "Council Security", "Council Scope",
        )}

        PROVISION(board, COMPANY_ID, "Governance manifest", config, None, None,
                  reconcile=True, judge_models=judges, seat_models=models, advisory_council=True)

        created = {
            body["name"]: body for path, body, _method in board.calls
            if path == f"{COMPANY_PATH}/agents" and body
        }
        self.assertEqual(len(SEATS) + len(WORKFLOW_SEATS), len(created))
        self.assertTrue(all(seat["adapterType"] == "opencode_local" for seat in created.values()))
        self.assertEqual("fixture/high", created["Reviewer"]["adapterConfig"]["model"])
        self.assertEqual("fixture/throughput", created["Comms"]["adapterConfig"]["model"])
        self.assertEqual("fixture/normal", created["SRE"]["adapterConfig"]["model"])
        for name in judges:
            self.assertEqual("fixture/high", created[name]["adapterConfig"]["model"])
            self.assertIn("feedback is advisory", created[name]["instructionsBundle"]["files"]["AGENTS.md"])
        self.assertEqual({"model": "fixture/normal", "dangerouslySkipPermissions": False}, config)
        self.assertFalse(any("/budgets/" in path and body is not None for path, body, _method in board.calls))

    def test_council_without_diversity_requires_advisory_mode(self):
        board = FakeBoard()
        judges = {name: "fixture/high" for name in (
            "Council Architecture", "Council Security", "Council Scope",
        )}

        with self.assertRaisesRegex(ValueError, "distinct provider families"):
            PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, None, None,
                      reconcile=True, judge_models=judges)
        self.assertFalse(any(body is not None for _path, body, _method in board.calls))

    def test_existing_spend_cap_is_preserved_when_no_cap_is_requested(self):
        board = FakeBoard(policies=[{"isActive": True, "hardStopEnabled": True}])

        with self.assertRaisesRegex(ValueError, "Existing spend caps are preserved"):
            PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, None, None)

        self.assertFalse(any(body is not None for _path, body, _method in board.calls))

    def test_reconcile_preserves_existing_hermes_seat_on_adapter_conflict(self):
        existing = existing_roster()
        existing[0]["adapterType"] = "hermes_local"
        board = FakeBoard(existing)
        judges = {
            "Council Architecture": "first/model",
            "Council Security": "second/model",
            "Council Scope": "third/model",
        }

        with self.assertRaisesRegex(ValueError, "adapter"):
            PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, 500, 100,
                      reconcile=True, judge_models=judges)
        self.assertFalse(any(body is not None for _path, body, _method in board.calls))

    def test_reconcile_rejects_unblocked_existing_review_worker(self):
        existing = existing_roster()
        existing.append({
            "id": "existing-lens", "name": "Review Lens Generalist", "role": "general",
            "adapterType": "opencode_local", "adapterConfig": OPENCODE,
            "reportsTo": existing[0]["id"], "permissions": {"canCreateAgents": False},
            "runtimeConfig": {"heartbeat": {"enabled": False}},
        })
        board = FakeBoard(existing)
        judges = {
            "Council Architecture": "first/model",
            "Council Security": "second/model",
            "Council Scope": "third/model",
        }

        with self.assertRaisesRegex(ValueError, "assignment block"):
            PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, 500, 100,
                      reconcile=True, judge_models=judges)
        self.assertFalse(any(body is not None for _path, body, _method in board.calls))

    def test_partial_budget_and_unknown_seat_model_are_rejected(self):
        board = FakeBoard()
        with self.assertRaisesRegex(ValueError, "budgets"):
            PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, 500, None)
        with self.assertRaisesRegex(ValueError, "known seats"):
            PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, None, None,
                      seat_models={"Not a seat": "fixture/model"})
        self.assertEqual([], board.calls)

    def test_cli_requires_an_explicit_spend_cap_choice(self):
        required = ["provision-paperclip.py", "http://127.0.0.1:1", COMPANY_ID, "manifest", "config"]
        for extra, message in (
            ([], "Provide both budget amounts"),
            (["--no-spend-caps", "--agent-cents", "100"], "cannot be combined"),
        ):
            output = io.StringIO()
            with self.subTest(extra=extra), patch.object(sys, "argv", required + extra), redirect_stderr(output):
                with self.assertRaises(SystemExit) as error:
                    PROVISIONER["main"]()
            self.assertEqual(2, error.exception.code)
            self.assertIn(message, output.getvalue())

    def test_review_roster_keeps_specialist_lenses_and_synthesis(self):
        board = FakeBoard()
        judges = {
            "Council Architecture": "first/model",
            "Council Security": "second/model",
            "Council Scope": "third/model",
        }

        PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, 500, 100,
                  reconcile=True, judge_models=judges)

        created = {
            body["name"]: body for path, body, _method in board.calls
            if path == f"{COMPANY_PATH}/agents" and body
        }
        for name in ("Reviewer", "Review Workflow Manager", "Review Lens Generalist",
                     "Review Lens Reliability", "Review Lens MVP", "Review Lens Security", "Review Synthesis"):
            self.assertIn(name, created)
            self.assertEqual("opencode_local", created[name]["adapterType"])
        for name in ("Review Lens Generalist", "Review Lens Reliability", "Review Lens MVP",
                     "Review Lens Security", "Review Synthesis"):
            self.assertTrue(created[name]["permissions"]["authorizationPolicy"]["protectedAgent"]["blockAssignment"])
        self.assertNotIn("authorizationPolicy", created["Reviewer"]["permissions"])

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
