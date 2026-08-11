#!/usr/bin/env bash
# Reversibly switch one topic between Claude and Codex providers.
#
# Usage:
#   tools/switch-topic-provider.sh <topic> status
#   tools/switch-topic-provider.sh <topic> claude
#   tools/switch-topic-provider.sh <topic> codex [--allow-channel-downtime]
set -euo pipefail

TOPIC_NAME="${1:?usage: switch-topic-provider.sh <topic> <status|claude|codex> [--allow-channel-downtime]}"
TARGET="${2:?usage: switch-topic-provider.sh <topic> <status|claude|codex> [--allow-channel-downtime]}"
ALLOW_CHANNEL_DOWNTIME="${3:-}"
TOPIC_DIR="$HOME/workspace/topic-${TOPIC_NAME}"
SEED_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLAUDE_UNIT="topic-agent@${TOPIC_NAME}.service"
CODEX_UNIT="topic-agent-codex@${TOPIC_NAME}.service"
STATE_BASE="${XDG_STATE_HOME:-$HOME/.local/state}/agentport/${TOPIC_NAME}"
PROVIDER_STATE="$STATE_BASE/provider.json"
RUNTIME_BASE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
LOCK_FILE="$RUNTIME_BASE/agentport-provider-${TOPIC_NAME}.lock"

[ -d "$TOPIC_DIR" ] || { echo "error: $TOPIC_DIR not found" >&2; exit 2; }
mkdir -p "$STATE_BASE"
chmod 700 "$STATE_BASE"

unit_active() {
  systemctl --user is-active --quiet "$1"
}

active_provider() {
  local claude=0 codex=0
  unit_active "$CLAUDE_UNIT" && claude=1
  unit_active "$CODEX_UNIT" && codex=1
  if [ "$claude" = 1 ] && [ "$codex" = 1 ]; then
    echo "conflict"
  elif [ "$codex" = 1 ]; then
    echo "codex"
  elif [ "$claude" = 1 ]; then
    echo "claude"
  else
    echo "none"
  fi
}

write_state() {
  local provider="$1" previous="$2"
  python3 - "$PROVIDER_STATE" "$TOPIC_NAME" "$provider" "$previous" <<'PY'
import json, os, pathlib, sys
from datetime import datetime, timezone
path = pathlib.Path(sys.argv[1])
data = {
    "version": 1,
    "topic": sys.argv[2],
    "activeProvider": sys.argv[3],
    "previousProvider": sys.argv[4],
    "updatedAt": datetime.now(timezone.utc).isoformat(),
}
temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
temp.write_text(json.dumps(data, indent=2) + "\n")
temp.chmod(0o600)
temp.replace(path)
PY
}

wait_active() {
  local unit="$1" attempts="${2:-20}"
  local i
  for ((i=0; i<attempts; i++)); do
    unit_active "$unit" && return 0
    sleep 1
  done
  return 1
}

wait_codex_health() {
  local i
  for ((i=0; i<30; i++)); do
    "$SEED_DIR/tools/run-codex-topic.py" health "$TOPIC_NAME" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

restore_provider() {
  local provider="$1"
  systemctl --user disable --now "$CODEX_UNIT" >/dev/null 2>&1 || true
  systemctl --user disable --now "$CLAUDE_UNIT" >/dev/null 2>&1 || true
  case "$provider" in
    claude)
      systemctl --user enable --now "$CLAUDE_UNIT" >/dev/null
      wait_active "$CLAUDE_UNIT" || true
      ;;
    codex)
      "$SEED_DIR/tools/run-codex-topic.py" arm "$TOPIC_NAME" >/dev/null 2>&1 || true
      systemctl --user enable --now "$CODEX_UNIT" >/dev/null || true
      wait_codex_health || true
      ;;
    none) ;;
  esac
}

has_non_discord_channel() {
  python3 - "$TOPIC_DIR/bot.env" <<'PY'
import pathlib, sys
values = {}
for raw in pathlib.Path(sys.argv[1]).read_text().splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip().strip("\"'")
keys = ("SLACK_BOT_TOKEN", "LINE_CHANNEL_ACCESS_TOKEN")
print("yes" if any(values.get(key) and "REPLACE_ME" not in values[key] for key in keys) else "no")
PY
}

show_status() {
  local active
  active="$(active_provider)"
  echo "topic=$TOPIC_NAME active=$active"
  systemctl --user is-enabled "$CLAUDE_UNIT" 2>/dev/null | sed 's/^/claude-enabled=/' || true
  systemctl --user is-enabled "$CODEX_UNIT" 2>/dev/null | sed 's/^/codex-enabled=/' || true
  if [ -f "$PROVIDER_STATE" ]; then
    echo "state=$PROVIDER_STATE"
    python3 -m json.tool "$PROVIDER_STATE"
  fi
  if [ "$active" = "codex" ]; then
    "$SEED_DIR/tools/run-codex-topic.py" health "$TOPIC_NAME" || true
  fi
}

if [ "$TARGET" = "status" ]; then
  show_status
  exit 0
fi

case "$TARGET" in
  claude|codex) ;;
  *) echo "error: provider must be status, claude, or codex" >&2; exit 2 ;;
esac

exec 9>"$LOCK_FILE"
flock -n 9 || { echo "error: another provider switch is in progress" >&2; exit 3; }

PREVIOUS="$(active_provider)"
if [ "$PREVIOUS" = "conflict" ]; then
  echo "error: Claude and Codex units are both active; resolve conflict first" >&2
  exit 4
fi
if [ "$PREVIOUS" = "$TARGET" ]; then
  echo "already active: $TARGET"
  show_status
  exit 0
fi

if [ "$TARGET" = "codex" ]; then
  if [ "$(has_non_discord_channel)" = "yes" ] && [ "$ALLOW_CHANNEL_DOWNTIME" != "--allow-channel-downtime" ]; then
    cat >&2 <<EOF
error: $TOPIC_NAME has Slack or LINE enabled, but the Codex provider currently
       supports Discord only. Re-run with --allow-channel-downtime to accept
       that those channels are offline while Codex owns the topic.
EOF
    exit 5
  fi
  "$SEED_DIR/tools/run-codex-topic.py" check "$TOPIC_NAME" >/dev/null
  if [ "$PREVIOUS" = "claude" ]; then
    systemctl --user disable --now "$CLAUDE_UNIT"
    if unit_active "$CLAUDE_UNIT"; then
      echo "error: Claude did not stop; refusing to start Codex" >&2
      restore_provider "$PREVIOUS"
      exit 6
    fi
  else
    systemctl --user disable --now "$CLAUDE_UNIT" >/dev/null 2>&1 || true
  fi
  if ! "$SEED_DIR/tools/run-codex-topic.py" arm "$TOPIC_NAME"; then
    echo "error: failed to arm Codex cursors; rolling back to $PREVIOUS" >&2
    restore_provider "$PREVIOUS"
    write_state "$PREVIOUS" codex
    exit 6
  fi
  if systemctl --user enable --now "$CODEX_UNIT" && wait_codex_health; then
    write_state codex "$PREVIOUS"
    echo "switched $TOPIC_NAME: $PREVIOUS -> codex"
    show_status
    exit 0
  fi

  echo "error: Codex failed health check; rolling back to $PREVIOUS" >&2
  restore_provider "$PREVIOUS"
  write_state "$PREVIOUS" codex
  exit 6
fi

if [ "$PREVIOUS" = "codex" ]; then
  systemctl --user disable --now "$CODEX_UNIT"
  if unit_active "$CODEX_UNIT"; then
    echo "error: Codex did not stop; refusing to start Claude" >&2
    restore_provider "$PREVIOUS"
    exit 7
  fi
else
  systemctl --user disable --now "$CODEX_UNIT" >/dev/null 2>&1 || true
fi
if systemctl --user enable --now "$CLAUDE_UNIT" && wait_active "$CLAUDE_UNIT"; then
  write_state claude "$PREVIOUS"
  echo "switched $TOPIC_NAME: $PREVIOUS -> claude"
  show_status
  exit 0
fi

echo "error: Claude failed to start; rolling back to $PREVIOUS" >&2
restore_provider "$PREVIOUS"
write_state "$PREVIOUS" claude
exit 7
