#!/usr/bin/env bash
# Attach to a topic agent's terminal, whichever way it was launched.
#
#   tools/attach-topic.sh <topic-name>
#
# Two launchers, two ways in, and the caller should not have to remember which
# one a topic is using today:
#
#   herdr  — `herdr agent attach <topic>`; detach with the herdr detach key
#   tmux   — each topic owns a dedicated server (socket name topic-<name>), so
#            a bare `tmux attach -t topic-<name>` does NOT find it; detach with
#            Ctrl-b d
#
# Either way: don't Ctrl-c. That sends SIGINT to the agent and interrupts
# whatever it was doing. `systemctl --user stop topic-agent-herdr@<topic>` is
# the clean shutdown path.
set -eu

TOPIC_NAME="${1:?usage: attach-topic.sh <topic-name>}"

# The name becomes a tmux socket path (/tmp/tmux-<uid>/topic-<name>), so it has
# to stay inside the conventional directory and produce a sane filename. Same
# alphabet create-topic.sh enforces.
if ! [[ "$TOPIC_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "error: topic name '$TOPIC_NAME' contains illegal characters (allowed: A-Z a-z 0-9 . _ -)" >&2
  exit 2
fi

if command -v herdr >/dev/null 2>&1 && herdr agent get "$TOPIC_NAME" >/dev/null 2>&1; then
  exec herdr agent attach "$TOPIC_NAME"
fi

if command -v tmux >/dev/null 2>&1 &&
   tmux -L "topic-${TOPIC_NAME}" has-session -t "topic-${TOPIC_NAME}" 2>/dev/null; then
  exec tmux -L "topic-${TOPIC_NAME}" attach -t "topic-${TOPIC_NAME}"
fi

echo "topic '$TOPIC_NAME' is not running under herdr or tmux" >&2
echo "  start it:  tools/run-topic-herdr.sh ~/workspace/topic-${TOPIC_NAME}" >&2
echo "  or:        systemctl --user start topic-agent-herdr@${TOPIC_NAME}" >&2
exit 1
