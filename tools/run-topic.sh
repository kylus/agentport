#!/usr/bin/env bash
# Launch a topic agent as a long-lived interactive Claude Code session.
#
#   tools/run-topic.sh <topic-name>                 # prepare, then exec claude
#   tools/run-topic.sh <topic-name> --print-argv    # prepare, print claude's argv
#   tools/run-topic.sh <topic-name> --print-prompt  # prepare, print bootstrap prompt
#
# This is the single source of truth for HOW a topic agent starts. It has two
# stages, and the split is the point:
#
#   prepare — resolve which channels are on, materialize their tokens and
#             access lists, regenerate .mcp.json to match, relink skills
#   launch  — exec claude with the argv that preparation implies
#
# Every front-end runs the same prepare stage. `--print-argv` stops after it and
# prints the argv instead of exec'ing, so a front-end that starts claude by some
# other means (tools/run-topic-herdr.sh hands it to `herdr agent start … --`)
# gets a session configured identically to the plain-exec path. Without that
# seam, each front-end reimplements channel resolution and they drift — which is
# exactly how the herdr launcher spent its first revision starting agents with
# no channels attached at all.
#
# Interactive, not `-p`: a long-lived session stays on the subscription pool,
# whereas one `claude -p` per inbound message is billed at API rates.
#
# Channels are OPT-IN per topic (empty or REPLACE_ME token in bot.env = that
# channel is off). At least one must be configured — a topic with none has
# nothing to reach it. Discord-only, LINE-only, Slack-only and any combination
# are all valid. Discord and LINE adapters are the ones this repo ships or
# vendors; Slack is supported when its external adapter is cloned separately.
#
# Per-topic isolation:
#   - <topic>/.slack-state, .discord-state, .line-state — tokens, access.json,
#     sessions. Only materialized for channels that are actually enabled.
#   - *_ROLE_HOOK_FILE all point at <topic>/.current-role — whichever adapter
#     delivers the next inbound message atomically rewrites it with 'owner' or
#     'contributor', and the PreToolUse role hook reads it to gate writes
#     regardless of which channel triggered the turn.
#   - <topic>/.mcp.json is REGENERATED on every launch, so bot.env stays the one
#     place a channel is turned on or off. A channel that is off never gets a
#     server block and its adapter is never spawned.
set -euo pipefail

TOPIC_NAME="${1:?usage: run-topic.sh <topic-name> [--print-argv|--print-prompt]}"
shift || true

MODE=exec
while [ $# -gt 0 ]; do
  case "$1" in
    --print-argv)   MODE=print-argv; shift;;
    --print-prompt) MODE=print-prompt; shift;;
    -h|--help)      sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

TOPIC_ROOT="${TOPIC_ROOT:-$HOME/workspace}"
TOPIC_DIR="$TOPIC_ROOT/topic-${TOPIC_NAME}"
SEED_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SLACK_PLUGIN_DIR="${SLACK_PLUGIN_DIR:-$HOME/workspace/claude-code-slack-channel}"
DISCORD_PLUGIN_DIR="${DISCORD_PLUGIN_DIR:-$HOME/workspace/claude-plugins-official/external_plugins/discord}"
# LINE adapter ships in this repo — no separate clone.
LINE_PLUGIN_DIR="${LINE_PLUGIN_DIR:-$SEED_DIR/channels/line}"

# Anything the caller should read must go to stdout only in print modes;
# progress chatter goes to stderr so `--print-argv` output stays parseable.
say() { if [ "$MODE" = exec ]; then echo "$@"; else echo "$@" >&2; fi; }

[ -d "$TOPIC_DIR" ] || { echo "error: $TOPIC_DIR not found" >&2; exit 1; }
[ -f "$TOPIC_DIR/bot.env" ] || { echo "error: $TOPIC_DIR/bot.env not found" >&2; exit 1; }

# Source bot env (tokens, owner ids, topic metadata).
set -a
# shellcheck disable=SC1091  # per-topic file, not present at lint time
source "$TOPIC_DIR/bot.env"
set +a

# Force subscription auth. If ANTHROPIC_API_KEY is set in the environment,
# Claude Code uses the metered API instead — which defeats the reason this runs
# as one long-lived interactive session rather than per-message `claude -p`.
unset ANTHROPIC_API_KEY

# ---------------------------------------------------------------- channels ---
# Checked symmetrically: empty or REPLACE_ME token = that channel is off.
SLACK_ENABLED=0
if [ -n "${SLACK_BOT_TOKEN:-}" ] && [[ "${SLACK_BOT_TOKEN}" != *REPLACE_ME* ]] \
   && [ -n "${SLACK_APP_TOKEN:-}" ] && [[ "${SLACK_APP_TOKEN}" != *REPLACE_ME* ]] \
   && [ -n "${OWNER_SLACK_USER_ID:-}" ] && [[ "${OWNER_SLACK_USER_ID}" != *REPLACE_ME* ]]; then
  SLACK_ENABLED=1
  [ -d "$SLACK_PLUGIN_DIR" ] || {
    echo "error: Slack adapter not found at $SLACK_PLUGIN_DIR" >&2
    echo "  it is external to this repo — clone it there, or clear the Slack" >&2
    echo "  tokens in $TOPIC_DIR/bot.env to turn the channel off." >&2
    exit 1
  }
fi

DISCORD_ENABLED=0
if [ -n "${DISCORD_BOT_TOKEN:-}" ] && [[ "${DISCORD_BOT_TOKEN}" != *REPLACE_ME* ]]; then
  DISCORD_ENABLED=1
  [ -d "$DISCORD_PLUGIN_DIR" ] || {
    echo "error: Discord adapter not found at $DISCORD_PLUGIN_DIR" >&2
    echo "  see docs/plugin-update-sop.md for where it is vendored from." >&2
    exit 1
  }
  if [ -z "${OWNER_DISCORD_USER_ID:-}" ]; then
    echo "error: DISCORD_BOT_TOKEN set but OWNER_DISCORD_USER_ID missing in $TOPIC_DIR/bot.env" >&2
    exit 2
  fi
fi

LINE_ENABLED=0
if [ -n "${LINE_CHANNEL_ACCESS_TOKEN:-}" ] && [[ "${LINE_CHANNEL_ACCESS_TOKEN}" != *REPLACE_ME* ]] \
   && [ -n "${LINE_CHANNEL_SECRET:-}" ] && [[ "${LINE_CHANNEL_SECRET}" != *REPLACE_ME* ]]; then
  LINE_ENABLED=1
  [ -d "$LINE_PLUGIN_DIR/node_modules" ] || {
    echo "error: LINE adapter deps missing" >&2
    echo "  install: cd $LINE_PLUGIN_DIR && bun install --frozen-lockfile" >&2
    exit 1
  }
  if [ -z "${OWNER_LINE_USER_ID:-}" ]; then
    echo "error: LINE tokens set but OWNER_LINE_USER_ID missing in $TOPIC_DIR/bot.env" >&2
    exit 2
  fi
fi

if [ "$SLACK_ENABLED" = "0" ] && [ "$DISCORD_ENABLED" = "0" ] && [ "$LINE_ENABLED" = "0" ]; then
  echo "error: no channel (Slack / Discord / LINE) is configured in $TOPIC_DIR/bot.env" >&2
  echo "  a topic with no channel has nothing to reach it — enable at least one." >&2
  exit 2
fi

# Every adapter runs on Bun.
command -v bun >/dev/null 2>&1 || {
  echo "error: bun not found in PATH — https://bun.sh/install" >&2; exit 3; }

# ------------------------------------------------------------ materialize ---
# Adapters read tokens from <state-dir>/.env (mode 0600), not from process.env.
# Rewritten on every launch so bot.env stays the single canonical source.
# Seed an adapter's access.json. The extra keys differ per adapter and are
# passed in rather than guessed: Slack keeps a channels+pending map and acks
# with :eyes:, Discord keeps groups and acks with the emoji itself, LINE keeps
# groups and has no ack reaction at all. Defaulting these wrongly is silent —
# the adapter just stops acking — so each caller states its own shape.
seed_access_json() {  # <access.json path> <owner id> <extra-key> [ack-reaction]
  python3 - "$1" "$2" "$3" "${4:-}" <<'PY'
import json, os, pathlib, sys
path, owner, extra, ack = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
p = pathlib.Path(path)
try:
    data = json.loads(p.read_text()) if p.exists() else {}
except (OSError, ValueError):
    data = {}
# Upstream's hardened default is dmPolicy=allowlist with an empty allowFrom,
# which silently drops every inbound DM. Merge the owner in without disturbing
# entries an operator added by hand.
data.setdefault("dmPolicy", "allowlist")
data.setdefault("allowFrom", [])
data.setdefault(extra, {})
if extra == "channels":
    data.setdefault("pending", {})
if ack:
    data.setdefault("ackReaction", ack)
if owner and owner not in data["allowFrom"]:
    data["allowFrom"].append(owner)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
os.chmod(tmp, 0o600)
os.replace(tmp, path)   # atomic: a reloading adapter never sees a half file
PY
}

write_env_file() {  # <target .env> <KEY=VALUE>...
  local target="$1"; shift
  local tmp="$target.$$.tmp"
  ( umask 077; printf '%s\n' "$@" > "$tmp" )
  mv -f "$tmp" "$target"
}

SLACK_STATE_DIR="$TOPIC_DIR/.slack-state"
if [ "$SLACK_ENABLED" = "1" ]; then
  mkdir -p "$SLACK_STATE_DIR"; chmod 700 "$SLACK_STATE_DIR"
  slack_lines=("SLACK_BOT_TOKEN=$SLACK_BOT_TOKEN" "SLACK_APP_TOKEN=$SLACK_APP_TOKEN")
  # Optional — only emit when bot.env carries one. Absent means the adapter's
  # user-token tools stay dormant rather than failing at call time.
  [ -n "${SLACK_USER_TOKEN:-}" ] && slack_lines+=("SLACK_USER_TOKEN=$SLACK_USER_TOKEN")
  write_env_file "$SLACK_STATE_DIR/.env" "${slack_lines[@]}"
  seed_access_json "$SLACK_STATE_DIR/access.json" "$OWNER_SLACK_USER_ID" channels eyes
fi

DISCORD_STATE_DIR="$TOPIC_DIR/.discord-state"
if [ "$DISCORD_ENABLED" = "1" ]; then
  mkdir -p "$DISCORD_STATE_DIR"; chmod 700 "$DISCORD_STATE_DIR"
  write_env_file "$DISCORD_STATE_DIR/.env" "DISCORD_BOT_TOKEN=$DISCORD_BOT_TOKEN"
  seed_access_json "$DISCORD_STATE_DIR/access.json" "$OWNER_DISCORD_USER_ID" groups "👀"
fi

# LINE's adapter embeds a loopback webhook listener; a TLS reverse proxy or
# tunnel must forward the public LINE webhook URL to 127.0.0.1:$LINE_WEBHOOK_PORT.
LINE_STATE_DIR="$TOPIC_DIR/.line-state"
if [ "$LINE_ENABLED" = "1" ]; then
  mkdir -p "$LINE_STATE_DIR"; chmod 700 "$LINE_STATE_DIR"
  write_env_file "$LINE_STATE_DIR/.env" \
    "LINE_CHANNEL_SECRET=$LINE_CHANNEL_SECRET" \
    "LINE_CHANNEL_ACCESS_TOKEN=$LINE_CHANNEL_ACCESS_TOKEN"
  seed_access_json "$LINE_STATE_DIR/access.json" "$OWNER_LINE_USER_ID" groups
fi

# --------------------------------------------------------------- .mcp.json ---
# Only enabled channels get a server block, so Claude Code never tries to spawn
# an adapter that was never installed.
python3 - "$TOPIC_DIR/.mcp.json" "$SLACK_ENABLED" "$DISCORD_ENABLED" \
  "$SLACK_PLUGIN_DIR" "$TOPIC_DIR" "${OWNER_SLACK_USER_ID:-}" \
  "$DISCORD_PLUGIN_DIR" "${OWNER_DISCORD_USER_ID:-}" \
  "$LINE_ENABLED" "$LINE_PLUGIN_DIR" "${OWNER_LINE_USER_ID:-}" \
  "${LINE_WEBHOOK_PORT:-18789}" <<'PY'
import json, pathlib, sys

(path, slack_on, discord_on, slack_plugin_dir, topic_dir, owner_slack,
 discord_plugin_dir, owner_discord, line_on, line_plugin_dir, owner_line,
 line_port) = sys.argv[1:13]
servers = {}
if slack_on == "1":
    servers["slack"] = {
        "command": "bun",
        "args": ["run", "--cwd", slack_plugin_dir, "server.ts"],
        "env": {
            "SLACK_STATE_DIR": f"{topic_dir}/.slack-state",
            "OWNER_SLACK_USER_ID": owner_slack,
            "SLACK_ROLE_HOOK_FILE": f"{topic_dir}/.current-role",
        },
    }
if discord_on == "1":
    servers["discord"] = {
        "command": "bun",
        "args": ["run", "--cwd", discord_plugin_dir, "start"],
        "env": {
            "DISCORD_STATE_DIR": f"{topic_dir}/.discord-state",
            "OWNER_DISCORD_USER_ID": owner_discord,
            "DISCORD_ROLE_HOOK_FILE": f"{topic_dir}/.current-role",
        },
    }
if line_on == "1":
    servers["line"] = {
        "command": "bun",
        "args": ["run", "--cwd", line_plugin_dir, "start"],
        "env": {
            "LINE_STATE_DIR": f"{topic_dir}/.line-state",
            "OWNER_LINE_USER_ID": owner_line,
            "LINE_ROLE_HOOK_FILE": f"{topic_dir}/.current-role",
            "LINE_WEBHOOK_PORT": line_port,
        },
    }
pathlib.Path(path).write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n")
PY

# Rebuild the topic's skill symlinks on every launch, same reasoning as
# .mcp.json above: a skill added to this repo reaches every topic at its next
# restart, and links to deleted skills get pruned instead of dangling.
"$SEED_DIR/tools/link-topic-skills.sh" "$TOPIC_DIR"

# ------------------------------------------------------------------- argv ---
PERM_ARGS=()
if [ "${SKIP_PERMS:-0}" = "1" ]; then
  PERM_ARGS+=("--dangerously-skip-permissions")
else
  PERM_ARGS+=("--permission-mode" "acceptEdits")
fi

CHANNEL_ARGS=()
[ "$SLACK_ENABLED" = "1" ] && CHANNEL_ARGS+=("server:slack")
[ "$DISCORD_ENABLED" = "1" ] && CHANNEL_ARGS+=("server:discord")
[ "$LINE_ENABLED" = "1" ] && CHANNEL_ARGS+=("server:line")

# Resume this topic's previous session if it has one. Without it every restart
# starts a fresh conversation: in-flight channel threads lose their context and
# the bootstrap prompt below gets replayed into an established conversation.
#
# ⚠️ Depends on Claude Code storing sessions at
#   ~/.claude/projects/<cwd with / replaced by ->/<uuid>.jsonl
# Verified against Claude Code 2.1.x. If that layout changes, --continue stops
# finding prior sessions and every topic silently starts fresh on restart —
# re-test this path when claude major-versions.
SESSIONS_DIR="$HOME/.claude/projects/$(echo "$TOPIC_DIR" | sed 's:/:-:g')"
RESUME_ARGS=()
HAS_PRIOR_SESSION=0
# AGENTPORT_NO_RESUME=1 forces a fresh conversation. Cold-start ingest wants
# this: bootstrap-topic.sh hands the agent a long playbook prompt, and resuming
# would drop that into the middle of an existing conversation instead.
if [ "${AGENTPORT_NO_RESUME:-0}" != "1" ] && \
   compgen -G "$SESSIONS_DIR/*.jsonl" > /dev/null 2>&1; then
  RESUME_ARGS+=("--continue")
  HAS_PRIOR_SESSION=1
fi

build_prompt() {
  local slack_line="" discord_line="" line_line=""
  [ "$SLACK_ENABLED" = "1" ] && slack_line="
當 Slack adapter 透過 MCP notification (channel=\"slack\") 把訊息送進來：
1 讀 meta.role：'owner' 可改 memory、approve；'contributor' 只能查詢 / 提 proposal
2 用 read-memory / propose-memory-update / approve-proposal 等 skills 處理
3 回應一律呼叫 mcp__slack__reply（chat_id 從 meta.chat_id，thread_ts 從 meta.thread_ts），不要用 stdout 輸出
4 mcp__slack__reply 一律帶 stream:true — 短訊息跟一般 post 沒有差別，長訊息才開始逐段更新，永遠 on 沒有副作用
5 記得 commit memory 變更，post-commit hook 會自動 push"
  [ "$DISCORD_ENABLED" = "1" ] && discord_line="
當 Discord adapter 透過 MCP notification (channel=\"discord\") 把訊息送進來：
1 讀 meta.role：'owner' 可改 memory、approve；'contributor' 只能查詢 / 提 proposal
2 用 read-memory / propose-memory-update / approve-proposal 等 skills 處理
3 回應一律呼叫 mcp__discord__reply（chat_id 從 meta.chat_id，可選 reply_to 從 meta.message_id 做 threading），不要用 stdout 輸出
4 沒有 stream 參數，一次性送出即可
5 記得 commit memory 變更，post-commit hook 會自動 push"
  [ "$LINE_ENABLED" = "1" ] && line_line="
當 LINE adapter 透過 MCP notification (channel=\"line\") 把訊息送進來：
1 讀 meta.role：'owner' 可改 memory、approve；'contributor' 只能查詢 / 提 proposal
2 用 read-memory / propose-memory-update / approve-proposal 等 skills 處理
3 回應一律呼叫 mcp__line__reply（chat_id 從 meta.chat_id），不要用 stdout 輸出
4 記得 commit memory 變更，post-commit hook 會自動 push"
  printf '%s' "你是 ${TOPIC_NAME} topic agent，已進入待命狀態。
${slack_line}${discord_line}${line_line}

平時你也可以接 owner 在這個 TUI 裡直接打的指令（這是 owner 的中控台）— 看上下文判斷是從哪個頻道來的還是 TUI 來的。"
}

# On a resume the bootstrap prompt would land as a new user message in an
# already-established conversation, so it is only ever sent on a first run.
PROMPT_ARG=()
if [ "$HAS_PRIOR_SESSION" = "0" ]; then
  PROMPT_ARG=("$(build_prompt)")
fi

# --strict-mcp-config + --mcp-config scope MCP servers to this topic only.
# Without it, user-level plugins auto-load and their bot processes fight the
# operator's own Claude Code session over the same long-poll connection.
#
# --channels only accepts plugin:NAME@MARKETPLACE entries; these adapters are
# wired as manually-configured MCP servers, so subscription goes through
# --dangerously-load-development-channels with one server:<name> per channel.
# Without a subscription entry the adapter emits notifications into the void:
# the gateway connects, the gate passes, the message gets its ack reaction, and
# Claude never sees it — the bot looks alive and answers nothing.
CLAUDE_ARGV=(
  "${RESUME_ARGS[@]}"
  --add-dir "$SEED_DIR"
  --mcp-config "$TOPIC_DIR/.mcp.json" --strict-mcp-config
  --dangerously-load-development-channels "${CHANNEL_ARGS[@]}"
  "${PERM_ARGS[@]}"
)

case "$MODE" in
  print-argv)
    # One argument per line. Every element here is a flag, a path or a
    # server:<name> token — none can contain a newline. The bootstrap prompt is
    # the only multi-line value and it is deliberately NOT included; a front-end
    # that needs it asks for --print-prompt separately.
    printf '%s\n' "${CLAUDE_ARGV[@]}"
    exit 0
    ;;
  print-prompt)
    [ "$HAS_PRIOR_SESSION" = "0" ] && build_prompt
    exit 0
    ;;
esac

# ------------------------------------------------------------------ launch ---
# Pre-export for shell-side use inside the session (the adapters themselves get
# their env from .mcp.json above).
export SLACK_STATE_DIR SLACK_PLUGIN_DIR
export DISCORD_STATE_DIR DISCORD_PLUGIN_DIR
export LINE_STATE_DIR LINE_PLUGIN_DIR

# How the hooks learn who is talking. Same file the adapters rewrite before
# delivering each inbound message (*_ROLE_HOOK_FILE in .mcp.json above), so the
# gate and the channel always agree. It has to come from the environment rather
# than from anything the model can type — see hooks/role_gate.py.
#
# Unset means contributor, which fails closed: forget this line and the owner
# gets locked out of their own TUI. Any other front-end must set it too.
export AGENTPORT_ROLE_FILE="$TOPIC_DIR/.current-role"

# --dangerously-load-development-channels raises an interactive confirmation at
# startup. Auto-answer it from the side, a few seconds in, so the TUI has
# rendered the prompt first.
#
# Empirically (netops, discord+line) the CLI shows ONE combined prompt no matter
# how many channels are enabled. Sending one "1" per channel is NOT harmless:
# the extra "1" lands in the chat input, gets submitted as a user message, and
# the agent burns a turn asking what "1" means. Send exactly one.
#
# Only fires when already inside tmux — the plain-exec path. Other front-ends
# answer the prompt themselves (see tools/run-topic-herdr.sh).
if [ -n "${TMUX:-}" ]; then
  (
    sleep 5
    tmux -L "topic-${TOPIC_NAME}" send-keys -t "topic-${TOPIC_NAME}" "1" Enter 2>/dev/null || true
  ) &
fi

cd "$TOPIC_DIR"
exec claude "${CLAUDE_ARGV[@]}" "${PROMPT_ARG[@]}"
