"""Resolve Gmail history notifications into real messages before Paperclip intake."""
import base64
from datetime import datetime, timezone
import json
import os
import re
import subprocess
import sys
import tempfile
from email.header import decode_header, make_header
from email.utils import parseaddr
from pathlib import Path

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
MAX_HISTORY_PAGES = 100
MAX_MESSAGES_PER_BATCH = 500
MAX_MESSAGE_TEXT = 4000
WORKER = Path(__file__).with_name("comms-intake-worker.mjs")


class ExpiredHistory(Exception):
    """The stored Gmail cursor is no longer available from the provider."""


class HistoryBatchError(Exception):
    """A history batch could not be fully resolved and delivered."""


def _decoded_notification(raw):
    if not isinstance(raw, dict):
        raise ValueError("Expected a Gmail notification object")
    message = raw.get("message")
    if message is None:
        return raw
    if not isinstance(message, dict) or not isinstance(message.get("data"), str):
        raise ValueError("Invalid Pub/Sub notification")
    try:
        decoded = base64.b64decode(message["data"], validate=True)
        notification = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError("Invalid Gmail notification data") from error
    if not isinstance(notification, dict):
        raise ValueError("Invalid Gmail notification data")
    return notification


def notification_history_id(raw):
    notification = _decoded_notification(raw)
    address = notification.get("emailAddress")
    history_id = notification.get("historyId")
    if not isinstance(address, str) or "@" not in address:
        raise ValueError("Invalid Gmail notification mailbox")
    if not isinstance(history_id, str) or not re.fullmatch(r"[0-9]{1,20}", history_id):
        raise ValueError("Invalid Gmail notification historyId")
    return address, history_id


def _status_code(error):
    response = getattr(error, "resp", None)
    return getattr(response, "status", getattr(error, "status_code", None))


def _header(message, name):
    headers = (message.get("payload") or {}).get("headers") or []
    value = next((header.get("value", "") for header in headers
                  if isinstance(header, dict) and header.get("name", "").lower() == name.lower()), "")
    return str(make_header(decode_header(value)))[:500]


def _plain_text(message):
    def visit(part):
        if not isinstance(part, dict):
            return None
        if part.get("mimeType") == "text/plain" and not part.get("filename"):
            encoded = (part.get("body") or {}).get("data")
            if isinstance(encoded, str):
                try:
                    padded = encoded + "=" * (-len(encoded) % 4)
                    return base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8", "replace")
                except (ValueError, UnicodeDecodeError) as error:
                    raise HistoryBatchError("Gmail returned an invalid plain-text body") from error
        for child in part.get("parts", []) or []:
            text = visit(child)
            if text is not None:
                return text
        return None

    payload = message.get("payload") or {}
    text = visit(payload)
    if text is not None:
        return text[:MAX_MESSAGE_TEXT]
    return str(message.get("snippet", ""))[:MAX_MESSAGE_TEXT]


def _message_event(message, history_id, mailbox):
    message_id = message.get("id")
    thread_id = message.get("threadId")
    from_header = _header(message, "From")
    from_address = parseaddr(from_header)[1] or from_header
    subject = _header(message, "Subject")
    if (not isinstance(message_id, str) or not re.fullmatch(r"[0-9a-f]{8,40}", message_id, re.I)
            or not isinstance(thread_id, str) or not re.fullmatch(r"[0-9a-f]{8,40}", thread_id, re.I)
            or not re.search(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", from_address)):
        raise HistoryBatchError("Gmail returned a message without valid identity headers")
    internal_date = message.get("internalDate")
    try:
        received_at = datetime.fromtimestamp(
            int(internal_date) / 1000, tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        received_at = None
    event = {
        "kind": "gmail",
        "mailbox": mailbox,
        "messageId": message_id,
        "threadId": thread_id,
        "historyId": str(history_id),
        "from": from_address,
        "subject": subject,
        "text": _plain_text(message),
    }
    if received_at is not None:
        event["receivedAt"] = received_at
    return event


def process_history(*, service, notification, cursor, deliver, save_cursor):
    """Deliver all additions since cursor and only then persist the returned high-water mark."""
    mailbox, _ = notification_history_id(notification)
    if not isinstance(cursor, str) or not re.fullmatch(r"[0-9]{1,20}", cursor):
        raise ValueError("A saved Gmail history cursor is required")

    page_token = None
    pages = 0
    delivered_ids = set()
    latest_history_id = cursor
    while True:
        pages += 1
        if pages > MAX_HISTORY_PAGES:
            raise HistoryBatchError("Gmail history pagination limit exceeded")
        options = {
            "userId": "me",
            "startHistoryId": cursor,
            "historyTypes": ["messageAdded"],
        }
        if page_token:
            options["pageToken"] = page_token
        try:
            result = service.users().history().list(**options).execute()
        except Exception as error:
            if _status_code(error) == 404:
                raise ExpiredHistory("Gmail history cursor expired; cursor was not advanced") from error
            raise
        current_history_id = result.get("historyId")
        if (pages == 1 and isinstance(current_history_id, str)
                and re.fullmatch(r"[0-9]{1,20}", current_history_id)
                and int(current_history_id) >= int(latest_history_id)):
            latest_history_id = current_history_id
        for record in result.get("history", []) or []:
            record_history_id = record.get("id", latest_history_id)
            for added in record.get("messagesAdded", []) or []:
                reference = added.get("message") or {}
                message_id = reference.get("id")
                if not isinstance(message_id, str):
                    raise HistoryBatchError("Gmail history record has no message id")
                if message_id in delivered_ids:
                    continue
                if len(delivered_ids) >= MAX_MESSAGES_PER_BATCH:
                    raise HistoryBatchError("Gmail message batch limit exceeded")
                try:
                    message = service.users().messages().get(
                        userId="me", id=message_id, format="full",
                    ).execute()
                except Exception as error:
                    raise HistoryBatchError("Unable to resolve a Gmail history message") from error
                if message.get("id") != message_id:
                    raise HistoryBatchError("Gmail returned a different message than requested")
                deliver(_message_event(message, record_history_id, mailbox))
                delivered_ids.add(message_id)
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    save_cursor(latest_history_id)
    return {"messageCount": len(delivered_ids), "historyId": latest_history_id}


def read_cursor(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError("Initialize the Gmail history cursor through the approved setup procedure") from error
    if not isinstance(value, dict) or not isinstance(value.get("historyId"), str):
        raise ValueError("Invalid Gmail history cursor file")
    return value["historyId"]


def write_cursor(path, history_id):
    cursor_path = Path(path)
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=cursor_path.parent,
                                     prefix=f".{cursor_path.name}.", delete=False) as temp_file:
        json.dump({"historyId": history_id}, temp_file)
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temporary_path = temp_file.name
    os.replace(temporary_path, cursor_path)


def _deliver_to_paperclip(event):
    required = ("PAPERCLIP_API_KEY", "PAPERCLIP_ORIGIN", "PAPERCLIP_COMPANY_ID", "PAPERCLIP_ROUTINE_ID")
    if any(not os.environ.get(name) for name in required):
        raise ValueError("Configure the separate Gmail receive Paperclip credentials")
    result = subprocess.run(
        ["node", str(WORKER), os.environ["PAPERCLIP_ORIGIN"], os.environ["PAPERCLIP_COMPANY_ID"],
         os.environ["PAPERCLIP_ROUTINE_ID"], "gmail", os.environ.get("GMAIL_ALLOWLIST", "")],
        input=json.dumps(event), text=True, capture_output=True, timeout=30,
        env={"PATH": os.environ.get("PATH", ""), "PAPERCLIP_API_KEY": os.environ["PAPERCLIP_API_KEY"]},
    )
    if result.returncode:
        raise HistoryBatchError("Paperclip did not accept the resolved Gmail message")


def _gmail_service():
    token_file = os.environ.get("GMAIL_READ_TOKEN_FILE")
    if not token_file:
        raise ValueError("Set a separate Gmail read-only token file")
    token_data = json.loads(Path(token_file).read_text(encoding="utf-8"))
    scopes = set(token_data.get("scopes", []))
    if scopes != {GMAIL_READONLY_SCOPE}:
        raise ValueError("Gmail intake requires its own gmail.readonly-only OAuth token")
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    credentials = Credentials.from_authorized_user_file(token_file, scopes=[GMAIL_READONLY_SCOPE])
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def main():
    notification = json.load(sys.stdin)
    mailbox, _ = notification_history_id(notification)
    expected_mailbox = os.environ.get("GMAIL_MAILBOX")
    if not expected_mailbox or mailbox.casefold() != expected_mailbox.casefold():
        raise ValueError("Gmail notification mailbox does not match configured receive account")
    cursor_file = os.environ.get("GMAIL_HISTORY_CURSOR_FILE", "/var/lib/ralph-gmail/history.json")
    result = process_history(
        service=_gmail_service(), notification=notification, cursor=read_cursor(cursor_file),
        deliver=_deliver_to_paperclip, save_cursor=lambda value: write_cursor(cursor_file, value),
    )
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
