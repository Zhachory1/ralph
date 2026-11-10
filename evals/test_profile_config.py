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
    "librarian",
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

    def test_librarian_has_terminal_and_not_code_execution(self):
        """#130, D-A: it pushes a branch through terminal/gh; it has nothing
        to compute, so code_execution is withheld rather than left unused."""
        declared = self.toolsets("librarian")
        self.assertIn("terminal", declared)
        self.assertNotIn("code_execution", declared)

    def test_librarian_pushes_with_the_maintain_token(self):
        """#130, D-A: the librarian reuses RALPH_MAINTAIN_TOKEN, like the
        author, builder and maintainer, and keeps it rather than losing it
        to the read-token sweep."""
        install = (ROOT / "bootstrap" / "install").read_text()
        push_block = install[install.index("for profile in ralph-maintainer"):]
        push_block = push_block[:push_block.index("done'")]
        self.assertIn("ralph-librarian", push_block)
        self.assertIn("*/ralph-librarian/*", install)

    def test_push_token_is_written_after_the_profiles_exist(self):
        """A profile created in this run has no .env until the profile loop
        has run, and the push-token loop skips a profile whose .env is absent.
        The read-token loop skips the librarian on purpose, so if the push
        token were written first, a first install would leave it no token."""
        install = (ROOT / "bootstrap" / "install").read_text()
        created = install.index("hermes profile create")
        pushed = install.index("for profile in ralph-maintainer ralph-builder ralph-author")
        self.assertLess(created, pushed)

    def test_librarian_does_not_get_the_repo_cache_mount(self):
        """dd-ralph-librarian 4.2: the cache spans every repository the
        instance has touched; the librarian needs one small repository and
        clones it instead."""
        install = (ROOT / "bootstrap" / "install").read_text()
        block = install[install.index("repo_cache_profile() {"):]
        block = block[:block.index("}")]
        self.assertNotIn("librarian", block)

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
                     "maintainer", "orchestrator", "researcher", "reviewer", "watcher",
                     "librarian"):
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


class ChatTokenDietTests(unittest.TestCase):
    """The WebUI chat (the default profile) resends its whole prompt on every
    model call, so what it never uses is paid for on each one: tool schemas
    and a skills index of everything Hermes bundles."""

    INSTALL = (ROOT / "bootstrap" / "install").read_text()
    DISABLED = ROOT / "profiles" / "default-disabled-skills.txt"

    def names(self) -> list[str]:
        return [line.strip() for line in self.DISABLED.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")]

    def test_install_disables_the_toolsets_the_chat_cannot_use(self):
        """browser needs a docker daemon the chat does not have; tts and
        vision are not used from chat. Disabled on the cli platform, which
        is the one hermes-webui reads."""
        self.assertIn("hermes tools disable browser tts vision", self.INSTALL)

    def test_install_keeps_the_board_after_disabling_toolsets(self):
        """The cut must not take the board with it: #105."""
        self.assertIn("hermes tools enable kanban", self.INSTALL)
        self.assertNotRegex(self.INSTALL, r"hermes tools disable[^\n]*kanban")

    def test_install_applies_the_disabled_skill_list_to_the_default_profile(self):
        self.assertIn("default-disabled-skills.txt", self.INSTALL)
        self.assertIn("write-profile-config.py --disable-skills", self.INSTALL)

    def test_install_merges_into_skills_disabled_instead_of_replacing_it(self):
        """PR #132 review: `config set skills.disabled` overwrote the owner's own list."""
        self.assertNotIn("config set skills.disabled", self.INSTALL)

    def test_install_ships_the_script_to_the_container_before_using_it(self):
        """The profile loop copies wmc.py late; the skills merge runs first."""
        used = self.INSTALL.index("/tmp/wmc.py --disable-skills")
        first_copy = self.INSTALL.index("/tmp/wmc.py")
        self.assertLess(first_copy, used)

    def test_the_google_workspace_wrappers_are_disabled_but_the_hub_stays(self):
        names = self.names()
        gws = [n for n in names if n.startswith("gws-")]
        self.assertGreaterEqual(len(gws), 15)
        self.assertNotIn("google-workspace", names)

    def test_the_skills_the_chat_works_with_are_not_disabled(self):
        names = self.names()
        for kept in ("github", "ralph-contributing", "sdlc-review", "docx", "pdf",
                     "xlsx", "systematic-debugging", "requesting-code-review",
                     "test-driven-development", "grounded-citations",
                     "caveman", "humanizer", "architecture-diagram"):
            self.assertNotIn(kept, names, kept)

    def test_the_disabled_skill_list_has_no_duplicates(self):
        names = self.names()
        self.assertEqual(len(names), len(set(names)))

    def test_skill_names_are_safe_to_embed_in_json(self):
        """install builds the JSON list in shell; a quote or backslash in a
        name would corrupt it silently."""
        for name in self.names():
            self.assertRegex(name, r"^[a-z0-9][a-z0-9._-]*$", name)


class DisableSkillsTests(unittest.TestCase):
    """`--disable-skills` adds Ralph's names to skills.disabled and keeps the rest."""

    def merged(self, existing: str, managed: str = "codex\nspike\n") -> dict:
        import yaml
        with tempfile.TemporaryDirectory() as d:
            names, cfg = pathlib.Path(d, "names.txt"), pathlib.Path(d, "config.yaml")
            names.write_text(managed)
            cfg.write_text(existing)
            wpc.disable_skills(str(names), str(cfg))
            return yaml.safe_load(cfg.read_text())

    def test_a_skill_the_owner_disabled_survives(self):
        out = self.merged("skills:\n  disabled:\n  - mine\n")
        self.assertEqual(["mine", "codex", "spike"], out["skills"]["disabled"])

    def test_inline_and_indented_lists_are_read_too(self):
        for existing in ("skills:\n  disabled: [mine]\n", "skills:\n  disabled:\n    - mine\n"):
            with self.subTest(existing=existing):
                self.assertEqual(["mine", "codex", "spike"], self.merged(existing)["skills"]["disabled"])

    def test_a_config_without_skills_gets_the_names(self):
        self.assertEqual(["codex", "spike"], self.merged("model:\n  default: m\n")["skills"]["disabled"])

    def test_other_skills_settings_and_other_blocks_are_untouched(self):
        out = self.merged("skills:\n  disabled:\n  - mine\n  external_dirs:\n  - /x\nother: 1\n")
        self.assertEqual(["/x"], out["skills"]["external_dirs"])
        self.assertEqual(1, out["other"])

    def test_an_inline_comment_on_an_existing_entry_is_not_part_of_its_name(self):
        """PR #132 review: `- mine # note` became `mine # note` in a flow list, where
        the `#` commented out the closing bracket and corrupted the config."""
        out = self.merged("skills:\n  disabled:\n    - mine # keep this skill disabled\n")
        self.assertEqual(["mine", "codex", "spike"], out["skills"]["disabled"])

    def test_a_trailing_comment_after_an_inline_list_is_dropped_from_names(self):
        out = self.merged("skills:\n  disabled: [mine, other] # hand picked\n")
        self.assertEqual(["mine", "other", "codex", "spike"], out["skills"]["disabled"])

    def test_a_hash_inside_a_quoted_name_is_kept(self):
        out = self.merged('skills:\n  disabled:\n    - "odd#name"\n')
        self.assertEqual(["odd#name", "codex", "spike"], out["skills"]["disabled"])

    def test_running_it_twice_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            names, cfg = pathlib.Path(d, "n"), pathlib.Path(d, "c")
            names.write_text("codex\n")
            cfg.write_text("skills:\n  disabled:\n  - mine\n")
            wpc.disable_skills(str(names), str(cfg))
            once = cfg.read_text()
            wpc.disable_skills(str(names), str(cfg))
            self.assertEqual(once, cfg.read_text())


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
                     "reviewer", "analyst", "experimenter", "auditor", "watcher", "librarian"):
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
