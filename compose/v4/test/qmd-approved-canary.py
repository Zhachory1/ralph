"""Verify pinned qmd lexical search indexes only the approved Git snapshot."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

QMD_VERSION = "2.8.3"  # Matches compose/Dockerfile.knowledge.
RFC = "docs/1-projects/fixture/rfc.md"
APPROVED = "coppermeadow"
DRAFT = "violetdraft"
LOG = "embertranscript"


def check_search(executable, root):
    source = root / "source"
    source.mkdir()
    env = {
        "PATH": os.environ["PATH"], "HOME": str(root), "XDG_CACHE_HOME": str(root / "cache"),
        "QMD_CONFIG_DIR": str(root / "config"), "INDEX_PATH": str(root / "cache/index.sqlite"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    }
    (root / "cache").mkdir()
    (root / "config").mkdir()

    def git(*args, cwd=source):
        return subprocess.check_output(["git", *args], cwd=cwd, env=env, text=True, timeout=15).strip()

    def qmd(*args):
        return subprocess.check_output([executable, *args], cwd=root, env=env, text=True, timeout=30).strip()

    assert qmd("--version").split()[:2] == ["qmd", QMD_VERSION], f"Expected qmd {QMD_VERSION}"
    git("init", "--template=", "-b", "main")
    git("config", "user.name", "Knowledge Canary")
    git("config", "user.email", "canary@fixture.invalid")
    rfc = source / RFC
    rfc.parent.mkdir(parents=True)
    rfc.write_text(f"# Approved RFC\n\n{APPROVED}\n", encoding="utf-8")
    git("add", "--", RFC)
    git("commit", "-m", "Approve RFC")
    approved_commit = git("rev-parse", "HEAD")
    git("switch", "-c", "draft")
    rfc.write_text(f"# Draft RFC\n\n{DRAFT}\n", encoding="utf-8")
    git("commit", "-am", "Unapproved revision")
    (rfc.parent / "transient.md").write_text(f"# Transient run log\n\n{LOG}\n", encoding="utf-8")

    snapshot = root / "approved"
    git("worktree", "add", "--detach", str(snapshot), approved_commit)
    assert git("rev-parse", "HEAD", cwd=snapshot) == approved_commit
    assert not git("status", "--porcelain", cwd=snapshot)
    qmd("collection", "add", str(snapshot / "docs"), "--name", "approved", "--mask", "**/*.md")
    qmd("update")

    def results(term):
        output = qmd("search", term, "-c", "approved", "--format", "json")
        return [] if not output else json.loads(output)

    hits = results(APPROVED)
    assert len(hits) == 1 and hits[0]["file"] == "qmd://approved/1-projects/fixture/rfc.md", hits
    assert not results(DRAFT), "Unapproved draft was searchable"
    assert not results(LOG), "Transient log was searchable"
    print("PASS: approved RFC is searchable; draft revision and transient log are absent")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qmd", default="qmd", help="Path to the pinned qmd executable.")
    args = parser.parse_args()
    executable = shutil.which(args.qmd)
    if executable is None:
        parser.error(f"qmd {QMD_VERSION} is required; use --qmd to point at the installed executable")
    with TemporaryDirectory(prefix="ralph-qmd-fixture-") as directory:
        check_search(str(Path(executable).absolute()), Path(directory))
