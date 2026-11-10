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

import json
import os
import pathlib
import re
import sys
import tempfile

# A top-level block and the indented lines under it.
# terminal is generated per profile by bootstrap/install (sandbox root, image,
# forwarded credentials, repo-cache mount) and appended to the source, so one
# write replaces what used to be a hermes config set call per key.
BLOCKS = ("model", "platform_toolsets", "approvals", "skills", "terminal", "mcp_servers")


def write_atomic(target: pathlib.Path, text: str) -> None:
    """Replace a live config whole or not at all: an interrupted write leaves
    the old file, never an empty or partial one (#134 review)."""
    # Unique per call: two writers never share a temp file (#134 review).
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    tmp = pathlib.Path(name)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    # Keep the old file's owner and mode: install runs this as root through
    # docker exec, and a root-owned config locks Hermes and hermes-webui out.
    # A new file (fresh install) takes its folder's owner and mode 644, never
    # mkstemp's root-owned 600 (#134 review).
    if target.exists():
        st = target.stat()
        mode = st.st_mode & 0o777
    else:
        st = target.parent.stat()
        mode = 0o644
    os.chmod(tmp, mode)
    if hasattr(os, "chown"):
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except PermissionError:
            pass  # not root: the file is already ours
    os.replace(tmp, target)


def read_yaml_text(path: pathlib.Path) -> str:
    """A config's text, always ending in a newline: block_re matches whole
    lines, so a last line without one is missed (#134 review)."""
    text = path.read_text() if path.exists() else ""
    return text if not text or text.endswith("\n") else text + "\n"


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


def _split_top_level_entries(entries: str) -> list[str]:
    """Split an `mcp_servers` body into whole top-level entries (header line
    plus everything under it), each kept together as one chunk.

    The outermost indent is whatever the *first* non-blank line uses -- Ralph
    always writes two spaces, but a hand-added entry may use one, two or
    three (#134 review). Every other top-level entry in the same block uses
    that same width (anything narrower would already be invalid YAML: see
    merge_mcp_servers' own first-line-sets-the-width assumption). A line
    belongs to the entry above it while it is blank or indented deeper than
    that outermost width; a line back at the outermost width starts the next
    entry. This is done once, before any name or tag is considered, so a
    *nested* property several levels inside one entry's own config (e.g. a
    hand-added server whose own `url` key happens to share a name with an
    unrelated overlay grant) is never mistaken for a different entry's header
    (#134 review: matching `header_re` against every line regardless of depth
    let a grant named the same as any nested YAML key delete that key wherever
    it appeared, in any server's body).
    """
    lines = entries.splitlines(keepends=True)
    width = None
    for line in lines:
        if line.strip():
            width = len(line) - len(line.lstrip(" "))
            break
    if width is None:
        return []
    chunks, current = [], []
    for line in lines:
        at_top = line.strip() and (len(line) - len(line.lstrip(" "))) == width
        if at_top and current:
            chunks.append("".join(current))
            current = []
        current.append(line)
    if current:
        chunks.append("".join(current))
    return chunks



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
    # One entry per name: the first block wins, so a profile's own server is
    # never shadowed by a later block of the same name (#134 review).
    seen, kept = set(), []
    for m in blocks:
        body = m.group(0).split("\n", 1)[1]
        # Whole top-level entries, at whatever indent this block's own
        # entries use -- not a fixed width (#134 review).
        for entry in _split_top_level_entries(body):
            if not entry.strip():
                continue
            # Strip optional quotes before comparing: YAML treats `knowledge:`
            # and `"knowledge":` as the same key, so dedup must too (#134 review).
            key = entry.splitlines()[0].split(":", 1)[0].strip().strip('"').strip("'")
            if key not in seen:
                seen.add(key)
                kept.append(entry if entry.endswith("\n") else entry + "\n")
    entries = "".join(kept)
    for m in reversed(blocks):
        src = src[:m.start()] + src[m.end():]
    return src + "mcp_servers:\n" + entries


def main(src_path: str, dst_path: str) -> int:
    src = merge_mcp_servers(read_yaml_text(pathlib.Path(src_path)))
    target = pathlib.Path(dst_path)
    dst = read_yaml_text(target)

    for name in BLOCKS:
        dst = apply_block(name, src, dst)

    write_atomic(target, dst)
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
    text = read_yaml_text(target)
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
    write_atomic(target, text)
    return 0


# Servers Ralph itself writes; an overlay may not shadow them (#134 review).
RESERVED_MCP = {"knowledge", "lens-tools"}
YAML_WORDS = {"true", "false", "yes", "no", "on", "off", "y", "n", "null"}
# Marks a server line this script wrote to the global config, so a later run
# can find and replace exactly those lines without a separate state file.
OVERLAY_TAG = "# ralph-mcp-overlay"


def overlay_mcp_block(path: str, profile: str) -> str:
    """The `mcp_servers:` block a private overlay's mcp.json grants `profile`
    (#133), or "" for none. The file maps a server name to `profiles` (names
    without the ralph- prefix; `default` is the WebUI chat), `config` (Hermes'
    mcp_servers entry, e.g. url) and `tools` (the tool allowlist, required).
    JSON, not YAML: the init image has no YAML library."""
    servers = json.loads(pathlib.Path(path).read_text())
    out = []
    for name, spec in servers.items():
        if name in RESERVED_MCP:
            raise SystemExit(f"mcp.json: {name} is a built-in server name")
        # A name is a YAML key and an entry in install's comma list. YAML reads
        # bare true, null, on, 1 ... as non-strings (#134 review); Hermes then
        # sees a server named True. Plain names only, and never those words.
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", name) or name in YAML_WORDS:
            raise SystemExit(f"mcp.json: server name {name!r}: lowercase letter first, then a-z 0-9 _ -, not a YAML keyword")
        profiles = spec.get("profiles")
        # A string here would match by substring ("researcher" in "researchers").
        # Empty is rejected too: a server nobody is granted to is a mistake in
        # the file, not a silent no-op (#134 review).
        if not isinstance(profiles, list) or not profiles or not all(isinstance(x, str) for x in profiles):
            raise SystemExit(f"mcp.json: {name}: profiles must be a non-empty list of names")
        if profile not in profiles:
            continue
        tools = spec.get("tools")
        if not isinstance(tools, list) or not tools or not all(isinstance(x, str) for x in tools):
            raise SystemExit(f"mcp.json: {name} needs a tools allowlist (a non-empty list of names)")
        out.append(f"  {name}: {OVERLAY_TAG}")
        config = spec.get("config", {})
        if not isinstance(config, dict):
            raise SystemExit(f"mcp.json: {name}: config must be an object")
        for key, value in config.items():
            # A newline here would start a new, unindented YAML line -- an
            # unallowlisted server entry hidden inside this one (#134 review).
            if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", key):
                raise SystemExit(f"mcp.json: {name}: config key {key!r} must be a plain identifier")
            out.append(f"    {key}: {json.dumps(value)}")
        out.append("    enabled: true")
        out.append("    tools:")
        out.append(f"      include: {json.dumps(spec['tools'])}")
    return ("mcp_servers:\n" + "\n".join(out) + "\n") if out else ""


def _remove_entries(entries: str, header_re: str) -> str:
    """Remove every top-level `mcp_servers` entry whose header line matches
    `header_re`, along with every line under it. Operates on whole chunks
    from `_split_top_level_entries`, so a match can only ever be a header
    line, never a nested property several levels deep (#134 review).
    """
    kept = []
    for chunk in _split_top_level_entries(entries):
        header = chunk.splitlines()[0] if chunk.strip() else ""
        if header.strip() and re.match(header_re, header.lstrip(" ")):
            continue
        kept.append(chunk)
    return "".join(kept)


def _drop_entry(entries: str, name: str) -> str:
    # The owner's key may be quoted (`"name":`), and YAML allows (and
    # ignores) whitespace before the colon (#134 review: `name :` was not
    # matched and survived as a duplicate).
    quoted = rf"[\"']{re.escape(name)}[\"']"
    header_re = rf"(?:{re.escape(name)}|{quoted})[ \t]*:"
    return _remove_entries(entries, header_re)


def set_overlay_global(path: str, mcp_json: str) -> int:
    """For the global config (the default profile), which can hold servers the
    owner added by hand: drop every server this function wrote last time
    (tagged `# ralph-mcp-overlay` on its header line), then add what mcp.json
    currently grants `default`. The tag is the only memory needed -- no
    separate state file to keep in sync with the config (#134: the overlay
    is one in-tree thing, not two).
    """
    target = pathlib.Path(path)
    text = read_yaml_text(target)
    block = block_re("mcp_servers").search(text)
    entries = block.group(0).split("\n", 1)[1] if block else ""
    entries = _remove_entries(entries, rf"[^ \n]+:[^\n]*{re.escape(OVERLAY_TAG)}")
    add = overlay_mcp_block(mcp_json, "default") if mcp_json else ""
    # A new grant can reuse a name an untagged (hand-added) entry already has;
    # the grant wins that name, as it would if it were simply typed in by
    # hand over the old line. Checked against a live duplicate-key failure.
    for name in re.findall(r"^  ([A-Za-z0-9_.-]+):", add, re.M):
        entries = _drop_entry(entries, name)
    entries += add.split("\n", 1)[1] if add else ""
    new_block = ("mcp_servers:\n" + entries) if entries.strip() else ""
    text = (text[:block.start()] + new_block + text[block.end():]) if block else text + new_block
    write_atomic(target, text)
    return 0


def _strip_comment(text: str) -> str:
    """Drop a YAML comment (` #` outside quotes) from the end of a value."""
    quote = ""
    for i, ch in enumerate(text):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
    return text.rstrip()


def _existing_disabled(skills_body: str) -> tuple[list[str], re.Match | None]:
    """The names already under `skills.disabled`, in either list style, and
    the match covering the key plus its items."""
    found = re.search(r"^([ \t]+)disabled:[ \t]*(.*)\n((?:\1[ \t]*-[ \t]+.*\n)*)", skills_body, re.M)
    if not found:
        return [], None
    inline, items = _strip_comment(found.group(2).strip()), found.group(3)
    raw = inline.strip("[]").split(",") if inline.startswith("[") else []
    raw += [_strip_comment(line.strip()[1:].strip()) for line in items.splitlines()]
    names = [n.strip().strip("\"'") for n in raw]
    return [n for n in names if n], found


def disable_skills(names_path: str, config_path: str) -> int:
    """Add Ralph's managed names to `skills.disabled`, keeping any the owner
    disabled by hand. Replacing the list would silently re-enable them on every
    install. Names Ralph stops managing stay disabled until removed here too:
    the file cannot tell its own past entries from the owner's. Idempotent."""
    managed = [line.strip() for line in pathlib.Path(names_path).read_text().splitlines()
               if line.strip() and not line.lstrip().startswith("#")]
    target = pathlib.Path(config_path)
    text = target.read_text() if target.exists() else ""
    block = block_re("skills").search(text)
    body = block.group(0).split("\n", 1)[1] if block else ""
    existing, found = _existing_disabled(body)
    merged = existing + [n for n in managed if n not in existing]
    # json.dumps quotes every name, so none can be read as a comment or a separator.
    entry = "  disabled: [" + ", ".join(json.dumps(n) for n in merged) + "]\n"
    if found:
        body = body[:found.start()] + entry + body[found.end():]
    else:
        body += entry
    new_block = "skills:\n" + body
    if block:
        text = text[:block.start()] + new_block + text[block.end():]
    else:
        text = text + ("" if not text or text.endswith("\n") else "\n") + new_block
    target.write_text(text)
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "--disable-skills":
        raise SystemExit(disable_skills(sys.argv[2], sys.argv[3]))
    if sys.argv[1] == "--knowledge":
        raise SystemExit(set_knowledge(sys.argv[3], sys.argv[2] == "on"))
    if sys.argv[1] == "--overlay-check":
        # Validate every entry before install records any name as owned.
        servers = json.loads(pathlib.Path(sys.argv[2]).read_text())
        for prof in {p for spec in servers.values() if isinstance(spec, dict)
                     for p in (spec.get("profiles") if isinstance(spec.get("profiles"), list) else [])} | {"default"}:
            overlay_mcp_block(sys.argv[2], prof)
        for name, spec in servers.items():
            if not isinstance(spec, dict) or not isinstance(spec.get("profiles"), list):
                raise SystemExit(f"mcp.json: {name}: profiles must be a list of names")
        raise SystemExit(0)
    if sys.argv[1] == "--overlay-block":
        sys.stdout.write(overlay_mcp_block(sys.argv[2], sys.argv[3]))
        raise SystemExit(0)
    if sys.argv[1] == "--overlay-global":
        raise SystemExit(set_overlay_global(sys.argv[3], sys.argv[2]))
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
