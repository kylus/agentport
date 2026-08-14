# agentport

Make a long-lived agent portable: swap the model provider underneath it, swap
the chat channel in front of it, and require human approval before anything is
written to its memory.

**This is not an agent framework.** It is the plumbing that sits between one
you already have and the things it talks to.

## What it does

**Provider is swappable.** The same topic agent runs on Claude Code or on
Codex. `tools/switch-topic-provider.sh <topic> claude|codex` flips it and can
flip it back — state and memory are untouched by the swap.

**Channel is swappable.** Discord and LINE adapters expose the same contract
(a stdio MCP server that delivers inbound chat as `notifications/claude/channel`
and exposes a send tool), so the agent does not know or care which one it is
attached to.

**Memory writes go through approval.** An agent cannot silently rewrite what it
believes. `propose-memory-update` creates a proposal; `approve-proposal` is
where a human accepts or rejects it, and only then does anything reach memory.
Reads are unrestricted; writes are gated. A sha handshake between review and
approval means "approve what I just read" is not the same command as "approve
whatever is in that file now" — see **[docs/approval-model.md](docs/approval-model.md)**
for the design, the four defences, and the one piece you must supply yourself.

**Memory syncs on a timer.** Session transcripts are distilled and repo files
snapshotted into git-versioned memory, so "what the agent knows" has a history
you can read and revert.

## Getting a topic running

```bash
tools/create-topic.sh <name>              # scaffolds ~/workspace/topic-<name>
tools/provision-discord-app.sh <dir>      # walks the Discord app creation flow
# fill in bot.env, then start it with your launcher
```

`create-topic.sh` is idempotent: it creates the memory sections, `pending/`,
`sources/`, the skill symlinks and a git repo (approval commits into it), and
will not overwrite an existing `bot.env`.

## What you bring yourself

This repo is deliberately narrow. It does not include:

- **a launcher.** systemd units for both providers are in `deploy/systemd/` as
  a starting point, but wiring them up is yours. If you run more than one topic
  agent, `tools/run-topic-herdr.sh` runs them under
  [herdr](https://herdr.dev) so their state (`idle` / `working` / `blocked` /
  `done`) is queryable instead of needing you to read panes — see
  [docs/herdr.md](docs/herdr.md). Optional; tmux still works.
- **an ingest layer.** `core/sync/repo_file_sync.py` pulls files from git
  repos; anything else (issue trackers, docs, wikis) you write yourself.
- **the wiring for the role hook.** `hooks/role_gate.py` is the hook that makes
  approval a control rather than a convention — `proposal.py` deliberately does
  not check roles and assumes something already blocked non-owners. The hook
  ships; installing it and deciding where the role comes from is yours, because
  that depends on how you launch agents. Until it is firing, anyone with a
  shell can approve. See [hooks/README.md](hooks/README.md).

## Layout

```
tools/      provider runtime (Codex), provider switch, Discord provisioning,
            plugin update lifecycle
channels/   LINE channel as a stdio MCP server
core/sync/  session distillation + repo file snapshot into memory
skills/     propose / approve / read memory
hooks/      the PreToolUse role gate that makes approval enforceable
tests/      the gate's deny rules, run as a subprocess the way Claude Code
            runs them
deploy/     systemd units for both providers and the memory sync timer
docs/       Codex app-server protocol notes, plugin update SOP
```

## Status

Extracted from a private codebase and published as the author's own work. The
provider-swap and LINE channel paths have run in a personal deployment; nothing
here has been exercised by anyone else yet. Treat it as working code with a
sample size of one.

## Licence

MIT — see [LICENSE](LICENSE).
