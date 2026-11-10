#!/usr/bin/env python3
"""Private-overlay MCP grants (#133): read mcp.json, validate it, and print
what install should apply to one profile's config with `hermes config set` /
`config unset`.

No YAML is read or written here. Hermes already owns its config files and
already has a command for "set one key" and "remove one key" that does not
disturb anything else in the file -- so the earlier version of this file,
which hand-edited YAML text to fold overlay entries in beside a profile's own
servers, was solving a problem Hermes had already solved. That version spent
many review rounds chasing indentation, quoting and comment-placement bugs in
its own regex-based entry splitter; none of those bugs can exist here, because
there is no text to parse.

    overlay-grants.py --check mcp.json                     # validate only, exit nonzero on error
    overlay-grants.py mcp.json <profile> <current-json>    # print "unset" / "set" lines

<current-json> is the profile's own mcp_servers, as `hermes config get
mcp_servers --json` already prints it ("{}" if the key is absent). Each
server this script ever granted carries its own `_overlay: true` -- a plain
JSON field written and read by hermes config, not a text marker -- so a name
removed from mcp.json entirely (not merely unassigned from this profile) is
still found and unset: it is any key in <current-json> with `_overlay: true`
that the current mcp.json no longer grants this profile. A hand-added server
never carries that field and is never touched in either direction.

mcp.json maps a server name to:
    profiles  non-empty list of profile base names ("default" = the WebUI
              chat); the server is granted only to the profiles named here
    config    the Hermes mcp_servers entry's own keys (e.g. url)
    tools     a non-empty allowlist (tools.include), required

Output, one directive per line, unset directives first. Each line is always
"<verb> <path> [<value...>]"; a bash `read -r verb path value` splits it
correctly even when <value> itself contains spaces (a JSON list), because
read's last variable absorbs the rest of the line:
    unset mcp_servers.<name>
    set   mcp_servers.<name>.<config-key> <value>
    set   mcp_servers.<name>.enabled true
    set   mcp_servers.<name>.tools.include ["a", "b"]

install runs each as `hermes <profile flags> config <verb> <path> [<value>]`.
Every name in mcp.json is unset for the profile before any grant is (re-)set,
so a name the profile no longer has is simply gone -- no record of prior
grants is needed, because the overlay file itself is the complete, current
list of names it has ever touched. A name install never sees here (a
hand-added server, `lens-tools`, `knowledge`) is never unset or set.
"""

import json
import pathlib
import re
import sys

RESERVED = {"knowledge", "lens-tools"}
NAME_RE = re.compile(r"[a-z][a-z0-9_-]*")


def load(path: str) -> dict:
    servers = json.loads(pathlib.Path(path).read_text())
    if not isinstance(servers, dict):
        raise SystemExit("mcp.json: must be a JSON object")
    for name, spec in servers.items():
        if name in RESERVED:
            raise SystemExit(f"mcp.json: {name} is a built-in server name")
        if not NAME_RE.fullmatch(name):
            raise SystemExit(f"mcp.json: server name {name!r}: lowercase letter first, then a-z 0-9 _ -")
        if not isinstance(spec, dict):
            raise SystemExit(f"mcp.json: {name}: must be an object")
        profiles = spec.get("profiles")
        if not isinstance(profiles, list) or not profiles or not all(isinstance(p, str) for p in profiles):
            raise SystemExit(f"mcp.json: {name}: profiles must be a non-empty list of names")
        tools = spec.get("tools")
        if not isinstance(tools, list) or not tools or not all(isinstance(t, str) for t in tools):
            raise SystemExit(f"mcp.json: {name} needs a tools allowlist (a non-empty list of names)")
        config = spec.get("config", {})
        if not isinstance(config, dict):
            raise SystemExit(f"mcp.json: {name}: config must be an object")
    return servers


def directives(servers: dict, profile: str, current: dict) -> list[str]:
    granted = {name for name, spec in servers.items() if profile in spec["profiles"]}
    out = []
    # Anything this script granted before (marked _overlay: true) that the
    # current file no longer grants: unset, even if the name is gone from
    # mcp.json entirely, not merely dropped from this profile's list.
    for name, cur in current.items():
        if isinstance(cur, dict) and cur.get("_overlay") is True and name not in granted:
            out.append(f"unset mcp_servers.{name}")
    # Every currently granted name, so a changed config/tools value replaces
    # the old one instead of merging with it.
    for name in granted:
        out.append(f"unset mcp_servers.{name}")
    for name in granted:
        spec = servers[name]
        for key, value in spec.get("config", {}).items():
            # `hermes config set` stores a string argument literally: it does
            # not decode it as JSON first, so json.dumps-ing a string value
            # (which wraps it in quote characters) stored those quote
            # characters as part of the string itself. A string is passed as
            # is; only a non-string value is dumped, since a shell argument
            # has no other way to carry a number, bool, list or null.
            rendered = value if isinstance(value, str) else json.dumps(value)
            out.append(f"set mcp_servers.{name}.{key} {rendered}")
        out.append(f"set mcp_servers.{name}.enabled true")
        out.append(f"set mcp_servers.{name}.tools.include {json.dumps(spec['tools'])}")
        out.append(f"set mcp_servers.{name}._overlay true")
    return out


if __name__ == "__main__":
    if sys.argv[1] == "--check":
        load(sys.argv[2])
        raise SystemExit(0)
    current = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    for line in directives(load(sys.argv[1]), sys.argv[2], current):
        print(line)
