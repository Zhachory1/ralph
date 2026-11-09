#!/usr/bin/env python3
"""The one staged-evidence reader `chat-reply` and `chat-route` both call.

Given a work root and an operation id, `read_item` returns the validated
`evidence/item.json` record or raises `Invalid` carrying the one reason it
refused. It replaces two drifting copies of the same checks, so both callers
now run every one of them: the workspace, its `evidence` directory and the
item file are each free of symlinks; the file is a regular file with exactly
one hard link, at most 64 KiB, and mode bits outside owner-read-write unset;
its JSON names an inbound message from a trusted sender, in a well-formed
space and thread, whose latest message name hashes to the operation id; and
`prior_operations` is a list of at most 20 other operation ids, none of them
this one.

Deliberately not checked here: whether the space is one this instance allows
(`CHAT_ROUTE_SPACES`). That list is `chat-reply`'s own input, not part of the
staged record, so the caller checks it after reading the item.
"""

import hashlib
import json
import re
import stat

OP_ID = re.compile(r"chat-[0-9a-f]{64}\Z")
SPACE = re.compile(r"spaces/[A-Za-z0-9_-]+\Z")
THREAD = re.compile(r"spaces/[A-Za-z0-9_-]+/threads/[A-Za-z0-9_.-]+\Z")
MAX_ITEM_BYTES = 65_536
MAX_PRIOR_OPERATIONS = 20


class Invalid(ValueError):
    """A staged evidence record failed one of the checks `read_item` runs."""


def read_item(root, op_id):
    workspace = root / op_id
    evidence = workspace / "evidence"
    path = evidence / "item.json"
    if workspace.is_symlink() or evidence.is_symlink() or path.is_symlink():
        raise Invalid("unsafe Chat evidence")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 \
            or info.st_size > MAX_ITEM_BYTES or info.st_mode & 0o077:
        raise Invalid("unsafe Chat evidence")
    try:
        item = json.loads(path.read_text())
        if item.get("kind") != "inbound" or item.get("sender_trusted") is not True:
            raise Invalid("Chat item is not a trusted inbound message")
        space, thread = item.get("space"), item.get("thread")
        messages = item.get("messages")
        message = messages[-1].get("name") if isinstance(messages, list) and messages else None
        prior = item.get("prior_operations", [])
    except (AttributeError, TypeError, KeyError, IndexError, json.JSONDecodeError) as error:
        raise Invalid("malformed Chat evidence") from error
    if not isinstance(space, str) or not SPACE.fullmatch(space) \
            or not isinstance(thread, str) or not THREAD.fullmatch(thread) \
            or not thread.startswith(space + "/threads/") \
            or not isinstance(message, str) or not message.startswith(space + "/messages/"):
        raise Invalid("Chat destination does not match its own message")
    if op_id != "chat-" + hashlib.sha256(message.encode()).hexdigest():
        raise Invalid("Chat operation does not match message")
    if not isinstance(prior, list) or len(prior) > MAX_PRIOR_OPERATIONS \
            or any(not isinstance(p, str) or not OP_ID.fullmatch(p) or p == op_id for p in prior):
        raise Invalid("invalid prior Chat operations")
    return item
