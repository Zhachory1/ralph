"""Moving one repository's review scope from the fleet to Ralph, or back.

No network, no live authority file touched: every test works against
temporary copies of the plain-text formats both sides actually use.

    python3 evals/test_cutover_repo.py
"""

import importlib.machinery
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "cutover-repo"

_loader = importlib.machinery.SourceFileLoader("cutover_repo", str(SCRIPT))
_spec = importlib.util.spec_from_loader("cutover_repo", _loader)
cutover_repo = importlib.util.module_from_spec(_spec)
_loader.exec_module(cutover_repo)


class ParsingTests(unittest.TestCase):
    def test_reads_the_fleet_authority_shape(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "authority.yaml"
            p.write_text("# comment\nrepos:\n  - owner/repo\n  - other/*\n")
            self.assertEqual(["owner/repo", "other/*"], cutover_repo.read_authority(p))

    def test_missing_authority_file_is_an_empty_list_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual([], cutover_repo.read_authority(pathlib.Path(d) / "nope.yaml"))

    def test_allowlist_is_flat_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "allow.txt"
            p.write_text("a/b\n# comment\nc/d\n\n")
            self.assertEqual(["a/b", "c/d"], cutover_repo.read_allowlist(p))


class GrantMatchingTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(cutover_repo.grants("a/b", "a/b"))
        self.assertFalse(cutover_repo.grants("a/b", "a/c"))

    def test_wildcard_match(self):
        self.assertTrue(cutover_repo.grants("a/b", "a/*"))
        self.assertFalse(cutover_repo.grants("a/b", "c/*"))


class WildcardRefusalTests(unittest.TestCase):
    """The case in production right now: Zhachory1/* grants every personal
    repo, not just the one being cut over. Narrowing it is a human decision,
    never automatic."""

    def test_a_wildcard_grant_is_refused_even_in_dry_run(self):
        with tempfile.TemporaryDirectory() as d:
            authority = pathlib.Path(d) / "authority.yaml"
            authority.write_text("repos:\n  - owner/*\n")
            allowlist = pathlib.Path(d) / "allow.txt"
            r = subprocess.run([sys.executable, str(SCRIPT), "--repo", "owner/repo",
                               "--fleet-authority", str(authority),
                               "--ralph-allowlist", str(allowlist)],
                              capture_output=True, text=True)
            self.assertEqual(2, r.returncode)
            self.assertIn("wildcard", r.stdout.lower())
            # Neither file touched.
            self.assertEqual("repos:\n  - owner/*\n", authority.read_text())
            self.assertFalse(allowlist.is_file())

    def test_a_wildcard_grant_is_refused_with_apply_too(self):
        with tempfile.TemporaryDirectory() as d:
            authority = pathlib.Path(d) / "authority.yaml"
            authority.write_text("repos:\n  - owner/*\n")
            allowlist = pathlib.Path(d) / "allow.txt"
            r = subprocess.run([sys.executable, str(SCRIPT), "--repo", "owner/repo",
                               "--fleet-authority", str(authority), "--ralph-allowlist",
                               str(allowlist), "--apply"],
                              capture_output=True, text=True)
            self.assertEqual(2, r.returncode)
            self.assertEqual("repos:\n  - owner/*\n", authority.read_text())


class CutoverTests(unittest.TestCase):
    def _files(self, d, fleet_repos):
        authority = pathlib.Path(d) / "authority.yaml"
        authority.write_text("repos:\n" + "".join(f"  - {r}\n" for r in fleet_repos))
        allowlist = pathlib.Path(d) / "allow.txt"
        return authority, allowlist

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            authority, allowlist = self._files(d, ["owner/repo", "other/thing"])
            before = authority.read_text()
            r = subprocess.run([sys.executable, str(SCRIPT), "--repo", "owner/repo",
                               "--fleet-authority", str(authority),
                               "--ralph-allowlist", str(allowlist)],
                              capture_output=True, text=True)
            self.assertEqual(0, r.returncode)
            self.assertEqual(before, authority.read_text())
            self.assertFalse(allowlist.is_file())

    def test_apply_moves_the_repo(self):
        with tempfile.TemporaryDirectory() as d:
            authority, allowlist = self._files(d, ["owner/repo", "other/thing"])
            r = subprocess.run([sys.executable, str(SCRIPT), "--repo", "owner/repo",
                               "--fleet-authority", str(authority),
                               "--ralph-allowlist", str(allowlist), "--apply"],
                              capture_output=True, text=True)
            self.assertEqual(0, r.returncode)
            self.assertNotIn("owner/repo", cutover_repo.read_authority(authority))
            self.assertIn("other/thing", cutover_repo.read_authority(authority))
            self.assertIn("owner/repo", cutover_repo.read_allowlist(allowlist))

    def test_unrelated_repos_are_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            authority, allowlist = self._files(d, ["owner/repo", "owner/other", "third/x"])
            subprocess.run([sys.executable, str(SCRIPT), "--repo", "owner/repo",
                           "--fleet-authority", str(authority), "--ralph-allowlist",
                           str(allowlist), "--apply"], capture_output=True, text=True)
            remaining = cutover_repo.read_authority(authority)
            self.assertIn("owner/other", remaining)
            self.assertIn("third/x", remaining)

    def test_second_run_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as d:
            authority, allowlist = self._files(d, ["owner/repo"])
            for _ in range(2):
                subprocess.run([sys.executable, str(SCRIPT), "--repo", "owner/repo",
                               "--fleet-authority", str(authority), "--ralph-allowlist",
                               str(allowlist), "--apply"], capture_output=True, text=True)
            self.assertEqual(["owner/repo"], cutover_repo.read_allowlist(allowlist))

    def test_rollback_reverses_a_cutover(self):
        with tempfile.TemporaryDirectory() as d:
            authority, allowlist = self._files(d, ["owner/repo"])
            subprocess.run([sys.executable, str(SCRIPT), "--repo", "owner/repo",
                           "--fleet-authority", str(authority), "--ralph-allowlist",
                           str(allowlist), "--apply"], capture_output=True, text=True)
            r = subprocess.run([sys.executable, str(SCRIPT), "--repo", "owner/repo",
                               "--fleet-authority", str(authority), "--ralph-allowlist",
                               str(allowlist), "--direction", "fleet", "--apply"],
                              capture_output=True, text=True)
            self.assertEqual(0, r.returncode)
            self.assertIn("owner/repo", cutover_repo.read_authority(authority))
            self.assertNotIn("owner/repo", cutover_repo.read_allowlist(allowlist))

    def test_rejects_a_malformed_repo(self):
        with tempfile.TemporaryDirectory() as d:
            authority, allowlist = self._files(d, [])
            r = subprocess.run([sys.executable, str(SCRIPT), "--repo", "not-a-repo",
                               "--fleet-authority", str(authority),
                               "--ralph-allowlist", str(allowlist)],
                              capture_output=True, text=True)
            self.assertEqual(2, r.returncode)


if __name__ == "__main__":
    unittest.main(verbosity=2)
