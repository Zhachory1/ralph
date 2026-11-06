"""bootstrap/cleanup-sandboxes: what it deletes, and what it never touches.

    python3 evals/test_cleanup_sandboxes.py
"""

import importlib.machinery
import importlib.util
import os
import pathlib
import tempfile
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "bootstrap" / "cleanup-sandboxes"
loader = importlib.machinery.SourceFileLoader("cleanup_sandboxes", str(SCRIPT))
spec = importlib.util.spec_from_loader("cleanup_sandboxes", loader)
cleanup = importlib.util.module_from_spec(spec)
loader.exec_module(cleanup)

NOW = 1_800_000_000
OLD = NOW - 2 * 3600


class CleanupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)

    def checkout(self, card_id, profile="ralph-maintainer", mtime=OLD):
        path = self.root / profile / "docker" / "default" / "workspace" / card_id
        path.mkdir(parents=True)
        (path / "file").write_text("x" * 100)
        for p in (path / "file", path):
            os.utime(p, (mtime, mtime))
        return path

    def cache(self, profile, size):
        home = self.root / profile / "docker" / "default" / "home"
        cache = home / ".cache"
        cache.mkdir(parents=True)
        (cache / "blob").write_bytes(b"x" * size)
        (home / "keep.txt").write_text("settings")
        return home

    def run_clean(self, states, running=(), dry_run=False):
        return cleanup.clean(self.root, states, set(running), NOW, dry_run)

    def test_a_finished_cards_checkout_is_deleted(self):
        path = self.checkout("t_aa")
        removed = self.run_clean({"t_aa": "done"})
        self.assertFalse(path.exists())
        self.assertEqual(1, len(removed))

    def test_an_archived_cards_checkout_is_deleted(self):
        path = self.checkout("t_ab")
        self.run_clean({"t_ab": "archived"})
        self.assertFalse(path.exists())

    def test_a_running_cards_checkout_is_kept(self):
        path = self.checkout("t_bb")
        self.run_clean({"t_bb": "running"})
        self.assertTrue(path.exists())

    def test_ready_blocked_and_todo_checkouts_are_kept(self):
        paths = [self.checkout(f"t_c{i}") for i in range(3)]
        self.run_clean({"t_c0": "ready", "t_c1": "blocked", "t_c2": "todo"})
        self.assertTrue(all(p.exists() for p in paths))

    def test_a_checkout_no_board_knows_is_kept(self):
        path = self.checkout("t_dd")
        self.run_clean({})
        self.assertTrue(path.exists())

    def test_a_recently_touched_finished_checkout_is_kept(self):
        path = self.checkout("t_ee", mtime=NOW - 60)
        self.run_clean({"t_ee": "done"})
        self.assertTrue(path.exists())

    def test_a_recent_non_card_directory_is_kept(self):
        path = self.checkout("master_check", mtime=NOW - 3600)
        self.run_clean({})
        self.assertTrue(path.exists())

    def test_a_non_card_directory_idle_a_day_is_deleted(self):
        """Live: the maintainer cloned 1 GB into workspace/master_check."""
        path = self.checkout("master_check", mtime=NOW - 25 * 3600)
        self.run_clean({})
        self.assertFalse(path.exists())

    def test_dry_run_deletes_nothing_but_reports(self):
        path = self.checkout("t_ff")
        removed = self.run_clean({"t_ff": "done"}, dry_run=True)
        self.assertTrue(path.exists())
        self.assertEqual(1, len(removed))

    def test_an_oversized_home_loses_its_caches_only(self):
        home = self.cache("ralph-builder", 2000)
        with mock.patch.object(cleanup, "HOME_LIMIT_BYTES", 1000):
            self.run_clean({})
        self.assertFalse((home / ".cache").exists())
        self.assertTrue((home / "keep.txt").exists())

    def test_a_home_under_the_limit_keeps_its_caches(self):
        home = self.cache("ralph-builder", 500)
        with mock.patch.object(cleanup, "HOME_LIMIT_BYTES", 1000):
            self.run_clean({})
        self.assertTrue((home / ".cache").exists())

    def test_a_running_profiles_caches_are_kept(self):
        home = self.cache("ralph-builder", 2000)
        with mock.patch.object(cleanup, "HOME_LIMIT_BYTES", 1000):
            self.run_clean({}, running={"ralph-builder"})
        self.assertTrue((home / ".cache").exists())

    def test_go_binaries_survive_a_cache_purge(self):
        """#99 review: `go` held installed tools as well as the module cache."""
        home = self.cache("ralph-builder", 2000)
        (home / "go" / "bin").mkdir(parents=True)
        (home / "go" / "bin" / "gopls").write_text("tool")
        (home / "go" / "pkg" / "mod").mkdir(parents=True)
        (home / "go" / "pkg" / "mod" / "dep").write_text("x" * 2000)
        with mock.patch.object(cleanup, "HOME_LIMIT_BYTES", 1000):
            self.run_clean({})
        self.assertTrue((home / "go" / "bin" / "gopls").exists())
        self.assertFalse((home / "go" / "pkg" / "mod").exists())

    def test_every_board_is_read_not_a_fixed_list(self):
        """#99 review: a custom board's cards must count too."""
        calls = []

        def fake(*args):
            calls.append(args)
            if args[:2] == ("kanban", "boards"):
                return mock.Mock(returncode=0, stdout='[{"slug": "custom"}]')
            return mock.Mock(returncode=0, stdout='[{"id": "t_x", "status": "ready", "assignee": "ralph-builder"}]')
        with mock.patch.object(cleanup, "hermes", side_effect=fake):
            states, busy = cleanup.card_states()
        self.assertEqual({"t_x": "ready"}, states)
        self.assertEqual({"ralph-builder"}, busy)
        self.assertIn(("kanban", "--board", "custom", "list", "--archived", "--json"), calls)

    def test_an_unreadable_board_deletes_nothing(self):
        path = self.checkout("t_aa")
        with mock.patch.object(cleanup, "card_states", return_value=None), \
                mock.patch("sys.argv", ["cleanup-sandboxes", "--root", str(self.root)]):
            self.assertEqual(1, cleanup.main())
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
