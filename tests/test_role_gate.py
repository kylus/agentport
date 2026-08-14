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
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "role_gate.py"
PROPOSE = REPO / "skills" / "propose-memory-update" / "propose.py"


def run_hook(tool, tool_input, cwd, role="contributor", env_extra=None):
    """Returns (decision, reason). decision is None when the hook stays silent."""
    env = {**os.environ, "AGENTPORT_ROLE": role, **(env_extra or {})}
    env.pop("AGENTPORT_ROLE_FILE", None)
    if role is None:
        env.pop("AGENTPORT_ROLE", None)
    for k, v in (env_extra or {}).items():
        env[k] = v
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
        env = {**os.environ, "AGENTPORT_ROLE": "contributor"}
        r = subprocess.run([sys.executable, str(HOOK)], input="not json",
                           capture_output=True, text=True, env=env, check=False)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
