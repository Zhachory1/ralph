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

    def test_the_researcher_reads_the_web_and_reaches_the_board(self):
        """#105: it can hand work to the orchestrator; it still runs no code."""
        declared = self.toolsets("researcher")
        self.assertIn("web", declared)
        self.assertIn("kanban", declared)
        self.assertNotIn("code_execution", declared)

    def test_general_profiles_reach_the_board(self):
        """#105: every profile that is not a narrow machine job has kanban."""
        for name in ("analyst", "auditor", "author", "builder", "experimenter",
                     "maintainer", "orchestrator", "researcher", "reviewer", "watcher"):
            with self.subTest(profile=name):
                self.assertIn("kanban", self.toolsets(name))

    def test_isolated_profiles_stay_off_the_board(self):
        """Lenses and synthesis must not plan or change their own review graph;
        the chat and inbox profiles read untrusted text with a fixed shape."""
        import yaml
        for name in ("lens-generalist", "lens-mvp", "lens-reliability", "lens-security",
                     "review-synthesis", "chat-maintainer", "chat-summarizer", "inbox-drafter"):
            with self.subTest(profile=name):
                self.assertNotIn("kanban", self.toolsets(name))
                config = yaml.safe_load((ROOT / "profiles" / name / "config.yaml").read_text())
                self.assertIn("kanban", config["agent"]["disabled_toolsets"])

    def test_install_enables_the_board_for_the_default_profile(self):
        self.assertIn("hermes tools enable kanban",
                      (ROOT / "bootstrap" / "install").read_text())

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
        # Named profiles (container and local paths) and the default SOUL (#128).
        self.assertEqual(3, install.count('cat "$PROFILES_DIR/STANDARDS.md"'))
        self.assertIn("Neither is Google Docs", (ROOT / "profiles" / "STANDARDS.md").read_text())

    def test_standards_name_no_employer_hosts_or_document_standards(self):
        """AGENTS.md: this repository is public, so no internal hostnames and
        no document-standard names. Documents are tracked in the docs repository."""
        standards = (ROOT / "profiles" / "STANDARDS.md").read_text()
        readme = (ROOT / "README.md").read_text()
        for text in (standards, readme):
            for banned in ("roktinternal", "Rokt", "Docshare", "Hub or"):
                self.assertNotIn(banned, text)
        for banned in ("Knowledge Management Runbook", "Document Dependency Structure",
                       "E2E Ownership Manifesto", "Pragmatic Guide to Tests"):
            self.assertNotIn(banned, standards)
        self.assertIn("RALPH_DOCS_REPO", standards)
        self.assertIn("front matter", standards)
        self.assertIn("Neither is Google Docs", standards)

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

    def test_knowledge_goes_only_to_searching_profiles(self):
        """#121: general profiles search the knowledge index; lenses, synthesis
        and the chat/inbox profiles never do."""
        install = (ROOT / "bootstrap" / "install").read_text()
        block = install[install.index("knowledge_profile() {"):]
        block = block[:block.index("}")]
        for name in ("orchestrator", "author", "builder", "maintainer", "researcher",
                     "reviewer", "analyst", "experimenter", "auditor", "watcher"):
            self.assertIn(name, block)
        for name in ("lens-", "review-synthesis", "chat-maintainer", "chat-summarizer", "inbox-drafter"):
            self.assertNotIn(name, block)
        self.assertIn("include: [query, get, multi_get, status]", install)

    def test_mcp_blocks_merge_without_losing_lens_tools(self):
        src = ("mcp_servers:\n  lens-tools:\n    command: x\n"
               "model:\n  default: m\n"
               "mcp_servers:\n  knowledge:\n    url: http://knowledge:8181/mcp\n")
        with tempfile.TemporaryDirectory() as d:
            s_path, d_path = pathlib.Path(d, "src.yaml"), pathlib.Path(d, "dst.yaml")
            s_path.write_text(src)
            d_path.write_text("mcp_servers:\n  old:\n    command: y\nother: 1\n")
            wpc.main(str(s_path), str(d_path))
            wpc.main(str(s_path), str(d_path))
            import yaml
            out = yaml.safe_load(d_path.read_text())
        self.assertEqual(["lens-tools", "knowledge"], list(out["mcp_servers"]))
        self.assertEqual(1, out["other"])

    def test_a_profile_loses_knowledge_when_the_source_has_no_mcp_servers(self):
        """#123 review: turning the feature off removes it from profiles too."""
        with tempfile.TemporaryDirectory() as d:
            s_path, d_path = pathlib.Path(d, "src.yaml"), pathlib.Path(d, "dst.yaml")
            s_path.write_text("model:\n  default: m\n")
            d_path.write_text("mcp_servers:\n  knowledge:\n    url: x\nother: 1\n")
            wpc.main(str(s_path), str(d_path))
            text = d_path.read_text()
        self.assertNotIn("knowledge", text)
        self.assertIn("other: 1", text)

    def test_global_knowledge_toggle_keeps_other_servers(self):
        """#123 review: the global config's own servers survive; turning the
        feature off removes only knowledge."""
        import yaml
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d, "config.yaml")
            path.write_text("model:\n  default: m\nmcp_servers:\n  mine:\n    url: http://x\nother: 1\n")
            wpc.set_knowledge(str(path), True)
            wpc.set_knowledge(str(path), True)
            on = yaml.safe_load(path.read_text())
            wpc.set_knowledge(str(path), False)
            off = yaml.safe_load(path.read_text())
        self.assertEqual(["mine", "knowledge"], list(on["mcp_servers"]))
        self.assertEqual(["mine"], list(off["mcp_servers"]))
        self.assertEqual(1, off["other"])

    def test_principles_search_first_and_write_down_what_lasts(self):
        """#121 (dd-ralph-knowledge D6/D7). install prepends PRINCIPLES to every
        SOUL, so these reach every profile."""
        text = (ROOT / "profiles" / "PRINCIPLES.md").read_text()
        self.assertIn("**Search before you plan or write; cite what you used.**", text)
        self.assertIn("**Write down what lasts.**", text)
        for part in ("memory/<decisions|root-causes|conventions>/", "memory/reports/",
                     "kind:", "source:", "profile:", "date:",
                     "gh api -X PUT repos/$RALPH_DOCS_REPO/contents/", "RALPH_DOCS_PUBLIC=1"):
            self.assertIn(part, text)
        install = (ROOT / "bootstrap" / "install").read_text()
        self.assertIn('cat "$PROFILES_DIR/PRINCIPLES.md"', install)

    def test_principles_say_search_fast(self):
        """Measured live: typed searches with rerank false return the same top
        hit in ~1 s that re-ranking returns in 108-239 s; a plain query also
        pulls the 1.3 GB expansion model (design D4 keeps it off)."""
        text = (ROOT / "profiles" / "PRINCIPLES.md").read_text()
        for part in ("typed `searches`", "`rerank: false`", "Never pass a plain-text `query`"):
            self.assertIn(part, text)

    def test_profiles_learn_the_docs_repo_and_its_visibility(self):
        install = (ROOT / "bootstrap" / "install").read_text()
        self.assertIn("RALPH_DOCS_REPO, RALPH_DOCS_PUBLIC]", install)
        # The default profile's sandbox allowlist is set separately (#124 review).
        self.assertIn('"RALPH_DOCS_REPO","RALPH_DOCS_PUBLIC"]', install)
        self.assertIn('gh repo view "$RALPH_DOCS_REPO" --json visibility', install)
        # Unknown visibility counts as public: the safe side.
        self.assertIn('DOCS_PUBLIC=1; [ "$vis" = PRIVATE ] || [ "$vis" = INTERNAL ] && DOCS_PUBLIC=0', install)

    def test_default_soul_is_built_like_the_others_and_hands_off_attachments(self):
        """#128: install writes PRINCIPLES + STANDARDS + DEFAULT_SOUL to the
        global SOUL; the text says to copy cleaned content into the card and
        never pass the path on."""
        install = (ROOT / "bootstrap" / "install").read_text()
        self.assertIn('cat "$PROFILES_DIR/DEFAULT_SOUL.md"; } |', install)
        self.assertIn('"${HERMES_HOME:-$HOME/.hermes}/SOUL.md"', install)
        soul = (ROOT / "profiles" / "DEFAULT_SOUL.md").read_text()
        self.assertIn("You are Hermes Agent", soul)
        for part in ("## Attachments", "--body-file", "Never hand on the attachment path",
                     "secrets", "RALPH_DOCS_PUBLIC=1"):
            self.assertIn(part, soul)
        self.assertFalse((ROOT / "profiles" / "default").exists(),
                         "profiles/default/ would create a ralph-default profile")

    def test_only_the_default_sandbox_mounts_chat_uploads(self):
        install = (ROOT / "bootstrap" / "install").read_text()
        self.assertIn('ATTACH_DIR="$RALPH_SANDBOX_ROOT/.webui-attachments"', install)
        self.assertIn('vols="\\"$ATTACH_DIR:$ATTACH_DIR:ro\\""', install)
        block = install[install.index("terminal_block() {"):]
        block = block[:block.index("\n}\n")]
        self.assertNotIn("ATTACH_DIR", block, "named profiles must never see uploads")
        compose = (ROOT / "compose" / "docker-compose.yml").read_text()
        self.assertIn("HERMES_WEBUI_ATTACHMENT_DIR: ${RALPH_SANDBOX_ROOT}/.webui-attachments", compose)

    def test_overlay_mcp_goes_only_to_named_profiles_with_an_allowlist(self):
        """#133: a private mcp.json grants each server to the profiles it names."""
        import json, yaml
        with tempfile.TemporaryDirectory() as d:
            mcp = pathlib.Path(d, "mcp.json")
            mcp.write_text(json.dumps({
                "team-memory": {"profiles": ["researcher", "default"],
                                "config": {"url": "https://memory.example/mcp/team/"},
                                "tools": ["recall", "reflect"]}}))
            block = wpc.overlay_mcp_block(str(mcp), "researcher")
            self.assertEqual("", wpc.overlay_mcp_block(str(mcp), "lens-mvp"))
            src, dst = pathlib.Path(d, "src.yaml"), pathlib.Path(d, "dst.yaml")
            src.write_text("mcp_servers:\n  knowledge:\n    url: http://knowledge:8181/mcp\n" + block)
            dst.write_text("other: 1\n")
            wpc.main(str(src), str(dst))
            out = yaml.safe_load(dst.read_text())
        self.assertEqual(["knowledge", "team-memory"], list(out["mcp_servers"]))
        self.assertEqual(["recall", "reflect"], out["mcp_servers"]["team-memory"]["tools"]["include"])
        self.assertEqual("https://memory.example/mcp/team/", out["mcp_servers"]["team-memory"]["url"])

    def test_overlay_mcp_requires_a_tool_allowlist(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            mcp = pathlib.Path(d, "mcp.json")
            mcp.write_text(json.dumps({"x": {"profiles": ["researcher"], "config": {"url": "u"}}}))
            with self.assertRaises(SystemExit):
                wpc.overlay_mcp_block(str(mcp), "researcher")

    def test_global_overlay_servers_are_replaced_and_hand_added_ones_kept(self):
        import json, yaml
        with tempfile.TemporaryDirectory() as d:
            cfg, mcp = pathlib.Path(d, "config.yaml"), pathlib.Path(d, "mcp.json")
            cfg.write_text("mcp_servers:\n  mine:\n    url: http://x\n  old-overlay:\n    url: http://o\nother: 1\n")
            mcp.write_text(json.dumps({"team-memory": {"profiles": ["default"],
                                       "config": {"url": "u"}, "tools": ["recall"]}}))
            wpc.set_overlay_global(str(cfg), str(mcp), "old-overlay")
            wpc.set_overlay_global(str(cfg), str(mcp), "team-memory")
            on = yaml.safe_load(cfg.read_text())
            wpc.set_overlay_global(str(cfg), "", "team-memory")
            off = yaml.safe_load(cfg.read_text())
        self.assertEqual(["mine", "team-memory"], list(on["mcp_servers"]))
        self.assertEqual(["mine"], list(off["mcp_servers"]))
        self.assertEqual(1, off["other"])

    def test_overlay_mcp_cannot_shadow_built_in_servers(self):
        """#134 review: knowledge and lens-tools are Ralph's names."""
        import json
        with tempfile.TemporaryDirectory() as d:
            mcp = pathlib.Path(d, "mcp.json")
            mcp.write_text(json.dumps({"knowledge": {"profiles": ["researcher"],
                                       "config": {"url": "u"}, "tools": ["query"]}}))
            with self.assertRaises(SystemExit):
                wpc.overlay_mcp_block(str(mcp), "researcher")

    def test_config_writes_are_atomic(self):
        """#134 review: a failure mid-write must leave the old config."""
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            cfg = pathlib.Path(d, "config.yaml")
            cfg.write_text("mcp_servers:\n  mine:\n    url: http://x\nother: 1\n")
            with mock.patch.object(wpc.os, "replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    wpc.set_overlay_global(str(cfg), "", "mine")
            self.assertIn("other: 1", cfg.read_text())
            self.assertIn("mine:", cfg.read_text())

    def test_atomic_write_keeps_owner_and_mode(self):
        """#134: as root, a renamed temp file would be root-owned and lock
        Hermes out of a 640 config. Owner and mode carry over."""
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            cfg = pathlib.Path(d, "config.yaml")
            cfg.write_text("a: 1\n")
            cfg.chmod(0o640)
            st = cfg.stat()
            with mock.patch.object(wpc.os, "chown") as chown:
                wpc.write_atomic(cfg, "a: 2\n")
            chown.assert_called_once()
            self.assertEqual((st.st_uid, st.st_gid), chown.call_args.args[1:])
            self.assertEqual(0o640, cfg.stat().st_mode & 0o777)
            self.assertEqual("a: 2\n", cfg.read_text())

    def test_global_writers_handle_a_file_without_a_final_newline(self):
        """#134 review: appending to `other: 1` with no newline made one bad line."""
        import json, yaml
        with tempfile.TemporaryDirectory() as d:
            cfg, mcp = pathlib.Path(d, "config.yaml"), pathlib.Path(d, "mcp.json")
            mcp.write_text(json.dumps({"team-memory": {"profiles": ["default"],
                                       "config": {"url": "u"}, "tools": ["recall"]}}))
            cfg.write_text("other: 1")
            wpc.set_overlay_global(str(cfg), str(mcp), "")
            self.assertEqual(1, yaml.safe_load(cfg.read_text())["other"])
            cfg.write_text("other: 1")
            wpc.set_knowledge(str(cfg), True)
            out = yaml.safe_load(cfg.read_text())
        self.assertEqual(1, out["other"])
        self.assertIn("knowledge", out["mcp_servers"])

    def test_install_writes_the_overlay_record_atomically(self):
        install = (ROOT / "bootstrap" / "install").read_text()
        self.assertEqual(2, install.count('mv -f "$1.tmp" "$1"\' sh "$state"'))

    def test_overlay_mcp_server_names_are_plain(self):
        """#134 review: a comma or space would break install's name record."""
        import json
        with tempfile.TemporaryDirectory() as d:
            mcp = pathlib.Path(d, "mcp.json")
            mcp.write_text(json.dumps({"a,b": {"profiles": ["researcher"],
                                       "config": {"url": "u"}, "tools": ["recall"]}}))
            with self.assertRaises(SystemExit):
                wpc.overlay_mcp_block(str(mcp), "researcher")

    def test_install_merges_overlay_names_without_a_failing_pipeline(self):
        """#134 review: with no grant, grep -v on empty input exits 1 and
        pipefail aborted install. The union is computed in Python."""
        install = (ROOT / "bootstrap" / "install").read_text()
        line = next(l for l in install.splitlines() if l.strip().startswith("all=$("))
        self.assertNotIn("grep", line)

    def test_global_overlay_replaces_a_hand_added_server_of_the_same_name(self):
        """#134 review: one key, never two."""
        import json, yaml
        with tempfile.TemporaryDirectory() as d:
            cfg, mcp = pathlib.Path(d, "config.yaml"), pathlib.Path(d, "mcp.json")
            cfg.write_text("mcp_servers:\n  team-memory:\n    url: http://old\n  mine:\n    url: http://x\n")
            mcp.write_text(json.dumps({"team-memory": {"profiles": ["default"],
                                       "config": {"url": "http://new"}, "tools": ["recall"]}}))
            wpc.set_overlay_global(str(cfg), str(mcp), "")
            text = cfg.read_text()
        self.assertEqual(1, text.count("  team-memory:"))
        self.assertEqual("http://new", yaml.safe_load(text)["mcp_servers"]["team-memory"]["url"])
        self.assertIn("mine", yaml.safe_load(text)["mcp_servers"])

    def test_install_records_overlay_names_before_writing_the_global_config(self):
        install = (ROOT / "bootstrap" / "install").read_text()
        record = install.index('printf \'%s\' "$all"')
        write = install.index('--overlay-global')
        self.assertLess(record, write)

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
