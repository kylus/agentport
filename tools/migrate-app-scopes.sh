#!/usr/bin/env bash
# Update an existing Slack app's bot scopes to match the current
# templates/slack-app-manifest.yml. Pulls the live manifest, merges in
# any scopes that the template has but the live app is missing, pushes
# the merged manifest back, then walks the operator through Reinstall +
# bot.env token swap.
#
# Usage:
#   tools/migrate-app-scopes.sh <topic-name>
#
# Prereqs:
#   - `slack login` once (credentials at ~/.slack/credentials.json)
#   - bot.env present at $HOME/workspace/topic-<name>/bot.env with
#     SLACK_APP_ID populated
#
# This intentionally only ADDS scopes — never removes. Removing scopes
# is rare and almost always wants human review.
set -euo pipefail

SEED_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOPIC_NAME="${1:?usage: migrate-app-scopes.sh <topic-name>}"
TOPIC_DIR="$HOME/workspace/topic-${TOPIC_NAME}"
TOPIC_REAL="$(readlink -f "$TOPIC_DIR" 2>/dev/null || echo "$TOPIC_DIR")"
MANIFEST_TEMPLATE="$SEED_DIR/templates/slack-app-manifest.yml"

[ -d "$TOPIC_REAL" ] || { echo "error: $TOPIC_DIR not found" >&2; exit 1; }
[ -f "$MANIFEST_TEMPLATE" ] || { echo "error: $MANIFEST_TEMPLATE missing" >&2; exit 1; }

set -a; source "$TOPIC_REAL/bot.env"; set +a
[ -n "${SLACK_APP_ID:-}" ] || { echo "error: SLACK_APP_ID not in bot.env" >&2; exit 2; }

CREDS="$HOME/.slack/credentials.json"
[ -r "$CREDS" ] || { echo "error: $CREDS not found — run 'slack login' first" >&2; exit 3; }

TOKEN="$(python3 -c "import json; c=json.load(open('$CREDS')); t=next(iter(c)); print(c[t]['token'])")"

# ---------- 1. Export current manifest ----------
EXPORT_BODY="$(python3 -c "import json,sys; print(json.dumps({'app_id': sys.argv[1]}))" "$SLACK_APP_ID")"
EXPORT_RESP="$(curl -s -X POST https://slack.com/api/apps.manifest.export \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$EXPORT_BODY")"

OK="$(echo "$EXPORT_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('ok'))")"
if [ "$OK" != "True" ]; then
  echo "apps.manifest.export failed:" >&2
  echo "$EXPORT_RESP" >&2
  exit 4
fi

# ---------- 2. Compute diff + merge ----------
# Parse the template YAML into a dict, walk a declarative RULES list,
# apply each rule against the live manifest dict. Adding a new field
# to reconcile = one entry in RULES. No more bespoke regex per field.
# Requires PyYAML (Ubuntu's python3-yaml package; pip install pyyaml).
MERGED_FILE="$(mktemp)"
trap 'rm -f "$MERGED_FILE"' EXIT

DIAG="$(python3 - "$MANIFEST_TEMPLATE" "$MERGED_FILE" <<PY
import json, sys
try:
    import yaml
except ImportError:
    print('ERR pyyaml not installed (apt install python3-yaml or pip install pyyaml)', file=sys.stderr)
    sys.exit(99)

template_path, out_path = sys.argv[1], sys.argv[2]
resp = json.loads('''$EXPORT_RESP''')
live = resp['manifest']
template = yaml.safe_load(open(template_path))

def dig(d, path):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur

def setdig(d, path, val):
    cur = d
    for k in path[:-1]:
        cur = cur.setdefault(k, {})
    cur[path[-1]] = val

# Declarative reconciliation table.
#   kind="union_list" — list-of-strings; missing items appended (sorted, dedup).
#   kind="overwrite"  — scalar; if live != template, overwrite live.
# To add a new manifest field, drop a new row here.
RULES = [
    {'kind': 'union_list', 'path': ['oauth_config', 'scopes', 'bot'],     'label': 'bot'},
    {'kind': 'union_list', 'path': ['oauth_config', 'scopes', 'user'],    'label': 'user'},
    {'kind': 'overwrite',  'path': ['settings', 'interactivity', 'is_enabled'], 'label': 'interactivity.is_enabled'},
]

missing_parts = []
mutated = False

for rule in RULES:
    tpl_val  = dig(template, rule['path'])
    live_val = dig(live,     rule['path'])

    if rule['kind'] == 'union_list':
        tpl_list  = list(tpl_val  or [])
        live_list = list(live_val or [])
        missing   = [s for s in tpl_list if s not in live_list]
        if missing:
            setdig(live, rule['path'], sorted(set(live_list + tpl_list)))
            mutated = True
            missing_parts.extend([f"{rule['label']}:{s}" for s in missing])

    elif rule['kind'] == 'overwrite':
        if tpl_val is None:
            continue
        if live_val != tpl_val:
            setdig(live, rule['path'], tpl_val)
            mutated = True
            missing_parts.append(f"{rule['label']}:{live_val}→{tpl_val}")

if not mutated:
    print('NO_DIFF')
    sys.exit(0)

with open(out_path, 'w') as f:
    json.dump(live, f)
print('MISSING ' + ' '.join(missing_parts))
PY
)"

case "$DIAG" in
  NO_DIFF)
    echo "✓ live app already has every scope the template lists. Nothing to do."
    exit 0
    ;;
  MISSING*)
    echo "→ ${DIAG}"
    ;;
  *)
    echo "merge step failed; diag was: $DIAG" >&2
    exit 5
    ;;
esac

# ---------- 3. Push merged manifest ----------
UPDATE_BODY="$(python3 -c "
import json, sys
manifest = json.load(open(sys.argv[2]))
print(json.dumps({'app_id': sys.argv[1], 'manifest': manifest}))
" "$SLACK_APP_ID" "$MERGED_FILE")"

UPDATE_RESP="$(curl -s -X POST https://slack.com/api/apps.manifest.update \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$UPDATE_BODY")"

OK="$(echo "$UPDATE_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('ok'))")"
if [ "$OK" != "True" ]; then
  echo "apps.manifest.update failed:" >&2
  echo "$UPDATE_RESP" >&2
  exit 6
fi
echo "✓ apps.manifest.update succeeded"

# ---------- 4. Walk owner through reinstall + token swap ----------
INSTALL_URL="https://api.slack.com/apps/$SLACK_APP_ID/install-on-team"
cat <<EOF

═══════════════════════════════════════════════════════════════════
  Manual step: reinstall the app to apply new scopes

  1. Open: $INSTALL_URL
  2. Click "Reinstall to Workspace" and approve the new permissions.
  3. Copy the new Bot User OAuth Token (starts with xoxb-).

  Slack will revoke the old token on reinstall, so the topic agent
  won't be able to reach Slack until bot.env is updated.
═══════════════════════════════════════════════════════════════════

EOF
read -r -p "Paste new SLACK_BOT_TOKEN (xoxb-...) or blank to skip both tokens: " NEW_BOT_TOKEN
if [ -z "$NEW_BOT_TOKEN" ]; then
  echo "Skipped. Update bot.env manually when ready; restart the service after."
  exit 0
fi

# Prefix check first — auth.test returns ok=true for BOTH xoxb- and
# xoxp- tokens (it's a generic auth probe), so a slot-swap would slip
# past the API validation. Catching the wrong prefix here is much
# louder than letting it write to bot.env and silently crash the
# plugin on next boot.
case "$NEW_BOT_TOKEN" in
  xoxb-*) ;;
  xoxp-*)
    echo "error: pasted token starts with xoxp- (user token). Bot OAuth Token starts with xoxb-." >&2
    echo "       Did you paste the user token in the bot prompt? Try again." >&2
    exit 7
    ;;
  *)
    echo "error: pasted token doesn't start with xoxb- (expected Bot User OAuth Token prefix)." >&2
    exit 7
    ;;
esac

# Validate against auth.test before writing.
AUTH="$(curl -s -X POST https://slack.com/api/auth.test \
  -H "Authorization: Bearer $NEW_BOT_TOKEN")"
AUTH_OK="$(echo "$AUTH" | python3 -c "import json,sys; print(json.load(sys.stdin).get('ok'))")"
if [ "$AUTH_OK" != "True" ]; then
  echo "auth.test rejected the pasted bot token — bot.env NOT modified:" >&2
  echo "$AUTH" >&2
  exit 7
fi

# If the manifest update touched user scopes too, the reinstall flow
# also issues a fresh xoxp- user token. Optional — operator can skip
# (e.g. they're not opting into the fetch_user_dms path on this topic).
NEW_USER_TOKEN=""
if [ -n "${TEMPLATE_HAS_USER_SCOPES:-}" ] || grep -qE '^\s*user:\s*$' "$MANIFEST_TEMPLATE" 2>/dev/null; then
  cat <<NOTE

(Manifest has user scopes — the reinstall page also showed a "user"
permissions block. Slack returned a user OAuth token alongside the bot
token, prefixed xoxp-. It's on the same install page near the bot token,
labeled "User OAuth Token". Pasting it here enables fetch_user_dms;
blank skips and leaves SLACK_USER_TOKEN unset.)
NOTE
  read -r -p "Paste SLACK_USER_TOKEN (xoxp-...) or blank to skip: " NEW_USER_TOKEN
  if [ -n "$NEW_USER_TOKEN" ]; then
    # Same slot-swap defense as the bot token above: auth.test passes
    # for either prefix, but loading a xoxb- into SLACK_USER_TOKEN
    # would make the plugin think it has a user token when it doesn't.
    case "$NEW_USER_TOKEN" in
      xoxp-*) ;;
      xoxb-*)
        echo "error: pasted token starts with xoxb- (bot token). User OAuth Token starts with xoxp-." >&2
        echo "       Did you paste the bot token in the user prompt? Try again." >&2
        exit 7
        ;;
      *)
        echo "error: pasted token doesn't start with xoxp- (expected User OAuth Token prefix)." >&2
        exit 7
        ;;
    esac
    USER_AUTH="$(curl -s -X POST https://slack.com/api/auth.test \
      -H "Authorization: Bearer $NEW_USER_TOKEN")"
    USER_OK="$(echo "$USER_AUTH" | python3 -c "import json,sys; print(json.load(sys.stdin).get('ok'))")"
    if [ "$USER_OK" != "True" ]; then
      echo "auth.test rejected the pasted user token — bot.env NOT modified:" >&2
      echo "$USER_AUTH" >&2
      exit 7
    fi
  fi
fi

# In-place rewrite of bot.env, atomically. Writes both tokens in one
# pass so the file stays consistent — never half-applied.
python3 - "$TOPIC_REAL/bot.env" "$NEW_BOT_TOKEN" "$NEW_USER_TOKEN" <<'PY'
import os, sys, pathlib
p = pathlib.Path(sys.argv[1])
new_bot  = sys.argv[2]
new_user = sys.argv[3]
patch = {'SLACK_BOT_TOKEN': new_bot}
if new_user:
    patch['SLACK_USER_TOKEN'] = new_user

lines = p.read_text().splitlines(keepends=True)
out, done = [], set()
for line in lines:
    matched = False
    for key, val in patch.items():
        if line.startswith(f'{key}='):
            out.append(f'{key}={val}\n')
            done.add(key)
            matched = True
            break
    if not matched:
        out.append(line)
for key, val in patch.items():
    if key not in done:
        out.append(f'{key}={val}\n')
tmp = str(p) + '.tmp'
with open(tmp, 'w') as f:
    f.writelines(out)
os.chmod(tmp, 0o600)
os.replace(tmp, p)
PY
echo "✓ bot.env updated. Restart the topic service to pick up the new token(s):"
echo "    systemctl --user restart topic-agent@${TOPIC_NAME}.service"
if [ -z "$NEW_USER_TOKEN" ] && grep -qE '^\s*user:\s*$' "$MANIFEST_TEMPLATE" 2>/dev/null; then
  echo
  echo "(SLACK_USER_TOKEN skipped — fetch_user_dms tool stays dormant on this topic.)"
fi
