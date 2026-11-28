"""Exercise maintenance Git safety using isolated local repositories."""

import copy
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from bootstrap import paperclip_maintain as maintain


class GitWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "git").symlink_to(shutil.which("git"))
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home),
            "PATH": str(self.bin) + os.pathsep + "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "PAPERCLIP_GITHUB_BROKER_URL": "https://broker.example.invalid",
            "PAPERCLIP_GITHUB_BROKER_TOKEN": "placeholder",
            "PAPERCLIP_GITHUB_LAUNCHER_DIR": str(self.bin),
        }
        self.workspace = self.root / "writer"
        self.workspace.mkdir()
        self.git(self.workspace, "init", "-b", "main")
        self.git(self.workspace, "config", "user.name", "Fixture")
        self.git(self.workspace, "config", "user.email", "fixture@example.invalid")
        self.git(self.workspace, "remote", "add", "origin", "https://github.com/owner/repo.git")
        (self.workspace / "root.txt").write_text("root\n")
        self.git(self.workspace, "add", "root.txt")
        self.git(self.workspace, "commit", "-m", "root")
        self.root_commit = self.git(self.workspace, "rev-parse", "HEAD")
        self.git(self.workspace, "checkout", "-b", "feature")
        (self.workspace / "feature.txt").write_text("feature\n")
        self.git(self.workspace, "add", "feature.txt")
        self.git(self.workspace, "commit", "-m", "feature")
        self.head = self.git(self.workspace, "rev-parse", "HEAD")
        self.git(self.workspace, "checkout", "main")
        (self.workspace / "base.txt").write_text("base\n")
        self.git(self.workspace, "add", "base.txt")
        self.git(self.workspace, "commit", "-m", "advance base")
        self.base = self.git(self.workspace, "rev-parse", "HEAD")
        self.git(self.workspace, "checkout", "feature")
        self.git(self.workspace, "merge", "--no-ff", "-m", "merge base", self.base)
        self.commit = self.git(self.workspace, "rev-parse", "HEAD")
        self.tree = self.git(self.workspace, "rev-parse", "HEAD^{tree}")
        repo = {"id": 13, "full_name": "owner/repo"}
        self.pr = {
            "number": 1,
            "base": {"repo": dict(repo), "sha": self.base, "ref": "main"},
            "head": {"repo": dict(repo), "sha": self.head, "ref": "feature"},
        }

    def git(self, directory, *args, check=True):
        result = subprocess.run(
            [str(self.bin / "git"), "-C", str(directory), *args],
            env=self.env, text=True, capture_output=True, check=check,
        )
        return result.stdout.strip()

    def adapter(self, runner=None):
        return maintain.GitWorkspace(runner=runner, env=self.env)

    def prepared(self):
        return {
            "workspace": str(self.workspace), "commit": self.commit, "tree": self.tree,
            "head": self.head, "base": self.base, "headRef": "feature",
            "baseRef": "main", "repo": "owner/repo",
        }

    def receiver(self):
        receiver = self.root / "receiver"
        receiver.mkdir()
        self.git(receiver, "init", "-b", "main")
        self.git(receiver, "remote", "add", "origin", "https://github.com/owner/repo.git")
        self.git(receiver, "fetch", str(self.workspace), self.head, self.base)
        self.git(receiver, "checkout", "--detach", self.head)
        return receiver

    def test_inspect_returns_captured_head_and_actual_base_for_merge(self):
        self.assertEqual(self.adapter().inspect(str(self.workspace), self.pr), self.prepared())

    def test_inspect_rejects_dirty_workspace_and_wrong_origin(self):
        adapter = self.adapter()
        (self.workspace / "untracked.txt").write_text("dirty\n")
        with self.assertRaises(maintain.MaintenanceError):
            adapter.inspect(str(self.workspace), self.pr)
        (self.workspace / "untracked.txt").unlink()
        self.git(self.workspace, "remote", "set-url", "origin", "https://github.com/other/repo.git")
        with self.assertRaises(maintain.MaintenanceError):
            adapter.inspect(str(self.workspace), self.pr)

    def test_inspect_independently_requires_head_and_actual_base_ancestry(self):
        adapter = self.adapter()
        self.git(self.workspace, "checkout", "--detach", self.base)
        with self.assertRaises(maintain.MaintenanceError):
            adapter.inspect(str(self.workspace), self.pr)
        self.git(self.workspace, "checkout", "--detach", self.head)
        with self.assertRaises(maintain.MaintenanceError):
            adapter.inspect(str(self.workspace), self.pr)

    def test_inspect_rejects_missing_base_and_commit_other_than_head(self):
        adapter = self.adapter()
        missing = copy.deepcopy(self.pr)
        missing["base"]["sha"] = "f" * 40
        with self.assertRaises(maintain.MaintenanceError):
            adapter.inspect(str(self.workspace), missing)
        with self.assertRaises(maintain.MaintenanceError):
            adapter.inspect(str(self.workspace), self.pr, commit=self.head)

    def test_bundle_contains_only_unpublished_commits_with_head_prerequisite(self):
        data = self.adapter().bundle(str(self.workspace), self.commit, self.head)
        self.assertIsInstance(data, bytes)
        bundle = self.root / "change.bundle"
        bundle.write_bytes(data)
        heads = self.git(self.workspace, "bundle", "list-heads", str(bundle))
        self.assertEqual(heads.split()[0], self.commit)
        result = subprocess.run(
            [str(self.bin / "git"), "-C", str(self.workspace), "bundle", "verify", str(bundle)],
            env=self.env, text=True, capture_output=True, check=True,
        )
        self.assertIn(self.head, result.stdout + result.stderr)

    def test_bundle_rejects_no_new_commit_and_empty_change(self):
        adapter = self.adapter()
        with self.assertRaises(maintain.MaintenanceError):
            adapter.bundle(str(self.workspace), self.head, self.head)
        self.git(self.workspace, "checkout", "--detach", self.head)
        self.git(self.workspace, "commit", "--allow-empty", "-m", "empty")
        empty = self.git(self.workspace, "rev-parse", "HEAD")
        with self.assertRaises(maintain.MaintenanceError):
            adapter.bundle(str(self.workspace), empty, self.head)

    def test_verify_bundle_imports_into_own_checkout_without_changing_head(self):
        adapter = self.adapter()
        data = adapter.bundle(str(self.workspace), self.commit, self.head)
        receiver = self.receiver()
        before = subprocess.run(
            [str(self.bin / "git"), "-C", str(receiver), "cat-file", "-e", self.commit],
            env=self.env, capture_output=True,
        )
        self.assertNotEqual(before.returncode, 0)
        result = adapter.verify_bundle(str(receiver), data, self.prepared())
        for field in ("head", "commit", "tree", "base", "repo"):
            self.assertEqual(result[field], self.prepared()[field])
        self.assertEqual(self.git(receiver, "rev-parse", "HEAD"), self.head)
        self.assertEqual(self.git(receiver, "rev-parse", self.commit + "^{tree}"), self.tree)
        self.assertEqual(self.git(receiver, "status", "--porcelain"), "")

    def test_verify_bundle_rejects_tampered_binding_and_wrong_repository(self):
        adapter = self.adapter()
        data = adapter.bundle(str(self.workspace), self.commit, self.head)
        receiver = self.receiver()
        for field, value in (("head", self.root_commit), ("commit", self.head),
                             ("tree", "f" * 40), ("base", "f" * 40), ("repo", "other/repo")):
            with self.subTest(field=field), self.assertRaises(maintain.MaintenanceError):
                adapter.verify_bundle(str(receiver), data, dict(self.prepared(), **{field: value}))
        self.git(receiver, "remote", "set-url", "origin", "https://github.com/other/repo.git")
        with self.assertRaises(maintain.MaintenanceError):
            adapter.verify_bundle(str(receiver), data, self.prepared())

    def test_push_uses_managed_wrapper_and_fixed_non_force_ref(self):
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            if "push" in args:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.run(args, **kwargs)

        self.adapter(runner).push(str(self.workspace), self.prepared())
        pushes = [(args, kwargs) for args, kwargs in calls if "push" in args]
        self.assertEqual(len(pushes), 1)
        args, kwargs = pushes[0]
        self.assertEqual(args[:4], [str(self.bin / "git"), "--no-replace-objects",
                                    "-C", str(self.workspace)])
        refspecs = (self.commit + ":refs/heads/feature", "HEAD:refs/heads/feature")
        self.assertIn(args[4:], [
            ["push", "origin", refspec] for refspec in refspecs
        ] + [
            ["push", "--porcelain", "origin", refspec] for refspec in refspecs
        ])
        self.assertFalse(any(arg.startswith(("--force", "+")) for arg in args))
        self.assertNotIn("rebase", args)
        self.assertNotIn("merge", args)
        self.assertTrue(kwargs["text"])
        self.assertTrue(kwargs["capture_output"])
        self.assertGreater(kwargs["timeout"], 0)
        self.assertEqual(kwargs["env"]["PAPERCLIP_GITHUB_BROKER_TOKEN"], "placeholder")

    def test_constructor_rejects_ambient_credentials_without_managed_authority(self):
        with self.assertRaises(maintain.MaintenanceError):
            maintain.GitWorkspace(env={"GH_TOKEN": "placeholder"})

    def test_push_rejects_a_different_effective_push_repository(self):
        def runner(args, **kwargs):
            if "push" in args:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.run(args, **kwargs)

        self.git(self.workspace, "remote", "set-url", "--push", "origin", "https://github.com/other/repo.git")
        with self.assertRaises(maintain.MaintenanceError):
            self.adapter(runner).push(str(self.workspace), self.prepared())

    def test_push_rejects_implicit_extra_tag_pushes(self):
        def runner(args, **kwargs):
            if "push" in args:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.run(args, **kwargs)

        self.git(self.workspace, "config", "push.followTags", "true")
        with self.assertRaises(maintain.MaintenanceError):
            self.adapter(runner).push(str(self.workspace), self.prepared())


if __name__ == "__main__":
    unittest.main()
