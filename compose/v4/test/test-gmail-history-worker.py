"""Exercise Gmail history resolution and cursor safety with an in-memory API shape."""
import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / "gmail-history-worker.py"
spec = importlib.util.spec_from_file_location("gmail_history_worker", path)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class Request:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class FakeService:
    def __init__(self, pages, messages):
        self.pages = pages
        self.message_data = messages
        self.history_options = []
        self.message_requests = []

    def users(self):
        return self

    def history(self):
        return self

    def messages(self):
        return self

    def list(self, **options):
        self.history_options.append(options)
        page = options.get("pageToken")
        return self.pages[page]

    def get(self, **options):
        self.message_requests.append(options)
        message_id = options["id"]
        return self.message_data[message_id]


def gmail_message(message_id, thread_id):
    body = base64.urlsafe_b64encode(f"Full resolved body for {message_id}".encode()).decode().rstrip("=")
    return Request({
        "id": message_id,
        "threadId": thread_id,
        "internalDate": "1795046400000",
        "snippet": f"Resolved content for {message_id}",
        "payload": {"headers": [
            {"name": "From", "value": "Requester <requester@example.com>"},
            {"name": "Subject", "value": "Status request"},
        ], "mimeType": "text/plain", "body": {"data": body}},
    })


def notification(history_id="30"):
    return {"emailAddress": "intake@example.com", "historyId": history_id}


class GmailHistoryTest(unittest.TestCase):
    def test_history_records_are_resolved_and_cursor_advances_after_all_pages(self):
        service = FakeService(
            pages={
                None: Request({"historyId": "35", "history": [{
                    "id": "31", "messagesAdded": [{"message": {"id": "a1b2c3d4"}}],
                }], "nextPageToken": "page-2"}),
                "page-2": Request({"historyId": "37", "history": [{
                    "id": "32", "messagesAdded": [{"message": {"id": "b1c2d3e4"}}],
                }]}),
            },
            messages={
                "a1b2c3d4": gmail_message("a1b2c3d4", "f1e2d3c4"),
                "b1c2d3e4": gmail_message("b1c2d3e4", "f1e2d3c4"),
            },
        )
        delivered = []
        saved = []
        result = worker.process_history(
            service=service, notification=notification(), cursor="29",
            deliver=delivered.append, save_cursor=saved.append,
        )

        self.assertEqual(result, {"messageCount": 2, "historyId": "35"})
        self.assertEqual([event["messageId"] for event in delivered], ["a1b2c3d4", "b1c2d3e4"])
        self.assertTrue(all(event["threadId"] == "f1e2d3c4" for event in delivered))
        self.assertEqual([event["historyId"] for event in delivered], ["31", "32"])
        self.assertTrue(all(event["from"] == "requester@example.com" for event in delivered))
        self.assertTrue(all(event["mailbox"] == "intake@example.com" for event in delivered))
        self.assertTrue(all(event["text"].startswith("Full resolved body") for event in delivered))
        self.assertEqual(saved, ["35"])
        self.assertEqual(len(service.message_requests), 2)
        self.assertTrue(all(request["format"] == "full" for request in service.message_requests))
        self.assertEqual([options["startHistoryId"] for options in service.history_options], ["29", "29"])

    def test_partial_delivery_does_not_advance_cursor(self):
        service = FakeService(
            pages={None: Request({"historyId": "35", "history": [{
                "id": "31", "messagesAdded": [
                    {"message": {"id": "a1b2c3d4"}}, {"message": {"id": "b1c2d3e4"}},
                ],
            }]})},
            messages={
                "a1b2c3d4": gmail_message("a1b2c3d4", "f1e2d3c4"),
                "b1c2d3e4": gmail_message("b1c2d3e4", "f1e2d3c4"),
            },
        )
        delivered = []
        saved = []

        def fail_second(event):
            delivered.append(event)
            if len(delivered) == 2:
                raise RuntimeError("Paperclip unavailable")

        with self.assertRaises(RuntimeError):
            worker.process_history(
                service=service, notification=notification(), cursor="29",
                deliver=fail_second, save_cursor=saved.append,
            )
        self.assertEqual(len(delivered), 2)
        self.assertEqual(saved, [])

    def test_expired_history_does_not_advance_or_reset_cursor(self):
        expired = RuntimeError("history not found")
        expired.resp = SimpleNamespace(status=404)
        service = FakeService(pages={None: Request(error=expired)}, messages={})
        saved = []
        with self.assertRaises(worker.ExpiredHistory):
            worker.process_history(
                service=service, notification=notification(), cursor="29",
                deliver=lambda _event: None, save_cursor=saved.append,
            )
        self.assertEqual(saved, [])

    def test_a_missing_resolved_message_does_not_advance_cursor(self):
        service = FakeService(
            pages={None: Request({"historyId": "35", "history": [{
                "id": "31", "messagesAdded": [{"message": {"id": "a1b2c3d4"}}],
            }]})},
            messages={"a1b2c3d4": Request(error=RuntimeError("message deleted"))},
        )
        saved = []
        with self.assertRaises(worker.HistoryBatchError):
            worker.process_history(
                service=service, notification=notification(), cursor="29",
                deliver=lambda _event: None, save_cursor=saved.append,
            )
        self.assertEqual(saved, [])

    def test_duplicate_history_entries_resolve_one_message(self):
        service = FakeService(
            pages={None: Request({"historyId": "35", "history": [
                {"id": "31", "messagesAdded": [{"message": {"id": "a1b2c3d4"}}]},
                {"id": "32", "messagesAdded": [{"message": {"id": "a1b2c3d4"}}]},
            ]})},
            messages={"a1b2c3d4": gmail_message("a1b2c3d4", "f1e2d3c4")},
        )
        delivered = []
        saved = []
        result = worker.process_history(
            service=service, notification=notification(), cursor="29",
            deliver=delivered.append, save_cursor=saved.append,
        )
        self.assertEqual(result["messageCount"], 1)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(len(service.message_requests), 1)
        self.assertEqual(saved, ["35"])

    def test_pubsub_envelope_is_unwrapped_but_does_not_supply_message_content(self):
        encoded = base64.b64encode(json.dumps(notification()).encode()).decode()
        self.assertEqual(worker.notification_history_id({"message": {"data": encoded}}),
                         ("intake@example.com", "30"))

    def test_gmail_token_with_send_scope_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "token.json"
            token_file.write_text(json.dumps({"scopes": [worker.GMAIL_READONLY_SCOPE,
                                                          "https://www.googleapis.com/auth/gmail.send"]}))
            with patch.dict("os.environ", {"GMAIL_READ_TOKEN_FILE": str(token_file)}):
                with self.assertRaisesRegex(ValueError, "gmail.readonly-only"):
                    worker._gmail_service()

    def test_cursor_write_replaces_the_file_atomically_with_only_the_history_id(self):
        with tempfile.TemporaryDirectory() as directory:
            cursor_path = Path(directory) / "cursor" / "history.json"
            worker.write_cursor(cursor_path, "35")
            self.assertEqual(worker.read_cursor(cursor_path), "35")
            self.assertEqual(json.loads(cursor_path.read_text()), {"historyId": "35"})


if __name__ == "__main__":
    unittest.main()
