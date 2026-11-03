"""A host-only Chat sender uses staged destination and one retry-safe request id."""

import hashlib
import importlib.machinery
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "bootstrap" / "chat-reply"


def load_script():
    loader = importlib.machinery.SourceFileLoader("chat_reply", str(SCRIPT))
    spec = importlib.util.spec_from_loader("chat_reply", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ChatReplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = pathlib.Path(self.tmp.name)
        self.env = base / "instance.env"
        self.env.write_text("RALPH_INSTANCE=ralph-work\nRALPH_WORKSPACES=./spaces\n"
                            "CHAT_ROUTE_SPACES=spaces/example\n")
        self.work = base / "spaces" / "chat-inbox"
        self.message = "spaces/example/messages/one"
        self.op = "chat-" + hashlib.sha256(self.message.encode()).hexdigest()
        self.evidence = self.work / self.op / "evidence"
        self.evidence.mkdir(parents=True)
        self.item = {"kind": "inbound", "sender_trusted": True,
                     "space": "spaces/example", "thread": "spaces/example/threads/first",
                     "messages": [{"name": self.message, "text": "test request"}]}
        self.write_item()
        self.task = {"id": "t_intake", "status": "done", "assignee": "ralph-chat-maintainer",
                     "body": f"operation_id: {self.op}\n",
                     "workspace_path": f"/workspaces/chat-inbox/{self.op}"}
        self.decision = {"decision": "route", "body": "Investigate the test request."}
        self.calls = []
        self.reply = load_script()

    def write_item(self):
        p = self.evidence / "item.json"
        p.write_text(json.dumps(self.item))
        p.chmod(0o600)

    def command(self, argv):
        self.calls.append(argv)
        if "list" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"tasks": [self.task]}), "")
        if "show" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "task": self.task, "runs": [{"outcome": "completed", "metadata": self.decision}],
            }), "")
        if "/ralph/bootstrap/chat-route" in argv:
            return subprocess.CompletedProcess(argv, 0, "routed: t_orchestrator\n", "")
        if "send" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"sent": True,
                                           "name": "spaces/example/messages/ack"}), "")
        raise AssertionError(argv)

    def send(self):
        with patch.object(self.reply, "command", side_effect=self.command):
            return self.reply.send_once("t_intake", self.env)

    def test_route_sends_fixed_receipt_to_bound_thread(self):
        self.assertEqual(0, self.send())
        send = next(a for a in self.calls if "send" in a)
        self.assertIn("--strict-thread", send)
        self.assertEqual("spaces/example/threads/first", send[send.index("--thread") + 1])
        self.assertEqual("spaces/example", send[send.index("send") + 1])
        self.assertIn("I'm working on it", send[send.index("send") + 2])
        self.assertNotIn("test request", " ".join(send))

    def test_hermes_session_metadata_does_not_block_reply(self):
        self.decision["worker_session_id"] = "session-1"
        self.assertEqual(0, self.send())

    def test_retry_reuses_request_id(self):
        self.assertEqual(0, self.send())
        self.assertEqual(0, self.send())
        ids = [a[a.index("--request-id") + 1] for a in self.calls if "send" in a]
        self.assertEqual(2, len(ids))
        self.assertEqual(ids[0], ids[1])

    def test_non_route_and_untrusted_do_not_send(self):
        for decision in ("ignore", "draft_reply", "escalate", "send"):
            with self.subTest(decision=decision):
                self.calls.clear()
                self.decision["decision"] = decision
                self.assertEqual(0, self.send())
                self.assertFalse(any("send" in a for a in self.calls))
        self.decision["decision"] = "route"
        self.item["sender_trusted"] = False
        self.write_item()
        self.assertEqual(2, self.send())
        self.assertFalse(any("send" in a for a in self.calls))

    def test_scan_sends_once_and_skips_confirmed_receipt(self):
        with patch.object(self.reply, "command", side_effect=self.command):
            self.assertEqual(0, self.reply.scan(self.env))
            self.assertEqual(0, self.reply.scan(self.env))
        self.assertEqual(1, len([a for a in self.calls if "send" in a]))
        marker = self.work / self.op / ".receipt-sent"
        self.assertTrue(marker.is_file())
        self.assertEqual(0o600, marker.stat().st_mode & 0o777)

    def test_different_thread_or_space_is_refused(self):
        self.item["thread"] = "spaces/other/threads/wrong"
        self.write_item()
        self.assertEqual(2, self.send())
        self.assertFalse(any("send" in a for a in self.calls))
        self.item["thread"] = "spaces/example/threads/first"
        self.item["space"] = "spaces/other"
        self.write_item()
        self.assertEqual(2, self.send())
        self.assertFalse(any("send" in a for a in self.calls))

    def test_world_readable_evidence_is_refused(self):
        (self.evidence / "item.json").chmod(0o644)
        self.assertEqual(2, self.send())
        self.assertFalse(any("send" in a for a in self.calls))

    def test_route_failure_does_not_send(self):
        def fails(argv):
            if "/ralph/bootstrap/chat-route" in argv:
                return subprocess.CompletedProcess(argv, 1, "", "failed")
            return self.command(argv)
        with patch.object(self.reply, "command", side_effect=fails):
            self.assertEqual(1, self.reply.send_once("t_intake", self.env))
        self.assertFalse(any("send" in a for a in self.calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
