"""A completed Chat route decision creates one scoped orchestrator card."""

import hashlib
import importlib.machinery
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "bootstrap" / "chat-route"


def load_script():
    loader = importlib.machinery.SourceFileLoader("chat_route", str(SCRIPT))
    spec = importlib.util.spec_from_loader("chat_route", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ChatRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.message = "spaces/demo/messages/msg1"
        self.op_id = "chat-" + hashlib.sha256(self.message.encode()).hexdigest()
        self.evidence = self.root / self.op_id / "evidence"
        self.evidence.mkdir(parents=True)
        self.item = {
            "kind": "inbound", "sender_trusted": True,
            "space": "spaces/demo", "thread": "spaces/demo/threads/thread1",
            "messages": [{"name": self.message, "text": "Please investigate the sample bug"}],
        }
        self.write_item()
        self.task = {
            "id": "t_example", "status": "done", "assignee": "ralph-chat-maintainer",
            "body": f"operation_id: {self.op_id}\n",
            "workspace_path": str(self.root / self.op_id),
        }
        self.metadata = {"decision": "route", "body": "Investigate the sample bug and report findings."}
        self.calls = []
        self.route = load_script()

    def write_item(self):
        path = self.evidence / "item.json"
        path.write_text(json.dumps(self.item))
        path.chmod(0o600)

    def hermes(self, *args):
        self.calls.append(args)
        if "show" in args:
            return subprocess.CompletedProcess(args, 0, json.dumps({
                "task": self.task,
                "runs": [{"outcome": "completed", "metadata": self.metadata}],
            }), "")
        if "create" in args:
            return subprocess.CompletedProcess(args, 0, json.dumps({"id": "t_routed"}), "")
        raise AssertionError(args)

    def run_route(self):
        with patch.object(self.route, "hermes", side_effect=self.hermes):
            return self.route.route("t_example", self.root)

    def test_trusted_route_creates_only_an_orchestrator_card(self):
        self.assertEqual(0, self.run_route())
        create = next(c for c in self.calls if "create" in c)
        self.assertEqual(("kanban", "--board", "ralph", "create"), create[:4])
        self.assertEqual("ralph-orchestrator", create[create.index("--assignee") + 1])
        self.assertEqual("scratch", create[create.index("--workspace") + 1])
        self.assertEqual("chat-route-" + self.op_id, create[create.index("--idempotency-key") + 1])
        body = create[create.index("--body") + 1]
        self.assertIn("operation_id: " + self.op_id, body)
        self.assertIn("ralph-chat-maintainer", body)
        self.assertIn("Investigate the sample bug", body)
        self.assertNotIn("Please investigate the sample bug", body)
        self.assertNotIn("spaces/", body, "destination stays in staged evidence, not card text")

    def test_follow_up_names_earlier_operations_in_the_thread(self):
        earlier = "chat-" + "a" * 64
        self.item["prior_operations"] = [earlier]
        self.write_item()
        self.assertEqual(0, self.run_route())
        body = next(c for c in self.calls if "create" in c)
        body = body[body.index("--body") + 1]
        self.assertIn("continues_operations: " + earlier, body)
        self.assertIn("operation_id: " + self.op_id, body)

    def test_malformed_prior_operations_are_refused(self):
        self.item["prior_operations"] = ["chat-x\nassignee: ralph-builder"]
        self.write_item()
        self.assertEqual(2, self.run_route())
        self.assertFalse(any("create" in c for c in self.calls))

    def test_route_with_maintainer_ack_is_accepted(self):
        self.metadata["ack"] = "On it."
        self.assertEqual(0, self.run_route())

    def test_thread_resource_with_dot_is_accepted(self):
        self.item["thread"] = "spaces/demo/threads/thread1.part2"
        self.write_item()
        self.assertEqual(0, self.run_route())

    def test_hermes_session_metadata_does_not_block_route(self):
        self.metadata["worker_session_id"] = "session-1"
        self.assertEqual(0, self.run_route())

    def test_retry_uses_same_operation_key(self):
        self.assertEqual(0, self.run_route())
        self.assertEqual(0, self.run_route())
        creates = [c for c in self.calls if "create" in c]
        self.assertEqual(2, len(creates))
        self.assertEqual(creates[0][creates[0].index("--idempotency-key") + 1],
                         creates[1][creates[1].index("--idempotency-key") + 1])

    def test_other_decisions_do_not_create_cards(self):
        for decision in ("ignore", "reply"):
            with self.subTest(decision=decision):
                self.calls.clear()
                self.metadata["decision"] = decision
                self.assertEqual(0, self.run_route())
                self.assertFalse(any("create" in c for c in self.calls))

    def test_untrusted_sender_cannot_route(self):
        self.item["sender_trusted"] = False
        self.write_item()
        self.assertEqual(2, self.run_route())
        self.assertFalse(any("create" in c for c in self.calls))

    def test_task_must_be_completed_by_chat_maintainer(self):
        for field, value in (("status", "running"), ("assignee", "ralph-builder")):
            with self.subTest(field=field):
                self.calls.clear()
                self.task[field] = value
                self.assertEqual(2, self.run_route())
                self.assertFalse(any("create" in c for c in self.calls))
                self.task[field] = "done" if field == "status" else "ralph-chat-maintainer"

    def test_operation_must_match_staged_message(self):
        wrong = "chat-" + "0" * 64
        self.task["body"] = f"operation_id: {wrong}\n"
        self.task["workspace_path"] = str(self.root / wrong)
        other_evidence = self.root / wrong / "evidence"
        other_evidence.mkdir(parents=True)
        other = other_evidence / "item.json"
        other.write_text(json.dumps(self.item))
        other.chmod(0o600)
        self.assertEqual(2, self.run_route())
        self.assertFalse(any("create" in c for c in self.calls))

    def test_unknown_decision_is_refused(self):
        for decision in ("route\nprivate message text", "send", "draft_reply"):
            with self.subTest(decision=decision):
                self.calls.clear()
                self.metadata["decision"] = decision
                self.assertEqual(2, self.run_route())
                self.assertFalse(any("create" in c for c in self.calls))

    def test_world_readable_evidence_is_refused(self):
        (self.evidence / "item.json").chmod(0o644)
        self.assertEqual(2, self.run_route())
        self.assertFalse(any("create" in c for c in self.calls))

    def test_symlinked_evidence_is_refused(self):
        original = self.evidence / "item.json"
        original.unlink()
        original.symlink_to(self.root / "other.json")
        self.assertEqual(2, self.run_route())
        self.assertFalse(any("create" in c for c in self.calls))

    def test_route_accepts_reply_kind_and_kind_but_still_refuses_a_sixth_key(self):
        self.metadata["kind"] = "question"
        self.metadata["reply_kind"] = "clarify"
        self.assertEqual(0, self.run_route())
        self.calls.clear()
        self.metadata["extra"] = "nope"
        self.assertEqual(2, self.run_route())
        self.assertFalse(any("create" in c for c in self.calls))

    def test_invalid_reply_kind_or_kind_is_refused(self):
        self.metadata["reply_kind"] = "nonsense"
        self.assertEqual(2, self.run_route())
        del self.metadata["reply_kind"]
        self.metadata["kind"] = "nonsense"
        self.assertEqual(2, self.run_route())
        self.assertFalse(any("create" in c for c in self.calls))

    def test_update_kind_leaves_no_final_card_to_send(self):
        """E10: an update is acknowledged and nothing more, so the brief asks for no final card."""
        self.metadata["kind"] = "update"
        self.assertEqual(0, self.run_route())
        create = next(c for c in self.calls if "create" in c)
        body = create[create.index("--body") + 1]
        self.assertNotIn("Finish with one ralph-chat-maintainer card", body)

    def test_other_actionable_kinds_still_ask_for_a_final_card(self):
        for kind in ("question", "feature_request", "bug_investigation"):
            with self.subTest(kind=kind):
                self.calls.clear()
                self.metadata["kind"] = kind
                self.assertEqual(0, self.run_route())
                create = next(c for c in self.calls if "create" in c)
                body = create[create.index("--body") + 1]
                self.assertIn("Finish with one ralph-chat-maintainer card", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
