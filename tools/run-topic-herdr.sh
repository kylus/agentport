#!/usr/bin/env bash
# Launch a topic agent inside herdr — a terminal multiplexer that reports agent
# state (idle / working / blocked / done) instead of leaving panes opaque.
#
#   tools/run-topic-herdr.sh <topic-dir> [--provider claude|codex] [--attach]
#   tools/run-topic-herdr.sh <topic-dir> --stop
#
# Why herdr rather than tmux: tmux keeps the process alive but tells you nothing
# about it. With several topic agents running, "which one is waiting on me"
# means cycling through panes. herdr infers state from terminal output — no
# cooperation from the agent required — so that question becomes a query:
#
#   herdr agent list                          # every agent and its state
#   herdr agent wait <topic> --until blocked  # block until it needs a human
#
# That last one is what makes unattended supervision possible: a script can wait
# for "genuinely blocked" instead of polling for output that looks stuck.
#
# This is a FRONT-END, not a second launcher. Channel resolution, token
# materialization, .mcp.json and the claude argv all come from
# tools/run-topic.sh --print-argv, so a topic started here is configured
# identically to one started by plain exec. The first version of this script
# called `herdr agent start --kind claude` with no arguments and produced an
# agent with no channels attached — looked healthy, could not receive a single
# message. Hence the seam.
#
# herdr is optional. tmux, systemd or a bare shell still work — this is one
# launcher, not the launcher. See docs/herdr.md.
set -euo pipefail

usage() { sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-1}"; }
[ $# -ge 1 ] || usage
case "$1" in -h|--help) usage 0;; esac

TOPIC_DIR="$(cd "$1" && pwd)"; shift
PROVIDER=claude
ATTACH=0
STOP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --provider) PROVIDER="${2:?--provider needs claude or codex}"; shift 2;;
    --attach)   ATTACH=1; shift;;
    --stop)     STOP=1; shift;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done
case "$PROVIDER" in claude|codex) ;; *) echo "--provider must be claude or codex" >&2; exit 2;; esac

SEED_DIR="$(cd "$(dirname "$0")/.." && pwd)"

command -v herdr >/dev/null || {
  echo "herdr not found — https://herdr.dev/docs/install/" >&2; exit 127; }

[ -d "$TOPIC_DIR/memory" ] && [ -d "$TOPIC_DIR/pending" ] || {
  echo "not a topic directory (no memory/ or pending/): $TOPIC_DIR" >&2
  echo "create one with tools/create-topic.sh" >&2; exit 2; }

TOPIC="$(basename "$TOPIC_DIR" | sed 's/^topic-//')"
AGENT="$TOPIC"
# run-topic.sh addresses topics by name under TOPIC_ROOT; we were handed a path.
export TOPIC_ROOT="$(dirname "$TOPIC_DIR")"

# The interactive confirmation raised by --dangerously-load-development-channels.
# Observed wording (Claude Code 2.1.x):
#   WARNING: Loading development channels
#   ❯ 1. I am using this for local development
#     2. Exit
# Answered only when it actually appears (see below). Override if the CLI
# rewords it: AGENTPORT_CONFIRM_REGEX='...' tools/run-topic-herdr.sh …
CONFIRM_REGEX="${AGENTPORT_CONFIRM_REGEX:-(?i)loading development channels}"
CONFIRM_TIMEOUT_MS="${AGENTPORT_CONFIRM_TIMEOUT_MS:-20000}"

# The server has to be up before any socket command. Starting it is idempotent;
# racing two launchers is not, so serialise on a lock.
exec 9>"${TMPDIR:-/tmp}/agentport-herdr.lock"
flock 9

if ! herdr status server 2>/dev/null | grep -q '^status: running'; then
  echo "starting herdr server…"
  nohup herdr server >/dev/null 2>&1 &
  for _ in $(seq 30); do
    herdr status server 2>/dev/null | grep -q '^status: running' && break
    sleep 1
  done
  herdr status server 2>/dev/null | grep -q '^status: running' || {
    echo "herdr server did not come up" >&2; exit 1; }
fi

agent_field() {  # <agent name> <field>
  herdr agent get "$1" 2>/dev/null \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("result") or d).get("agent",{}).get(sys.argv[1],""))' "$2" \
    2>/dev/null || true
}

# Stop: close the whole workspace this topic owns, not just its pane. Closing
# the pane alone leaves an empty workspace behind, and those accumulate one per
# restart until the sidebar is unreadable. A topic that is not running is not an
# error — `systemctl stop` on an already-stopped unit must still succeed.
if [ "$STOP" = "1" ]; then
  if ! herdr agent get "$AGENT" >/dev/null 2>&1; then
    echo "agent '$AGENT' is not running"
    exit 0
  fi
  PANE_ID="$(agent_field "$AGENT" pane_id)"
  if [ -z "$PANE_ID" ]; then
    echo "agent '$AGENT' has no pane_id — nothing to close" >&2
    exit 0
  fi
  herdr workspace close "${PANE_ID%%:*}" >/dev/null 2>&1 || true
  echo "stopped '$AGENT' (workspace ${PANE_ID%%:*} closed)"
  exit 0
fi

# Already running? Attach or report, never start a second copy — two agents on
# one topic directory would race each other's git commits.
if herdr agent get "$AGENT" >/dev/null 2>&1; then
  STATE="$(agent_field "$AGENT" agent_status)"
  echo "agent '$AGENT' already running (state: ${STATE:-unknown})"
  [ "$ATTACH" = "1" ] && exec herdr agent attach "$AGENT"
  echo "attach with: herdr agent attach $AGENT"
  exit 0
fi

# Prepare the topic and get the argv this launch implies. This also
# regenerates .mcp.json, materializes adapter tokens and relinks skills — the
# prepare stage is idempotent, which is why asking for the prompt separately
# below costs correctness nothing.
mapfile -t CLAUDE_ARGV < <("$SEED_DIR/tools/run-topic.sh" "$TOPIC" --print-argv)
[ "${#CLAUDE_ARGV[@]}" -gt 0 ] || { echo "run-topic.sh produced no argv" >&2; exit 1; }
# Empty on a resume: the bootstrap prompt is only ever sent on a first run.
BOOTSTRAP_PROMPT="$("$SEED_DIR/tools/run-topic.sh" "$TOPIC" --print-prompt)"

WS_JSON="$(herdr workspace create --cwd "$TOPIC_DIR" --label "$TOPIC")"
PANE="$(printf '%s' "$WS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("result") or d)["root_pane"]["pane_id"])')"
WORKSPACE="${PANE%%:*}"

teardown() { herdr workspace close "$WORKSPACE" >/dev/null 2>&1 || true; }

# Prime the pane's shell with the topic's env. The adapters get their own env
# from .mcp.json, but skills that notify the owner read $OWNER_*_USER_ID and
# friends from the session's shell. Sourced from the file rather than passed
# via `workspace create --env`, which would put every token on a command line
# for `ps` to read.
# AGENTPORT_ROLE_FILE is what the hooks read to tell owner from contributor.
# run-topic.sh exports it on the plain-exec path, but those exports die with
# that process — here the agent inherits this pane's shell instead, so it has
# to be set again. Miss it and the gate fails closed on the owner.
herdr pane run "$PANE" \
  "set -a; . '$TOPIC_DIR/bot.env'; set +a; unset ANTHROPIC_API_KEY; \
export AGENTPORT_ROLE_FILE='$TOPIC_DIR/.current-role'" >/dev/null

# The pane exists before the shell inside it is ready to be replaced by an
# agent — starting immediately returns agent_pane_busy. Retry until the shell
# settles, and tear the workspace down if it never does, rather than leaving an
# orphan behind (that is exactly what a failed run used to do).
started=0
for _ in $(seq 15); do
  if herdr agent start "$AGENT" --kind "$PROVIDER" --pane "$PANE" \
       -- "${CLAUDE_ARGV[@]}" >/dev/null 2>&1; then
    started=1; break
  fi
  sleep 1
done
if [ "$started" != "1" ]; then
  echo "could not start '$AGENT' in $PANE — last error:" >&2
  herdr agent start "$AGENT" --kind "$PROVIDER" --pane "$PANE" \
    -- "${CLAUDE_ARGV[@]}" 2>&1 | head -5 >&2 || true
  echo "--- pane tail ---" >&2
  herdr pane read "$PANE" --lines 20 2>/dev/null >&2 || true
  teardown
  echo "workspace $WORKSPACE removed" >&2
  exit 1
fi

# --dangerously-load-development-channels raises an interactive confirmation at
# startup, and `agent start` returns as soon as the TUI is interactive — which
# it already is while the prompt is up. So the answer has to happen HERE, after
# the agent exists, and synchronously: an earlier revision backgrounded this and
# armed an EXIT trap to reap it, which killed the waiter the moment the script
# finished. The agent came up and sat at the prompt forever, state "blocked",
# with every channel unconnected.
#
# Answer it ONLY once it is actually on screen. A "1" typed when no prompt is
# showing lands in the chat input, gets submitted as a user message, and the
# agent burns a turn asking what "1" means. wait-output is what makes this
# conditional rather than a hopeful sleep — and it searches existing output
# before polling, so a prompt already up is matched immediately.
#
# One "1" total, no matter how many channels are enabled: the CLI shows a single
# combined prompt (verified with netops on discord+line).
if herdr pane wait-output "$PANE" --regex "$CONFIRM_REGEX" \
     --timeout "$CONFIRM_TIMEOUT_MS" >/dev/null 2>&1; then
  herdr pane send-keys "$PANE" "1" enter >/dev/null 2>&1 || true
  # Confirm it actually took: still blocked means the keystroke went somewhere
  # else, and every channel is still unconnected. Worth saying out loud rather
  # than reporting a successful start.
  sleep 3
  if [ "$(agent_field "$AGENT" agent_status)" = "blocked" ]; then
    echo "warning: '$AGENT' is still blocked after answering the channel prompt" >&2
    echo "  check it with: herdr agent attach $AGENT" >&2
  fi
fi

# Border label: without this every topic pane just reads "claude".
herdr pane rename "$PANE" "$TOPIC" >/dev/null 2>&1 || true

# First run only. On a resume this is empty and the session picks up its own
# history instead of being handed a fresh set of standing instructions.
if [ -n "$BOOTSTRAP_PROMPT" ]; then
  herdr agent prompt "$AGENT" "$BOOTSTRAP_PROMPT" >/dev/null 2>&1 \
    || echo "warning: bootstrap prompt not delivered — send it by hand" >&2
fi

echo "started '$AGENT' ($PROVIDER) in $TOPIC_DIR"
echo "  state:  herdr agent get $AGENT"
echo "  attach: herdr agent attach $AGENT"
echo "  wait:   herdr agent wait $AGENT --until blocked"

[ "$ATTACH" = "1" ] && exec herdr agent attach "$AGENT"
exit 0
