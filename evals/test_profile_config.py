"""The installer applies what a repo profile declares.

It used to copy only the model block, so a toolset declared in profiles/ never
reached the installed profile. The orchestrator went an entire feature without
the kanban toolset: it could read a brief, reason about a card graph, and then
not create one, because the tool its whole job needs was never granted. Nothing
errored, which is why it took a feature to notice.
"""

import importlib.machinery
import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "write-profile-config.py"

_loader = importlib.machinery.SourceFileLoader("wpc", str(SCRIPT))
_spec = importlib.util.spec_from_loader("wpc", _loader)
wpc = importlib.util.module_from_spec(_spec)
_loader.exec_module(wpc)

REPO_PROFILE = """model:
  provider: anthropic
  default: claude-sonnet-5
platform_toolsets:
  cli:
    - kanban
    - file
  api_server: [no_mcp]
"""


class ApplyTests(unittest.TestCase):
    def write(self, existing: str = "") -> str:
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "src.yaml"
            dst = pathlib.Path(tmp) / "dst.yaml"
            src.write_text(REPO_PROFILE)
            if existing:
                dst.write_text(existing)
            wpc.main(str(src), str(dst))
            return dst.read_text()

    def test_writes_both_blocks_into_a_fresh_profile(self):
        """A profile Hermes just created has no config.yaml at all."""
        out = self.write()
        self.assertIn("claude-sonnet-5", out)
        self.assertIn("kanban", out)

    def test_grants_the_toolset_a_profile_declares(self):
        """The bug: a declared toolset silently never reached the install."""
        out = self.write("model:\n  default: old\n")
        self.assertIn("kanban", out)

    def test_replaces_rather_than_duplicates_an_existing_block(self):
        out = self.write("model:\n  default: old\nplatform_toolsets:\n  cli:\n    - file\n")
        self.assertEqual(1, out.count("platform_toolsets:"))
        self.assertEqual(1, out.count("model:"))
        self.assertIn("kanban", out)
        self.assertNotIn("old", out)

    def test_replaces_the_terminal_block_whole(self):
        """install appends the terminal block; a key it stops writing (the
        repo-cache mount) must leave the installed profile too."""
        with tempfile.TemporaryDirectory() as d:
            src, dst = pathlib.Path(d, "src.yaml"), pathlib.Path(d, "dst.yaml")
            src.write_text("terminal:\n  backend: docker\n")
            dst.write_text("terminal:\n  backend: local\n  docker_volumes: [x]\n")
            wpc.main(str(src), str(dst))
            self.assertEqual("terminal:\n  backend: docker\n", dst.read_text())

    def test_leaves_hermes_generated_config_alone(self):
        """Everything outside the two blocks is Hermes', and version-specific."""
        out = self.write("onboarding:\n  seen:\n    prompt: true\n_config_version: 46\n")
        self.assertIn("onboarding:", out)
        self.assertIn("_config_version: 46", out)


ALL = (
    "orchestrator", "author", "reviewer", "builder", "researcher",
    "analyst", "experimenter", "auditor", "watcher", "chat-maintainer",
)


class DeclarationTests(unittest.TestCase):
    """What the shipped profiles ask for, so a lost grant fails here."""

    def toolsets(self, name: str) -> list[str]:
        """The granted list, not the file: a comment explaining why a toolset
        is withheld must not read as granting it."""
        import yaml
        config = yaml.safe_load((ROOT / "profiles" / name / "config.yaml").read_text())
        return list((config.get("platform_toolsets") or {}).get("cli") or [])

    def test_every_designed_profile_exists(self):
        for name in ALL:
            self.assertTrue((ROOT / "profiles" / name / "SOUL.md").exists(), name)
            self.assertTrue((ROOT / "profiles" / name / "config.yaml").exists(), name)

    def test_every_profile_may_act_when_dispatched(self):
        """A card worker has nobody present to answer an approval prompt, and
        the default refuses the gated tools outright. A profile's config
        replaces the global one rather than merging, so this cannot be set
        once centrally: each profile carries it or silently cannot work."""
        import yaml
        for name in ALL:
            config = yaml.safe_load((ROOT / "profiles" / name / "config.yaml").read_text())
            approvals = config.get("approvals") or {}
            self.assertEqual("approve", approvals.get("single_query_mode"), name)

    def test_the_orchestrator_can_reach_the_board(self):
        self.assertIn("kanban", self.toolsets("orchestrator"))

    def test_code_running_profiles_can_run_code(self):
        for name in ("builder", "author", "experimenter"):
            self.assertIn("code_execution", self.toolsets(name), name)

    def test_builder_can_push_from_credential_forwarding_terminal(self):
        self.assertIn("terminal", self.toolsets("builder"))

    def test_author_can_push_docs_from_credential_forwarding_terminal(self):
        self.assertIn("terminal", self.toolsets("author"))

    def test_read_only_profiles_cannot_run_code(self):
        """The reason these profiles are separate boundaries at all. A
        read-only credential next to a shell is not read-only for long."""
        for name in ("researcher", "analyst", "watcher"):
            self.assertNotIn("code_execution", self.toolsets(name), name)

    def test_every_profile_can_reach_github_from_a_terminal(self):
        """docs/decisions/0001: every profile can use gh. terminal is the toolset
        that carries the forwarded token; code_execution scrubs it."""
        import yaml
        for f in sorted((ROOT / "profiles").glob("*/config.yaml")):
            with self.subTest(profile=f.parent.name):
                config = yaml.safe_load(f.read_text())
                self.assertIn("terminal", self.toolsets(f.parent.name))
                disabled = (config.get("agent") or {}).get("disabled_toolsets") or []
                self.assertNotIn("terminal", disabled)

    def test_the_researcher_reads_the_web_and_cannot_plan(self):
        declared = self.toolsets("researcher")
        self.assertIn("web", declared)
        for forbidden in ("kanban", "code_execution"):
            self.assertNotIn(forbidden, declared, forbidden)

    def test_the_chat_maintainer_reads_untrusted_chat_and_holds_nothing_else(self):
        """It reads what strangers wrote to the owner, so it gets the drafter's
        shape: staged files in, a decision out. A terminal for gh (decision 0001),
        no network, no board write, and no lens-tools tool that could send."""
        import yaml
        config = yaml.safe_load((ROOT / "profiles" / "chat-maintainer" / "config.yaml").read_text())
        self.assertEqual(["terminal", "file"], self.toolsets("chat-maintainer"))
        disabled = config["agent"]["disabled_toolsets"]
        for withheld in ("code_execution", "web", "browser", "kanban", "delegation"):
            self.assertIn(withheld, disabled, withheld)
        allowed = config["mcp_servers"]["lens-tools"]["tools"]["include"]
        self.assertEqual(
            {"evidence_read", "evidence_search", "kanban_show", "kanban_heartbeat",
             "kanban_complete", "kanban_block"},
            set(allowed),
        )

    def test_the_chat_maintainer_holds_no_chat_credential(self):
        """The Google Chat token lives on the host, like the Gmail token. A
        profile that cannot reach it cannot be talked into using it."""
        text = (ROOT / "profiles" / "chat-maintainer" / "config.yaml").read_text()
        for needle in ("GCHAT", "WEBHOOK", "chat_token", "client_secret", "TOKEN"):
            self.assertNotIn(needle, text.replace("HERMES_KANBAN", ""), needle)


class VoiceTests(unittest.TestCase):
    """Every profile is Ralph, mounts the voice lane, and writes for its
    reader: lenses talk to agents (caveman), everything else may reach a
    person (simplified-technical-english and stop-slop)."""

    AGENT_ONLY = {"lens-generalist", "lens-mvp", "lens-reliability", "lens-security"}
    VOICE = ("caveman", "stop-slop", "simplified-technical-english", "i-have-adhd")

    def profiles(self):
        return sorted(p for p in (ROOT / "profiles").iterdir() if (p / "SOUL.md").is_file())

    def test_there_are_profiles(self):
        self.assertGreaterEqual(len(self.profiles()), 16)

    def test_every_profile_mounts_the_voice_lane(self):
        for p in self.profiles():
            with self.subTest(profile=p.name):
                self.assertIn("/ralph/skills/voice", (p / "config.yaml").read_text())

    def test_install_puts_the_engineering_standards_before_every_role(self):
        """Owner instruction 2026-11-04: every profile follows the coding,
        testing, ownership and documentation standards."""
        install = (ROOT / "bootstrap" / "install").read_text()
        self.assertEqual(2, install.count('cat "$PROFILES_DIR/STANDARDS.md"'))
        self.assertIn("Neither is Google Docs", (ROOT / "profiles" / "STANDARDS.md").read_text())

    def test_read_profiles_use_the_configured_read_token(self):
        """Decision 0002: an instance can give its read profiles the owner's token."""
        install = (ROOT / "bootstrap" / "install").read_text()
        self.assertIn("READ_TOKEN=${RALPH_PROFILE_READ_TOKEN:-${RALPH_GITHUB_TOKEN:-}}", install)
        self.assertNotIn('-e READ_TOKEN="', install, "a token on a command line is visible to ps")

    def test_buildkite_token_goes_only_to_maintainer_and_builder_after_profiles_exist(self):
        """#107: CI logs for the profiles that fix builds; written after the
        profile loop so a fresh install gets it (review finding)."""
        install = (ROOT / "bootstrap" / "install").read_text()
        block = install[install.index("# CI logs for the profiles"):]
        self.assertIn("for profile in ralph-maintainer ralph-builder; do", block)
        self.assertLess(install.index('for dir in "$PROFILES_DIR"/*/; do'),
                        install.index("# CI logs for the profiles"))
        self.assertIn('[ -f "$f" ] || { : > "$f"; chmod 600 "$f"; }', block)

    def test_every_profile_is_known_as_ralph(self):
        for p in self.profiles():
            with self.subTest(profile=p.name):
                self.assertIn("You are Ralph", (p / "SOUL.md").read_text())

    def test_voice_follows_the_reader(self):
        for p in self.profiles():
            soul = (p / "SOUL.md").read_text()
            with self.subTest(profile=p.name):
                self.assertIn("i-have-adhd", soul)
                if p.name in self.AGENT_ONLY:
                    self.assertIn("Follow the `caveman`", soul)
                else:
                    self.assertIn("simplified-technical-english", soul)
                    self.assertIn("Do not use `caveman`", soul)

    def test_the_voice_lane_holds_the_four_skills(self):
        lane = ROOT / "skills" / "voice"
        tracked = {d.name for d in lane.iterdir() if d.is_dir()}
        manifest = (ROOT / "skills" / "third-party.yaml").read_text()
        for s in self.VOICE:
            with self.subTest(skill=s):
                self.assertTrue(s in tracked or f"name: {s}\n    lane: voice" in manifest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
