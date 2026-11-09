"""refresh-knowledge (#121, dd-ralph-knowledge D3): fast-forwards the docs
checkout, re-indexes, and on failure keeps the index and records the error.
qmd is stubbed; git is real, against a local bare repository.

    python3 evals/test_refresh_knowledge.py
"""

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
REFRESH = ROOT / "bootstrap" / "refresh-knowledge"


def git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


class RefreshTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = d = pathlib.Path(tmp.name)
        self.origin, self.work, self.root = d / "origin.git", d / "work", d / "k"
        git("init", "-q", "--bare", "-b", "main", str(self.origin))
        git("clone", "-q", str(self.origin), str(self.work))
        self.commit("a.md", "first")
        git("clone", "-q", str(self.origin), str(self.root / "repo"))
        stub = d / "bin"
        stub.mkdir()
        (stub / "qmd").write_text('#!/bin/sh\necho "qmd $*" >> "$CALLS"\n[ "$1" != "$QMD_FAIL" ]\n')
        (stub / "qmd").chmod(0o755)
        self.env = {"PATH": f"{stub}:/usr/bin:/bin", "CALLS": str(d / "calls"),
                    "KNOWLEDGE_ROOT": str(self.root), "GH_TOKEN": "t", "HOME": str(d)}

    def commit(self, name, text):
        (self.work / name).write_text(text)
        git("add", name, cwd=self.work)
        git("commit", "-qm", name, cwd=self.work)
        git("push", "-q", "origin", "HEAD:main", cwd=self.work)

    def refresh(self, **extra):
        return subprocess.run(["bash", str(REFRESH)], env={**self.env, **extra},
                              capture_output=True, text=True)

    def status(self):
        return json.loads((self.root / "status.json").read_text())

    def test_fast_forwards_and_reindexes(self):
        self.commit("memory.md", "new note")
        r = self.refresh()
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertTrue((self.root / "repo" / "memory.md").exists())
        calls = (self.d / "calls").read_text()
        self.assertIn("qmd update", calls)
        self.assertIn("qmd embed", calls)
        s = self.status()
        self.assertTrue(s["ok"])
        self.assertEqual(s["last_attempt"], s["last_success"])
        self.assertEqual("", s["error"])

    def test_fetch_failure_keeps_the_index_and_records_the_error(self):
        self.assertEqual(0, self.refresh().returncode)
        first = self.status()["last_success"]
        (self.d / "calls").unlink()
        self.origin.rename(self.d / "gone.git")
        r = self.refresh()
        self.assertNotEqual(0, r.returncode)
        self.assertFalse((self.d / "calls").exists(), "a failed fetch must not touch the index")
        self.assertTrue((self.root / "repo" / "a.md").exists())
        s = self.status()
        self.assertFalse(s["ok"])
        self.assertIn("fetch failed", s["error"])
        self.assertEqual(first, s["last_success"])

    def test_a_failed_first_clone_is_retried(self):
        """#125 review: no checkout yet, so refresh clones; a failure retries next time."""
        import shutil as sh
        sh.rmtree(self.root / "repo")
        # Hermetic: the GitHub URL resolves to the local origin, never the network.
        (self.d / ".gitconfig").write_text(
            f'[url "file://{self.origin}"]\n\tinsteadOf = https://github.com/o/docs.git\n')
        hidden = self.d / "hidden.git"
        self.origin.rename(hidden)
        r = self.refresh(KNOWLEDGE_REPO="o/docs")
        self.assertNotEqual(0, r.returncode)
        self.assertIn("clone", self.status()["error"])
        hidden.rename(self.origin)
        r = self.refresh(KNOWLEDGE_REPO="o/docs")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertTrue((self.root / "repo" / "a.md").exists())
        self.assertTrue(self.status()["ok"])

    def test_embed_failure_is_recorded(self):
        r = self.refresh(QMD_FAIL="embed")
        self.assertNotEqual(0, r.returncode)
        s = self.status()
        self.assertFalse(s["ok"])
        self.assertIn("embed failed", s["error"])
        self.assertEqual("", s["last_success"])


    @unittest.skipUnless(shutil.which("flock"), "flock (util-linux) is in the knowledge image, not macOS")
    def test_refreshes_are_serialized(self):
        """#125 review: a second refresh waits for the first's lock."""
        self.root.mkdir(exist_ok=True)
        lock = open(self.root / ".refresh.lock", "w")
        self.addCleanup(lock.close)
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX)
        with self.assertRaises(subprocess.TimeoutExpired):
            subprocess.run(["bash", str(REFRESH)], env=self.env, capture_output=True, timeout=2)
        self.assertFalse((self.d / "calls").exists())
        fcntl.flock(lock, fcntl.LOCK_UN)
        self.assertEqual(0, self.refresh().returncode)


class LoopTests(unittest.TestCase):
    def test_review_loop_refreshes_hourly_when_the_service_runs(self):
        text = (ROOT / "bootstrap" / "run-review-loop").read_text()
        self.assertIn('kstamp="$WORK/.knowledge-stamp"', text)
        self.assertIn('-mmin -60', text)
        self.assertIn("docker inspect -f '{{.State.Running}}' \"$kc\"", text)
        self.assertIn("/ralph/bootstrap/refresh-knowledge", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
