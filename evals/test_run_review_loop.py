"""run-review-loop's startup: which scope it reads and whose token it trusts.

Runs the real script with stub `gh`, `python3` and `sleep` on PATH, so no
network, no board, and one pass only.

    python3 evals/test_run_review_loop.py
"""

import os
import pathlib
import subprocess
import tempfile
import time
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


class KnowledgeRefreshTests(unittest.TestCase):
    """#130, dd-ralph-librarian 3.4: a moved docs head forces the refresh."""

    def run_pass(self, remote_head, stamp_fresh, knowledge_head, refresh_rc=0):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            stub = d / "bin"
            stub.mkdir()
            work = d / "work"
            work.mkdir()
            calls = d / "calls"
            (stub / "gh").write_text(
                "#!/bin/sh\n"
                'case "$*" in *commits/HEAD*) echo "$REMOTE_HEAD"; exit 0;; esac\n'
                "exit 0\n")
            # docker: `inspect` reports the knowledge container running;
            # `exec ... refresh-knowledge` is recorded and exits $REFRESH_RC;
            # any other `exec` (the done-synthesis list) answers empty.
            (stub / "docker").write_text(
                "#!/bin/sh\n"
                '[ "$1" = inspect ] && { echo true; exit 0; }\n'
                'case "$*" in\n'
                '    *refresh-knowledge) echo "$*" >> "$CALLS"; exit "$REFRESH_RC";;\n'
                "esac\n"
                "echo '[]'\n")
            (stub / "python3").write_text(
                "#!/bin/sh\n"
                '[ "$1" = -c ] && exec /usr/bin/python3 "$@"\n'
                "exit 0\n")
            (stub / "sleep").write_text("#!/bin/sh\nkill $PPID\n")
            for f in stub.iterdir():
                f.chmod(0o755)
            kstamp = work / ".knowledge-stamp"
            khead = work / ".knowledge-head"
            if stamp_fresh:
                kstamp.touch()
            if knowledge_head is not None:
                khead.write_text(knowledge_head)
            env = {"PATH": f"{stub}:/usr/bin:/bin", "TMPDIR": str(d), "CALLS": str(calls),
                   "REMOTE_HEAD": remote_head, "REFRESH_RC": str(refresh_rc),
                   "RALPH_HERMES_CONTAINER": "x", "RALPH_REVIEW_WORK": str(work),
                   "RALPH_REVIEW_ALLOW": "o/r", "RALPH_DOCS_REPO": "owner/docs"}
            subprocess.run(["bash", str(SCRIPT)], env=env,
                           capture_output=True, text=True, timeout=30)
            refreshed = calls.exists()
            return refreshed, (khead.read_text() if khead.exists() else None)

    def test_differing_head_runs_refresh_even_when_stamp_fresh(self):
        refreshed, head = self.run_pass(remote_head="new-sha", stamp_fresh=True,
                                         knowledge_head="old-sha")
        self.assertTrue(refreshed, "a moved docs head must force the refresh")
        self.assertEqual("new-sha", head)

    def test_matching_head_and_fresh_stamp_runs_nothing(self):
        refreshed, head = self.run_pass(remote_head="same-sha", stamp_fresh=True,
                                         knowledge_head="same-sha")
        self.assertFalse(refreshed, "an unmoved head and a fresh stamp need no refresh")
        self.assertEqual("same-sha", head)

    def test_failed_refresh_leaves_head_unchanged_for_retry(self):
        refreshed, head = self.run_pass(remote_head="new-sha", stamp_fresh=True,
                                         knowledge_head="old-sha", refresh_rc=1)
        self.assertTrue(refreshed)
        self.assertEqual("old-sha", head, "a failed refresh must not record a head it never indexed")


class LibrarianCardTests(unittest.TestCase):
    """#130, dd-ralph-librarian 2.1, 3.5: the review loop creates the weekly card."""

    def run_pass(self, docs_repo="owner/docs", stamp=None, pr_heads="", create_rc=0,
                 gh_rc=0, lib_cards="[]", list_rc=0):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            stub = d / "bin"
            stub.mkdir()
            work = d / "work"
            work.mkdir()
            calls = d / "calls"
            # gh: `api .../pulls` answers the stubbed open-pull-request heads.
            (stub / "gh").write_text(
                "#!/bin/sh\n"
                'case "$*" in "api --paginate "*"/pulls?"*) printf "%s" "$PR_HEADS"; exit "$GH_RC";; esac\n'
                "exit 0\n")
            # docker: `inspect` reports the knowledge container not running,
            # so that block does nothing here; `create` is recorded and
            # exits $CREATE_RC; any other `exec` (the done-synthesis list)
            # answers empty.
            (stub / "docker").write_text(
                "#!/bin/sh\n"
                '[ "$1" = inspect ] && { echo false; exit 0; }\n'
                'case "$*" in\n'
                '    *" create "*) echo "$*" >> "$CALLS"; exit "$CREATE_RC";;\n'
                '    *"assignee ralph-librarian"*) printf "%s" "$LIB_CARDS"; exit "$LIST_RC";;\n'
                "esac\n"
                "echo '[]'\n")
            (stub / "python3").write_text(
                "#!/bin/sh\n"
                '[ "$1" = -c ] && exec /usr/bin/python3 "$@"\n'
                "exit 0\n")
            (stub / "sleep").write_text("#!/bin/sh\nkill $PPID\n")
            for f in stub.iterdir():
                f.chmod(0o755)
            lstamp = work / ".librarian-stamp"
            if stamp == "fresh":
                lstamp.touch()
            elif stamp == "week-old":
                lstamp.touch()
                old = time.time() - 8 * 86400
                os.utime(lstamp, (old, old))
            env = {"PATH": f"{stub}:/usr/bin:/bin", "TMPDIR": str(d), "CALLS": str(calls),
                   "PR_HEADS": pr_heads, "CREATE_RC": str(create_rc),
                   "GH_RC": str(gh_rc), "LIB_CARDS": lib_cards, "LIST_RC": str(list_rc),
                   "RALPH_HERMES_CONTAINER": "x", "RALPH_REVIEW_WORK": str(work),
                   "RALPH_REVIEW_ALLOW": "o/r"}
            if docs_repo is not None:
                env["RALPH_DOCS_REPO"] = docs_repo
            subprocess.run(["bash", str(SCRIPT)], env=env,
                           capture_output=True, text=True, timeout=30)
            created = calls.read_text().splitlines() if calls.exists() else []
            return created, lstamp.exists()

    def test_fresh_stamp_creates_no_card(self):
        created, _ = self.run_pass(stamp="fresh")
        self.assertEqual([], created)

    def test_absent_stamp_creates_exactly_one_card(self):
        created, stamped = self.run_pass(stamp=None)
        self.assertEqual(1, len(created))
        self.assertIn("--board queues", created[0])
        self.assertIn("--assignee ralph-librarian", created[0])
        self.assertIn("librarian:", created[0])
        self.assertTrue(stamped, "a successful create touches the stamp")

    def test_week_old_stamp_creates_a_card(self):
        created, stamped = self.run_pass(stamp="week-old")
        self.assertEqual(1, len(created))
        self.assertTrue(stamped)

    def test_open_librarian_pr_creates_no_card_regardless_of_stamp(self):
        created, _ = self.run_pass(stamp=None, pr_heads="librarian/2026-11-03\n")
        self.assertEqual([], created, "an open librarian/ pull request skips creation")

    def test_docs_repo_unset_creates_no_card(self):
        created, _ = self.run_pass(docs_repo=None, stamp=None)
        self.assertEqual([], created)

    def test_the_card_carries_a_weekly_idempotency_key(self):
        """The overlap checks are not atomic with the create. The key lets the
        board return the existing card to a second, concurrent invocation."""
        created, _ = self.run_pass(stamp=None)
        self.assertEqual(1, len(created))
        self.assertRegex(created[0], r"--idempotency-key librarian-weekly-\d{4}-W\d{2}")

    def test_a_failed_pr_query_skips_the_poll_instead_of_reading_as_no_overlap(self):
        created, stamped = self.run_pass(stamp=None, gh_rc=1)
        self.assertEqual([], created, "a guard that could not be read must not create a card")
        self.assertFalse(stamped)

    def test_a_failed_board_query_skips_the_poll(self):
        created, stamped = self.run_pass(stamp=None, list_rc=1)
        self.assertEqual([], created)
        self.assertFalse(stamped)

    def test_a_queued_or_running_librarian_card_blocks_a_second_one(self):
        for status in ("ready", "running", "blocked", "todo"):
            with self.subTest(status=status):
                created, _ = self.run_pass(
                    stamp=None, lib_cards='[{"id": "t_1", "status": "%s"}]' % status)
                self.assertEqual([], created, "a card with no PR yet is still a run in flight")

    def test_a_finished_librarian_card_does_not_block_the_next_week(self):
        created, stamped = self.run_pass(
            stamp="week-old", lib_cards='[{"id": "t_1", "status": "done"}]')
        self.assertEqual(1, len(created))
        self.assertTrue(stamped)

    def test_the_pr_query_is_paginated_so_a_busy_repository_cannot_hide_a_run(self):
        with tempfile.TemporaryDirectory() as d:
            log = pathlib.Path(d) / "gh.log"
            stub = pathlib.Path(d) / "bin"
            stub.mkdir()
            (stub / "gh").write_text(
                "#!/bin/sh\n"
                'case "$*" in "api --paginate "*"/pulls?"*) echo "$*" >> "%s"; exit 0;; esac\nexit 0\n' % log)
            (stub / "docker").write_text(
                "#!/bin/sh\n"
                '[ "$1" = inspect ] && { echo false; exit 0; }\n'
                "echo '[]'\n")
            (stub / "python3").write_text(
                "#!/bin/sh\n"
                '[ "$1" = -c ] && exec /usr/bin/python3 "$@"\nexit 0\n')
            (stub / "sleep").write_text("#!/bin/sh\nkill $PPID\n")
            for f in stub.iterdir():
                f.chmod(0o755)
            work = pathlib.Path(d) / "work"
            work.mkdir()
            subprocess.run(["bash", str(SCRIPT)],
                           env={"PATH": f"{stub}:/usr/bin:/bin", "TMPDIR": d,
                                "RALPH_HERMES_CONTAINER": "x", "RALPH_REVIEW_WORK": str(work),
                                "RALPH_REVIEW_ALLOW": "o/r", "RALPH_DOCS_REPO": "owner/docs"},
                           capture_output=True, text=True, timeout=30)
            self.assertIn("--paginate", log.read_text(),
                          "a fixed first page can miss an open librarian PR")

    def test_failed_create_leaves_stamp_untouched(self):
        created, stamped = self.run_pass(stamp=None, create_rc=1)
        self.assertEqual(1, len(created), "a failing create is still attempted")
        self.assertFalse(stamped, "a failed create must not record a stamp it never earned")


if __name__ == "__main__":
    unittest.main(verbosity=2)
