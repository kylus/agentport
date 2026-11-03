"""The prepare stage and the scaffold, run for real against throwaway dirs.

Two jobs:

1. Pin what a plain topic gets. `--print-argv`, `--print-prompt`, the seeded
   access.json, the regenerated .mcp.json and the scaffold tree are compared
   with golden files captured BEFORE the assistant kind existed. Adding a kind
   must not move a single byte of what every existing topic receives — a
   drift here is silent in production (an agent with a changed argv still
   starts and looks healthy).

2. Every refusal of the assistant launch guard is a claim, so each has a test,
   and so does the neighbouring shape that must still pass.

Hermetic: HOME, TOPIC_ROOT and the plugin dirs are temp paths, and `bun` is a
stub on PATH — run-topic.sh only checks that it exists in print modes. Golden
files store paths as $HOME / $SEED so they hold on any machine.

Regenerate goldens only when a change to plain topics is intended:
    AGENTPORT_UPDATE_GOLDEN=1 python3 -m unittest tests.test_launch
"""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from support import GIT_ENV, REPO

GOLDEN = REPO / "tests" / "golden"
RUN = REPO / "tools" / "run-topic.sh"
CREATE = REPO / "tools" / "create-topic.sh"
UPDATE = os.environ.get("AGENTPORT_UPDATE_GOLDEN") == "1"

OWNER = "100000000000000001"
OTHER = "100000000000000002"
# Shaped like nothing real; the CI credential grep must never match it.
FAKE_TOKEN = "fake-discord-token-for-tests"


class Sandbox:
    """A temp HOME with a stub bun and an empty Discord adapter dir."""

    def __init__(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="agentport-launch-"))
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        bun = self.bin / "bun"
        bun.write_text("#!/bin/sh\nexit 0\n")
        bun.chmod(0o755)
        self.discord = self.home / "discord-adapter"
        self.discord.mkdir()
        self.env = {
            **GIT_ENV,
            "HOME": str(self.home),
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "TOPIC_ROOT": str(self.home / "workspace"),
            "DISCORD_PLUGIN_DIR": str(self.discord),
        }
        for k in ("SKIP_PERMS", "AGENTPORT_NO_RESUME", "TMUX", "AGENTPORT_KIND"):
            self.env.pop(k, None)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def norm(self, text):
        return (text.replace(str(REPO), "$SEED")
                    .replace(str(self.home), "$HOME"))

    def topic(self, name, bot_env):
        d = self.home / "workspace" / f"topic-{name}"
        d.mkdir(parents=True)
        (d / "bot.env").write_text(bot_env)
        return d

    def run(self, *args, check=True):
        return subprocess.run([str(RUN), *args], env=self.env, text=True,
                              capture_output=True, check=check)

    def create(self, *args):
        return subprocess.run([str(CREATE), *args], env=self.env, text=True,
                              capture_output=True, check=True, cwd=self.root)


def discord_env(extra=""):
    return (f"DISCORD_BOT_TOKEN={FAKE_TOKEN}\n"
            f"OWNER_DISCORD_USER_ID={OWNER}\n" + extra)


def check_golden(case, name, actual):
    path = GOLDEN / name
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return
    case.assertEqual(actual, path.read_text(encoding="utf-8"),
                     f"{name} drifted for plain topics")


def tree_digest(root, norm):
    """Relative path → sha256 (or link target) for everything a scaffold made.

    .git is reduced to the one file inside it we put there on purpose: the
    post-commit hook. Object ids carry timestamps and would never match.
    Contents are hashed after `norm`: .claude/settings.json embeds the
    absolute checkout path, so a raw hash would only ever match one machine.
    """
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if rel == ".git" or (rel.startswith(".git/")
                             and rel != ".git/hooks/post-commit"):
            continue
        if p.is_symlink():
            out.append(f"{rel} -> {norm(os.readlink(p))}")
        elif p.is_dir():
            out.append(f"{rel}/")
        else:
            body = norm(p.read_text(encoding="utf-8")).encode("utf-8")
            out.append(f"{rel} {hashlib.sha256(body).hexdigest()}")
    return "\n".join(out) + "\n"


class PlainTopicUnchanged(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def test_discord_topic_prepare_outputs(self):
        d = self.sb.topic("demo", discord_env())
        argv = self.sb.run("demo", "--print-argv").stdout
        prompt = self.sb.run("demo", "--print-prompt").stdout
        check_golden(self, "plain-discord.argv", self.sb.norm(argv))
        check_golden(self, "plain-discord.prompt", self.sb.norm(prompt))
        check_golden(self, "plain-discord.access.json",
                     self.sb.norm((d / ".discord-state" / "access.json").read_text()))
        check_golden(self, "plain-discord.mcp.json",
                     self.sb.norm((d / ".mcp.json").read_text()))

    def test_skip_perms_argv(self):
        self.sb.topic("demo", discord_env())
        self.sb.env["SKIP_PERMS"] = "1"
        argv = self.sb.run("demo", "--print-argv").stdout
        check_golden(self, "plain-discord-skipperms.argv", self.sb.norm(argv))

    def test_existing_groups_survive_reseed(self):
        d = self.sb.topic("demo", discord_env())
        state = d / ".discord-state"
        state.mkdir()
        (state / "access.json").write_text(json.dumps({
            "dmPolicy": "allowlist", "allowFrom": [OTHER],
            "groups": {"555": {"requireMention": False, "allowFrom": []}}}))
        self.sb.run("demo", "--print-argv")
        check_golden(self, "plain-discord-reseed.access.json",
                     self.sb.norm((state / "access.json").read_text()))

    def test_scaffold_default(self):
        self.sb.create("demo")
        d = self.sb.home / "workspace" / "topic-demo"
        check_golden(self, "scaffold-default.tree", tree_digest(d, self.sb.norm))

    def test_scaffold_explicit_target(self):
        target = self.sb.root / "elsewhere"
        self.sb.create("demo", str(target))
        check_golden(self, "scaffold-target.tree", tree_digest(target, self.sb.norm))


if __name__ == "__main__":
    unittest.main()
