"""Copy the model block from a repo profile into an installed profile.

Only the model block is Ralph's. Everything else in a Hermes config.yaml is
generated and version-specific, so it is left exactly as found. A freshly
created profile has no config.yaml at all until something writes one.
"""

import pathlib
import re
import sys

MODEL_BLOCK = re.compile(r"^model:\n(?:  .*\n)+", re.M)


def main(src: str, dst: str) -> int:
    block = MODEL_BLOCK.search(pathlib.Path(src).read_text())
    if not block:
        return 0

    target = pathlib.Path(dst)
    if not target.exists():
        target.write_text(block.group(0))
        return 0

    existing = target.read_text()
    if MODEL_BLOCK.search(existing):
        existing = MODEL_BLOCK.sub(block.group(0), existing, count=1)
    else:
        existing = block.group(0) + existing
    target.write_text(existing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
