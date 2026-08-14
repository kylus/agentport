#!/usr/bin/env bash
# Launch a topic agent inside herdr — a terminal multiplexer that reports
# agent state (idle / working / blocked / done) instead of leaving panes opaque.
#
#   tools/run-topic-herdr.sh <topic-dir> [--provider claude|codex] [--attach]
#
# Why herdr rather than tmux: tmux keeps the process alive but tells you nothing
# about it. With several topic agents running, "which one is waiting on me"
# needs cycling through panes. herdr infers state from terminal output — no
# cooperation from the agent required — so that question becomes a query:
#
#   herdr agent list                          # every agent and its state
#   herdr agent wait <topic> --until blocked  # block until it needs a human
#
# That last one is what makes unattended supervision possible: a script can
# wait for "genuinely blocked" instead of polling for output that looks stuck.
#
# herdr is optional. tmux, systemd or a bare shell still work — this is one
# launcher, not the launcher. See docs/herdr.md.
set -euo pipefail

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-1}"; }
[ $# -ge 1 ] || usage
case "$1" in -h|--help) usage 0;; esac

TOPIC_DIR="$(cd "$1" && pwd)"; shift
PROVIDER=claude
ATTACH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --provider) PROVIDER="${2:?--provider needs claude or codex}"; shift 2;;
    --attach)   ATTACH=1; shift;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done
case "$PROVIDER" in claude|codex) ;; *) echo "--provider must be claude or codex" >&2; exit 2;; esac

command -v herdr >/dev/null || {
  echo "herdr not found — https://herdr.dev/docs/install/" >&2; exit 127; }

[ -d "$TOPIC_DIR/memory" ] && [ -d "$TOPIC_DIR/pending" ] || {
  echo "not a topic directory (no memory/ or pending/): $TOPIC_DIR" >&2
  echo "create one with tools/create-topic.sh" >&2; exit 2; }

TOPIC="$(basename "$TOPIC_DIR" | sed 's/^topic-//')"
AGENT="$TOPIC"

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

# Already running? Attach or report, never start a second copy — two agents on
# one topic directory would race each other's git commits.
if herdr agent get "$AGENT" >/dev/null 2>&1; then
  STATE="$(herdr agent get "$AGENT" 2>/dev/null \
           | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("result") or d).get("agent",{}).get("agent_status","unknown"))' \
           2>/dev/null || echo unknown)"
  echo "agent '$AGENT' already running (state: $STATE)"
  [ "$ATTACH" = "1" ] && exec herdr agent attach "$AGENT"
  echo "attach with: herdr agent attach $AGENT"
  exit 0
fi

WS_JSON="$(herdr workspace create --cwd "$TOPIC_DIR" --label "$TOPIC")"
PANE="$(printf '%s' "$WS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("result") or d)["root_pane"]["pane_id"])')"
WORKSPACE="${PANE%%:*}"

# The pane exists before the shell inside it is ready to be replaced by an
# agent — starting immediately returns agent_pane_busy. Retry until the shell
# settles, and tear the workspace down if it never does, rather than leaving an
# orphan behind (that is exactly what a failed run used to do).
started=0
for _ in $(seq 15); do
  if herdr agent start "$AGENT" --kind "$PROVIDER" --pane "$PANE" >/dev/null 2>&1; then
    started=1; break
  fi
  sleep 1
done
if [ "$started" != "1" ]; then
  herdr workspace close "$WORKSPACE" >/dev/null 2>&1 || true
  echo "could not start '$AGENT' in $PANE — workspace $WORKSPACE removed" >&2
  echo "last error:" >&2
  herdr agent start "$AGENT" --kind "$PROVIDER" --pane "$PANE" 2>&1 | head -2 >&2 || true
  exit 1
fi

echo "started '$AGENT' ($PROVIDER) in $TOPIC_DIR"
echo "  state:  herdr agent get $AGENT"
echo "  attach: herdr agent attach $AGENT"
echo "  wait:   herdr agent wait $AGENT --until blocked"

[ "$ATTACH" = "1" ] && exec herdr agent attach "$AGENT"
exit 0
