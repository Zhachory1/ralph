"""Test Google Chat ingress without live credentials or provider calls."""
import importlib.util
import http.client
import io
import json
import socket
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / "chat-ingress.py"
spec = importlib.util.spec_from_file_location("chat_ingress", path)
ingress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingress)

AUDIENCE = "https://chat.fixture.invalid/ingress"
EMAIL = "chat@system.gserviceaccount.com"


def event():
    return {
        "type": "MESSAGE",
        "space": {"name": "spaces/fixture"},
        "message": {
            "name": "spaces/fixture/messages/message-1",
            "thread": {"name": "spaces/fixture/threads/thread-1"},
            "text": "Please help.",
            "sender": {"type": "HUMAN"},
            "createTime": "2026-01-01T00:00:00Z",
        },
    }


class ChatIngressTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.verified = []

    def verify(self, token, audience):
        self.verified.append((token, audience))
        return {"email": EMAIL, "email_verified": True}

    def handle(self, data=None, authorization="Bearer fixture", verify=None, dispatch=None):
        return ingress.handle_request(
            authorization=authorization,
            body=json.dumps(event() if data is None else data).encode(),
            audience=AUDIENCE,
            verify_token=verify or self.verify,
            dispatch=dispatch or self.sent.append,
        )

    def test_valid_chat_message_reaches_existing_worker_shape(self):
        self.assertEqual(self.handle(), 200)
        self.assertEqual(self.verified, [("fixture", AUDIENCE)])
        self.assertEqual(self.sent, [{
            "kind": "chat",
            "type": "MESSAGE",
            "space": "spaces/fixture",
            "message": {
                "name": "spaces/fixture/messages/message-1",
                "thread": "spaces/fixture/threads/thread-1",
                "text": "Please help.",
                "sender": {"type": "HUMAN"},
                "createTime": "2026-01-01T00:00:00Z",
            },
        }])

    def test_missing_or_invalid_authorization_never_dispatches(self):
        self.assertEqual(self.handle(authorization=""), 401)
        self.assertEqual(self.handle(authorization="Bearer invalid", verify=lambda *_: (_ for _ in ()).throw(ValueError())), 401)
        self.assertEqual(self.sent, [])

    def test_wrong_or_unverified_service_account_never_dispatches(self):
        for claims in ({"email": "other@fixture.invalid", "email_verified": True},
                       {"email": EMAIL, "email_verified": False}):
            self.assertEqual(self.handle(verify=lambda *_: claims), 401)
        self.assertEqual(self.sent, [])

    def test_verifier_outage_returns_retryable_failure(self):
        def unavailable(*_):
            raise ingress.VerificationUnavailable()
        self.assertEqual(self.handle(verify=unavailable), 503)
        self.assertEqual(self.sent, [])

    def test_non_message_and_bot_events_do_not_dispatch(self):
        self.assertEqual(self.handle({"type": "ADDED_TO_SPACE"}), 200)
        bot = event()
        bot["message"]["sender"]["type"] = "BOT"
        self.assertEqual(self.handle(bot), 200)
        own = event()
        own["message"]["clientAssignedMessageId"] = "client-ralph-response"
        self.assertEqual(self.handle(own), 200)
        self.assertEqual(self.sent, [])

    def test_malformed_event_and_oversize_body_are_rejected(self):
        self.assertEqual(self.handle({"type": "MESSAGE", "space": {}}), 400)
        self.assertEqual(ingress.handle_request(
            authorization="Bearer fixture", body=b"{", audience=AUDIENCE,
            verify_token=self.verify, dispatch=self.sent.append,
        ), 400)
        self.assertEqual(ingress.handle_request(
            authorization="Bearer fixture", body=b"x" * (ingress.MAX_BODY_BYTES + 1),
            audience=AUDIENCE, verify_token=self.verify, dispatch=self.sent.append,
        ), 413)
        self.assertEqual(self.sent, [])

    def test_dispatch_failure_returns_retryable_failure(self):
        def failed(_):
            raise RuntimeError("private fixture content")
        output = io.StringIO()
        with patch("sys.stderr", output):
            self.assertEqual(self.handle(dispatch=failed), 503)
        self.assertNotIn("private fixture content", output.getvalue())
        self.assertEqual(self.sent, [])

    def test_intake_worker_rejection_is_not_retried(self):
        def invalid(_):
            raise ingress.InvalidChatEvent()
        self.assertEqual(self.handle(dispatch=invalid), 400)
        self.assertEqual(self.sent, [])

    def test_http_boundary_rejects_duplicate_authorization(self):
        handler = ingress.make_handler(audience=AUDIENCE, verify_token=self.verify, dispatch=self.sent.append)
        server = ingress.ChatHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            body = json.dumps(event()).encode()
            conn.putrequest("POST", "/chat")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(body)))
            conn.putheader("Authorization", "Bearer fixture")
            conn.putheader("Authorization", "Bearer other")
            conn.endheaders(body)
            self.assertEqual(conn.getresponse().status, 401)
            self.assertEqual(self.sent, [])
            conn.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def assert_slow_request_does_not_block(self, prefix):
        handler = ingress.make_handler(audience=AUDIENCE, verify_token=self.verify, dispatch=self.sent.append)
        with patch.object(ingress, "SOCKET_TIMEOUT_SECONDS", 0.1):
            server = ingress.ChatHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            slow = socket.create_connection(("127.0.0.1", server.server_port), timeout=2)
            stop = threading.Event()

            def trickle():
                while not stop.wait(0.02):
                    try:
                        slow.sendall(b"x")
                    except OSError:
                        return

            writer = threading.Thread(target=trickle, daemon=True)
            conn = None
            try:
                slow.sendall(prefix)
                writer.start()
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                conn.request("POST", "/chat", json.dumps(event()), {
                    "Content-Type": "application/json", "Authorization": "Bearer fixture",
                })
                self.assertEqual(conn.getresponse().status, 200)
                self.assertEqual(len(self.sent), 1)
            finally:
                if conn is not None:
                    conn.close()
                stop.set()
                writer.join(timeout=2)
                slow.close()
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()

    def test_incomplete_headers_do_not_block_later_requests(self):
        self.assert_slow_request_does_not_block(b"POST /chat HTTP/1.1\r\nHost: fixture\r\n")

    def test_incomplete_body_does_not_block_later_requests(self):
        self.assert_slow_request_does_not_block(
            b"POST /chat HTTP/1.1\r\nHost: fixture\r\nContent-Type: application/json\r\n"
            b"Authorization: Bearer fixture\r\nContent-Length: 100\r\n\r\n{"
        )

    def test_worker_receives_normalized_event_without_other_credentials(self):
        result = types.SimpleNamespace(returncode=0)
        with patch.object(ingress.subprocess, "run", return_value=result) as run:
            dispatch = ingress.make_dispatch("http://127.0.0.1:3100", "company", "routine", "spaces/fixture", "fixture")
            dispatch({"kind": "chat"})
        args, kwargs = run.call_args
        self.assertEqual(args[0][-2:], ["chat", "spaces/fixture"])
        self.assertEqual(json.loads(kwargs["input"]), {"kind": "chat"})
        self.assertEqual(set(kwargs["env"]), {"PATH", "PAPERCLIP_API_KEY"})

    def test_worker_input_and_transport_failures_remain_distinct(self):
        dispatch = ingress.make_dispatch("http://127.0.0.1:3100", "company", "routine", "spaces/fixture", "fixture")
        with patch.object(ingress.subprocess, "run", return_value=types.SimpleNamespace(returncode=2)):
            with self.assertRaises(ingress.InvalidChatEvent):
                dispatch({"kind": "chat"})
        with patch.object(ingress.subprocess, "run", return_value=types.SimpleNamespace(returncode=1)):
            with self.assertRaises(RuntimeError):
                dispatch({"kind": "chat"})

    def test_google_library_receives_configured_audience_and_bounded_transport(self):
        modules = {}
        for name in ("google", "google.auth", "google.auth.transport", "google.oauth2"):
            modules[name] = types.ModuleType(name)
            modules[name].__path__ = []
        errors = types.ModuleType("google.auth.exceptions")
        errors.GoogleAuthError = type("GoogleAuthError", (Exception,), {})
        errors.TransportError = type("TransportError", (errors.GoogleAuthError,), {})
        transport = types.ModuleType("google.auth.transport.requests")
        calls = []
        transport.Request = lambda: lambda *args, **kwargs: calls.append((args, kwargs))
        tokens = types.ModuleType("google.oauth2.id_token")

        def verify(token, request, audience):
            self.assertEqual((token, audience), ("fixture", AUDIENCE))
            request("https://keys.fixture.invalid")
            return {"email": EMAIL, "email_verified": True}

        tokens.verify_oauth2_token = verify
        modules.update({"google.auth.exceptions": errors, "google.auth.transport.requests": transport,
                        "google.oauth2.id_token": tokens})
        with patch.dict(sys.modules, modules):
            self.assertEqual(ingress.verify_google_token("fixture", AUDIENCE)["email"], EMAIL)
            tokens.verify_oauth2_token = lambda *_: (_ for _ in ()).throw(errors.GoogleAuthError())
            self.assertEqual(self.handle(verify=ingress.verify_google_token), 401)
            tokens.verify_oauth2_token = lambda *_: (_ for _ in ()).throw(errors.TransportError())
            self.assertEqual(self.handle(verify=ingress.verify_google_token), 503)
        self.assertEqual(calls[0][1]["timeout"], 10)


if __name__ == "__main__":
    unittest.main()
