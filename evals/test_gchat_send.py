"""Google Chat sends to an exact thread with a retry-safe request id."""

import argparse
import importlib.util
import pathlib
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "skills/ops/google-chat/gchat.py"
spec = importlib.util.spec_from_file_location("gchat", SCRIPT)
gchat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gchat)


class ChatSendTests(unittest.TestCase):
    def send(self, *, strict=True, thread="spaces/example/threads/one"):
        calls = []

        class Messages:
            def create(self, **kwargs):
                calls.append(kwargs)
                return mock.Mock(execute=lambda: {"name": "spaces/example/messages/ack"})

        service = mock.Mock()
        service.spaces.return_value.messages.return_value = Messages()
        args = argparse.Namespace(space="spaces/example", text="Test acknowledgement",
                                  thread=thread, strict_thread=strict, request_id="ack-example-1",
                                  message_id="client-ralph-ack-example")
        with mock.patch.object(gchat, "_svc", return_value=service), \
                mock.patch.object(gchat, "_emit"):
            gchat.cmd_send(args)
        return calls

    def test_strict_reply_cannot_start_a_new_thread(self):
        calls = self.send()
        self.assertEqual(1, len(calls))
        self.assertEqual("REPLY_MESSAGE_OR_FAIL", calls[0]["messageReplyOption"])
        self.assertEqual("spaces/example/threads/one", calls[0]["body"]["thread"]["name"])
        self.assertEqual("ack-example-1", calls[0]["requestId"])
        self.assertEqual("client-ralph-ack-example", calls[0]["messageId"])

    def test_manual_thread_send_keeps_existing_fallback(self):
        self.assertEqual("REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD",
                         self.send(strict=False)[0]["messageReplyOption"])

    def test_strict_send_without_thread_fails_before_api_call(self):
        with self.assertRaises(ValueError):
            self.send(thread=None)

    def test_strict_send_to_another_space_fails_before_api_call(self):
        with self.assertRaises(ValueError):
            self.send(thread="spaces/other/threads/one")


if __name__ == "__main__":
    unittest.main(verbosity=2)
