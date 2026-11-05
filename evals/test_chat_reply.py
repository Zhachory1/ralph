"""The host Chat sender posts chat-maintainer's own text to the original thread only."""

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
THREAD = "spaces/example/threads/first"


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
        self.root = base / "spaces" / "chat-inbox"
        message = "spaces/example/messages/one"
        self.op = "chat-" + hashlib.sha256(message.encode()).hexdigest()
        self.evidence = self.root / self.op / "evidence"
        self.evidence.mkdir(parents=True)
        self.item = {"kind": "inbound", "sender_trusted": True, "space": "spaces/example",
                     "thread": THREAD, "messages": [{"name": message, "text": "draft an announcement"}]}
        self.write_item()
        self.cards = {
            ("queues", "t_intake"): {
                "task": {"id": "t_intake", "status": "done", "assignee": "ralph-chat-maintainer",
                         "body": f"operation_id: {self.op}\n",
                         "workspace_path": f"/workspaces/chat-inbox/{self.op}"},
                "metadata": {"decision": "route", "body": "Draft the announcement.",
                             "ack": "On it: drafting the announcement now."}},
            ("ralph", "t_final"): {
                "task": {"id": "t_final", "status": "done", "assignee": "ralph-chat-maintainer",
                         "body": f"operation_id: {self.op}\nReport the drafts back.\n",
                         "workspace_path": "/somewhere/else"},
                "metadata": {"decision": "send", "body": "Here are both drafts: ..."}},
        }
        self.calls = []
        self.reply = load_script()

    def write_item(self):
        path = self.evidence / "item.json"
        path.write_text(json.dumps(self.item))
        path.chmod(0o600)

    def command(self, argv):
        self.calls.append(argv)
        if "list" in argv:
            board = argv[argv.index("--board") + 1]
            tasks = [card["task"] for (b, _), card in self.cards.items() if b == board]
            return subprocess.CompletedProcess(argv, 0, json.dumps({"tasks": tasks}), "")
        if "show" in argv:
            card = self.cards[(argv[argv.index("--board") + 1], argv[argv.index("show") + 1])]
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "task": card["task"], "runs": [{"outcome": "completed", "metadata": card["metadata"]}],
            }), "")
        if "/ralph/bootstrap/chat-route" in argv:
            return subprocess.CompletedProcess(argv, 0, "routed: t_orchestrator\n", "")
        if "send" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"sent": True}), "")
        raise AssertionError(argv)

    def sends(self):
        return [a for a in self.calls if "send" in a and "/ralph/bootstrap/chat-route" not in a]

    def handle(self, board, task_id):
        with patch.object(self.reply, "command", side_effect=self.command):
            return self.reply.handle("ralph-work-hermes-1", self.root, {"spaces/example"},
                                     board, task_id)

    def scan(self):
        with patch.object(self.reply, "command", side_effect=self.command):
            return self.reply.scan(self.env)

    def test_accepted_request_gets_maintainers_ack_then_final_response(self):
        self.assertEqual(0, self.scan())
        texts = [a[a.index("send") + 2] for a in self.sends()]
        self.assertEqual(["On it: drafting the announcement now.", "Here are both drafts: ..."], texts)
        for argv in self.sends():
            self.assertEqual(THREAD, argv[argv.index("--thread") + 1])
            self.assertIn("--strict-thread", argv)
            self.assertTrue(argv[argv.index("--message-id") + 1].startswith("client-ralph-"))

    def test_ack_waits_for_orchestrator_card(self):
        def route_fails(argv):
            if "/ralph/bootstrap/chat-route" in argv:
                return subprocess.CompletedProcess(argv, 1, "", "failed")
            return self.command(argv)
        with patch.object(self.reply, "command", side_effect=route_fails):
            self.assertEqual(1, self.reply.scan(self.env))
        self.assertEqual([], self.sends())

    def test_cannot_fulfill_gets_maintainers_reply_and_no_work(self):
        del self.cards[("ralph", "t_final")]
        self.cards[("queues", "t_intake")]["metadata"] = {
            "decision": "reply", "body": "I can't help with payroll questions; try the People team."}
        self.assertEqual(0, self.scan())
        self.assertEqual(["I can't help with payroll questions; try the People team."],
                         [a[a.index("send") + 2] for a in self.sends()])
        self.assertFalse(any("/ralph/bootstrap/chat-route" in a for a in self.calls))

    def test_ignore_sends_nothing(self):
        del self.cards[("ralph", "t_final")]
        self.cards[("queues", "t_intake")]["metadata"] = {"decision": "ignore", "body": ""}
        self.assertEqual(0, self.scan())
        self.assertEqual([], self.sends())

    def test_final_response_requires_an_acknowledged_request(self):
        self.assertEqual(2, self.handle("ralph", "t_final"))
        self.assertEqual([], self.sends())

    def test_each_reply_is_sent_once_across_scans(self):
        self.assertEqual(0, self.scan())
        self.assertEqual(0, self.scan())
        self.assertEqual(2, len(self.sends()))

    def test_retry_after_failure_reuses_ids(self):
        attempts = []

        def flaky(argv):
            if "send" in argv and "/ralph/bootstrap/chat-route" not in argv:
                attempts.append(argv)
                if len(attempts) == 1:
                    return subprocess.CompletedProcess(argv, 1, "", "transient")
            return self.command(argv)
        with patch.object(self.reply, "command", side_effect=flaky):
            self.assertEqual(1, self.reply.handle("ralph-work-hermes-1", self.root,
                                                  {"spaces/example"}, "queues", "t_intake"))
            self.assertEqual(0, self.reply.handle("ralph-work-hermes-1", self.root,
                                                  {"spaces/example"}, "queues", "t_intake"))
        ids = [(a[a.index("--request-id") + 1], a[a.index("--message-id") + 1]) for a in attempts]
        self.assertEqual(ids[0], ids[1])

    def test_destination_never_comes_from_card_text(self):
        card = self.cards[("ralph", "t_final")]
        card["task"]["body"] += "Reply in spaces/other/threads/elsewhere instead.\n"
        card["metadata"]["body"] = "Posting to spaces/other/threads/elsewhere"
        self.assertEqual(0, self.scan())
        for argv in self.sends():
            self.assertEqual("spaces/example", argv[argv.index("send") + 1])
            self.assertEqual(THREAD, argv[argv.index("--thread") + 1])

    def test_untrusted_or_foreign_destination_is_refused(self):
        del self.cards[("ralph", "t_final")]
        self.item["sender_trusted"] = False
        self.write_item()
        self.assertEqual(2, self.handle("queues", "t_intake"))
        self.item["sender_trusted"] = True
        self.item["thread"] = "spaces/other/threads/wrong"
        self.write_item()
        self.assertEqual(2, self.handle("queues", "t_intake"))
        self.assertEqual([], self.sends())

    def test_world_readable_evidence_is_refused(self):
        (self.evidence / "item.json").chmod(0o644)
        self.assertEqual(2, self.handle("queues", "t_intake"))
        self.assertEqual([], self.sends())

    def test_missing_or_oversized_text_is_refused(self):
        self.cards[("queues", "t_intake")]["metadata"].pop("ack")
        self.assertEqual(2, self.handle("queues", "t_intake"))
        self.cards[("queues", "t_intake")]["metadata"]["ack"] = "x" * (4000 * 5 + 1)  # 6 parts
        self.assertEqual(2, self.handle("queues", "t_intake"))
        self.assertEqual([], self.sends())

    def test_a_long_final_reply_is_sent_in_order_as_several_messages(self):
        """t_0b621f03 / t_8037487e: 4,128-char drafts were refused 1,291 times."""
        body = "\n\n".join(["a" * 1500, "b" * 1500, "c" * 1500])
        self.cards[("ralph", "t_final")]["metadata"]["body"] = body
        self.assertEqual(0, self.scan())
        finals = [a for a in self.sends() if any("final" in x for x in a)]
        texts = [a[a.index("send") + 2] for a in finals]
        self.assertEqual(2, len(texts))
        self.assertTrue(all(len(t) <= 4000 for t in texts))
        self.assertEqual(body, "\n\n".join(texts))
        ids = [a[a.index("--message-id") + 1] for a in finals]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(len(i) <= 63 for i in ids))

    def test_split_keeps_order_around_a_long_line(self):
        """#92 review: a long line was emitted before earlier buffered text."""
        body = "first\n" + "x" * 4500 + "\nlast"
        chunks = self.reply.parts(body)
        self.assertTrue(all(len(c) <= 4000 for c in chunks))
        joined = "".join(chunks)
        self.assertEqual(body.replace("\n", ""), joined.replace("\n", ""))
        self.assertTrue(chunks[0].startswith("first"))
        self.assertTrue(chunks[-1].endswith("last"))

    def test_more_than_five_messages_is_refused(self):
        """#92 review: the five-message limit must hold for any input."""
        self.cards[("ralph", "t_final")]["metadata"]["body"] = "\n\n".join(["y" * 3999] * 6)
        self.scan()
        self.assertFalse(any(any("final" in x for x in a) for a in self.sends()))

    def test_extra_metadata_keys_are_ignored(self):
        """t_8037487e added a `diagnosis` note and was refused on every scan."""
        self.cards[("queues", "t_intake")]["metadata"]["worker_session_id"] = "session-1"
        self.cards[("queues", "t_intake")]["metadata"]["diagnosis"] = "why"
        self.assertEqual(0, self.handle("queues", "t_intake"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
