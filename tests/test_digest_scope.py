"""Which session files a topic is allowed to read.

Keywords match text, and the same hostnames appear in work that has nothing to
do with the topic. Everything extracted is read by an agent, so a bad filter
costs tokens on the way in and dilutes memory on the way out.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "core" / "sync"))

from session_digest import project_filter


class TestProjectFilter(unittest.TestCase):
    TOPIC = pathlib.Path("/home/u/workspace/topic-netops")

    def allowed(self, config=None):
        return project_filter(self.TOPIC, config or {})

    def test_unrelated_projects_pass_when_nothing_is_configured(self):
        # No config must not mean "read nothing" — a topic that never
        # configured scope should still get its digests.
        self.assertTrue(self.allowed()("-home-u-projects-kylus-site"))

    def test_the_topic_never_reads_its_own_sessions(self):
        # Reading its own output back turns the agent's own speculation into a
        # cited source, and does it again every night.
        own = "-home-u-workspace-topic-netops"
        self.assertFalse(self.allowed()(own))
        # Not even by asking for it.
        self.assertFalse(self.allowed({"include_projects": ["topic-netops"]})(own))

    def test_subagent_transcripts_are_never_read(self):
        self.assertFalse(self.allowed()("subagents"))
        self.assertFalse(self.allowed({"include_projects": ["subagents"]})("subagents"))

    def test_exclusions_are_substring_matches(self):
        allowed = self.allowed({"exclude_projects": ["idle-game", "kylus-site"]})
        self.assertFalse(allowed("-home-u-projects-idle-game"))
        self.assertFalse(allowed("-home-u-projects-kylus-site"))
        self.assertTrue(allowed("-home-u-projects-network-mgmt"))

    def test_an_allowlist_excludes_everything_else(self):
        allowed = self.allowed({"include_projects": ["network-mgmt"]})
        self.assertTrue(allowed("-home-u-projects-network-mgmt"))
        self.assertFalse(allowed("-home-u-projects-Local-ma"))

    def test_exclusion_wins_over_inclusion(self):
        allowed = self.allowed({"include_projects": ["network"],
                                "exclude_projects": ["network-mgmt-old"]})
        self.assertFalse(allowed("-home-u-projects-network-mgmt-old"))
        self.assertTrue(allowed("-home-u-projects-network-mgmt"))

    def test_empty_strings_in_config_do_not_match_everything(self):
        # "" is a substring of every name; a stray empty entry would otherwise
        # silently exclude the whole world.
        allowed = self.allowed({"exclude_projects": ["", "idle-game"]})
        self.assertTrue(allowed("-home-u-projects-network-mgmt"))
        self.assertFalse(allowed("-home-u-projects-idle-game"))


if __name__ == "__main__":
    unittest.main()
