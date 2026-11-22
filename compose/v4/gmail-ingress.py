"""Authenticate bounded Google Pub/Sub push requests before Gmail history intake."""
import argparse
import base64
import binascii
import json
import os
import re
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

MAX_BODY_BYTES = 256 * 1024
MAX_DATA_BYTES = 64 * 1024
MAX_TOKEN_BYTES = 8192
SOCKET_TIMEOUT_SECONDS = 10
WORKER_TIMEOUT_SECONDS = 240
WORKER = Path(__file__).with_name("gmail-history-worker.py")
ALLOWED_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
HISTORY_ID = re.compile(r"[0-9]{1,20}\Z")


class VerificationUnavailable(Exception):
    """Google's verification keys could not be fetched."""


class GmailHTTPServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(SOCKET_TIMEOUT_SECONDS)
        return connection, address


def verify_google_token(token, audience):
    from google.auth.exceptions import GoogleAuthError, TransportError
    from google.auth.transport.requests import Request
    from google.oauth2.id_token import verify_oauth2_token

    request = Request()

    def bounded_request(*args, **kwargs):
        kwargs["timeout"] = 10
        return request(*args, **kwargs)

    try:
        return verify_oauth2_token(token, bounded_request, audience)
    except TransportError as error:
        raise VerificationUnavailable() from error
    except GoogleAuthError as error:
        raise ValueError("Invalid Google token") from error


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _json_object(data):
    try:
        value = json.loads(data, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise ValueError("Malformed JSON") from error
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def validate_envelope(body, *, expected_subscription, expected_mailbox):
    """Validate the Pub/Sub push envelope and decoded Gmail notification."""
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("Pub/Sub request is too large")
    envelope = _json_object(body)
    if set(envelope) - {"message", "subscription", "deliveryAttempt"}:
        raise ValueError("Unexpected Pub/Sub envelope fields")
    if envelope.get("subscription") != expected_subscription:
        raise ValueError("Unexpected Pub/Sub subscription")
    attempt = envelope.get("deliveryAttempt")
    if attempt is not None and (type(attempt) is not int or not 1 <= attempt <= 1000000):
        raise ValueError("Invalid Pub/Sub delivery attempt")
    message = envelope.get("message")
    if not isinstance(message, dict) or set(message) - {"data", "messageId", "publishTime", "attributes"}:
        raise ValueError("Invalid Pub/Sub message")
    encoded = message.get("data")
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_DATA_BYTES + 2) // 3):
        raise ValueError("Invalid or oversized Pub/Sub message data")
    if "messageId" in message and (not isinstance(message["messageId"], str) or len(message["messageId"]) > 256):
        raise ValueError("Invalid Pub/Sub message ID")
    if "publishTime" in message and (not isinstance(message["publishTime"], str) or len(message["publishTime"]) > 64):
        raise ValueError("Invalid Pub/Sub publish time")
    attributes = message.get("attributes", {})
    if (not isinstance(attributes, dict) or len(attributes) > 32
            or any(not isinstance(key, str) or len(key) > 128
                   or not isinstance(value, str) or len(value) > 1024
                   for key, value in attributes.items())):
        raise ValueError("Invalid Pub/Sub message attributes")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("Invalid Pub/Sub message encoding") from error
    if len(decoded) > MAX_DATA_BYTES:
        raise ValueError("Gmail notification data is too large")
    notification = _json_object(decoded)
    mailbox = notification.get("emailAddress")
    history_id = notification.get("historyId")
    if not isinstance(mailbox, str) or "@" not in mailbox or len(mailbox) > 320:
        raise ValueError("Invalid Gmail notification mailbox")
    if not isinstance(history_id, str) or not HISTORY_ID.fullmatch(history_id):
        raise ValueError("Invalid Gmail notification history ID")
    if mailbox.casefold() != expected_mailbox.strip().casefold():
        raise ValueError("Gmail notification mailbox does not match configured mailbox")
    return envelope


def handle_request(*, authorization, body, audience, service_account, expected_subscription,
                   expected_mailbox, verify_token, dispatch):
    if len(body) > MAX_BODY_BYTES:
        return 413
    if (not isinstance(authorization, str) or len(authorization) > MAX_TOKEN_BYTES
            or not re.fullmatch(r"Bearer [^\s]+", authorization)):
        return 401
    try:
        claims = verify_token(authorization[7:], audience)
    except ValueError:
        return 401
    except Exception:
        print("Gmail Pub/Sub token verification unavailable", file=sys.stderr)
        return 503
    if (not isinstance(claims, dict)
            or claims.get("iss") not in ALLOWED_ISSUERS
            or claims.get("aud") != audience
            or not isinstance(claims.get("email"), str)
            or claims["email"].casefold() != service_account.casefold()
            or claims.get("email_verified") is not True):
        return 401
    try:
        envelope = validate_envelope(
            body, expected_subscription=expected_subscription, expected_mailbox=expected_mailbox,
        )
    except ValueError:
        return 400
    try:
        dispatch(envelope)
    except Exception:
        # No request data, token, message body, or upstream response is logged.
        print("Gmail history processing failed; Pub/Sub may retry", file=sys.stderr)
        return 503
    return 204


def make_handler(*, audience, service_account, expected_subscription, expected_mailbox,
                 verify_token, dispatch):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.read_aborted = threading.Event()
            self.header_deadline = threading.Timer(SOCKET_TIMEOUT_SECONDS, self.abort_read)
            self.header_deadline.daemon = True
            self.header_deadline.start()

        def abort_read(self):
            self.read_aborted.set()
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        def parse_request(self):
            try:
                parsed = super().parse_request()
            except OSError:
                if self.read_aborted.is_set():
                    return False
                raise
            finally:
                self.header_deadline.cancel()
            return parsed and not self.read_aborted.is_set()

        def finish(self):
            self.header_deadline.cancel()
            super().finish()

        def log_message(self, _format, *_args):
            pass

        def respond(self, status):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "0" if status == 204 else "2")
            self.end_headers()
            if status != 204:
                self.wfile.write(b"{}")

        def do_POST(self):
            if self.read_aborted.is_set():
                return
            if self.path != "/gmail":
                return self.respond(404)
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                return self.respond(415)
            lengths = self.headers.get_all("Content-Length", [])
            if (self.headers.get("Transfer-Encoding") or len(lengths) != 1
                    or len(lengths[0]) > 10 or not lengths[0].isdigit()):
                return self.respond(400)
            authorizations = self.headers.get_all("Authorization", [])
            if len(authorizations) != 1:
                return self.respond(401)
            length = int(lengths[0])
            if length > MAX_BODY_BYTES:
                return self.respond(413)
            body_deadline = threading.Timer(SOCKET_TIMEOUT_SECONDS, self.abort_read)
            body_deadline.daemon = True
            body_deadline.start()
            try:
                body = self.rfile.read(length)
            except (OSError, socket.timeout):
                self.close_connection = True
                return
            finally:
                body_deadline.cancel()
            if self.read_aborted.is_set():
                return
            if len(body) != length:
                return self.respond(400)
            status = handle_request(
                authorization=authorizations[0], body=body, audience=audience,
                service_account=service_account, expected_subscription=expected_subscription,
                expected_mailbox=expected_mailbox, verify_token=verify_token, dispatch=dispatch,
            )
            self.respond(status)

    return Handler


def make_dispatch(worker_environment):
    def dispatch(envelope):
        result = subprocess.run(
            [sys.executable, str(WORKER)], input=json.dumps(envelope), text=True,
            capture_output=True, timeout=WORKER_TIMEOUT_SECONDS, env=worker_environment,
        )
        if result.returncode:
            raise RuntimeError("Gmail history worker did not complete successfully")

    return dispatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--service-account", required=True)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--mailbox", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    audience = urlsplit(args.audience)
    if audience.scheme != "https" or not audience.hostname or audience.username or audience.password or audience.fragment:
        parser.error("Use the configured HTTPS Pub/Sub push audience")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", args.service_account):
        parser.error("Use the configured Pub/Sub push service-account email")
    if not re.fullmatch(r"projects/[^/]+/subscriptions/[^/]+", args.subscription):
        parser.error("Use the exact Pub/Sub subscription resource name")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", args.mailbox):
        parser.error("Use the configured Gmail mailbox")
    if not 1 <= args.port <= 65535:
        parser.error("Use a valid receiver port")
    if os.environ.get("GMAIL_MAILBOX", "").casefold() != args.mailbox.casefold():
        parser.error("GMAIL_MAILBOX must match the configured receiver mailbox")
    required = ("PAPERCLIP_API_KEY", "PAPERCLIP_ORIGIN", "PAPERCLIP_COMPANY_ID", "PAPERCLIP_ROUTINE_ID",
                "GMAIL_READ_TOKEN_FILE", "GMAIL_HISTORY_CURSOR_FILE")
    if any(not os.environ.get(name) for name in required):
        parser.error("Configure the separate Gmail read-only and Paperclip receive settings")
    try:
        import google.auth.transport.requests  # noqa: F401
        import google.oauth2.id_token  # noqa: F401
    except ImportError:
        parser.error("Install the existing google-auth and requests host dependencies")

    worker_environment = os.environ.copy()
    handler = make_handler(
        audience=args.audience, service_account=args.service_account,
        expected_subscription=args.subscription, expected_mailbox=args.mailbox,
        verify_token=verify_google_token, dispatch=make_dispatch(worker_environment),
    )
    bind_host = os.environ.get("GMAIL_INGRESS_BIND_HOST", "127.0.0.1")
    if bind_host not in ("127.0.0.1", "0.0.0.0"):
        parser.error("GMAIL_INGRESS_BIND_HOST must be loopback or the isolated Compose interface")
    GmailHTTPServer((bind_host, args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
