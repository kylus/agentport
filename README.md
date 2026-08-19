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
tools/       the launcher (run-topic.sh) and its front-ends, topic scaffolding
             and bootstrap, provider runtime (Codex), provider switch, channel
             provisioning, plugin update lifecycle
templates/   what a new topic is made of: persona, memory sections, hook
             wiring, post-commit auto-push
channels/    LINE channel as a stdio MCP server
core/ingest/ pull one GitHub / GitLab / Drive / Slack item into sources/
core/sync/   session distillation + repo file snapshot into memory
skills/      read / propose / approve memory, ingest, meeting notes, support
             thread triage
hooks/       the PreToolUse role gate that makes approval enforceable, and the
             Stop hook that keeps memory/ committed
tests/       every deny rule, run as a subprocess the way Claude Code runs it
deploy/      systemd units (herdr, tmux, Codex, memory sync) and the installer
docs/        the product brief, setup, approval model, herdr, Codex app-server
             protocol notes, plugin update SOP
```

## Launching a topic

`tools/run-topic.sh <topic>` is the single source of truth for how a topic agent
starts. It runs in two stages: **prepare** — work out which channels are enabled
from `bot.env`, materialize their tokens and access lists, regenerate
`.mcp.json` to match, relink skills — and then **launch**.

Every front-end shares the prepare stage:

```bash
tools/run-topic.sh <topic>                  # prepare, then exec claude
tools/run-topic.sh <topic> --print-argv     # prepare, print the argv instead
tools/run-topic-herdr.sh ~/workspace/topic-<topic>   # the same argv, inside herdr
```

That seam is load-bearing. A front-end that assembles its own command line
drifts from this one, and the drift is silent: the herdr launcher's first
revision started `claude` with no arguments at all, producing an agent that
looked perfectly healthy and could not receive a single message. Front-ends ask
`--print-argv` what to run; they do not decide it.

## Status

Extracted from a private codebase and published as the author's own work. The
first extraction took the architecture and left the operational half behind —
skills shipped without their SKILL.md and so never loaded, the launcher never
came across at all — so a topic could be described by this repo but not run by
it. That gap is closed: scaffolding, launching, supervising and gating a topic
all live here now, and a live topic agent runs on it.

Still a sample size of one. The provider-swap, LINE and Discord paths have run
in a personal deployment; nothing here has been exercised by anyone else.

**What you need that is not in this repo.** The chat adapters are external, and
both need a patch that upstream does not carry: before handing a message to the
agent, the adapter writes whether the sender is owner or contributor into
`<topic>/.current-role`. Without it the role gate has no role to read and fails
closed on everyone, including you.

- **Discord** — [`kylus/claude-plugins-official`](https://github.com/kylus/claude-plugins-official),
  branch `feat/owner-role-hook`, a fork of Anthropic's. Public; clone it and go.
- **Slack** — no public fork exists. Fork
  [`jeremylongshore/claude-code-slack-channel`](https://github.com/jeremylongshore/claude-code-slack-channel)
  and apply the same patch yourself.
- **LINE** — ships here, in `channels/line/`. Nothing to clone.

Channels are opt-in per topic, so Discord-only or LINE-only is a complete
setup — start there rather than with Slack.

## Licence

MIT — see [LICENSE](LICENSE).
