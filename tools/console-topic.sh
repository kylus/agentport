#!/usr/bin/env bash
# Owner console — an interactive claude TUI rooted in the topic repo, with no
# channel adapters attached. Use it to read memory, approve pending proposals,
# ingest a source by hand, or ask the topic anything.
#
#   tools/console-topic.sh <topic-name>
#
# This is deliberately NOT the same thing as attaching to the running agent
# (tools/attach-topic.sh). The console is a second, separate session: it does
# not touch the live agent's conversation, and it does not connect to Discord,
# LINE or Slack. Nothing you type here is visible on a channel.
#
# One caveat worth knowing: both this and the live agent can commit to the same
# topic git repo. Approving the same proposal in both places is a conflict you
# have to resolve by hand, so approve in one or the other.
set -euo pipefail

TOPIC_NAME="${1:?usage: console-topic.sh <topic-name>}"
TOPIC_ROOT="${TOPIC_ROOT:-$HOME/workspace}"
TOPIC_DIR="$TOPIC_ROOT/topic-${TOPIC_NAME}"
SEED_DIR="$(cd "$(dirname "$0")/.." && pwd)"

[ -d "$TOPIC_DIR" ] || { echo "error: $TOPIC_DIR not found" >&2; exit 1; }

# Force subscription auth rather than the metered API.
unset ANTHROPIC_API_KEY

if [ -f "$TOPIC_DIR/bot.env" ]; then
  set -a
  # shellcheck disable=SC1091  # per-topic file, not present at lint time
  source "$TOPIC_DIR/bot.env"
  set +a
fi

# This session IS the owner, stated explicitly rather than inferred.
#
# The role gate fails closed: unset means contributor. On the agent's own
# session the role comes from .current-role, which the channel adapters rewrite
# per inbound message — but nobody writes it for a console the owner opened by
# hand, so without this line the owner is gated as a contributor and cannot
# approve anything, edit memory, or work out why.
#
# AGENTPORT_ROLE takes precedence over AGENTPORT_ROLE_FILE, so this also means
# a stale "contributor" left in .current-role by the last inbound message does
# not leak into the console.
export AGENTPORT_ROLE=owner

cd "$TOPIC_DIR"

exec claude --add-dir "$SEED_DIR" --permission-mode acceptEdits \
  "Owner console for topic: $TOPIC_NAME. Read CLAUDE.md and memory/*.md first to load current state, then wait for instructions. This session has no channel attached — nothing here is visible to anyone else."
