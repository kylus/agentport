#!/usr/bin/env bash
# Guided onboarding for a new topic agent.
#
# Walks the user through:
#   1. Dependency / auth checks (uv, gh, slack, claude subscription)
#   2. Service tokens (~/.claude/secrets/agentport.env)
#   3. Topic creation (memory repo, Slack app)
#   4. Token sanity checks (auth.test, gh, github, clickup)
#   5. Launches bootstrap TUI as the owner control plane
#
# Usage:
#   tools/onboard-topic.sh
#
# Re-runnable: skips steps already done. Safe to abort and resume.

set -euo pipefail

SEED_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INGEST_ENV="$HOME/.claude/secrets/agentport.env"
mkdir -p "$(dirname "$INGEST_ENV")"

# ---------- presentation helpers ----------
bold() { printf "\033[1m%s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$*"; }
ask()  { printf "  ➤ %s " "$*"; }

step() { echo; bold "== $* =="; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { fail "$1 not found — $2"; return 1; }
}

env_has() { grep -q "^$1=" "$INGEST_ENV" 2>/dev/null; }

env_set() {
  local k="$1" v="$2"
  if env_has "$k"; then
    # replace
    sed -i.bak "s|^${k}=.*|${k}=${v}|" "$INGEST_ENV" && rm -f "${INGEST_ENV}.bak"
  else
    echo "${k}=${v}" >> "$INGEST_ENV"
  fi
  chmod 600 "$INGEST_ENV"
}

# ---------- step 1: dependencies ----------
step "1/5 dependency check"

for cmd in python3 git curl jq; do
  if command -v "$cmd" >/dev/null 2>&1; then ok "$cmd installed"; else fail "$cmd missing — install via apt/brew"; exit 1; fi
done

confirm_install() {
  local pkg="$1" url="$2"
  warn "$pkg missing"
  echo "  installer URL: $url"
  echo "  the script will download + run this. supply-chain trust required."
  ask "proceed? [y/N]"; read -r ans
  [[ "${ans:-N}" =~ ^[Yy]$ ]] || { fail "aborted — install $pkg manually then re-run"; exit 1; }
}

if command -v uv >/dev/null 2>&1; then
  ok "uv installed ($(uv --version))"
else
  confirm_install "uv" "https://astral.sh/uv/install.sh"
  curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh
  bash /tmp/uv-install.sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if command -v gh >/dev/null 2>&1; then
  ok "gh installed"
else
  fail "gh CLI missing — install: https://cli.github.com/"
  exit 1
fi

if command -v claude >/dev/null 2>&1; then
  ok "claude CLI installed ($(claude --version 2>&1 | head -1))"
else
  fail "claude CLI missing — install: https://docs.claude.com/claude-code"
  exit 1
fi

# Channel choice — Slack and Discord are symmetric opt-in; a topic needs at
# least one. This decides which CLIs/auth we check below and which
# provision script runs in step 4.
echo
echo "  channels for this topic:"
echo "    [1] Discord only"
echo "    [2] Slack only"
echo "    [3] both"
ask "pick number [1]:"; read -r CHAN_PICK
WANT_SLACK=0; WANT_DISCORD=0
case "${CHAN_PICK:-1}" in
  1) WANT_DISCORD=1 ;;
  2) WANT_SLACK=1 ;;
  3) WANT_SLACK=1; WANT_DISCORD=1 ;;
  *) fail "invalid choice"; exit 1 ;;
esac

if [ "$WANT_SLACK" = "1" ]; then
  if command -v slack >/dev/null 2>&1; then
    ok "slack CLI installed ($(slack --version 2>&1 | head -1))"
  else
    confirm_install "slack CLI" "https://downloads.slack-edge.com/slack-cli/install.sh"
    curl -fsSL https://downloads.slack-edge.com/slack-cli/install.sh -o /tmp/slack-install.sh
    bash /tmp/slack-install.sh
  fi
fi

# ---------- step 2: auth ----------
step "2/5 auth check"

# Claude subscription
unset ANTHROPIC_API_KEY
if [ -r "$HOME/.claude/.credentials.json" ] && grep -q "claudeAiOauth" "$HOME/.claude/.credentials.json" 2>/dev/null; then
  ok "Claude subscription auth present"
else
  warn "Claude not logged in (subscription)"
  ask "run 'claude login' now? [Y/n]"; read -r ans
  if [[ "${ans:-Y}" =~ ^[Yy]?$ ]]; then claude login; fi
fi

# GitHub via gh
if gh auth status >/dev/null 2>&1; then
  GH_USER="$(gh api user --jq '.login' 2>/dev/null)"
  ok "gh logged in as $GH_USER"
else
  warn "gh not logged in"
  ask "run 'gh auth login' now? [Y/n]"; read -r ans
  if [[ "${ans:-Y}" =~ ^[Yy]?$ ]]; then gh auth login; fi
fi

# Slack — must be logged in; if user skips, abort before scaffolding anything.
SLACK_TEAM_ID=""; SLACK_TEAM_DOMAIN=""; SLACK_USER_ID=""
if [ "$WANT_SLACK" = "1" ]; then
if ! slack auth list 2>/dev/null | grep -q "Team ID"; then
  warn "slack not logged in"
  ask "run 'slack login' now? [Y/n]"; read -r ans
  if [[ "${ans:-Y}" =~ ^[Yy]?$ ]]; then
    slack login
  else
    fail "slack login is required for topic provisioning. aborting — no files created."
    exit 1
  fi
fi
# Multi-team selector: list each authenticated team and let the operator
# pick. Default to the only/first if there's one.
TEAMS_JSON="$(python3 -c "
import json
c = json.load(open('$HOME/.slack/credentials.json'))
print(json.dumps([(tid, c[tid].get('team_domain') or '?', c[tid].get('user_id') or '?') for tid in c]))
")"
TEAM_COUNT="$(python3 -c "import json; print(len(json.loads('''$TEAMS_JSON''')))")"
if [ "$TEAM_COUNT" -gt 1 ]; then
  echo
  echo "  multiple Slack workspaces authenticated:"
  python3 -c "
import json
for i, (tid, dom, uid) in enumerate(json.loads('''$TEAMS_JSON''')):
    print(f'    [{i+1}] {dom} (team={tid}, you={uid})')
"
  ask "pick number:"; read -r idx
  TEAM_PICK="$(python3 -c "
import json
teams = json.loads('''$TEAMS_JSON''')
print('|'.join(teams[int('$idx')-1]))
")"
else
  TEAM_PICK="$(python3 -c "
import json
teams = json.loads('''$TEAMS_JSON''')
print('|'.join(teams[0]))
")"
fi
SLACK_TEAM_ID="${TEAM_PICK%%|*}"
rest="${TEAM_PICK#*|}"; SLACK_TEAM_DOMAIN="${rest%%|*}"; SLACK_USER_ID="${rest#*|}"
ok "slack logged in (team=$SLACK_TEAM_DOMAIN / $SLACK_TEAM_ID, you=$SLACK_USER_ID)"
fi  # WANT_SLACK

# ---------- step 3: service tokens ----------
step "3/5 service tokens (~/.claude/secrets/agentport.env)"

touch "$INGEST_ENV"; chmod 600 "$INGEST_ENV"

# GitHub: use gh fallback automatically (github_ingest.py handles it).
ok "GitHub auth: github_ingest.py uses 'gh auth token' automatically (no PAT needed)"

if [ "$WANT_SLACK" = "1" ]; then
  # Slack: bot token from provision-slack-app.sh handles ingest (channels the
  # bot is invited to). No separate user token / xoxp- needed.
  ok "Slack ingest: bot token covers ingest of channels the bot is in"
fi

# Google Drive is deliberately NOT prompted here. It's an optional
# secondary service — most topics don't need it. To enable later:
#   echo 'GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/sa.json' >> ~/.claude/secrets/agentport.env
# The ingest scripts will pick it up automatically once present.

# ---------- step 4: create topic ----------
step "4/5 create topic"

ask "topic name (kebab-case, no spaces, e.g. qa-team):"; read -r TOPIC_NAME
[ -n "$TOPIC_NAME" ] || { fail "topic name required"; exit 1; }

ask "memory repo on GitHub (owner/repo or git URL; blank = local-only):"; read -r MEMORY_REPO

ask "source-of-truth repo this topic tracks (owner/repo; blank if non-repo topic):"; read -r SCM_REPO

ask "bot display name [${TOPIC_NAME}-agent]:"; read -r BOT_NAME
BOT_NAME="${BOT_NAME:-${TOPIC_NAME}-agent}"

TOPIC_DIR="$HOME/workspace/topic-${TOPIC_NAME}"
TOPIC_REAL="$(readlink -f "$TOPIC_DIR" 2>/dev/null || echo "$TOPIC_DIR")"
BOT_ENV="$TOPIC_REAL/bot.env"

# Skip create-topic if the topic already exists (resume case)
if [ -e "$TOPIC_DIR" ] || [ -d "$TOPIC_REAL" ]; then
  ok "topic dir already exists ($TOPIC_REAL) — skipping create-topic.sh"
else
  "$SEED_DIR/tools/create-topic.sh" "$TOPIC_NAME" "$MEMORY_REPO" "$SCM_REPO" || { fail "create-topic failed"; exit 2; }
  ok "topic repo scaffolded"
fi

# Skip provision-slack-app only if bot.env has a REAL bot token (not the
# 'xoxb-REPLACE_ME' placeholder create-topic writes).
needs_provision() {
  [ -r "$BOT_ENV" ] || return 0
  local tok
  tok="$(grep '^SLACK_BOT_TOKEN=' "$BOT_ENV" | cut -d= -f2-)"
  [[ "$tok" =~ ^xoxb- ]] && [[ "$tok" != *REPLACE_ME* ]] || return 0
  return 1
}
if [ "$WANT_SLACK" = "1" ]; then
if needs_provision; then
  # Pass the user-selected team so provision uses the right workspace.
  SLACK_TEAM_ID="$SLACK_TEAM_ID" \
    "$SEED_DIR/tools/provision-slack-app.sh" "$TOPIC_NAME" "$BOT_NAME" \
      || { fail "provision-slack-app failed"; exit 3; }
  ok "Slack app created"
else
  ok "Slack app already provisioned (SLACK_BOT_TOKEN present) — skipping"
fi

# Auto-fill OWNER_SLACK_USER_ID using the team the user picked above.
if ! grep -E '^OWNER_SLACK_USER_ID=U[A-Z0-9]+' "$BOT_ENV" >/dev/null 2>&1; then
  if [ -n "${SLACK_USER_ID:-}" ]; then
    sed -i.bak "s|^OWNER_SLACK_USER_ID=.*|OWNER_SLACK_USER_ID=${SLACK_USER_ID}|" "$BOT_ENV" && rm -f "${BOT_ENV}.bak"
    ok "OWNER_SLACK_USER_ID auto-filled: $SLACK_USER_ID"
  fi
else
  ok "OWNER_SLACK_USER_ID already set"
fi

# Prompt for app-level token (only Slack UI step left). Validate strictly,
# rejecting the 'xapp-REPLACE_ME' placeholder create-topic seeds.
existing_xapp="$(grep '^SLACK_APP_TOKEN=' "$BOT_ENV" | cut -d= -f2-)"
if [[ "$existing_xapp" =~ ^xapp- ]] && [[ "$existing_xapp" != *REPLACE_ME* ]]; then
  ok "SLACK_APP_TOKEN already set"
else
  echo
  warn "ONE manual step left: generate App-Level Token (Slack has no API for this)"
  APP_ID="$(grep '^SLACK_APP_ID=' "$BOT_ENV" | cut -d= -f2)"
  echo "  open: https://api.slack.com/apps/${APP_ID}/general"
  echo "  → Basic Information → 'App-Level Tokens' → 'Generate Token and Scopes'"
  echo "     name: socket-mode   scope: connections:write"
  echo
  while true; do
    ask "paste the xapp-… token (or 'skip' to resume later):"; read -r APP_TOKEN
    if [ "$APP_TOKEN" = "skip" ]; then
      warn "SLACK_APP_TOKEN left blank; re-run onboard-topic.sh after generating it to complete setup."
      break
    fi
    if [[ ! "$APP_TOKEN" =~ ^xapp- ]]; then
      fail "doesn't look like a Slack app-level token (must start with 'xapp-'). try again or type 'skip'."
      continue
    fi
    sed -i.bak "s|^SLACK_APP_TOKEN=.*|SLACK_APP_TOKEN=${APP_TOKEN}|" "$BOT_ENV" && rm -f "${BOT_ENV}.bak"
    chmod 600 "$BOT_ENV"
    ok "SLACK_APP_TOKEN saved"
    break
  done
fi
fi  # WANT_SLACK

# Default SKIP_PERMS=1 (b option) per user preference — channel-independent
grep -q '^SKIP_PERMS' "$BOT_ENV" || echo 'SKIP_PERMS=1' >> "$BOT_ENV"

# Discord provisioning — interactive script walks the Developer Portal
# steps and fills DISCORD_BOT_TOKEN + OWNER_DISCORD_USER_ID into bot.env.
if [ "$WANT_DISCORD" = "1" ]; then
  disc_tok="$(grep '^DISCORD_BOT_TOKEN=' "$BOT_ENV" 2>/dev/null | cut -d= -f2-)"
  if [ -n "$disc_tok" ] && [[ "$disc_tok" != *REPLACE_ME* ]]; then
    ok "Discord bot already provisioned (DISCORD_BOT_TOKEN present) — skipping"
  else
    "$SEED_DIR/tools/provision-discord-app.sh" "$TOPIC_NAME" \
      || { fail "provision-discord-app failed"; exit 3; }
    ok "Discord bot provisioned"
  fi
fi

# ---------- step 5: integration tests ----------
step "5/5 integration sanity checks"

source "$BOT_ENV"

if [ "$WANT_SLACK" = "1" ]; then
  # Slack auth.test with bot token
  SLACK_OK="$(curl -s -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" https://slack.com/api/auth.test | python3 -c "import json,sys; print(json.load(sys.stdin).get('ok'))" 2>/dev/null || echo "False")"
  [ "$SLACK_OK" = "True" ] && ok "Slack bot token valid" || fail "Slack bot token rejected"
fi

if [ "$WANT_DISCORD" = "1" ]; then
  DISCORD_OK="$(curl -s -H "Authorization: Bot ${DISCORD_BOT_TOKEN}" https://discord.com/api/v10/users/@me | python3 -c "import json,sys; print('True' if json.load(sys.stdin).get('id') else 'False')" 2>/dev/null || echo "False")"
  [ "$DISCORD_OK" = "True" ] && ok "Discord bot token valid" || fail "Discord bot token rejected"
fi

# GitHub via github_ingest
if gh auth token >/dev/null 2>&1; then ok "GitHub auth via gh — ready"; else warn "GitHub auth missing"; fi

# Push memory repo (if remote set)
if [ -n "$MEMORY_REPO" ]; then
  cd "$TOPIC_REAL"
  if git push -u origin HEAD:main 2>&1 | tail -5; then
    ok "topic repo pushed to GitHub"
  else
    warn "first push failed — you may need to push manually later"
  fi
fi

echo
bold "== ready to bootstrap =="
echo
echo "Role + approve flow:"
echo "  - You are the owner for this topic (Slack=${SLACK_USER_ID:-n/a} / Discord=see bot.env)."
echo "    Anyone else on any channel is a 'contributor'."
echo "  - Owner @bot: can do everything (write memory, approve, ingest)."
echo "  - Contributor @bot: query / summarize / submit propose-memory-update"
echo "    (lands in pending/, doesn't auto-write memory)."
echo "  - When contributor submits, bot DMs you with the pending file."
echo "    Reply '@bot approve <file>' or '@bot reject <file> <reason>'."
echo "  - Enforced at tool level by PreToolUse hook — prompt can't bypass."
echo
echo "Next: launch the cold-start TUI. This same TUI doubles as your owner"
echo "control plane afterwards — don't close it."
echo
ask "launch bootstrap TUI now? [Y/n]"; read -r ans
if [[ "${ans:-Y}" =~ ^[Yy]?$ ]]; then
  exec "$SEED_DIR/tools/bootstrap-topic.sh" "$TOPIC_NAME"
else
  echo "later: $SEED_DIR/tools/bootstrap-topic.sh $TOPIC_NAME"
fi
