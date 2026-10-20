"""Copy the blocks Ralph owns from a repo profile into an installed profile.

Ralph owns three things in a Hermes config.yaml: which model a profile runs,
which toolsets it may use, and whether its unattended runs may use the tools
that would otherwise wait for an approval nobody is there to give. Everything else is generated and version-specific,
so it is left exactly as found.

Toolsets are not cosmetic. A profile without the toolset its job needs reads
its brief, reasons about the work, and then cannot act: the orchestrator went
a whole feature without the kanban toolset, so it could describe a card graph
and never create one. Copying only the model block is what hid that.

A freshly created profile has no config.yaml at all until something writes one.
"""

import pathlib
import re
import sys

# A top-level block and the indented lines under it.
BLOCKS = ("model", "platform_toolsets", "approvals")


def block_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"^{name}:\n(?:[ \t]+.*\n)+", re.M)


def apply_block(name: str, src: str, dst: str) -> str:
    found = block_re(name).search(src)
    if not found:
        return dst
    if block_re(name).search(dst):
        return block_re(name).sub(lambda _: found.group(0), dst, count=1)
    return found.group(0) + dst


def main(src_path: str, dst_path: str) -> int:
    src = pathlib.Path(src_path).read_text()
    target = pathlib.Path(dst_path)
    dst = target.read_text() if target.exists() else ""

    for name in BLOCKS:
        dst = apply_block(name, src, dst)

    target.write_text(dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
