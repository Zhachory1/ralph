"""bootstrap/reap-sandboxes: which idle sandbox containers it removes, which
instance's it never touches, and which live ones it leaves alone.

    python3 evals/test_reap_sandboxes.py
"""

import importlib.machinery
import importlib.util
import pathlib
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "bootstrap" / "reap-sandboxes"
loader = importlib.machinery.SourceFileLoader("reap_sandboxes", str(SCRIPT))
spec = importlib.util.spec_from_loader("reap_sandboxes", loader)
reap = importlib.util.module_from_spec(spec)
loader.exec_module(reap)

ROOT = "/workspaces/sandboxes"


def ps_names(*names):
    return mock.Mock(returncode=0, stdout="\n".join(names) + "\n" if names else "", stderr="")


def inspect_mount(source):
    return mock.Mock(returncode=0, stdout=source, stderr="")


def ps_eo_comm(*procs):
    body = "\n".join(("COMMAND", *procs))
    return mock.Mock(returncode=0, stdout=body, stderr="")


class SandboxContainersTests(unittest.TestCase):
    """Only this instance's own sandboxes are candidates at all."""

    def test_a_container_mounted_under_this_root_is_a_candidate(self):
        with mock.patch.object(reap.subprocess, "run") as run:
            run.side_effect = [ps_names("hermes-aa"),
                               inspect_mount(f"{ROOT}/ralph-lens-mvp/docker/default/workspace")]
            self.assertEqual(["hermes-aa"], reap.sandbox_containers(ROOT))

    def test_a_container_mounted_under_a_different_instances_root_is_not(self):
        with mock.patch.object(reap.subprocess, "run") as run:
            run.side_effect = [ps_names("hermes-aa"),
                               inspect_mount("/workspaces-work/sandboxes/ralph-lens-mvp/docker/default/workspace")]
            self.assertEqual([], reap.sandbox_containers(ROOT))

    def test_a_root_that_is_a_path_prefix_but_not_a_real_parent_is_not_a_match(self):
        """/workspaces/sandboxes2 must not match /workspaces/sandboxes."""
        with mock.patch.object(reap.subprocess, "run") as run:
            run.side_effect = [ps_names("hermes-aa"),
                               inspect_mount(f"{ROOT}2/ralph-lens-mvp/docker/default/workspace")]
            self.assertEqual([], reap.sandbox_containers(ROOT))

    def test_a_container_with_no_workspace_mount_is_not_a_candidate(self):
        with mock.patch.object(reap.subprocess, "run") as run:
            run.side_effect = [ps_names("hermes-aa"), inspect_mount("")]
            self.assertEqual([], reap.sandbox_containers(ROOT))

    def test_an_unreadable_container_list_yields_no_candidates(self):
        """Nothing to reap conservatively beats acting on a partial list."""
        with mock.patch.object(reap.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="boom")
            self.assertEqual([], reap.sandbox_containers(ROOT))

    def test_multiple_containers_are_each_checked(self):
        with mock.patch.object(reap.subprocess, "run") as run:
            run.side_effect = [
                ps_names("hermes-aa", "hermes-bb"),
                inspect_mount(f"{ROOT}/ralph-lens-mvp/docker/default/workspace"),
                inspect_mount("/workspaces-work/sandboxes/ralph-lens-mvp/docker/default/workspace"),
            ]
            self.assertEqual(["hermes-aa"], reap.sandbox_containers(ROOT))


class IsIdleTests(unittest.TestCase):
    """Only docker-init, sleep, and this check's own ps call mean idle."""

    def test_docker_init_and_sleep_only_is_idle(self):
        with mock.patch.object(reap.subprocess, "run",
                               return_value=ps_eo_comm("docker-init", "sleep")):
            self.assertTrue(reap.is_idle("hermes-aa"))

    def test_an_active_agent_process_is_not_idle(self):
        with mock.patch.object(reap.subprocess, "run",
                               return_value=ps_eo_comm("docker-init", "sleep", "python3")):
            self.assertFalse(reap.is_idle("hermes-aa"))

    def test_an_unreachable_container_is_not_idle(self):
        """A read failure is never treated as idle -- keep it rather than guess."""
        with mock.patch.object(reap.subprocess, "run",
                               return_value=mock.Mock(returncode=1, stdout="", stderr="")):
            self.assertFalse(reap.is_idle("hermes-aa"))


NOW = 1_800_000_000
OLD_START = "2026-01-01T00:00:00.123456789Z"  # long before NOW


def started_at(stamp=OLD_START):
    return mock.Mock(returncode=0, stdout=stamp + "\n", stderr="")


def exec_events(*epochs):
    return mock.Mock(returncode=0, stdout="".join(f"{e}\n" for e in epochs), stderr="")


def ok():
    return mock.Mock(returncode=0, stdout="", stderr="")


class MainTests(unittest.TestCase):
    """The end-to-end wiring: only an idle, in-scope container past the grace
    period is removed, and a failed removal fails the run."""

    def run_main(self, argv, run_side_effects):
        import sys
        with mock.patch.object(reap.subprocess, "run", side_effect=run_side_effects) as run, \
                mock.patch.object(reap.time, "time", return_value=NOW), \
                mock.patch.object(sys, "argv", ["reap-sandboxes", *argv]):
            rc = reap.main()
        return run, rc

    def candidate(self):
        return [ps_names("hermes-aa"),
                inspect_mount(f"{ROOT}/ralph-lens-mvp/docker/default/workspace")]

    @staticmethod
    def rm_calls(run):
        return [c for c in run.call_args_list if c.args[0][:2] == ["docker", "rm"]]

    def test_an_idle_container_past_the_grace_period_is_removed(self):
        run, rc = self.run_main(["--sandbox-root", ROOT], self.candidate() + [
            ps_eo_comm("docker-init", "sleep"), started_at(), exec_events(), ok()])
        self.assertEqual(1, len(self.rm_calls(run)))
        self.assertIn("hermes-aa", self.rm_calls(run)[0].args[0])
        self.assertEqual(0, rc)

    def test_a_busy_container_is_kept(self):
        run, rc = self.run_main(["--sandbox-root", ROOT], self.candidate() + [
            ps_eo_comm("docker-init", "sleep", "python3")])
        self.assertEqual([], self.rm_calls(run))

    def test_a_sleeping_container_with_a_recent_command_is_kept(self):
        """Review finding: an agent between terminal calls leaves its sandbox
        sleeping. A recent exec means its task may still be live."""
        recent = NOW - 600
        run, rc = self.run_main(["--sandbox-root", ROOT], self.candidate() + [
            ps_eo_comm("docker-init", "sleep"), started_at(), exec_events(recent)])
        self.assertEqual([], self.rm_calls(run))
        self.assertEqual(0, rc)

    def test_a_recently_started_container_with_no_commands_is_kept(self):
        run, rc = self.run_main(["--sandbox-root", ROOT], self.candidate() + [
            ps_eo_comm("docker-init", "sleep"),
            started_at("2027-01-15T07:50:00.000000000Z"),  # NOW minus 10 minutes
            exec_events()])
        self.assertEqual([], self.rm_calls(run))

    def test_unreadable_activity_keeps_the_container(self):
        run, rc = self.run_main(["--sandbox-root", ROOT], self.candidate() + [
            ps_eo_comm("docker-init", "sleep"), started_at(),
            mock.Mock(returncode=1, stdout="", stderr="events failed")])
        self.assertEqual([], self.rm_calls(run))

    def test_a_failed_removal_is_not_counted_and_fails_the_run(self):
        """Review finding: a failed docker rm was reported as reaped and the
        run exited 0, so the hourly stamp hid it until the next hour."""
        run, rc = self.run_main(["--sandbox-root", ROOT], self.candidate() + [
            ps_eo_comm("docker-init", "sleep"), started_at(), exec_events(),
            mock.Mock(returncode=1, stdout="", stderr="conflict")])
        self.assertEqual(1, len(self.rm_calls(run)))
        self.assertEqual(1, rc)

    def test_dry_run_reports_without_removing(self):
        run, rc = self.run_main(["--sandbox-root", ROOT, "--dry-run"], self.candidate() + [
            ps_eo_comm("docker-init", "sleep"), started_at(), exec_events()])
        self.assertEqual([], self.rm_calls(run))
        self.assertEqual(0, rc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
