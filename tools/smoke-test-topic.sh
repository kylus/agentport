#!/usr/bin/env bash
# Sanity-test a topic after bootstrap. Channel-aware: runs Slack checks only
# when Slack is enabled in bot.env, Discord checks only when Discord is —
# same opt-in detection as run-topic.sh. Verifies:
#   1. memory/ has committed content
#   2. ingest_state.json is 'ready'
#   3. Per enabled channel: token valid, plugin installed + typechecks,
#      .mcp.json wired, and ONE test DM to the owner:
#      "🤖 smoke-test ping — if you see this, the bot can reach you."
#
# Usage:
#   tools/smoke-test-topic.sh <topic-name>

set -eu

TOPIC_NAME="${1:?usage: smoke-test-topic.sh <topic-name>}"
TOPIC_DIR="$HOME/workspace/topic-${TOPIC_NAME}"
TOPIC_REAL="$(readlink -f "$TOPIC_DIR" 2>/dev/null || echo "$TOPIC_DIR")"
SEED_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SLACK_PLUGIN_DIR="${SLACK_PLUGIN_DIR:-$HOME/workspace/claude-code-slack-channel}"
DISCORD_PLUGIN_DIR="${DISCORD_PLUGIN_DIR:-$HOME/workspace/claude-plugins-official/external_plugins/discord}"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$*"; FAIL=$((${FAIL:-0}+1)); }

[ -d "$TOPIC_REAL" ] || { fail "topic dir not found: $TOPIC_DIR"; exit 1; }
set -a; source "$TOPIC_REAL/bot.env"; set +a

bold "smoke-test: $TOPIC_NAME"

# Channel enablement — same rules as run-topic.sh
SLACK_ENABLED=0
if [ -n "${SLACK_BOT_TOKEN:-}" ] && [[ "${SLACK_BOT_TOKEN}" != *REPLACE_ME* ]] \
   && [ -n "${SLACK_APP_TOKEN:-}" ] && [[ "${SLACK_APP_TOKEN}" != *REPLACE_ME* ]] \
   && [ -n "${OWNER_SLACK_USER_ID:-}" ] && [[ "${OWNER_SLACK_USER_ID}" != *REPLACE_ME* ]]; then
  SLACK_ENABLED=1
fi
DISCORD_ENABLED=0
if [ -n "${DISCORD_BOT_TOKEN:-}" ] && [[ "${DISCORD_BOT_TOKEN}" != *REPLACE_ME* ]] \
   && [ -n "${OWNER_DISCORD_USER_ID:-}" ] && [[ "${OWNER_DISCORD_USER_ID}" != *REPLACE_ME* ]]; then
  DISCORD_ENABLED=1
fi

if [ "$SLACK_ENABLED" = "0" ] && [ "$DISCORD_ENABLED" = "0" ]; then
  fail "neither Slack nor Discord configured in bot.env — nothing to smoke-test"
  exit 1
fi
[ "$SLACK_ENABLED" = "1" ] && ok "Slack channel enabled"
[ "$DISCORD_ENABLED" = "1" ] && ok "Discord channel enabled"

# 1. memory has content
MEM_LINES=$(wc -l "$TOPIC_REAL"/memory/*.md 2>/dev/null | tail -1 | awk '{print $1}')
if [ "${MEM_LINES:-0}" -gt 30 ]; then
  ok "memory has content ($MEM_LINES total lines)"
else
  fail "memory looks empty ($MEM_LINES lines)"
fi

# 2. state is ready
STATE=$(python3 -c "import json; print(json.load(open('$TOPIC_REAL/ingest_state.json')).get('state',''))" 2>/dev/null || echo "")
if [ "$STATE" = "ready" ]; then
  ok "ingest_state = ready"
else
  fail "ingest_state = '$STATE' (expected 'ready')"
fi

# 3. bun (both plugins run on it)
if ! command -v bun >/dev/null 2>&1; then
  fail "bun not installed — run: curl -fsSL https://bun.sh/install | bash"
fi

# ---------- Slack checks ----------
if [ "$SLACK_ENABLED" = "1" ]; then
  AUTH=$(curl -s -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" https://slack.com/api/auth.test)
  if echo "$AUTH" | grep -q '"ok":true'; then
    ok "Slack auth.test ok"
  else
    fail "Slack auth.test failed: $AUTH"
  fi

  if [ ! -f "$SLACK_PLUGIN_DIR/server.ts" ]; then
    fail "Slack plugin not found at $SLACK_PLUGIN_DIR — clone your claude-code-slack-channel fork there"
  elif (cd "$SLACK_PLUGIN_DIR" && bun run typecheck) >/tmp/slack_plugin_check.log 2>&1; then
    ok "Slack plugin typechecks (at $SLACK_PLUGIN_DIR)"
  else
    fail "Slack plugin typecheck failed; see /tmp/slack_plugin_check.log"
  fi

  PING=$(curl -s -X POST https://slack.com/api/chat.postMessage \
    -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
    -H "Content-Type: application/json; charset=utf-8" \
    -d "{\"channel\":\"$OWNER_SLACK_USER_ID\",\"text\":\"🤖 $TOPIC_NAME smoke-test ping — bot can reach owner DM. Reply '@$TOPIC_NAME-agent test' in any channel after starting the listener to verify the full loop.\"}")
  if echo "$PING" | grep -q '"ok":true'; then
    ok "Slack ping DM sent to owner ($OWNER_SLACK_USER_ID)"
  else
    fail "Slack ping DM failed: $PING"
  fi
fi

# ---------- Discord checks ----------
if [ "$DISCORD_ENABLED" = "1" ]; then
  ME=$(curl -s -H "Authorization: Bot ${DISCORD_BOT_TOKEN}" https://discord.com/api/v10/users/@me)
  if echo "$ME" | grep -q '"id"'; then
    BOT_TAG=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('username','?'))" "$ME" 2>/dev/null || echo "?")
    ok "Discord token valid (bot: $BOT_TAG)"
  else
    fail "Discord token check failed: $ME"
  fi

  if [ ! -f "$DISCORD_PLUGIN_DIR/server.ts" ]; then
    fail "Discord plugin not found at $DISCORD_PLUGIN_DIR — clone your claude-plugins-official fork there"
  elif (cd "$DISCORD_PLUGIN_DIR" && bun run typecheck) >/tmp/discord_plugin_check.log 2>&1; then
    ok "Discord plugin typechecks (at $DISCORD_PLUGIN_DIR)"
  else
    fail "Discord plugin typecheck failed; see /tmp/discord_plugin_check.log"
  fi

  # Ping DM: open (or reuse) the bot↔owner DM channel, then post.
  # Requires the owner to share at least one server with the bot.
  DM_CH=$(curl -s -X POST https://discord.com/api/v10/users/@me/channels \
    -H "Authorization: Bot $DISCORD_BOT_TOKEN" -H 'Content-Type: application/json' \
    -d "{\"recipient_id\":\"$OWNER_DISCORD_USER_ID\"}" \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
  if [ -z "$DM_CH" ]; then
    fail "could not open Discord DM channel with owner ($OWNER_DISCORD_USER_ID) — does the owner share a server with the bot?"
  else
    PING=$(curl -s -X POST "https://discord.com/api/v10/channels/$DM_CH/messages" \
      -H "Authorization: Bot $DISCORD_BOT_TOKEN" -H 'Content-Type: application/json' \
      -d "{\"content\":\"🤖 $TOPIC_NAME smoke-test ping — bot can reach owner DM. After starting the listener, @ the bot in a registered channel (or DM it) to verify the full loop.\"}")
    if echo "$PING" | grep -q '"id"'; then
      ok "Discord ping DM sent to owner ($OWNER_DISCORD_USER_ID)"
    else
      fail "Discord ping DM failed: $PING"
    fi
  fi
fi

# .mcp.json wiring (regenerated by run-topic.sh; only meaningful if it exists)
if [ -f "$TOPIC_REAL/.mcp.json" ]; then
  if [ "$SLACK_ENABLED" = "1" ] && ! grep -q '"slack"' "$TOPIC_REAL/.mcp.json"; then
    fail ".mcp.json missing slack entry (run-topic.sh regenerates it on next launch — only a problem if it persists)"
  fi
  if [ "$DISCORD_ENABLED" = "1" ] && ! grep -q '"discord"' "$TOPIC_REAL/.mcp.json"; then
    fail ".mcp.json missing discord entry (run-topic.sh regenerates it on next launch — only a problem if it persists)"
  fi
  ok ".mcp.json present"
fi

echo
if [ "${FAIL:-0}" -eq 0 ]; then
  bold "✓ all checks passed"
  echo "Next: start the listener, then @ the bot on an enabled channel:"
  echo "  $SEED_DIR/tools/run-topic-herdr.sh $TOPIC_DIR"
  echo "  (or under systemd: systemctl --user start topic-agent-herdr@$TOPIC_NAME)"
else
  bold "✗ $FAIL check(s) failed — fix before relying on this topic"
  exit 1
fi
