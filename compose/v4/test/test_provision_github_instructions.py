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
        if path.startswith("agents/") and body is None:
            agent_id = path.split("/")[1]
            seat = next((agent for agent in self.existing if agent["id"] == agent_id), None)
            if seat and "/instructions-bundle/file?" in path:
                return {"content": seat["instructionsBundle"]["files"]["AGENTS.md"]}
            if seat and path == f"agents/{agent_id}":
                return {**seat, "access": {"taskAssignSource": "explicit_grant", "canAssignTasks": True}}
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


def roster_with_manager(instructions):
    roster = existing_roster()
    roster.append({
        "id": "existing-manager", "name": "Feature Workflow Manager", "role": "general",
        "adapterType": "opencode_local", "adapterConfig": OPENCODE,
        "reportsTo": roster[0]["id"], "permissions": {"canCreateAgents": False},
        "runtimeConfig": {"heartbeat": {"enabled": False}},
        "instructionsBundle": {"files": {"AGENTS.md": instructions}},
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
        manager = created["Feature Workflow Manager"]["instructionsBundle"]["files"]["AGENTS.md"]
        self.assertIn("Council findings are advisory", manager)
        self.assertNotIn("all three judges supported", manager)
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

    def test_routed_models_from_the_same_family_require_advisory_mode(self):
        board = FakeBoard()
        judges = {
            "Council Architecture": "openai/gpt-6.2-sol",
            "Council Security": "openrouter/openai/gpt-6.1-terra",
            "Council Scope": "anthropic/claude-fixture",
        }

        with self.assertRaisesRegex(ValueError, "distinct provider families"):
            PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, None, None,
                      reconcile=True, judge_models=judges)
        self.assertFalse(any(body is not None for _path, body, _method in board.calls))

    def test_unknown_gateway_cannot_claim_three_family_diversity(self):
        board = FakeBoard()
        judges = {
            "Council Architecture": "gateway/openai/gpt-6.2-sol",
            "Council Security": "anthropic/claude-fixture",
            "Council Scope": "google/gemini-fixture",
        }

        with self.assertRaisesRegex(ValueError, "distinct provider families"):
            PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, None, None,
                      reconcile=True, judge_models=judges)
        self.assertFalse(any(body is not None for _path, body, _method in board.calls))

    def test_provider_prefix_cannot_disguise_model_family(self):
        board = FakeBoard()
        judges = {
            "Council Architecture": "openai/gpt-6.2-sol",
            "Council Security": "anthropic/gpt-6.1-terra",
            "Council Scope": "google/gemini-fixture",
        }

        with self.assertRaisesRegex(ValueError, "distinct provider families"):
            PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, None, None,
                      reconcile=True, judge_models=judges)
        self.assertFalse(any(body is not None for _path, body, _method in board.calls))

    def test_unrecognized_openai_model_cannot_claim_a_known_family(self):
        board = FakeBoard()
        judges = {
            "Council Architecture": "openai/o3unknown",
            "Council Security": "anthropic/claude-fixture",
            "Council Scope": "google/gemini-fixture",
        }

        with self.assertRaisesRegex(ValueError, "distinct provider families"):
            PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, None, None,
                      reconcile=True, judge_models=judges)
        self.assertFalse(any(body is not None for _path, body, _method in board.calls))

    def test_existing_manager_requires_advisory_guidance_before_reconciliation(self):
        board = FakeBoard(roster_with_manager("Require unanimous Council support."))
        judges = {name: "openai/gpt-6.2-sol" for name in (
            "Council Architecture", "Council Security", "Council Scope",
        )}

        with self.assertRaisesRegex(ValueError, "advisory"):
            PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, None, None,
                      reconcile=True, judge_models=judges, advisory_council=True)
        self.assertFalse(any(body is not None for _path, body, _method in board.calls))

    def test_reconciliation_preserves_manager_with_advisory_guidance(self):
        board = FakeBoard(roster_with_manager(
            "coordinate the advisory Council. " + PROVISIONER["ADVISORY_COUNCIL_GATE"]))
        judges = {name: "openai/gpt-6.2-sol" for name in (
            "Council Architecture", "Council Security", "Council Scope",
        )}

        roster = PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, None, None,
                           reconcile=True, judge_models=judges, advisory_council=True)

        self.assertEqual("existing-manager", roster["Feature Workflow Manager"])
        self.assertFalse(any(path.startswith("agents/existing-manager") and body is not None
                             for path, body, _method in board.calls))

    def test_reconciliation_rejects_conflicting_council_guidance(self):
        instructions = PROVISIONER["ADVISORY_COUNCIL_GATE"] + PROVISIONER["COUNCIL_GATE"]
        board = FakeBoard(roster_with_manager(instructions))
        judges = {name: "openai/gpt-6.2-sol" for name in (
            "Council Architecture", "Council Security", "Council Scope",
        )}

        with self.assertRaisesRegex(ValueError, "Council guidance"):
            PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, None, None,
                      reconcile=True, judge_models=judges, advisory_council=True)
        self.assertFalse(any(body is not None for _path, body, _method in board.calls))

    def test_reconciliation_rejects_advisory_manager_in_nonadvisory_mode(self):
        board = FakeBoard(roster_with_manager(PROVISIONER["ADVISORY_COUNCIL_GATE"]))
        judges = {
            "Council Architecture": "openai/gpt-fixture",
            "Council Security": "anthropic/claude-fixture",
            "Council Scope": "google/gemini-fixture",
        }

        with self.assertRaisesRegex(ValueError, "Council guidance"):
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
            "Council Architecture": "openai/gpt-fixture",
            "Council Security": "anthropic/claude-fixture",
            "Council Scope": "google/gemini-fixture",
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
            "Council Architecture": "openai/gpt-fixture",
            "Council Security": "anthropic/claude-fixture",
            "Council Scope": "google/gemini-fixture",
        }

        with self.assertRaisesRegex(ValueError, "assignment block"):
            PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, 500, 100,
                      reconcile=True, judge_models=judges)
        self.assertFalse(any(body is not None for _path, body, _method in board.calls))

    def test_reconcile_preserves_explicitly_released_review_worker(self):
        existing = existing_roster()
        released = {
            "id": "existing-lens", "name": "Review Lens Generalist", "role": "general",
            "adapterType": "opencode_local", "adapterConfig": {"model": "fixture/previous"},
            "reportsTo": existing[0]["id"],
            "permissions": {"canCreateAgents": False, "authorizationPolicy": {
                "protectedAgent": {"blockAssignment": False},
            }},
            "runtimeConfig": {"heartbeat": {"enabled": False}},
        }
        existing.append(released)
        board = FakeBoard(existing)
        judges = {
            "Council Architecture": "openrouter/openai/gpt-6.2-sol",
            "Council Security": "anthropic/claude-fixture",
            "Council Scope": "google/gemini-fixture",
        }

        roster = PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, 500, 100,
                           reconcile=True, judge_models=judges)

        self.assertEqual("existing-lens", roster["Review Lens Generalist"])
        self.assertEqual(released, board.existing[-1])
        self.assertFalse(any(path.startswith("agents/existing-lens") and body is not None
                             for path, body, _method in board.calls))

    def test_reconcile_rejects_an_explicit_model_change(self):
        board = FakeBoard(existing_roster())
        judges = {
            "Council Architecture": "openai/gpt-fixture",
            "Council Security": "anthropic/claude-fixture",
            "Council Scope": "google/gemini-fixture",
        }

        with self.assertRaisesRegex(ValueError, "Conflicting model"):
            PROVISION(board, COMPANY_ID, "Governance manifest", OPENCODE, 500, 100,
                      reconcile=True, judge_models=judges, seat_models={"SWE": "fixture/different"})
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
            "Council Architecture": "openai/gpt-fixture",
            "Council Security": "anthropic/claude-fixture",
            "Council Scope": "google/gemini-fixture",
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
        for name in ("Review Workflow Manager", "Review Lens Generalist", "Review Lens Reliability", "Review Lens MVP",
                     "Review Lens Security", "Review Synthesis"):
            self.assertTrue(created[name]["permissions"]["authorizationPolicy"]["protectedAgent"]["blockAssignment"])
        self.assertNotIn("authorizationPolicy", created["Reviewer"]["permissions"])
        synthesis = created["Review Synthesis"]["instructionsBundle"]["files"]["AGENTS.md"]
        self.assertIn("publish only for a dispatch-issued review task", synthesis)
        self.assertIn("Never merge", synthesis)

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
            "Council Architecture": "anthropic/claude-fixture",
            "Council Security": "google/gemini-fixture",
            "Council Scope": "openai/gpt-fixture",
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
