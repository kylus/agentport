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



ASSISTANT = "AGENTPORT_KIND=assistant\n"


class AssistantScaffold(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def test_kind_flag_after_name_is_not_a_target_dir(self):
        # The exact call the host rollout uses.
        self.sb.create("assistant", "--kind", "assistant")
        d = self.sb.home / "workspace" / "topic-assistant"
        self.assertTrue(d.is_dir())
        self.assertFalse((self.sb.root / "--kind").exists())
        self.assertFalse((self.sb.root / "assistant").exists())
        self.assertIn("AGENTPORT_KIND=assistant", (d / "bot.env").read_text())
        self.assertIn("Owner's Assistant", (d / "CLAUDE.md").read_text())
        self.assertNotIn("Topic Expert", (d / "CLAUDE.md").read_text())

    def test_kind_flag_before_name_and_equals_form(self):
        self.sb.create("--kind=assistant", "a1")
        self.sb.create("--kind", "assistant", "a2", str(self.sb.root / "t2"))
        self.assertIn("AGENTPORT_KIND=assistant",
                      (self.sb.home / "workspace" / "topic-a1" / "bot.env").read_text())
        self.assertIn("AGENTPORT_KIND=assistant",
                      (self.sb.root / "t2" / "bot.env").read_text())

    def test_no_auto_push_hook(self):
        self.sb.create("assistant", "--kind", "assistant")
        d = self.sb.home / "workspace" / "topic-assistant"
        self.assertFalse((d / ".git" / "hooks" / "post-commit").exists())

    def test_topic_kind_still_gets_auto_push_hook(self):
        self.sb.create("demo", "--kind", "topic")
        d = self.sb.home / "workspace" / "topic-demo"
        self.assertTrue((d / ".git" / "hooks" / "post-commit").exists())

    def test_unknown_kind_refused(self):
        with self.assertRaises(subprocess.CalledProcessError) as cm:
            self.sb.create("x", "--kind", "root")
        self.assertEqual(cm.exception.returncode, 2)
        self.assertFalse((self.sb.home / "workspace" / "topic-x").exists())

    def test_bot_env_not_overwritten_on_rerun(self):
        self.sb.create("assistant", "--kind", "assistant")
        env = self.sb.home / "workspace" / "topic-assistant" / "bot.env"
        env.write_text("kept\n")
        self.sb.create("assistant", "--kind", "assistant")
        self.assertEqual(env.read_text(), "kept\n")


class AssistantGuard(unittest.TestCase):
    """Each refusal, plus the neighbouring shape that must still launch."""

    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def prepare(self, bot_env, access=None):
        d = self.sb.topic("assistant", bot_env)
        if access is not None:
            state = d / ".discord-state"
            state.mkdir()
            (state / "access.json").write_text(json.dumps(access))
        return d, self.sb.run("assistant", "--print-argv", check=False)

    def assert_refused(self, r, needle):
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(needle, r.stderr)
        self.assertEqual(r.stdout, "", "a refused launch must print no argv")

    def test_clean_launch(self):
        d, r = self.prepare(discord_env(ASSISTANT))
        self.assertEqual(r.returncode, 0, r.stderr)
        access = json.loads((d / ".discord-state" / "access.json").read_text())
        self.assertEqual(access["allowFrom"], [OWNER])
        self.assertEqual(access["dmPolicy"], "allowlist")
        self.assertEqual(access["groups"], {})
        mcp = json.loads((d / ".mcp.json").read_text())["mcpServers"]
        self.assertEqual(set(mcp), {"discord"})
        self.assertEqual(mcp["discord"]["env"]["DISCORD_ACCESS_MODE"], "static")

    def test_assistant_prompt_is_not_topic_prompt(self):
        self.sb.topic("assistant", discord_env(ASSISTANT))
        prompt = self.sb.run("assistant", "--print-prompt").stdout
        self.assertIn("通用助理", prompt)
        self.assertNotIn("propose-memory-update", prompt)
        self.assertNotIn("post-commit hook 會自動 push", prompt)

    def test_owner_only_group_is_allowed(self):
        _, r = self.prepare(discord_env(ASSISTANT), {
            "dmPolicy": "allowlist", "allowFrom": [OWNER],
            "groups": {"555": {"requireMention": False, "allowFrom": [OWNER]}}})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_group_with_empty_allowfrom_refused(self):
        _, r = self.prepare(discord_env(ASSISTANT), {
            "dmPolicy": "allowlist", "allowFrom": [OWNER],
            "groups": {"555": {"requireMention": False, "allowFrom": []}}})
        self.assert_refused(r, "groups[555].allowFrom")

    def test_group_with_missing_allowfrom_refused(self):
        _, r = self.prepare(discord_env(ASSISTANT), {
            "dmPolicy": "allowlist", "allowFrom": [OWNER],
            "groups": {"555": {"requireMention": True}}})
        self.assert_refused(r, "groups[555].allowFrom")

    def test_group_with_other_user_refused(self):
        _, r = self.prepare(discord_env(ASSISTANT), {
            "dmPolicy": "allowlist", "allowFrom": [OWNER],
            "groups": {"555": {"allowFrom": [OWNER, OTHER]}}})
        self.assert_refused(r, "groups[555].allowFrom")

    def test_extra_dm_user_refused(self):
        _, r = self.prepare(discord_env(ASSISTANT),
                            {"dmPolicy": "allowlist", "allowFrom": [OTHER]})
        self.assert_refused(r, "allowFrom must be exactly")

    def test_pairing_refused(self):
        _, r = self.prepare(discord_env(ASSISTANT),
                            {"dmPolicy": "pairing", "allowFrom": [OWNER]})
        self.assert_refused(r, "dmPolicy")

    def test_corrupt_access_json_reseeds_to_owner_only(self):
        d = self.sb.topic("assistant", discord_env(ASSISTANT))
        state = d / ".discord-state"
        state.mkdir()
        (state / "access.json").write_text("{not json")
        r = self.sb.run("assistant", "--print-argv", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        access = json.loads((state / "access.json").read_text())
        self.assertEqual((access["dmPolicy"], access["allowFrom"]),
                         ("allowlist", [OWNER]))

    def test_slack_enabled_refused(self):
        slack = self.sb.home / "slack-adapter"
        slack.mkdir()
        self.sb.env["SLACK_PLUGIN_DIR"] = str(slack)
        _, r = self.prepare(discord_env(ASSISTANT + "SLACK_BOT_TOKEN=a\n"
                                        "SLACK_APP_TOKEN=b\nOWNER_SLACK_USER_ID=U1\n"))
        self.assert_refused(r, "Discord only")

    def test_line_enabled_refused(self):
        line = self.sb.home / "line-adapter"
        (line / "node_modules").mkdir(parents=True)
        self.sb.env["LINE_PLUGIN_DIR"] = str(line)
        _, r = self.prepare(discord_env(ASSISTANT + "LINE_CHANNEL_SECRET=a\n"
                                        "LINE_CHANNEL_ACCESS_TOKEN=b\nOWNER_LINE_USER_ID=U1\n"))
        self.assert_refused(r, "Discord only")

    def test_discord_missing_refused(self):
        line = self.sb.home / "line-adapter"
        (line / "node_modules").mkdir(parents=True)
        self.sb.env["LINE_PLUGIN_DIR"] = str(line)
        self.sb.topic("assistant", ASSISTANT + "LINE_CHANNEL_SECRET=a\n"
                      "LINE_CHANNEL_ACCESS_TOKEN=b\nOWNER_LINE_USER_ID=U1\n")
        r = self.sb.run("assistant", "--print-argv", check=False)
        self.assert_refused(r, "Discord only")

    def test_owner_unset_refused(self):
        self.sb.topic("assistant", ASSISTANT + f"DISCORD_BOT_TOKEN={FAKE_TOKEN}\n")
        r = self.sb.run("assistant", "--print-argv", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("OWNER_DISCORD_USER_ID", r.stderr)

    def test_unknown_kind_refused(self):
        self.sb.topic("assistant", discord_env("AGENTPORT_KIND=root\n"))
        r = self.sb.run("assistant", "--print-argv", check=False)
        self.assert_refused(r, "AGENTPORT_KIND")

    def test_kind_from_caller_env_ignored(self):
        self.sb.topic("demo", discord_env())
        self.sb.env["AGENTPORT_KIND"] = "assistant"
        argv = self.sb.run("demo", "--print-argv").stdout
        self.assertEqual(self.sb.norm(argv),
                         (GOLDEN / "plain-discord.argv").read_text(encoding="utf-8"))

    def test_add_dirs_in_argv(self):
        a, b = self.sb.home / "p1", self.sb.home / "p2"
        a.mkdir()
        b.mkdir()
        self.sb.topic("assistant", discord_env(ASSISTANT + f"ASSISTANT_ADD_DIRS={a}:{b}\n"))
        argv = self.sb.run("assistant", "--print-argv").stdout.splitlines()
        self.assertIn(f"--add-dir\n{a}\n--add-dir\n{b}", "\n".join(argv))

    def test_add_dir_missing_refused(self):
        _, r = self.prepare(discord_env(ASSISTANT + "ASSISTANT_ADD_DIRS=/nonexistent-dir\n"))
        self.assert_refused(r, "ASSISTANT_ADD_DIRS")

    def test_add_dirs_ignored_for_plain_topic(self):
        a = self.sb.home / "p1"
        a.mkdir()
        self.sb.topic("demo", discord_env(f"ASSISTANT_ADD_DIRS={a}\n"))
        argv = self.sb.run("demo", "--print-argv").stdout
        self.assertEqual(self.sb.norm(argv),
                         (GOLDEN / "plain-discord.argv").read_text(encoding="utf-8"))

    def test_print_argv_paths_stay_in_home(self):
        # A different OS user must get a launch that never points into
        # someone else's home: every absolute path is $HOME or the checkout.
        self.sb.topic("assistant", discord_env(ASSISTANT))
        argv = self.sb.run("assistant", "--print-argv").stdout.splitlines()
        for a in argv:
            if a.startswith("/"):
                self.assertTrue(a.startswith((str(self.sb.home), str(REPO))), a)

if __name__ == "__main__":
    unittest.main()
