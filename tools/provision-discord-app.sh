#!/usr/bin/env bash
# Provision a Discord bot for a topic agent.
#
# Unlike Slack, Discord has no manifest-create API for bots — app + bot
# creation is a manual Developer Portal flow. This script walks the operator
# through it, validates the pasted bot token via Discord's REST API, and
# writes DISCORD_BOT_TOKEN + OWNER_DISCORD_USER_ID into the topic's bot.env.
#
# Prereq: none (no CLI login step like `slack login` — Discord bot tokens
# are minted per-application in the Developer Portal, there's no local
# credential store to read from).
#
# Usage:
#   tools/provision-discord-app.sh <topic-name>
#
# Discord is OPTIONAL per topic — a topic works Slack-only if this script is
# never run. your launcher checks DISCORD_BOT_TOKEN and skips the Discord
# plugin entirely when it's empty.

set -euo pipefail

TOPIC_NAME="${1:?usage: provision-discord-app.sh <topic-name>}"
TOPIC_DIR="$HOME/workspace/topic-${TOPIC_NAME}"   # symlink set by your scaffold step
TOPIC_REAL="$(readlink -f "$TOPIC_DIR" 2>/dev/null || echo "$TOPIC_DIR")"

[ -d "$TOPIC_REAL" ] || { echo "error: $TOPIC_DIR not found — run your scaffold step first" >&2; exit 1; }

BOT_ENV="$TOPIC_REAL/bot.env"
[ -f "$BOT_ENV" ] || { echo "error: $BOT_ENV not found — run your scaffold step first" >&2; exit 1; }

echo
echo "============================================================"
echo " step 1 of 3 — create a Discord application + bot"
echo
echo "   open: https://discord.com/developers/applications"
echo
echo "   1) 'New Application' → give it a name (e.g. ${TOPIC_NAME}-agent)"
echo "   2) sidebar → Bot → give the bot a username"
echo "   3) scroll to 'Privileged Gateway Intents' → enable:"
echo "        - Message Content Intent   (required — without it inbound"
echo "          messages arrive with empty content)"
echo "============================================================"
echo
read -r -p "Press ENTER once the bot is created and the intent is enabled: " _

echo
echo "============================================================"
echo " step 2 of 3 — generate + paste the bot token"
echo
echo "   still on the Bot page: scroll up to 'Token' → 'Reset Token'"
echo "   copy it now — it's only shown once"
echo "============================================================"
echo
read -r -p "Paste the bot token here, then ENTER: " BOT_TOKEN
[ -n "$BOT_TOKEN" ] || { echo "no token given, aborting" >&2; exit 2; }

# Validate via Discord's REST API — GET /users/@me with the bot token is
# the equivalent of Slack's auth.test: confirms the token works and tells
# us the bot's own identity without needing any other credential.
AUTH_RESP="$(curl -s -H "Authorization: Bot ${BOT_TOKEN}" https://discord.com/api/v10/users/@me)"
BOT_USERNAME="$(echo "$AUTH_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('username',''))" 2>/dev/null || true)"
BOT_ID="$(echo "$AUTH_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || true)"
if [ -z "$BOT_ID" ]; then
  echo "error: token validation failed against /users/@me:" >&2
  echo "$AUTH_RESP" >&2
  exit 3
fi
echo "  bot identity confirmed: ${BOT_USERNAME:-?} (${BOT_ID})"

# Invite URL — bot scope + the fixed permission set the official plugin's
# README recommends. Integration type must be Guild Install for servers;
# DM-only deployments technically need zero permissions but inviting with
# these now avoids a second trip when guild channels get added later.
PERMISSIONS=274878008384   # View Channels, Send Messages, Send Messages in
                            # Threads, Read Message History, Attach Files,
                            # Add Reactions
INVITE_URL="https://discord.com/api/oauth2/authorize?client_id=${BOT_ID}&scope=bot&permissions=${PERMISSIONS}"

echo
echo "============================================================"
echo " step 3 of 3 — invite the bot + confirm your own Discord user id"
echo
echo "   Discord won't let you DM a bot unless you share a server with it."
echo
echo "   open: $INVITE_URL"
echo "   → pick a server you're in → Authorize"
echo
echo "   Then grab YOUR OWN user id (the topic owner — the only person who"
echo "   can write memory directly):"
echo "     User Settings → Advanced → enable Developer Mode"
echo "     right-click your own name/avatar → Copy User ID"
echo "============================================================"
echo
read -r -p "Paste your own Discord user id (snowflake) here, then ENTER: " OWNER_ID
[ -n "$OWNER_ID" ] || { echo "no owner id given, aborting" >&2; exit 4; }
case "$OWNER_ID" in
  ''|*[!0-9]*)
    echo "warn: '${OWNER_ID}' doesn't look like a numeric snowflake — continuing anyway, but double-check it" >&2
    ;;
esac

# Splice token + owner id into bot.env in one pass — same technique as
# the Slack equivalent (replace matching KEY= lines, append if absent).
python3 - "$BOT_ENV" "$BOT_TOKEN" "$OWNER_ID" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
patch = {
    "DISCORD_BOT_TOKEN": sys.argv[2],
    "OWNER_DISCORD_USER_ID": sys.argv[3],
}
lines = p.read_text().splitlines()
out = []
done = set()
for ln in lines:
    if "=" in ln and not ln.lstrip().startswith("#"):
        k = ln.split("=", 1)[0]
        if k in patch:
            out.append(f"{k}={patch[k]}")
            done.add(k)
            continue
    out.append(ln)
for k, v in patch.items():
    if k not in done:
        out.append(f"{k}={v}")
p.write_text("\n".join(out) + "\n")
PY
chmod 600 "$BOT_ENV"

echo
echo "== bot.env updated =="
echo
echo "  bot username:   ${BOT_USERNAME:-?}"
echo "  bot id:         ${BOT_ID}"
echo "  owner user id:  ${OWNER_ID}"
echo
echo "Next: restart (or first-launch) the topic session so it picks up the"
echo "new token — tools/your launcher detects a non-empty DISCORD_BOT_TOKEN"
echo "and subscribes the Discord channel automatically:"
echo "  tools/your launcher ${TOPIC_NAME}"
echo
echo "Then DM the bot from your Discord account to confirm it replies."
