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
BLOCKS = ("model", "platform_toolsets", "approvals", "skills", "terminal")
# mcp_servers is not copied here (#134): Ralph's own servers (knowledge, the
# private overlay's grants) are set directly on the installed profile with
# `hermes config set`/`unset` in bootstrap/install, never as merged text --
# Hermes already owns that file's YAML and already knows how to add or remove
# one key in it without disturbing anything else.


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
    # Zero or more indented lines: a block present but empty (`skills:` with
    # nothing under it) still matches, so apply_block replaces it in place.
    return re.compile(rf"^{name}:\n(?:[ \t]+.*\n)*", re.M)


def apply_block(name: str, src: str, dst: str) -> str:
    found = block_re(name).search(src)
    if not found:
        return dst
    if block_re(name).search(dst):
        return block_re(name).sub(lambda _: found.group(0), dst, count=1)
    return found.group(0) + dst


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


def main(src_path: str, dst_path: str) -> int:
    src = read_yaml_text(pathlib.Path(src_path))
    target = pathlib.Path(dst_path)
    dst = read_yaml_text(target)

    for name in BLOCKS:
        dst = apply_block(name, src, dst)

    write_atomic(target, dst)
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "--disable-skills":
        raise SystemExit(disable_skills(sys.argv[2], sys.argv[3]))
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
