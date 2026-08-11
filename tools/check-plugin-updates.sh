#!/usr/bin/env bash
# Check if claude-code-slack-channel plugin has upstream updates.
# If new commits are found, post a Slack notification to the topic owner.
#
# Called by sync-session-memory.sh as part of the nightly timer.
# Can also be run standalone:
#   tools/check-plugin-updates.sh <topic-name>
set -euo pipefail

TOPIC_NAME="${1:?usage: check-plugin-updates.sh <topic-name>}"
TOPIC_DIR="$HOME/workspace/topic-${TOPIC_NAME}"
SLACK_PLUGIN_DIR="${SLACK_PLUGIN_DIR:-$HOME/workspace/claude-code-slack-channel}"
SEED_DIR="$(cd "$(dirname "$0")/.." && pwd)"

[ -d "$TOPIC_DIR" ]       || { echo "error: $TOPIC_DIR not found" >&2; exit 1; }
[ -d "$SLACK_PLUGIN_DIR" ] || { echo "error: plugin dir not found" >&2; exit 1; }

# Load bot token for Slack notifications
set -a; source "$TOPIC_DIR/bot.env"; set +a

# ── Check for upstream updates ──────────────────────────────────────────────
cd "$SLACK_PLUGIN_DIR"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
git fetch origin "$BRANCH" --quiet 2>&1 || { echo "warn: git fetch failed" >&2; exit 0; }

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH")"

if [ "$LOCAL" = "$REMOTE" ]; then
  echo "plugin up to date ($BRANCH)"
  exit 0
fi

# Count new commits and get their summaries
BEHIND=$(git rev-list HEAD..origin/"$BRANCH" --count)
SUMMARIES=$(git log HEAD..origin/"$BRANCH" --oneline | head -5)

echo "plugin has $BEHIND new commit(s) on $BRANCH"
echo "$SUMMARIES"

# ── Notify owner via Slack DM (bot token → owner user_id) ───────────────────
# chat.postMessage with channel=<user_id> opens a DM from bot to owner.
MSG="⚠️ *Slack plugin 有新版本需要更新*

*分支*: \`${BRANCH}\`
*落後*: ${BEHIND} 個 commit

最新更新：
\`\`\`
${SUMMARIES}
\`\`\`

*更新步驟*：
\`\`\`
cd ~/projects/agentport
tools/update-plugin.sh
\`\`\`
或參考 \`docs/plugin-update-sop.md\`"

RESP=$(curl -sf -X POST https://slack.com/api/chat.postMessage \
  -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "$(python3 -c "
import json, sys
print(json.dumps({'channel': sys.argv[1], 'text': sys.argv[2]}))
" "${OWNER_SLACK_USER_ID}" "$MSG")" 2>/dev/null || echo '{"ok":false}')

if python3 -c "import json,sys; assert json.loads(sys.argv[1]).get('ok')" "$RESP" 2>/dev/null; then
  echo "notified $OWNER_SLACK_USER_ID via Slack DM"
else
  echo "warn: Slack notification failed: $RESP" >&2
fi
