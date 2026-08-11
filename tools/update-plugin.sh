#!/usr/bin/env bash
# Update the claude-code-slack-channel plugin and restart all running topic agents.
#
# Usage:
#   tools/update-plugin.sh [<topic-name> ...]
#
# With no arguments: restarts ALL enabled topic-agent@* services.
# With topic names:  restarts only the specified topics.
set -euo pipefail

SLACK_PLUGIN_DIR="${SLACK_PLUGIN_DIR:-$HOME/workspace/claude-code-slack-channel}"

[ -d "$SLACK_PLUGIN_DIR" ] || { echo "error: plugin dir not found at $SLACK_PLUGIN_DIR" >&2; exit 1; }

# ── 1. Pull latest plugin ────────────────────────────────────────────────────
cd "$SLACK_PLUGIN_DIR"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "== pulling $BRANCH =="
git pull --rebase origin "$BRANCH"

# ── 2. Reinstall dependencies ────────────────────────────────────────────────
echo "== bun install =="
bun install 2>&1 | tail -3

# ── 3. Restart topic agents ──────────────────────────────────────────────────
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

if [ $# -gt 0 ]; then
  TOPICS=("$@")
else
  # Auto-detect all enabled topic-agent@* services
  TOPICS=()
  while IFS= read -r svc; do
    topic="${svc#topic-agent@}"
    topic="${topic%.service}"
    TOPICS+=("$topic")
  done < <(systemctl --user list-units 'topic-agent@*.service' --no-legend --plain \
           | awk '{print $1}')
fi

if [ ${#TOPICS[@]} -eq 0 ]; then
  echo "no running topic agents found"
  exit 0
fi

echo "== restarting topics: ${TOPICS[*]} =="
for TOPIC in "${TOPICS[@]}"; do
  echo "  restarting topic-agent@${TOPIC}.service ..."
  systemctl --user restart "topic-agent@${TOPIC}.service"
  # Auto-confirm --dangerously-load-development-channels prompt
  sleep 6
  tmux -L "topic-${TOPIC}" send-keys -t "topic-${TOPIC}" "1" Enter 2>/dev/null || true
  sleep 3
  # Verify bun came up
  if ps aux | grep -q "bun run --cwd.*claude-code-slack-channel" 2>/dev/null; then
    echo "  ✓ topic-${TOPIC}: plugin running"
  else
    echo "  ✗ topic-${TOPIC}: plugin not detected — check TUI with tools/your attach command ${TOPIC}"
  fi
done

echo "== done =="
