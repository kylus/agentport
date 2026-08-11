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
believes. `propose-memory-update` produces a diff; `approve-proposal` is where a
human accepts or rejects it. Reads are unrestricted; writes are gated.

**Memory syncs on a timer.** Session transcripts are distilled and repo files
snapshotted into git-versioned memory, so "what the agent knows" has a history
you can read and revert.

## What you bring yourself

This repo is deliberately narrow. It assumes you already have:

- a topic directory containing a `bot.env` and whatever config your agent reads
- a launcher that starts the agent (systemd units for both providers are in
  `deploy/systemd/` as a starting point)
- your own ingest, if you want the agent to read from external sources

There is no scaffolding command and no ingest layer here. Those were part of a
different codebase and are not included.

## Layout

```
tools/      provider runtime (Codex), provider switch, Discord provisioning,
            plugin update lifecycle
channels/   LINE channel as a stdio MCP server
core/sync/  session distillation + repo file snapshot into memory
skills/     propose / approve / read memory
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
