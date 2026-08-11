#!/usr/bin/env bash
# Sync a topic's memory by extracting relevant content from recent Claude Code
# sessions, writing a digest to sources/sessions/, and notifying the live topic
# agent via tmux so it ingests the digest and updates memory automatically.
#
# Called by the agentport-memory-sync@<topic>.timer systemd unit daily at 02:00.
#
# Usage:
#   tools/sync-session-memory.sh <topic-name>
#
# Requirements:
#   - topic-<name>/bot.env  (SLACK_BOT_TOKEN, SLACK_BOT_USER_ID, OWNER_SLACK_USER_ID)
#   - ~/.slack/credentials.json  (user OAuth token for sending the DM as owner)
#   - python3 in PATH
set -euo pipefail

TOPIC_NAME="${1:?usage: sync-session-memory.sh <topic-name>}"
TOPIC_DIR="$HOME/workspace/topic-${TOPIC_NAME}"
SEED_DIR="$(cd "$(dirname "$0")/.." && pwd)"

[ -d "$TOPIC_DIR" ] || { echo "error: $TOPIC_DIR not found" >&2; exit 1; }

# Load bot tokens
set -a
source "$TOPIC_DIR/bot.env"
set +a

echo "== checking plugin updates for ${TOPIC_NAME} =="
"$SEED_DIR/tools/check-plugin-updates.sh" "$TOPIC_NAME" 2>&1 || \
  echo "warn: plugin update check failed (non-fatal)"

echo "== syncing configured repos for ${TOPIC_NAME} =="
python3 "$SEED_DIR/core/sync/repo_file_sync.py" --topic-dir "$TOPIC_DIR" 2>&1 || \
  echo "warn: repo_file_sync failed (non-fatal)"

echo "== extracting session digest for ${TOPIC_NAME} =="
DIGEST_OUT="$(python3 "$SEED_DIR/core/sync/session_digest.py" \
  --topic-dir "$TOPIC_DIR" 2>&1)"
echo "$DIGEST_OUT"

# If no digest was written (no new content) we still update last_sync (done
# inside the Python script). Nothing more to do.
if ! echo "$DIGEST_OUT" | grep -q '^digest:'; then
  echo "nothing new — exiting"
  exit 0
fi

DIGEST_REL="$(echo "$DIGEST_OUT" | grep '^digest:' | sed 's|^digest: ||; s|'"$TOPIC_DIR/"'||')"

# Commit the new digest + updated ingest_state.json
cd "$TOPIC_DIR"
git add "sources/sessions/" ingest_state.json
git commit -m "sync: session digest $(date -u +%Y-%m-%d)" \
  --no-gpg-sign 2>/dev/null || git commit -m "sync: session digest $(date -u +%Y-%m-%d)"

echo "== committed digest: $DIGEST_REL =="

# Notify the live topic agent via tmux send-keys — injects the ingest command
# directly into the agent's TUI as if the owner typed it. No external tokens
# needed; works as long as the topic's tmux server is running.
#
# Fallback: if tmux server is not up (service stopped), write a pending notice
# file instead. The topic's CLAUDE.md instructs the agent to check pending/ for
# AUTO_SYNC_* files on each conversation start and process them automatically.
INGEST_CMD="ingest sources/sessions/$(basename "$DIGEST_REL") 並更新 memory section"

if tmux -L "topic-${TOPIC_NAME}" has-session -t "topic-${TOPIC_NAME}" 2>/dev/null; then
  # Give the agent a blank line first so it's not mid-reply, then send command.
  tmux -L "topic-${TOPIC_NAME}" send-keys -t "topic-${TOPIC_NAME}" "" Enter 2>/dev/null || true
  sleep 1
  tmux -L "topic-${TOPIC_NAME}" send-keys -t "topic-${TOPIC_NAME}" "$INGEST_CMD" Enter
  echo "== injected ingest command into topic-${TOPIC_NAME} TUI =="
else
  # Service not running — leave a pending notice for next startup
  PENDING_DIR="$TOPIC_DIR/pending"
  mkdir -p "$PENDING_DIR"
  NOTICE_FILE="$PENDING_DIR/AUTO_SYNC_$(date -u +%Y%m%d-%H%M).md"
  cat > "$NOTICE_FILE" <<NOTICE
# Auto-Sync Notice

digest: ${DIGEST_REL}
created: $(date -u +%Y-%m-%dT%H:%M:%SZ)

請 ingest 此 session digest 並更新相關 memory section。
NOTICE
  echo "== wrote pending notice: $NOTICE_FILE (service not running) =="
fi
