#!/usr/bin/env bash
# Write fresh Slack tokens into a topic's bot.env without re-running the
# whole scope-migration flow. Use this after a reinstall when the
# manifest is already aligned (migrate-app-scopes.sh exits at NO_DIFF
# before its token prompt, so there's otherwise no path to just swap
# tokens).
#
# Usage:
#   tools/set-slack-tokens.sh <topic-name>
#
# Prompts for the Bot User OAuth Token (xoxb-) and optionally the User
# OAuth Token (xoxp-). Tokens are read with `read -rs` (no terminal
# echo, never lands in shell history). Validates the prefix + auth.test
# before touching bot.env. Atomic write, mode 0600.
set -euo pipefail

TOPIC_NAME="${1:?usage: set-slack-tokens.sh <topic-name>}"
TOPIC_DIR="$HOME/workspace/topic-${TOPIC_NAME}"
TOPIC_REAL="$(readlink -f "$TOPIC_DIR" 2>/dev/null || echo "$TOPIC_DIR")"
BOT_ENV="$TOPIC_REAL/bot.env"

[ -f "$BOT_ENV" ] || { echo "error: $BOT_ENV not found" >&2; exit 1; }

# --- Bot token (required) ---
read -rsp "Paste Bot User OAuth Token (xoxb-...): " NEW_BOT_TOKEN; echo
[ -n "$NEW_BOT_TOKEN" ] || { echo "error: no bot token given" >&2; exit 2; }
case "$NEW_BOT_TOKEN" in
  xoxb-*) ;;
  xoxp-*) echo "error: that's a user token (xoxp-). Bot token starts with xoxb-." >&2; exit 2;;
  *)      echo "error: doesn't look like a bot token (expected xoxb- prefix)." >&2; exit 2;;
esac
BOT_AUTH="$(curl -s -X POST https://slack.com/api/auth.test -H "Authorization: Bearer $NEW_BOT_TOKEN")"
[ "$(echo "$BOT_AUTH" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("ok"))')" = "True" ] || {
  echo "auth.test rejected the bot token — nothing written:" >&2; echo "$BOT_AUTH" >&2; exit 3; }

# --- User token (optional) ---
read -rsp "Paste User OAuth Token (xoxp-...) or ENTER to skip: " NEW_USER_TOKEN; echo
if [ -n "$NEW_USER_TOKEN" ]; then
  case "$NEW_USER_TOKEN" in
    xoxp-*) ;;
    xoxb-*) echo "error: that's a bot token (xoxb-). User token starts with xoxp-." >&2; exit 2;;
    *)      echo "error: doesn't look like a user token (expected xoxp- prefix)." >&2; exit 2;;
  esac
  USER_AUTH="$(curl -s -X POST https://slack.com/api/auth.test -H "Authorization: Bearer $NEW_USER_TOKEN")"
  [ "$(echo "$USER_AUTH" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("ok"))')" = "True" ] || {
    echo "auth.test rejected the user token — nothing written:" >&2; echo "$USER_AUTH" >&2; exit 3; }
fi

# --- Atomic write ---
python3 - "$BOT_ENV" "$NEW_BOT_TOKEN" "$NEW_USER_TOKEN" <<'PY'
import os, sys, pathlib
p = pathlib.Path(sys.argv[1])
patch = {'SLACK_BOT_TOKEN': sys.argv[2]}
if sys.argv[3]:
    patch['SLACK_USER_TOKEN'] = sys.argv[3]
out, done = [], set()
for ln in p.read_text().splitlines(keepends=True):
    hit = False
    for k, v in patch.items():
        if ln.startswith(f'{k}='):
            out.append(f'{k}={v}\n'); done.add(k); hit = True; break
    if not hit:
        out.append(ln)
for k, v in patch.items():
    if k not in done:
        out.append(f'{k}={v}\n')
tmp = str(p) + '.tmp'
open(tmp, 'w').writelines(out)
os.chmod(tmp, 0o600)
os.replace(tmp, p)
PY

echo "✓ bot.env updated. Restart to apply:"
echo "    systemctl --user restart topic-agent@${TOPIC_NAME}.service"
