# Running topic agents under herdr

[herdr](https://herdr.dev) is a terminal multiplexer that reports what the agent
in each pane is doing — `idle`, `working`, `blocked`, `done` — instead of
leaving the pane opaque the way tmux does.

This is optional. tmux, systemd or a bare shell all still work. But if you run
more than one topic agent, it changes a manual question into a queryable one.

## Why it matters here

With tmux, "which of my agents is waiting on me?" is answered by cycling
through panes and reading them. With several long-lived topic agents that is
the whole cost of running them.

herdr infers state from terminal output — the agent needs no cooperation, so
Claude Code and Codex work unmodified — and exposes it over a socket API:

```bash
herdr agent list                          # every agent and its state
herdr agent get <topic>                   # one agent, as JSON
herdr agent wait <topic> --until blocked  # returns when it genuinely blocks
herdr agent attach <topic>                # drop into its terminal
```

`agent wait` is the one that changes what you can build. A supervisor script can
wait for *blocked* rather than polling output and guessing whether silence means
thinking or stuck.

## Setup

```bash
curl -fsSL https://herdr.dev/install.sh | sh     # or see herdr.dev/docs/install
tools/run-topic-herdr.sh ~/workspace/topic-<name> --provider claude|codex
```

The launcher starts the herdr server if it is not running, creates a workspace
rooted at the topic directory, and starts the agent in it.

It is a front-end, not a second launcher: the claude argv comes from
`tools/run-topic.sh --print-argv`, so an agent started here is configured
exactly like one started by plain exec. See the README's "Launching a topic".

**It refuses to start a second agent on a topic that already has one.** Two
agents in one topic directory would race each other's git commits — approval
writes to `memory/` and commits, so a duplicate is not merely wasteful.

Under systemd:

```bash
systemctl --user link "$PWD/deploy/systemd/topic-agent-herdr@.service"
systemctl --user enable --now topic-agent-herdr@<topic>
```

`link` rather than `cp` so edits to the unit in the repo are what actually runs
— a copy silently goes stale. Stopping the unit closes the topic's whole
workspace, not just its pane; closing the pane alone leaves an empty workspace
behind and they accumulate one per restart.

## The startup confirmation

Topic agents subscribe to their channels with
`--dangerously-load-development-channels`, and Claude Code raises an interactive
confirmation for it at startup:

```
WARNING: Loading development channels
❯ 1. I am using this for local development
  2. Exit
```

Two things about answering it automatically, both learned by getting them wrong:

**Send exactly one `1`, regardless of how many channels are enabled.** The CLI
shows a single combined prompt (verified with a topic on Discord + LINE). A
second `1` lands in the chat input, gets submitted as a user message, and the
agent burns a turn asking what "1" means.

**Send it only once the prompt is actually on screen, and do it synchronously.**
`herdr agent start` returns as soon as the TUI is interactive — which it already
is while sitting at this prompt, so `agent start` reports success on an agent
that has connected to nothing. The launcher therefore waits for the prompt with
`herdr pane wait-output` *after* the agent exists, then answers it, then checks
that the agent left the `blocked` state. An earlier revision backgrounded that
waiter and armed an EXIT trap to reap it — which killed the waiter the instant
the script finished, leaving the agent parked at the prompt indefinitely with
every channel unconnected.

## Verifying the installer

herdr's install script fetches a manifest containing SHA-256 checksums, then
verifies the downloaded binary against it and aborts on mismatch. Worth knowing:
**the checksum and the script come from the same origin**, so this protects
against a tampered download, not against a compromised herdr.dev. If that
matters to you, take the checksum from the GitHub release instead:

```bash
curl -fsSL https://herdr.dev/latest.json | python3 -m json.tool | grep -A5 sha256
```

## Footprint

Measured on a J4125 (4-core, 8 GB): the idle server holds **~14 MB RSS at ~2%
CPU**, plus two helper processes of a few MB each. Light enough to leave running
on a small always-on box, which is the deployment this repo assumes.

## Known rough edge

A freshly created workspace returns a pane before the shell inside it is ready,
and `herdr agent start` against that pane fails with `agent_pane_busy`. The
launcher retries for 15 seconds and, if the shell never settles, closes the
workspace it created rather than leaving an orphan behind.

If you script against the API yourself, handle this — the first call after
`workspace create` will usually fail.
