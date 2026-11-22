"""Exercise Gmail history resolution and cursor safety with an in-memory API shape."""
import base64
import importlib.util
import json
import tempfile
import threading
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
    def __init__(self, pages, messages, mailbox="intake@example.com"):
        self.pages = pages
        self.message_data = messages
        self.mailbox = mailbox
        self.history_options = []
        self.message_requests = []
        self.profile_requests = []

    def users(self):
        return self

    def history(self):
        return self

    def messages(self):
        return self

    def getProfile(self, **options):
        self.profile_requests.append(options)
        return Request({"emailAddress": self.mailbox})

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
        "labelIds": ["INBOX"],
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
            service=service, notification=notification(), cursor="29", expected_mailbox="intake@example.com",
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
        self.assertEqual(service.profile_requests, [{"userId": "me"}])
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
                service=service, notification=notification(), cursor="29", expected_mailbox="intake@example.com",
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
                service=service, notification=notification(), cursor="29", expected_mailbox="intake@example.com",
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
                service=service, notification=notification(), cursor="29", expected_mailbox="intake@example.com",
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
            service=service, notification=notification(), cursor="29", expected_mailbox="intake@example.com",
            deliver=delivered.append, save_cursor=saved.append,
        )
        self.assertEqual(result["messageCount"], 1)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(len(service.message_requests), 1)
        self.assertEqual(saved, ["35"])

    def test_wrong_authenticated_mailbox_causes_no_history_read_delivery_or_cursor_advance(self):
        service = FakeService(
            pages={None: Request({"historyId": "35", "history": []})},
            messages={}, mailbox="other@example.com",
        )
        delivered = []
        saved = []
        with self.assertRaisesRegex(ValueError, "does not match configured receive account"):
            worker.process_history(
                service=service, notification=notification(), cursor="29",
                expected_mailbox="intake@example.com", deliver=delivered.append, save_cursor=saved.append,
            )
        self.assertEqual(service.profile_requests, [{"userId": "me"}])
        self.assertEqual(service.history_options, [])
        self.assertEqual(service.message_requests, [])
        self.assertEqual(delivered, [])
        self.assertEqual(saved, [])

    def test_sent_and_draft_messages_are_resolved_but_never_delivered(self):
        sent = gmail_message("a1b2c3d4", "f1e2d3c4").result
        sent["labelIds"] = ["INBOX", "SENT"]
        draft = gmail_message("b1c2d3e4", "f1e2d3c4").result
        draft["labelIds"] = ["DRAFT"]
        inbound = gmail_message("c1d2e3f4", "f1e2d3c4").result
        service = FakeService(
            pages={None: Request({"historyId": "35", "history": [{
                "id": "31", "messagesAdded": [
                    {"message": {"id": "a1b2c3d4"}},
                    {"message": {"id": "b1c2d3e4"}},
                    {"message": {"id": "c1d2e3f4"}},
                ],
            }]})},
            messages={
                "a1b2c3d4": Request(sent),
                "b1c2d3e4": Request(draft),
                "c1d2e3f4": Request(inbound),
            },
        )
        delivered = []
        saved = []
        result = worker.process_history(
            service=service, notification=notification(), cursor="29",
            expected_mailbox="intake@example.com", deliver=delivered.append, save_cursor=saved.append,
        )
        self.assertEqual([event["messageId"] for event in delivered], ["c1d2e3f4"])
        self.assertEqual(result, {"messageCount": 1, "historyId": "35"})
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

    def test_cursor_directory_fsync_failure_restores_the_previous_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            cursor_path = Path(directory) / "cursor" / "history.json"
            worker.write_cursor(cursor_path, "29")
            with patch.object(worker.os, "fsync", side_effect=[None, OSError("directory fsync failed"), None, None]):
                with self.assertRaisesRegex(OSError, "directory fsync failed"):
                    worker.write_cursor(cursor_path, "35")
            self.assertEqual(worker.read_cursor(cursor_path), "29")

    def test_cursor_commit_failure_keeps_the_stored_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            cursor_path = Path(directory) / "cursor" / "history.json"
            worker.write_cursor(cursor_path, "29")
            service = FakeService(pages={None: Request({"historyId": "35", "history": []})}, messages={})
            with patch.object(worker, "write_cursor", side_effect=OSError("cursor unavailable")):
                with self.assertRaisesRegex(OSError, "cursor unavailable"):
                    worker.process_notification(
                        service=service, notification=notification(), cursor_file=cursor_path,
                        expected_mailbox="intake@example.com", deliver=lambda _event: None,
                    )
            self.assertEqual(worker.read_cursor(cursor_path), "29")

    def test_competing_notifications_serialize_read_delivery_and_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            cursor_path = Path(directory) / "history.json"
            worker.write_cursor(cursor_path, "10")
            first_service = FakeService(
                pages={None: Request({"historyId": "20", "history": [{
                    "id": "19", "messagesAdded": [{"message": {"id": "a1b2c3d4"}}],
                }]})},
                messages={"a1b2c3d4": gmail_message("a1b2c3d4", "f1e2d3c4")},
            )
            second_service = FakeService(
                pages={None: Request({"historyId": "15", "history": []})}, messages={},
            )
            first_delivery_started = threading.Event()
            finish_first_delivery = threading.Event()
            second_started = threading.Event()
            second_finished = threading.Event()
            errors = []

            def hold_first_delivery(_event):
                first_delivery_started.set()
                if not finish_first_delivery.wait(timeout=5):
                    raise TimeoutError("test release was not signalled")

            def process_first():
                try:
                    worker.process_notification(
                        service=first_service, notification=notification("30"), cursor_file=cursor_path,
                        expected_mailbox="intake@example.com", deliver=hold_first_delivery,
                    )
                except Exception as error:
                    errors.append(error)

            def process_second():
                second_started.set()
                try:
                    worker.process_notification(
                        service=second_service, notification=notification("15"), cursor_file=cursor_path,
                        expected_mailbox="intake@example.com", deliver=lambda _event: None,
                    )
                except Exception as error:
                    errors.append(error)
                finally:
                    second_finished.set()

            first = threading.Thread(target=process_first)
            second = threading.Thread(target=process_second)
            first.start()
            self.assertTrue(first_delivery_started.wait(timeout=5))
            second.start()
            self.assertTrue(second_started.wait(timeout=5))
            self.assertFalse(second_finished.wait(timeout=0.1))
            finish_first_delivery.set()
            first.join(timeout=5)
            second.join(timeout=5)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(first_service.history_options[0]["startHistoryId"], "10")
            self.assertEqual(second_service.history_options[0]["startHistoryId"], "20")
            self.assertEqual(worker.read_cursor(cursor_path), "20")


if __name__ == "__main__":
    unittest.main()
