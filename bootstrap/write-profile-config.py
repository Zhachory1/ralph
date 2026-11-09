"""Copy the blocks Ralph owns from a repo profile into an installed profile.

Ralph owns four things in a Hermes config.yaml: which model a profile runs,
which toolsets it may use, whether its unattended runs may use the tools that
would otherwise wait for an approval nobody is there to give, and which skill
lanes it mounts. Everything else is generated and version-specific, so it is
left exactly as found.

Toolsets are not cosmetic. A profile without the toolset its job needs reads
its brief, reasons about the work, and then cannot act: the orchestrator went
a whole feature without the kanban toolset, so it could describe a card graph
and never create one. Copying only the model block is what hid that.

Skills are the same failure in a new place. This list is an allowlist, so a
block it does not name is dropped in silence: the profile files said which
lanes to mount, the installed configs did not, and `skills list` showed the
builtins alone. A block added to a profile and not added here does nothing,
and says nothing about doing nothing.

A freshly created profile has no config.yaml at all until something writes one.
"""

import pathlib
import re
import sys

# A top-level block and the indented lines under it.
# terminal is generated per profile by bootstrap/install (sandbox root, image,
# forwarded credentials, repo-cache mount) and appended to the source, so one
# write replaces what used to be a hermes config set call per key.
BLOCKS = ("model", "platform_toolsets", "approvals", "skills", "terminal", "mcp_servers")


def block_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"^{name}:\n(?:[ \t]+.*\n)+", re.M)


def apply_block(name: str, src: str, dst: str) -> str:
    found = block_re(name).search(src)
    if not found:
        # mcp_servers is wholly Ralph's in a profile config: absent from the
        # source means none, so a stale entry (knowledge after the feature is
        # turned off) is removed. Other blocks keep Hermes' own value.
        if name == "mcp_servers":
            return block_re(name).sub("", dst, count=1)
        return dst
    if block_re(name).search(dst):
        return block_re(name).sub(lambda _: found.group(0), dst, count=1)
    return found.group(0) + dst


def merge_mcp_servers(src: str) -> str:
    """Fold every top-level `mcp_servers:` block in the source into one.

    install appends the knowledge server (#121) as its own `mcp_servers:` block
    after the profile's config; a profile with lens-tools then has two, and
    the copy below takes only the first. Server entries are the indented lines
    under each block, so joining them under one header keeps both servers.
    """
    blocks = list(block_re("mcp_servers").finditer(src))
    if len(blocks) < 2:
        return src
    entries = "".join(m.group(0).split("\n", 1)[1] for m in blocks)
    for m in reversed(blocks):
        src = src[:m.start()] + src[m.end():]
    return src + "mcp_servers:\n" + entries


def main(src_path: str, dst_path: str) -> int:
    src = merge_mcp_servers(pathlib.Path(src_path).read_text())
    target = pathlib.Path(dst_path)
    dst = target.read_text() if target.exists() else ""

    for name in BLOCKS:
        dst = apply_block(name, src, dst)

    target.write_text(dst)
    return 0


KNOWLEDGE_ENTRY = (
    "  knowledge:\n"
    '    url: "http://knowledge:8181/mcp"\n'
    "    enabled: true\n"
    "    tools:\n"
    "      include: [query, get, multi_get, status]\n"
)


def set_knowledge(path: str, on: bool) -> int:
    """Add or remove only the `knowledge` server in a config's mcp_servers,
    leaving every other server untouched (the global config can hold servers
    the owner added by hand). Idempotent."""
    target = pathlib.Path(path)
    text = target.read_text() if target.exists() else ""
    block = block_re("mcp_servers").search(text)
    entries = block.group(0).split("\n", 1)[1] if block else ""
    # Drop any existing knowledge entry: its header line and its deeper lines.
    entry_re = re.compile(r"^  knowledge:\n(?:    .*\n|\s*\n)*", re.M)
    entries = entry_re.sub("", entries)
    if on:
        entries += KNOWLEDGE_ENTRY
    new_block = ("mcp_servers:\n" + entries) if entries.strip() else ""
    if block:
        text = text[:block.start()] + new_block + text[block.end():]
    else:
        text = text + new_block
    target.write_text(text)
    return 0


def overlay_mcp_block(path: str, profile: str) -> str:
    """The `mcp_servers:` block a private overlay's mcp.json grants `profile`
    (#133), or "" for none. The file maps a server name to `profiles` (names
    without the ralph- prefix; `default` is the WebUI chat), `config` (Hermes'
    mcp_servers entry, e.g. url) and `tools` (the tool allowlist, required).
    JSON, not YAML: the init image has no YAML library."""
    import json
    servers = json.loads(pathlib.Path(path).read_text())
    out = []
    for name, spec in servers.items():
        if profile not in spec.get("profiles", []):
            continue
        if not spec.get("tools"):
            raise SystemExit(f"mcp.json: {name} has no tools allowlist")
        out.append(f"  {name}:")
        for key, value in spec.get("config", {}).items():
            out.append(f"    {key}: {json.dumps(value)}")
        out.append("    enabled: true")
        out.append("    tools:")
        out.append(f"      include: {json.dumps(spec['tools'])}")
    return ("mcp_servers:\n" + "\n".join(out) + "\n") if out else ""


def set_overlay_global(path: str, mcp_json: str, previous: str) -> int:
    """For the global config (the default profile), which can hold servers the
    owner added by hand: drop the overlay servers it had before (`previous`,
    comma separated), then add the ones mcp.json grants `default`."""
    target = pathlib.Path(path)
    text = target.read_text() if target.exists() else ""
    block = block_re("mcp_servers").search(text)
    entries = block.group(0).split("\n", 1)[1] if block else ""
    names = [n for n in previous.split(",") if n]
    for name in names:
        entries = re.sub(rf"^  {re.escape(name)}:\n(?:    .*\n|\s*\n)*", "", entries, flags=re.M)
    add = overlay_mcp_block(mcp_json, "default") if mcp_json else ""
    entries += add.split("\n", 1)[1] if add else ""
    new_block = ("mcp_servers:\n" + entries) if entries.strip() else ""
    text = (text[:block.start()] + new_block + text[block.end():]) if block else text + new_block
    target.write_text(text)
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "--knowledge":
        raise SystemExit(set_knowledge(sys.argv[3], sys.argv[2] == "on"))
    if sys.argv[1] == "--overlay-block":
        sys.stdout.write(overlay_mcp_block(sys.argv[2], sys.argv[3]))
        raise SystemExit(0)
    if sys.argv[1] == "--overlay-global":
        raise SystemExit(set_overlay_global(sys.argv[4], sys.argv[2], sys.argv[3]))
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
