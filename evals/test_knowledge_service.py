"""The knowledge service (#121, dd-ralph-knowledge D1/D2): what install and the
container are configured to do. Live search is checked against a running
instance, not here.

    python3 evals/test_knowledge_service.py
"""

import os
import pathlib
import subprocess
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
INIT = ROOT / "bootstrap" / "knowledge-init"
COMPOSE = yaml.safe_load((ROOT / "compose" / "docker-compose.yml").read_text())


class KnowledgeInitTests(unittest.TestCase):
    """knowledge-init writes QMD's config; qmd and git are stubbed."""

    def run_init(self, repo=""):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = pathlib.Path(tmp.name)
        stub = d / "bin"
        stub.mkdir()
        for name in ("qmd", "git"):
            (stub / name).write_text("#!/bin/sh\necho \"$0 $*\" >> \"$CALLS\"\n")
            (stub / name).chmod(0o755)
        env = {"PATH": f"{stub}:/usr/bin:/bin", "CALLS": str(d / "calls"),
               "KNOWLEDGE_ROOT": str(d / "k"), "QMD_CONFIG_DIR": str(d / "k" / "config"),
               "INDEX_PATH": str(d / "k" / "cache" / "qmd" / "index.sqlite"),
               "KNOWLEDGE_REPO": repo, "GH_TOKEN": "t"}
        r = subprocess.run(["bash", str(INIT)], env=env, capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stderr)
        config = yaml.safe_load((d / "k" / "config" / "index.yml").read_text())
        calls = (d / "calls").read_text() if (d / "calls").exists() else ""
        return config, calls, d

    def test_indexes_the_docs_repo_and_ralph_docs(self):
        config, _, d = self.run_init("o/docs")
        self.assertEqual({"docs", "ralph"}, set(config["collections"]))
        self.assertEqual(str(d / "k" / "repo"), config["collections"]["docs"]["path"])
        self.assertEqual("/ralph/docs", config["collections"]["ralph"]["path"])
        self.assertIn("/memory", config["collections"]["docs"]["context"])

    def test_no_query_expansion_model(self):
        """dd-ralph-knowledge D4: expansion is off; its 1.3 GB model is never set."""
        config, _, _ = self.run_init()
        self.assertEqual({"embed", "rerank"}, set(config["models"]))

    def test_clones_the_docs_repo_without_the_token_on_the_command_line(self):
        _, calls, _ = self.run_init("o/docs")
        self.assertIn("clone -q https://github.com/o/docs.git", calls)
        self.assertNotIn("password=t", calls)

    def test_indexes_and_embeds(self):
        _, calls, _ = self.run_init()
        self.assertIn("qmd update", calls)
        self.assertIn("qmd embed", calls)


class KnowledgeServiceTests(unittest.TestCase):
    def test_service_is_internal_and_bounded(self):
        svc = COMPOSE["services"]["knowledge"]
        self.assertNotIn("ports", svc, "the knowledge service must not be published to the host")
        self.assertIn("mem_limit", svc)
        self.assertEqual("1", str(svc["environment"]["QMD_EMBED_PARALLELISM"]))
        self.assertIn("knowledge:/var/lib/ralph-knowledge", svc["volumes"])
        self.assertIn("knowledge", COMPOSE["volumes"])

    def test_service_does_not_start_unless_selected(self):
        """#122 review: without the profile, the service never starts."""
        self.assertEqual(["knowledge"], COMPOSE["services"]["knowledge"]["profiles"])
        example = (ROOT / "compose" / ".env.example").read_text()
        self.assertIn("COMPOSE_PROFILES=knowledge", example)

    def test_dockerfile_pins_qmd_and_forces_cpu(self):
        text = (ROOT / "compose" / "Dockerfile.knowledge").read_text()
        self.assertIn("ARG QMD_VERSION=2.8.3", text)
        self.assertIn("QMD_FORCE_CPU=1", text)

    def test_install_runs_knowledge_init_only_when_the_service_exists(self):
        """One switch (#122 review): COMPOSE_PROFILES; install follows the container."""
        text = (ROOT / "bootstrap" / "install").read_text()
        self.assertIn('docker inspect "$RALPH_KNOWLEDGE_CONTAINER"', text)
        self.assertNotIn("RALPH_KNOWLEDGE:-", text)
        self.assertIn("/ralph/bootstrap/knowledge-init", text)
        self.assertIn("-e GH_TOKEN ", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
