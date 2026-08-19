# agentport — notes for anyone (or anything) changing this repo

Plumbing between a long-lived agent and the things it talks to: the provider
underneath, the chat channel in front, and the approval gate on its memory.
Read `README.md` first for what it does; this file is about how to change it
without breaking the parts that are load-bearing.

## Checks

```bash
uvx ruff@0.16.2 check .              # the version CI pins
python3 -m unittest discover -s tests
python3 hooks/role_gate.py --self-test
```

CI runs the first two. The third answers a different question — whether the
gate is wired into a real topic and actually firing — so run it from inside a
topic directory, not from here.

## Invariants

**One prepare stage.** `tools/run-topic.sh` decides which channels are on,
materializes their tokens, regenerates `.mcp.json` and produces the claude
argv. Front-ends ask it (`--print-argv`); they never assemble a command line
themselves. Every time that seam has been bypassed the copies drifted, and the
drift was silent — an agent with no channels attached looks perfectly healthy
and simply never receives anything.

**Every deny is a claim, and every claim has a test.** `hooks/role_gate.py` is
the only thing making approval a control rather than a convention. A rule with
no test disappears in the next refactor while CI stays green. When you add an
allowance, add its negative case too: the test that proves the neighbouring
shape is still refused is the one that matters.

**The gate fails closed.** An unset, empty or unreadable role is a
contributor. Anything that resolves a role from something the model can type
is a bug, not a convenience — `AGENTPORT_ROLE=owner python3 …` sets that
variable for the command, not for the hook.

**Secrets live in `bot.env` and nowhere else.** Adapter `.env` files and
`access.json` are regenerated from it on every launch, so flipping a channel
on or off is a one-file edit. Never put a token on a command line; `ps` is
world-readable.

## Where things surprise people

- Skills need a `SKILL.md`. A directory of scripts without one is invisible to
  Claude Code — it looks installed and never loads.
- `.git/hooks/post-commit` is not carried by a clone. Re-run
  `tools/create-topic.sh` against an existing topic to reinstall it; the script
  is idempotent and leaves everything else alone.
- The session-resume path depends on Claude Code storing sessions at
  `~/.claude/projects/<cwd with / replaced by ->/<uuid>.jsonl`. If that layout
  changes, `--continue` silently stops finding prior sessions and every topic
  starts fresh on restart. Re-test when claude major-versions.
