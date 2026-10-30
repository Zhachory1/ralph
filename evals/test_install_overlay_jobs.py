"""bootstrap/install-overlay-jobs: a job lands in its own profile, and only there.

Runs the real script against a temporary Hermes home on the local
filesystem (no container, no network).

    python3 evals/test_install_overlay_jobs.py
"""

import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "install-overlay-jobs"


class InstallOverlayJobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.tmp.name)
        self.home = d / "home"
        for p in ("ralph-inbox-drafter", "ralph-maintainer"):
            (self.home / "profiles" / p).mkdir(parents=True)
        (self.home / "profiles" / "ralph-inbox-drafter" / ".env").write_text("KEEP_ME=1\nOPT_A=old\n")
        self.jobs = d / "jobs"
        self.job = self.jobs / "digest"
        self.job.mkdir(parents=True)
        (self.job / "run.py").write_text("print('run')\n")
        (self.job / "helper.py").write_text("X = 1\n")
        (self.job / "README.md").write_text("not copied\n")
        (self.job / "job.env").write_text("# comment\nPROFILE=ralph-inbox-drafter\nOPTIONS=OPT_A OPT_B OPT_UNSET\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, **env):
        full = {k: v for k, v in os.environ.items() if not k.startswith("OPT_")}
        full.update(env)
        return subprocess.run([sys.executable, str(SCRIPT), "--jobs-root", str(self.jobs),
                               "--hermes-home", str(self.home)],
                              env=full, capture_output=True, text=True, timeout=30)

    def profile(self, name):
        return self.home / "profiles" / name

    def test_scripts_land_in_the_owning_profile(self):
        r = self.run_it(OPT_A="a", OPT_B="b")
        self.assertEqual(0, r.returncode, r.stderr)
        scripts = self.profile("ralph-inbox-drafter") / "scripts"
        self.assertEqual("print('run')\n", (scripts / "run.py").read_text())
        self.assertTrue((scripts / "helper.py").exists())
        self.assertFalse((scripts / "README.md").exists(), "only *.py is installed")

    def test_options_go_to_that_profile_only(self):
        self.run_it(OPT_A="a", OPT_B="b")
        env = (self.profile("ralph-inbox-drafter") / ".env").read_text().splitlines()
        self.assertIn("KEEP_ME=1", env, "other lines are kept")
        self.assertIn("OPT_A=a", env, "an existing option is replaced")
        self.assertNotIn("OPT_A=old", env)
        self.assertIn("OPT_B=b", env)
        self.assertFalse((self.profile("ralph-maintainer") / ".env").exists())
        self.assertFalse((self.profile("ralph-maintainer") / "scripts").exists())

    def test_profile_env_is_private(self):
        self.run_it(OPT_A="a")
        mode = stat.S_IMODE((self.profile("ralph-inbox-drafter") / ".env").stat().st_mode)
        self.assertEqual(0o600, mode)

    def test_values_are_never_printed(self):
        r = self.run_it(OPT_A="secret-value-a", OPT_B="secret-value-b")
        self.assertNotIn("secret-value", r.stdout + r.stderr)
        self.assertIn("OPT_A", r.stdout, "option names are reported")

    def test_an_unset_option_is_named_and_left_out(self):
        r = self.run_it(OPT_A="a")
        self.assertIn("OPT_B", r.stdout)
        self.assertIn("OPT_UNSET", r.stdout)
        self.assertNotIn("OPT_UNSET=", (self.profile("ralph-inbox-drafter") / ".env").read_text())

    def test_a_job_without_job_env_is_skipped(self):
        (self.job / "job.env").unlink()
        self.assertEqual(0, self.run_it(OPT_A="a").returncode)
        self.assertFalse((self.profile("ralph-inbox-drafter") / "scripts").exists())

    def test_a_missing_profile_fails_and_writes_nothing(self):
        (self.job / "job.env").write_text("PROFILE=ralph-nonexistent\nOPTIONS=OPT_A\n")
        r = self.run_it(OPT_A="a")
        self.assertNotEqual(0, r.returncode)
        self.assertFalse((self.home / "profiles" / "ralph-nonexistent").exists())

    def test_profile_must_be_a_ralph_profile(self):
        """job.env cannot aim a job at another tool's profile or out of
        profiles/ with a path. Each target here exists on disk, so only the
        name check can stop it."""
        (self.home / "profiles" / "default").mkdir()
        (self.home / "escaped").mkdir()
        for bad, landed in (("default", self.home / "profiles" / "default"),
                            ("ralph-inbox-drafter/../../escaped", self.home / "escaped")):
            with self.subTest(profile=bad):
                (self.job / "job.env").write_text(f"PROFILE={bad}\nOPTIONS=OPT_A\n")
                self.assertNotEqual(0, self.run_it(OPT_A="a").returncode)
                self.assertFalse((landed / "scripts").exists(), f"{bad} wrote outside its profile")
                self.assertFalse((landed / ".env").exists())

    def test_no_jobs_root_is_a_no_op(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--jobs-root", "/nonexistent",
                            "--hermes-home", str(self.home)], capture_output=True, text=True)
        self.assertEqual(0, r.returncode)


if __name__ == "__main__":
    unittest.main(verbosity=2)
