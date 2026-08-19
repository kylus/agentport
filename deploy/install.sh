#!/usr/bin/env bash
# Install agentport's systemd user units into ~/.config/systemd/user/
# (via symlink, so a `git pull` rolls out template changes immediately).
#
# Usage:
#   bash deploy/install.sh
#
# Then enable a specific topic:
#   systemctl --user enable --now topic-agent-herdr@<topic-name>.service
#
# That unit runs the agent inside herdr, which makes its state queryable
# (`herdr agent list`). topic-agent@ is the tmux alternative for hosts that
# would rather not run herdr — same launcher underneath, opaque panes.
#
# To survive logout / reboot without manual login, run once:
#   sudo loginctl enable-linger "$USER"
set -euo pipefail

SEED_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$HOME/.config/systemd/user"

# `systemctl --user` talks to a per-user dbus socket at $XDG_RUNTIME_DIR/bus.
# pam_systemd sets XDG_RUNTIME_DIR on real SSH/desktop logins, but `sudo su -`,
# `machinectl shell`, and many CI-style shells strip it. The user manager
# itself is usually still alive (logind starts it on first login), so we just
# need to point this shell at it.
if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
  candidate="/run/user/$(id -u)"
  if [ -S "$candidate/bus" ]; then
    export XDG_RUNTIME_DIR="$candidate"
    export DBUS_SESSION_BUS_ADDRESS="unix:path=$candidate/bus"
    cat <<NOTE
note    XDG_RUNTIME_DIR was unset; auto-pointed to $candidate.
        Persist by adding to ~/.bashrc:
          export XDG_RUNTIME_DIR=/run/user/\$(id -u)
          export DBUS_SESSION_BUS_ADDRESS=unix:path=\$XDG_RUNTIME_DIR/bus
NOTE
  else
    cat >&2 <<ERR
error   No XDG_RUNTIME_DIR and no dbus socket at $candidate/bus.
        Your user systemd manager is not running for this UID.
        Options:
          1. Log in fresh: ssh $USER@localhost (pam_systemd will start it)
          2. Have an admin: sudo loginctl enable-linger "$USER"
             (persistent — user manager survives logout / reboot)
ERR
    exit 1
  fi
fi

# Channel plugin presence check. Channels are opt-in per topic — a host
# only needs the plugin(s) its topics actually enable, so a missing plugin
# is informational, not fatal. agentport deliberately doesn't vendor either
# plugin (both are upstream forks patched with the owner-role hook).
#
# Auto-clone is available for the Slack plugin when SLACK_PLUGIN_REPO is
# set to your fork (owner/repo). Discord's plugin lives inside the
# claude-plugins-official monorepo — clone that manually (see docs/setup.md).
SLACK_PLUGIN_DIR="${SLACK_PLUGIN_DIR:-$HOME/workspace/claude-code-slack-channel}"
SLACK_PLUGIN_REPO="${SLACK_PLUGIN_REPO:-}"
SLACK_PLUGIN_BRANCH="${SLACK_PLUGIN_BRANCH:-feat/owner-role-hook}"
DISCORD_PLUGIN_DIR="${DISCORD_PLUGIN_DIR:-$HOME/workspace/claude-plugins-official/external_plugins/discord}"

if [ -d "$SLACK_PLUGIN_DIR/.git" ]; then
  current_branch="$(git -C "$SLACK_PLUGIN_DIR" branch --show-current 2>/dev/null || echo)"
  if [ "$current_branch" = "$SLACK_PLUGIN_BRANCH" ]; then
    echo "ok      Slack plugin at $SLACK_PLUGIN_DIR (branch $current_branch)"
  else
    echo "warn    Slack plugin on branch '$current_branch', not '$SLACK_PLUGIN_BRANCH'" >&2
  fi
elif [ -n "$SLACK_PLUGIN_REPO" ]; then
  command -v gh >/dev/null 2>&1 || { echo "error: gh CLI needed to clone the Slack plugin" >&2; exit 1; }
  gh auth status >/dev/null 2>&1 || { echo "error: gh not authenticated — run: gh auth login" >&2; exit 1; }
  command -v bun >/dev/null 2>&1 || { echo "error: bun not found — install: curl -fsSL https://bun.sh/install | bash" >&2; exit 1; }
  echo "clone   $SLACK_PLUGIN_REPO → $SLACK_PLUGIN_DIR"
  mkdir -p "$(dirname "$SLACK_PLUGIN_DIR")"
  gh repo clone "$SLACK_PLUGIN_REPO" "$SLACK_PLUGIN_DIR"
  (cd "$SLACK_PLUGIN_DIR" && git checkout "$SLACK_PLUGIN_BRANCH" && bun install --frozen-lockfile)
  echo "ok      Slack plugin installed at $SLACK_PLUGIN_DIR"
else
  echo "info    Slack plugin not present at $SLACK_PLUGIN_DIR — fine for Discord-only hosts."
  echo "        To auto-install: SLACK_PLUGIN_REPO=<your-fork>/claude-code-slack-channel bash deploy/install.sh"
fi

if [ -f "$DISCORD_PLUGIN_DIR/server.ts" ]; then
  echo "ok      Discord plugin at $DISCORD_PLUGIN_DIR"
else
  echo "info    Discord plugin not present at $DISCORD_PLUGIN_DIR — fine for Slack-only hosts."
  echo "        Install: gh repo clone <your-fork>/claude-plugins-official ~/workspace/claude-plugins-official"
  echo "                 (branch feat/owner-role-hook; then bun install in external_plugins/discord)"
fi

mkdir -p "$TARGET"

for unit in "$SEED_DIR/deploy/systemd"/*.service "$SEED_DIR/deploy/systemd"/*.timer; do
  name="$(basename "$unit")"
  link="$TARGET/$name"
  if [ -L "$link" ] && [ "$(readlink -f "$link")" = "$(readlink -f "$unit")" ]; then
    echo "ok      $name (already linked)"
  else
    ln -sfn "$unit" "$link"
    echo "linked  $name"
  fi
done

systemctl --user daemon-reload
echo
echo "Done. Next steps:"
echo "  systemctl --user enable --now topic-agent-herdr@<topic-name>.service"
echo "  systemctl --user status   topic-agent-herdr@<topic-name>.service"
echo "  (tmux instead of herdr: topic-agent@<topic-name>.service)"
echo "  tools/attach-topic.sh <topic-name>   # live view of the claude TUI"
echo
echo "Codex provider (installed but not enabled automatically):"
echo "  tools/run-codex-topic.py check <topic-name>"
echo "  tools/run-codex-topic.py self-test <topic-name>"
echo "  tools/switch-topic-provider.sh <topic-name> status"
echo "  tools/switch-topic-provider.sh <topic-name> codex"
echo "  tools/switch-topic-provider.sh <topic-name> claude"
echo
echo "Survive reboot/logout (one-time): sudo loginctl enable-linger \"$USER\""
