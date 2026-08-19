"""The Stop hook's claim: an owner cannot end a turn on uncommitted memory/.

Run the same way Claude Code runs it — a subprocess fed a JSON payload on
stdin — so a hook that only works when imported still fails here.
"""
import json
import os
import pathlib
import subprocess
import sys
import unittest

from support import REPO, git, make_topic

HOOK = REPO / "hooks" / "memory_clean.py"


def run_hook(cwd, role="owner"):
    """Returns (decision, reason); decision is None when the turn may end."""
    env = {**os.environ, "AGENTPORT_ROLE": role,
           "CLAUDE_PROJECT_DIR": str(cwd)}
    env.pop("AGENTPORT_ROLE_FILE", None)
    payload = json.dumps({"hook_event_name": "Stop", "session_id": "s1"})
    r = subprocess.run([sys.executable, str(HOOK)], input=payload,
                       capture_output=True, text=True, env=env, cwd=str(cwd),
                       check=False)
    assert r.returncode == 0, f"hook exited {r.returncode}: {r.stderr}"
    if not r.stdout.strip():
        return None, ""
    out = json.loads(r.stdout)
    return out["decision"], out["reason"]


class MemoryCleanCase(unittest.TestCase):
    def setUp(self):
        self.dir = make_topic()
        import shutil
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)


class TestCleanTree(MemoryCleanCase):
    def test_a_committed_memory_lets_the_turn_end(self):
        decision, reason = run_hook(self.dir)
        self.assertIsNone(decision, reason)

    def test_changes_outside_memory_are_not_this_hook_s_business(self):
        (self.dir / "sources" / "note.md").write_text("scratch\n")
        decision, reason = run_hook(self.dir)
        self.assertIsNone(decision, reason)


class TestDirtyTree(MemoryCleanCase):
    def test_unstaged_memory_blocks(self):
        (self.dir / "memory" / "decisions.md").write_text("# decisions\nnew\n")
        decision, reason = run_hook(self.dir)
        self.assertEqual(decision, "block")
        self.assertIn("unstaged", reason)

    def test_staged_but_uncommitted_memory_blocks(self):
        (self.dir / "memory" / "decisions.md").write_text("# decisions\nnew\n")
        git("add", "memory/decisions.md", cwd=self.dir)
        decision, reason = run_hook(self.dir)
        self.assertEqual(decision, "block")
        self.assertIn("staged", reason)

    def test_an_untracked_section_blocks(self):
        # A whole section that has never been in the repo is the easiest kind
        # to lose, and the least likely to be noticed.
        (self.dir / "memory" / "risks.md").write_text("# risks\n")
        decision, reason = run_hook(self.dir)
        self.assertEqual(decision, "block")
        self.assertIn("untracked", reason)

    def test_committing_clears_the_block(self):
        (self.dir / "memory" / "decisions.md").write_text("# decisions\nnew\n")
        self.assertEqual(run_hook(self.dir)[0], "block")
        git("add", "-A", cwd=self.dir)
        git("commit", "-q", "-m", "memory: record the decision", cwd=self.dir)
        self.assertIsNone(run_hook(self.dir)[0])


class TestRoles(MemoryCleanCase):
    def test_contributors_are_exempt(self):
        # They cannot write memory/ at all, so blocking them on a dirty tree
        # would be a turn that can never end.
        (self.dir / "memory" / "decisions.md").write_text("# decisions\nnew\n")
        decision, _ = run_hook(self.dir, role="contributor")
        self.assertIsNone(decision)

    def test_an_unset_role_is_treated_as_contributor(self):
        (self.dir / "memory" / "decisions.md").write_text("# decisions\nnew\n")
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.dir)}
        env.pop("AGENTPORT_ROLE", None)
        env.pop("AGENTPORT_ROLE_FILE", None)
        r = subprocess.run([sys.executable, str(HOOK)], input="{}",
                           capture_output=True, text=True, env=env,
                           cwd=str(self.dir), check=False)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")


class TestNonTopicDirectory(unittest.TestCase):
    def test_a_directory_with_no_memory_is_ignored(self):
        import tempfile
        d = pathlib.Path(tempfile.mkdtemp(prefix="agentport-notopic-"))
        import shutil
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        decision, _ = run_hook(d)
        self.assertIsNone(decision)


if __name__ == "__main__":
    unittest.main()
