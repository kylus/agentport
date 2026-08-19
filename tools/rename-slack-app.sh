#!/usr/bin/env bash
# Rename an existing Slack app via the apps.manifest.update API.
#
# Usage:
#   tools/rename-slack-app.sh <app-id> <new-display-name>
#
# Example:
#   tools/rename-slack-app.sh A0B40Q7CPGA qa-team-agent
#
# Works against any app owned by your slack-login'd user. After update you
# should Reinstall in the workspace (api.slack.com/apps/<id>/install-on-team)
# for the new display name to appear in Slack UIs.

set -euo pipefail

APP_ID="${1:?usage: rename-slack-app.sh <app-id> <new-display-name>}"
NEW_NAME="${2:?usage: rename-slack-app.sh <app-id> <new-display-name>}"

CREDS="$HOME/.slack/credentials.json"
TOKEN="$(python3 -c "import json; c=json.load(open('$CREDS')); t=next(iter(c)); print(c[t]['token'])")"

# Fetch current manifest so we only mutate the names.
RESP="$(curl -s -X POST https://slack.com/api/apps.manifest.export \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "import json,sys; print(json.dumps({'app_id': sys.argv[1]}))" "$APP_ID")")"

OK="$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('ok'))")"
if [ "$OK" != "True" ]; then
  echo "apps.manifest.export failed:" >&2
  echo "$RESP" >&2
  exit 1
fi

UPDATED="$(python3 -c "
import json, sys
resp = json.loads('''$RESP''')
m = resp['manifest']
m.setdefault('display_information', {})['name'] = '''$NEW_NAME'''
m.setdefault('features', {}).setdefault('bot_user', {})['display_name'] = '''$NEW_NAME'''
print(json.dumps({'app_id': '$APP_ID', 'manifest': m}))
")"

RESP2="$(curl -s -X POST https://slack.com/api/apps.manifest.update \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$UPDATED")"

OK2="$(echo "$RESP2" | python3 -c "import json,sys; print(json.load(sys.stdin).get('ok'))")"
if [ "$OK2" != "True" ]; then
  echo "apps.manifest.update failed:" >&2
  echo "$RESP2" >&2
  exit 2
fi
echo "renamed app $APP_ID → $NEW_NAME"
echo
echo "Reinstall to make the change visible in Slack UI:"
echo "  https://api.slack.com/apps/${APP_ID}/install-on-team"
