#!/usr/bin/env bash
# Provision a Slack app for a topic agent via the apps.manifest.create API.
#
# Slack CLI v4 is geared toward "Run-on-Slack" platform apps (TypeScript/Deno
# on Slack-hosted infra), not custom Socket Mode bots. So we don't use the
# CLI's `slack` subcommands directly — we just read the user OAuth token
# the CLI already stored after `slack login`, then call Slack's HTTP API.
#
# Prereq:
#   - `slack login` once (stores token in ~/.slack/credentials.json)
#   - `jq` not required (we use python3 for parsing)
#
# Usage:
#   tools/provision-slack-app.sh <topic-name>
#
# What you'll do manually after this script runs:
#   1. Click the install URL the script prints → approve in browser (1 click)
#   2. Generate App-Level Token in Slack UI (no API exists for this; ~30s)
#   3. Paste your own Slack member ID into bot.env (1 lookup)

set -euo pipefail

SEED_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOPIC_NAME="${1:?usage: provision-slack-app.sh <topic-name> [<bot-display-name>]}"
TOPIC_DIR="$HOME/workspace/topic-${TOPIC_NAME}"   # symlink set by create-topic.sh
TOPIC_REAL="$(readlink -f "$TOPIC_DIR" 2>/dev/null || echo "$TOPIC_DIR")"

# Always confirm the display name with the user — defaults to <topic>-agent
# but Slack shows this as the bot's display name everywhere, so getting it
# wrong is highly visible.
DEFAULT_NAME="${TOPIC_NAME}-agent"
if [ -n "${2:-}" ]; then
  BOT_DISPLAY_NAME="$2"
else
  read -r -p "Slack bot display name [${DEFAULT_NAME}]: " BOT_DISPLAY_NAME
  BOT_DISPLAY_NAME="${BOT_DISPLAY_NAME:-$DEFAULT_NAME}"
fi
echo "using bot display name: ${BOT_DISPLAY_NAME}"

[ -d "$TOPIC_REAL" ] || { echo "error: $TOPIC_DIR not found — run create-topic.sh first" >&2; exit 1; }

CREDS="$HOME/.slack/credentials.json"
[ -r "$CREDS" ] || { echo "error: $CREDS not found — run 'slack login' first" >&2; exit 2; }

# Extract user OAuth token + team_id. If SLACK_TEAM_ID is in env (set by
# onboard-topic.sh's multi-team selector), honour it; otherwise fall back
# to the first team in the credentials file.
read TOKEN TEAM_ID <<< "$(SLACK_TEAM_ID="${SLACK_TEAM_ID:-}" python3 -c "
import json, os, sys
c = json.load(open('$CREDS'))
preferred = os.environ.get('SLACK_TEAM_ID') or ''
team_id = preferred if preferred in c else next(iter(c))
print(c[team_id]['token'], team_id)
")"
[ -n "$TOKEN" ] || { echo "error: no Slack OAuth token found in $CREDS" >&2; exit 3; }
echo "== using slack token for team $TEAM_ID =="

# Resolve who the operator is (= topic owner) via auth.test on the user
# OAuth token. user_id is the trustworthy Slack-set identifier — we
# confirm with the operator before splicing it into bot.env so a wrong
# slack login or pre-set token doesn't silently bake in the wrong owner.
echo "== checking owner identity =="
OWNER_AUTH="$(curl -s -H "Authorization: Bearer $TOKEN" https://slack.com/api/auth.test)"
OWNER_ID="$(echo "$OWNER_AUTH" | python3 -c "import json,sys; print(json.load(sys.stdin).get('user_id',''))")"
OWNER_NAME="$(echo "$OWNER_AUTH" | python3 -c "import json,sys; print(json.load(sys.stdin).get('user',''))")"
if [ -z "$OWNER_ID" ]; then
  echo "error: auth.test failed: $OWNER_AUTH" >&2
  exit 7
fi
echo "  detected owner: ${OWNER_NAME:-?} (${OWNER_ID})"
read -r -p "Confirm this is the topic owner [Y/n]: " OWNER_OK
case "${OWNER_OK:-y}" in
  [yY]|[yY][eE][sS]|"") ;;
  *)
    read -r -p "Paste the correct Slack member ID (U…), or blank to abort: " OWNER_ID
    [ -n "$OWNER_ID" ] || { echo "aborted" >&2; exit 8; }
    ;;
esac

# Render the manifest YAML → JSON (Slack's API accepts both).
MANIFEST_TMPL="$SEED_DIR/templates/slack-app-manifest.yml"
RENDERED_YAML="/tmp/slack-app-${TOPIC_NAME}.yml"
sed -e "s|{{TOPIC_NAME}}|${TOPIC_NAME}|g" \
    -e "s|{{BOT_DISPLAY_NAME}}|${BOT_DISPLAY_NAME}|g" \
    "$MANIFEST_TMPL" > "$RENDERED_YAML"

# YAML → JSON via Python (Slack API needs JSON-stringified manifest).
MANIFEST_JSON="$(python3 -c "
import json, sys
try:
    import yaml
except ImportError:
    sys.stderr.write('python3 -m pip install pyyaml --user   (then re-run)\n')
    sys.exit(2)
print(json.dumps(yaml.safe_load(open('$RENDERED_YAML'))))
")"

# 1. Create app
echo "== creating Slack app from manifest =="
RESP="$(curl -s -X POST https://slack.com/api/apps.manifest.create \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "$(python3 -c "import json,sys; print(json.dumps({'manifest': sys.argv[1]}))" "$MANIFEST_JSON")")"

APP_ID="$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('app_id',''))")"
if [ -z "$APP_ID" ]; then
  echo "manifest.create failed:" >&2
  echo "$RESP" >&2
  exit 4
fi
echo "  app_id: $APP_ID"

INSTALL_URL="https://api.slack.com/apps/${APP_ID}/install-on-team"

# 2. Tell user to install via the app's Install page (NOT the oauth_authorize_url
# from manifest.create — that flow needs a redirect_uri, which Socket Mode
# apps don't set, so it fails with "Something went wrong when authorizing").
echo
echo "============================================================"
echo " step 1 of 2 — install the app + grab the BOT token"
echo
echo "   open: $INSTALL_URL"
echo
echo "   1) click 'Install to <Workspace>' → Allow"
echo "   2) after install you land on OAuth & Permissions"
echo "   3) copy the 'Bot User OAuth Token' (starts with xoxb-)"
echo "============================================================"
echo
read -r -p "Paste the bot token (xoxb-…) here, then ENTER: " BOT_TOKEN
[ -n "$BOT_TOKEN" ] || { echo "no token given, aborting" >&2; exit 5; }
case "$BOT_TOKEN" in
  xoxb-*) ;;
  *) echo "warn: token doesn't start with xoxb- — continuing but check it's the Bot token not the App token" >&2;;
esac

# 3. auth.test → bot user_id
AUTH_TEST="$(curl -s -H "Authorization: Bearer ${BOT_TOKEN}" https://slack.com/api/auth.test)"
BOT_USER_ID="$(echo "$AUTH_TEST" | python3 -c "import json,sys; print(json.load(sys.stdin).get('user_id',''))")"
[ -n "$BOT_USER_ID" ] || { echo "auth.test failed: $AUTH_TEST" >&2; exit 6; }
echo "  bot_user_id: $BOT_USER_ID"

# 4. step 2 of 2 — prompt for the APP token. Slack's API has no endpoint
# for creating App-Level tokens (apps.connections.* covers connecting via
# them, not minting them), so this is the one piece of the provisioning
# flow we can't automate. Walk the user through it inline rather than
# punting to a final printf they'd skim past.
echo
echo "============================================================"
echo " step 2 of 2 — generate the APP-LEVEL token (xapp-)"
echo
echo "   open: https://api.slack.com/apps/${APP_ID}/general"
echo
echo "   1) scroll to the 'App-Level Tokens' section"
echo "   2) click 'Generate Token and Scopes'"
echo "      • Name: socket-mode   (anything works, just label it)"
echo "      • Add Scope: connections:write   (required for Socket Mode)"
echo "      • click Generate"
echo "   3) copy the token (starts with xapp-) — visible only this once"
echo "============================================================"
echo
read -r -p "Paste the app token (xapp-…) here, then ENTER: " APP_TOKEN
[ -n "$APP_TOKEN" ] || { echo "no app token given, aborting" >&2; exit 5; }
case "$APP_TOKEN" in
  xapp-*) ;;
  *) echo "warn: token doesn't start with xapp- — continuing but check you grabbed the App-Level Token, not the Bot token" >&2;;
esac

# 5. Splice everything into bot.env in one pass.
BOT_ENV="$TOPIC_REAL/bot.env"
python3 - "$BOT_ENV" "$BOT_TOKEN" "$APP_TOKEN" "$BOT_USER_ID" "$APP_ID" "$OWNER_ID" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
patch = {
    "SLACK_BOT_TOKEN": sys.argv[2],
    "SLACK_APP_TOKEN": sys.argv[3],
    "SLACK_BOT_USER_ID": sys.argv[4],
    "SLACK_APP_ID": sys.argv[5],
    "OWNER_SLACK_USER_ID": sys.argv[6],
}
# Drop any inline-comment-on-value the template seeded (e.g. the
# "# generate manually..." note on SLACK_APP_TOKEN) — replacing the
# whole line keeps the file clean.
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

# 6. Also push bot token to ingest env
INGEST_ENV="$HOME/.claude/secrets/agentport.env"
mkdir -p "$(dirname "$INGEST_ENV")"
if ! grep -q '^SLACK_INGEST_TOKEN=' "$INGEST_ENV" 2>/dev/null; then
  echo "SLACK_INGEST_TOKEN=$BOT_TOKEN" >> "$INGEST_ENV"
  chmod 600 "$INGEST_ENV"
fi

echo
echo "== bot.env fully populated =="
echo
echo "  app_id:         $APP_ID"
echo "  bot_user_id:    $BOT_USER_ID"
echo "  owner_user_id:  $OWNER_ID"
echo
echo "Next: enable + start the service (XDG_RUNTIME_DIR must be set):"
echo "  export XDG_RUNTIME_DIR=/run/user/\$(id -u)"
echo "  systemctl --user enable --now topic-agent@${TOPIC_NAME}.service"
echo "  tools/attach-topic.sh ${TOPIC_NAME}   # verify Socket Mode connected"
