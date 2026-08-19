#!/usr/bin/env python3
"""memory_clean.py — the Stop hook that keeps memory/ from drifting from git.

A topic agent is supposed to commit each memory edit as it makes it. When it
does not, the working tree and the repo disagree, and the next `--continue`
resume reads a memory nobody reviewed and nobody can cite. This hook refuses to
let a turn end while memory/ is dirty, which turns "remember to commit" from a
habit into a property.

    owner        → dirty memory/ blocks the turn until it is committed
    contributor  → always allowed to stop

Contributors are exempt because the PreToolUse gate already denies them every
write to memory/. Enforcing it for them would mean blocking a turn over changes
they are structurally unable to make, and the turn could never end.

Role resolution is shared with role_gate.py on purpose. Two hooks that disagree
about who the owner is would be worse than either of them alone.

Install: wire as a `Stop` hook in the topic's .claude/settings.json — see
hooks/README.md. Both hooks read AGENTPORT_ROLE / AGENTPORT_ROLE_FILE.
"""
import contextlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from role_gate import resolve_role


def git_dirty(topic_dir: str) -> list[str]:
    """Which kinds of uncommitted change memory/ has, if any.

    Three separate questions, because they fail differently: an unstaged edit is
    usually the agent forgetting, a staged one is a half-finished commit, and an
    untracked file is a whole section that has never been in the repo at all.
    """
    kinds = []

    def git(*args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=topic_dir,
                              capture_output=True, text=True, check=False)

    if git("diff", "--quiet", "--", "memory/").returncode == 1:
        kinds.append("unstaged")
    if git("diff", "--cached", "--quiet", "--", "memory/").returncode == 1:
        kinds.append("staged")
    untracked = git("ls-files", "--others", "--exclude-standard", "memory/")
    if untracked.returncode == 0 and untracked.stdout.strip():
        kinds.append("untracked")
    return kinds


def main() -> int:
    # The payload is read and discarded: Stop hooks get session metadata, and
    # nothing in it should change whether uncommitted memory is acceptable.
    with contextlib.suppress(OSError):
        sys.stdin.read()

    if resolve_role() != "owner":
        return 0

    topic_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if not os.path.isdir(os.path.join(topic_dir, "memory")):
        # Not a topic directory — nothing this hook has an opinion about.
        return 0

    kinds = git_dirty(topic_dir)
    if not kinds:
        # Clean. Emit nothing: no decision means the turn ends normally.
        return 0

    json.dump({
        "decision": "block",
        "reason": (f"memory/ has {', '.join(kinds)} changes that are not "
                   "committed. Commit them before ending the turn — the "
                   "post-commit hook pushes automatically."),
    }, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
