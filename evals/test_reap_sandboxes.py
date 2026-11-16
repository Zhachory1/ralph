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


class MainTests(unittest.TestCase):
    """The end-to-end wiring: only an idle, in-scope container is removed."""

    def run_main(self, argv, run_side_effects):
        import sys
        with mock.patch.object(reap.subprocess, "run", side_effect=run_side_effects) as run, \
                mock.patch.object(sys, "argv", ["reap-sandboxes", *argv]):
            reap.main()
        return run

    def test_an_idle_in_scope_container_is_removed(self):
        run = self.run_main(
            ["--sandbox-root", ROOT],
            [ps_names("hermes-aa"),
             inspect_mount(f"{ROOT}/ralph-lens-mvp/docker/default/workspace"),
             ps_eo_comm("docker-init", "sleep"),
             mock.Mock(returncode=0, stdout="", stderr="")],
        )
        rm_calls = [c for c in run.call_args_list if c.args[0][:2] == ["docker", "rm"]]
        self.assertEqual(1, len(rm_calls))
        self.assertIn("hermes-aa", rm_calls[0].args[0])

    def test_a_busy_in_scope_container_is_kept(self):
        run = self.run_main(
            ["--sandbox-root", ROOT],
            [ps_names("hermes-aa"),
             inspect_mount(f"{ROOT}/ralph-lens-mvp/docker/default/workspace"),
             ps_eo_comm("docker-init", "sleep", "python3")],
        )
        rm_calls = [c for c in run.call_args_list if c.args[0][:2] == ["docker", "rm"]]
        self.assertEqual(0, len(rm_calls))

    def test_dry_run_reports_without_removing(self):
        run = self.run_main(
            ["--sandbox-root", ROOT, "--dry-run"],
            [ps_names("hermes-aa"),
             inspect_mount(f"{ROOT}/ralph-lens-mvp/docker/default/workspace"),
             ps_eo_comm("docker-init", "sleep")],
        )
        rm_calls = [c for c in run.call_args_list if c.args[0][:2] == ["docker", "rm"]]
        self.assertEqual(0, len(rm_calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
