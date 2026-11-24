"""Authenticate Google Chat events and send them to the existing Routine intake."""
import argparse
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

CHAT_SERVICE_ACCOUNT = "chat@system.gserviceaccount.com"
MAX_BODY_BYTES = 256 * 1024
MAX_TOKEN_BYTES = 8192
SOCKET_TIMEOUT_SECONDS = 10
WORKER = Path(__file__).with_name("comms-intake-worker.mjs")


class VerificationUnavailable(Exception):
    """Google's verification service could not be reached."""


class InvalidChatEvent(Exception):
    """The existing intake worker rejected the event."""


class ChatHTTPServer(HTTPServer):
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


def handle_request(*, authorization, body, audience, verify_token, dispatch):
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
        print("Chat token verification unavailable", file=sys.stderr)
        return 503
    if (not isinstance(claims, dict) or claims.get("email") != CHAT_SERVICE_ACCOUNT
            or claims.get("email_verified") is not True):
        return 401
    try:
        event = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return 400
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        return 400
    if event["type"] != "MESSAGE":
        return 200
    space, message = event.get("space"), event.get("message")
    if not isinstance(space, dict) or not isinstance(message, dict):
        return 400
    sender = message.get("sender")
    if not isinstance(sender, dict) or not isinstance(sender.get("type"), str):
        return 400
    if sender["type"] != "HUMAN":
        return 200
    client_id = message.get("clientAssignedMessageId")
    if isinstance(client_id, str) and client_id.startswith("client-ralph-"):
        return 200
    if not all(isinstance(value, str) and value for value in (
        space.get("name"), message.get("name"), message.get("text"),
    )):
        return 400
    thread = message.get("thread")
    if thread is None:
        thread = event.get("thread")
    if thread is not None and (not isinstance(thread, dict) or not isinstance(thread.get("name"), str)):
        return 400
    normalized_message = {
        "name": message["name"],
        "text": message["text"],
        "sender": {"type": sender["type"]},
    }
    if thread is not None:
        normalized_message["thread"] = thread["name"]
    for key in ("createTime", "clientAssignedMessageId"):
        if key in message:
            normalized_message[key] = message[key]
    normalized = {"kind": "chat", "type": "MESSAGE", "space": space["name"], "message": normalized_message}
    try:
        dispatch(normalized)
    except InvalidChatEvent:
        return 400
    except Exception:
        print("Chat intake dispatch failed", file=sys.stderr)
        return 503
    return 200


def make_handler(*, audience, verify_token, dispatch):
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
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_POST(self):
            if self.read_aborted.is_set():
                return
            if self.path != "/chat":
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
                authorization=authorizations[0],
                body=body, audience=audience, verify_token=verify_token, dispatch=dispatch,
            )
            self.respond(status)

    return Handler


def make_dispatch(origin, company_id, routine_id, spaces, token):
    def dispatch(event):
        result = subprocess.run(
            ["node", str(WORKER), origin, company_id, routine_id, "chat", spaces],
            input=json.dumps(event), text=True, capture_output=True, timeout=25,
            env={"PATH": os.environ.get("PATH", ""), "PAPERCLIP_API_KEY": token},
        )
        if result.returncode == 2:
            raise InvalidChatEvent()
        if result.returncode:
            raise RuntimeError("Paperclip intake failed")

    return dispatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--company-id", required=True)
    parser.add_argument("--routine-id", required=True)
    parser.add_argument("--spaces", required=True)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    url = urlsplit(args.audience)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        parser.error("Use the configured HTTPS Google Chat endpoint as the audience")
    if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]+", args.origin):
        parser.error("Use a loopback Paperclip origin")
    uuid = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
    if not re.fullmatch(uuid, args.company_id) or not re.fullmatch(uuid, args.routine_id):
        parser.error("Use valid Paperclip company and Routine IDs")
    spaces = args.spaces.split(",")
    if (not 1 <= len(spaces) <= 16 or len(set(spaces)) != len(spaces)
            or any(not re.fullmatch(r"spaces/[A-Za-z0-9_-]+", space) for space in spaces)):
        parser.error("Use a nonempty Chat space allowlist")
    if not 1 <= args.port <= 65535:
        parser.error("Use a valid loopback port")
    token = os.environ.pop("PAPERCLIP_API_KEY", "")
    if not token:
        parser.error("Set PAPERCLIP_API_KEY for the intake bridge")
    try:
        import google.auth.transport.requests  # noqa: F401
        import google.oauth2.id_token  # noqa: F401
    except ImportError:
        parser.error("Install the existing google-auth and requests host dependencies")
    handler = make_handler(
        audience=args.audience, verify_token=verify_google_token,
        dispatch=make_dispatch(args.origin, args.company_id, args.routine_id, args.spaces, token),
    )
    ChatHTTPServer(("127.0.0.1", args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
