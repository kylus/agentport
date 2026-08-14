"""Shared fixture: a throwaway topic directory that behaves like a real one.

The approval scripts commit, so the fixture needs a real git repo — and a
hermetic one. `GIT_ENV` pins the identity and cuts the global and system
config out entirely: a developer with `commit.gpgsign = true` would otherwise
watch these tests fail on their machine and pass in CI, which teaches people
to distrust the suite.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
PROPOSE = REPO / "skills" / "propose-memory-update" / "propose.py"
PROPOSAL = REPO / "skills" / "approve-proposal" / "proposal.py"

SECTIONS = ("background", "current_understanding", "decisions",
            "open_questions", "commitments", "people")

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "agentport tests",
    "GIT_AUTHOR_EMAIL": "tests@example.invalid",
    "GIT_COMMITTER_NAME": "agentport tests",
    "GIT_COMMITTER_EMAIL": "tests@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, env=GIT_ENV, check=True).stdout.strip()


def make_topic(prefix="agentport-test-"):
    """A topic directory with the six sections and an initialised git repo."""
    d = pathlib.Path(tempfile.mkdtemp(prefix=prefix))
    (d / "pending").mkdir()
    (d / "memory").mkdir()
    (d / "sources").mkdir()
    for s in SECTIONS:
        (d / "memory" / f"{s}.md").write_text(f"# {s}\n", encoding="utf-8")
    git("init", "-q", "-b", "main", cwd=d)
    git("add", "-A", cwd=d)
    git("commit", "-q", "-m", "init", cwd=d)
    return d


class TopicCase(unittest.TestCase):
    """Base class: self.dir is a fresh topic directory, removed afterwards."""

    def setUp(self):
        self.dir = make_topic()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def run_script(self, script, *args):
        return subprocess.run([sys.executable, str(script), *args],
                              cwd=self.dir, capture_output=True, text=True,
                              env=GIT_ENV, check=False)

    def propose(self, *args):
        return self.run_script(PROPOSE, *args)

    def proposal(self, *args):
        return self.run_script(PROPOSAL, *args)

    def write_draft(self, text="The team decided to keep the LINE adapter.",
                    name=".draft.md"):
        p = self.dir / "pending" / name
        p.write_text(text + "\n", encoding="utf-8")
        return p

    def pending_files(self):
        return sorted(f.name for f in (self.dir / "pending").iterdir()
                      if f.is_file() and not f.name.startswith("."))

    def memory(self, section):
        return (self.dir / "memory" / f"{section}.md").read_text(encoding="utf-8")

    def head_files(self):
        """Paths touched by the most recent commit."""
        out = git("show", "--name-only", "--pretty=format:", "HEAD", cwd=self.dir)
        return sorted(p for p in out.splitlines() if p.strip())

    def head_message(self):
        return git("log", "-1", "--pretty=%s", cwd=self.dir)

    def assertCleanTree(self):
        self.assertEqual(git("status", "--porcelain", cwd=self.dir), "",
                         "the scripts left uncommitted changes behind")
