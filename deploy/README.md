# deploy/

Version-controlled service definitions for topic agents.

Why these exist: the Slack MCP plugin inherits the lifetime of the
`claude` process that spawned it (stdin EOF → shutdown — by MCP spec,
not a bug). When the session ends, Slack Socket Mode disconnects and
the bot looks dead even though tmux is still attached. The systemd
unit here respawns claude after crashes so the bot self-heals.

Background on the runtime model (Slack ↔ MCP plugin ↔ claude TUI ↔
memory repo) and troubleshooting recipes live in the main
[README — Runtime architecture](../README.md#runtime-architecture) and
[FAQ](../README.md#faq). This file only covers the deploy mechanics.

## Layout

    deploy/
      systemd/
        topic-agent@.service   # templated; %i = topic name
      install.sh                    # symlinks units → ~/.config/systemd/user/
      README.md

## Install (one-time per machine)

```sh
bash deploy/install.sh
```

Symlinks every unit in `deploy/systemd/` into `~/.config/systemd/user/`
and runs `daemon-reload`. Re-run after `git pull` if new units land.
The script auto-detects + sets `XDG_RUNTIME_DIR` for `sudo su -` style
shells (see FAQ #6 in the main README for the why).

To start at boot without a logged-in shell:

```sh
sudo loginctl enable-linger "$USER"   # needs admin once
```

Without `enable-linger`, the user manager exits on full logout and the
bot stops with it (see FAQ #7 if your bot account has no sudo).

## Enable / inspect / stop a topic

```sh
systemctl --user enable --now topic-agent@<name>.service
systemctl --user status   topic-agent@<name>.service
journalctl --user -u topic-agent@<name>.service -f
../tools/attach-topic.sh <name>           # live view of the claude TUI
systemctl --user restart  topic-agent@<name>.service
systemctl --user disable --now topic-agent@<name>.service
```

Detach with `Ctrl-b d`. **Don't `Ctrl-c`** — the supervisor traps INT/HUP
so it survives, but SIGINT still reaches claude inside and interrupts
whatever it's mid-operation. Killing the tmux session externally does
not stop the service (`Type=oneshot` + `RemainAfterExit`); use
`systemctl --user stop`.

## Why tmux is still in the loop

`tools/run-topic.sh` launches `claude` with
`--dangerously-load-development-channels server:slack`, which prompts
for interactive confirmation. The script auto-answers via
`tmux send-keys`. A pure-systemd `Type=simple` service would hang there
because the unit has no PTY.

The unit's ExecStart is:

```
tmux -L topic-%i new-session -d -s topic-%i \
  'cd ~/projects/agentport && trap "" INT HUP && while true; do tools/run-topic.sh %i; sleep 10; done'
```

systemd creates a per-topic tmux server (`-L topic-%i`, dedicated socket
under `/tmp/tmux-<uid>/topic-<name>`); the inner `while` loop respawns
claude on crash; `trap "" INT HUP` keeps the supervisor alive when
operators accidentally Ctrl-C inside an attached pane. Operators use
`tools/attach-topic.sh <name>` which knows the `-L` convention.

Per-topic tmux servers are how we keep topics' lifecycles independent —
restarting one topic doesn't touch the others' cgroups or sockets. Earlier
versions used the default tmux server; that turned out to cross-contaminate
process tracking between topics.

A future cleanup would teach `run-topic.sh` a headless mode (feed the
confirmation answer over a PTY allocated by `script(1)` or `expect`)
so the unit can drop tmux. Not blocking.

## PATH gotcha

systemd user services don't inherit `~/.bashrc` PATH. The unit pins
PATH explicitly so `bun` (Slack plugin dep) and anything else under
`$HOME` is reachable. If you add a new under-`$HOME` tool (cargo, nvm,
pyenv), extend the `Environment=PATH=` line in the unit.
