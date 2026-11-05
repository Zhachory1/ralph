"""run-review-loop's startup: which scope it reads and whose token it trusts.

Runs the real script with stub `gh`, `python3` and `sleep` on PATH, so no
network, no board, and one pass only.

    python3 evals/test_run_review_loop.py
"""

import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "run-review-loop"


class StartupTests(unittest.TestCase):
    def run_loop(self, **env):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            stub = d / "bin"
            stub.mkdir()
            calls = d / "calls"
            # gh: `api user` answers as the stub login; record PR search flags.
            (stub / "gh").write_text(
                "#!/bin/sh\n"
                '[ "$1 $2" = "api user" ] && { echo "${STUB_LOGIN}"; exit 0; }\n'
                '[ "$1 $2" = "search prs" ] && printf "%s\\n" "$*" > "$GH_SEARCH"\n'
                "exit 0\n")
            # python3: record the allowlist each discover-reviews call is given.
            (stub / "python3").write_text(
                "#!/bin/sh\n"
                'while [ $# -gt 0 ]; do [ "$1" = --allow-file ] && cat "$2" >> "$CALLS"; shift; done\n'
                "exit 0\n")
            # sleep ends the loop after one pass: the loop ignores its status,
            # so it kills its caller.
            (stub / "sleep").write_text("#!/bin/sh\nkill $PPID\n")
            (stub / "docker").write_text("#!/bin/sh\nexit 0\n")
            for f in stub.iterdir():
                f.chmod(0o755)
            search = d / "gh-search"
            full = {"PATH": f"{stub}:/usr/bin:/bin", "TMPDIR": str(d),
                    "CALLS": str(calls), "GH_SEARCH": str(search),
                    "RALPH_HERMES_CONTAINER": "x",
                    "RALPH_REVIEW_ALLOWLIST": str(d / "missing.txt"), **env}
            r = subprocess.run(["bash", str(SCRIPT)], env=full,
                               capture_output=True, text=True, timeout=30)
            self.gh_search = search.read_text() if search.exists() else ""
            return r, calls.read_text() if calls.exists() else ""

    def test_inline_allow_is_one_repo_per_line(self):
        _, allow = self.run_loop(RALPH_REVIEW_ALLOW="Zhachory1/*  ROKT/op3", STUB_LOGIN="")
        self.assertEqual(["Zhachory1/*", "ROKT/op3"], allow.split())

    def test_file_is_used_when_inline_is_unset(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("owner/from-file\n")
        try:
            _, allow = self.run_loop(RALPH_REVIEW_ALLOWLIST=f.name, STUB_LOGIN="")
            self.assertEqual("owner/from-file\n", allow)
        finally:
            os.unlink(f.name)

    def test_wrong_account_refuses_to_start(self):
        r, allow = self.run_loop(RALPH_GITHUB_LOGIN="Zhachory1", STUB_LOGIN="zhach1",
                                 RALPH_REVIEW_ALLOW="Zhachory1/*")
        self.assertEqual(1, r.returncode)
        self.assertIn("refusing to start", r.stderr)
        self.assertEqual("", allow, "nothing may be admitted under the wrong account")

    def test_unreachable_github_retries_instead_of_refusing(self):
        """A network blip at startup must not read as the wrong account."""
        r, allow = self.run_loop(RALPH_GITHUB_LOGIN="Zhachory1", STUB_LOGIN="",
                                 RALPH_REVIEW_ALLOW="Zhachory1/*")
        self.assertNotIn("refusing to start", r.stderr)
        self.assertIn("retrying", r.stderr)
        self.assertEqual("", allow, "nothing may be admitted before identity is checked")

    def test_matching_account_starts(self):
        r, allow = self.run_loop(RALPH_GITHUB_LOGIN="Zhachory1", STUB_LOGIN="Zhachory1",
                                 RALPH_REVIEW_ALLOW="Zhachory1/*")
        self.assertNotIn("refusing to start", r.stderr)
        self.assertEqual("Zhachory1/*\n", allow)

    def test_maintenance_search_excludes_design_change_requests(self):
        self.run_loop(RALPH_MAINTAIN_LOGIN="Zhachory1",
                      RALPH_MAINTAIN_ALLOW="Zhachory1/*", STUB_LOGIN="Zhachory1")
        self.assertIn("search prs", self.gh_search)
        self.assertIn("-- -label:dd-changes", self.gh_search)

    def test_maintenance_search_excludes_configured_docs_repo(self):
        self.run_loop(RALPH_MAINTAIN_LOGIN="Zhachory1",
                      RALPH_MAINTAIN_ALLOW="Zhachory1/*", RALPH_DOCS_REPO="Zhachory1/docs",
                      STUB_LOGIN="Zhachory1")
        self.assertIn("-- -label:dd-changes -repo:Zhachory1/docs", self.gh_search)




class PublishSkipTests(unittest.TestCase):
    """A done synthesis card with a final publish outcome is not re-checked."""

    def run_pass(self, published, publish_rc):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            stub = d / "bin"
            stub.mkdir()
            calls = d / "calls"
            (stub / "gh").write_text("#!/bin/sh\nexit 0\n")
            (stub / "docker").write_text("#!/bin/sh\necho '[{\"id\": \"t_a\"}, {\"id\": \"t_b\"}]'\n")
            (stub / "python3").write_text(
                "#!/bin/sh\n"
                'case "$1" in */publish-review) echo "$3" >> "$CALLS"; exit "$RC";; esac\n'
                '[ "$1" = -c ] && exec /usr/bin/python3 "$@"\n'
                "exit 0\n")
            (stub / "sleep").write_text("#!/bin/sh\nkill $PPID\n")
            for f in stub.iterdir():
                f.chmod(0o755)
            record = d / "published"
            record.write_text("".join(f"{i}\n" for i in published))
            env = {"PATH": f"{stub}:/usr/bin:/bin", "TMPDIR": str(d), "CALLS": str(calls),
                   "RC": str(publish_rc), "RALPH_HERMES_CONTAINER": "x",
                   "RALPH_REVIEW_ALLOW": "o/r", "RALPH_REVIEW_PUBLISHED": str(record)}
            subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
            return (calls.read_text().split() if calls.exists() else []), record.read_text().split()

    def test_a_recorded_card_is_not_published_again(self):
        called, _ = self.run_pass(["t_a"], 0)
        self.assertEqual(["t_b"], called)

    def test_a_final_outcome_is_recorded(self):
        _, record = self.run_pass([], 2)
        self.assertEqual(["t_a", "t_b"], record)

    def test_an_ambiguous_post_is_not_retried(self):
        """#89 review: re-running exit 3 risks a duplicate review."""
        _, record = self.run_pass([], 3)
        self.assertEqual(["t_a", "t_b"], record)

    def test_an_error_is_retried_next_pass(self):
        _, record = self.run_pass([], 1)
        self.assertEqual([], record)

if __name__ == "__main__":
    unittest.main(verbosity=2)
