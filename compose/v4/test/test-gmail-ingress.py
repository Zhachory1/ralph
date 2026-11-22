"""Exercise the Gmail Pub/Sub receiver without live Google credentials."""
import base64
import http.client
import importlib.util
import json
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / "gmail-ingress.py"
spec = importlib.util.spec_from_file_location("gmail_ingress", path)
ingress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingress)

AUDIENCE = "https://gmail.fixture.invalid/push"
SERVICE_ACCOUNT = "gmail-push@fixture-project.iam.gserviceaccount.com"
SUBSCRIPTION = "projects/fixture-project/subscriptions/gmail-inbound"
MAILBOX = "intake@example.com"


def envelope(*, mailbox=MAILBOX, data=None, subscription=SUBSCRIPTION):
    if data is None:
        data = json.dumps({"emailAddress": mailbox, "historyId": "12345"}).encode()
    return {
        "subscription": subscription,
        "message": {
            "data": base64.b64encode(data).decode(),
            "messageId": "message-123",
            "publishTime": "2026-11-22T00:00:00Z",
        },
    }


def claims(**updates):
    value = {
        "iss": "https://accounts.google.com",
        "aud": AUDIENCE,
        "email": SERVICE_ACCOUNT,
        "email_verified": True,
    }
    value.update(updates)
    return value


class GmailIngressTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.verified = []

    def verify(self, token, audience):
        self.verified.append((token, audience))
        return claims()

    def handle(self, data=None, authorization="Bearer fixture", verify=None, dispatch=None):
        body = json.dumps(envelope() if data is None else data).encode()
        return ingress.handle_request(
            authorization=authorization, body=body, audience=AUDIENCE,
            service_account=SERVICE_ACCOUNT, expected_subscription=SUBSCRIPTION,
            expected_mailbox=MAILBOX, verify_token=verify or self.verify,
            dispatch=dispatch or self.sent.append,
        )

    def test_valid_notification_dispatches_the_original_envelope_and_acknowledges(self):
        source = envelope()
        self.assertEqual(self.handle(source), 204)
        self.assertEqual(self.verified, [("fixture", AUDIENCE)])
        self.assertEqual(self.sent, [source])

    def test_absent_invalid_or_unavailable_oidc_is_not_dispatched(self):
        self.assertEqual(self.handle(authorization=""), 401)
        self.assertEqual(self.handle(verify=lambda *_: (_ for _ in ()).throw(ValueError("bad token"))), 401)
        self.assertEqual(self.handle(verify=lambda *_: (_ for _ in ()).throw(ingress.VerificationUnavailable())), 503)
        self.assertEqual(self.sent, [])

    def test_wrong_issuer_audience_or_service_account_is_rejected(self):
        for bad_claims in (
            claims(iss="https://issuer.fixture.invalid"),
            claims(aud="https://other.fixture.invalid/push"),
            claims(email="other@fixture-project.iam.gserviceaccount.com"),
            claims(email_verified=False),
        ):
            self.assertEqual(self.handle(verify=lambda *_args, c=bad_claims: c), 401)
        self.assertEqual(self.sent, [])

    def test_subscription_envelope_data_and_mailbox_are_validated_before_dispatch(self):
        invalid_envelopes = [
            envelope(subscription="projects/other/subscriptions/elsewhere"),
            envelope(data=b"not json"),
            envelope(mailbox="other@example.com"),
            envelope(data=json.dumps({"emailAddress": MAILBOX, "historyId": "-1"}).encode()),
        ]
        bad_base64 = envelope()
        bad_base64["message"]["data"] = "%%%"
        invalid_envelopes.append(bad_base64)
        for value in invalid_envelopes:
            self.assertEqual(self.handle(value), 400)
        self.assertEqual(self.sent, [])

    def test_oversized_request_and_decoded_data_are_rejected(self):
        status = ingress.handle_request(
            authorization="Bearer fixture", body=b"x" * (ingress.MAX_BODY_BYTES + 1),
            audience=AUDIENCE, service_account=SERVICE_ACCOUNT,
            expected_subscription=SUBSCRIPTION, expected_mailbox=MAILBOX,
            verify_token=self.verify, dispatch=self.sent.append,
        )
        self.assertEqual(status, 413)
        oversized = envelope(data=b"x" * (ingress.MAX_DATA_BYTES + 1))
        self.assertEqual(self.handle(oversized), 400)
        self.assertEqual(self.sent, [])

    def test_duplicate_json_fields_are_rejected(self):
        body = b'{"subscription":"' + SUBSCRIPTION.encode() + b'","subscription":"other",' \
               b'"message":{"data":"e30="}}'
        self.assertEqual(ingress.handle_request(
            authorization="Bearer fixture", body=body, audience=AUDIENCE,
            service_account=SERVICE_ACCOUNT, expected_subscription=SUBSCRIPTION,
            expected_mailbox=MAILBOX, verify_token=self.verify, dispatch=self.sent.append,
        ), 400)
        self.assertEqual(self.sent, [])

    def test_dispatch_failure_returns_retryable_non_success_without_logging_payload(self):
        output = []
        dispatch = lambda _envelope: (_ for _ in ()).throw(RuntimeError("private message content"))
        with patch("sys.stderr") as stderr:
            self.assertEqual(self.handle(dispatch=dispatch), 503)
            output.append(stderr.write.call_args.args[0])
        self.assertNotIn("private message content", "".join(output))

    def test_http_boundary_returns_success_only_after_worker_dispatch(self):
        handler = ingress.make_handler(
            audience=AUDIENCE, service_account=SERVICE_ACCOUNT,
            expected_subscription=SUBSCRIPTION, expected_mailbox=MAILBOX,
            verify_token=self.verify, dispatch=self.sent.append,
        )
        server = ingress.GmailHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        conn = None
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            body = json.dumps(envelope()).encode()
            conn.request("POST", "/gmail", body, {
                "Content-Type": "application/json", "Authorization": "Bearer fixture",
            })
            self.assertEqual(conn.getresponse().status, 204)
            self.assertEqual(len(self.sent), 1)
            conn.close()
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request("POST", "/gmail", body, {"Content-Type": "application/json"})
            self.assertEqual(conn.getresponse().status, 401)
            self.assertEqual(len(self.sent), 1)
        finally:
            if conn is not None:
                conn.close()
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_worker_process_failure_is_retryable(self):
        with patch.object(ingress.subprocess, "run", return_value=types.SimpleNamespace(returncode=1)) as run:
            dispatch = ingress.make_dispatch({"PATH": "/usr/bin", "PAPERCLIP_API_KEY": "fixture"})
            self.assertEqual(self.handle(dispatch=dispatch), 503)
        self.assertEqual(run.call_args.kwargs["timeout"], ingress.WORKER_TIMEOUT_SECONDS)
        self.assertEqual(run.call_args.kwargs["env"]["PAPERCLIP_API_KEY"], "fixture")

    def test_google_verifier_uses_the_configured_audience_and_bounded_key_fetch(self):
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
            return claims()

        tokens.verify_oauth2_token = verify
        modules.update({"google.auth.exceptions": errors, "google.auth.transport.requests": transport,
                        "google.oauth2.id_token": tokens})
        with patch.dict(sys.modules, modules):
            self.assertEqual(ingress.verify_google_token("fixture", AUDIENCE), claims())
            tokens.verify_oauth2_token = lambda *_: (_ for _ in ()).throw(errors.TransportError())
            with self.assertRaises(ingress.VerificationUnavailable):
                ingress.verify_google_token("fixture", AUDIENCE)
        self.assertEqual(calls[0][1]["timeout"], 10)


if __name__ == "__main__":
    unittest.main()
