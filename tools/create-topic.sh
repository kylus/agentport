#!/usr/bin/env bash
# Scaffold a topic directory that the rest of agentport expects.
#
#   tools/create-topic.sh <topic-name> [target-dir]
#
# Produces (target-dir defaults to ~/workspace/topic-<name>):
#
#   topic-<name>/
#   ├── bot.env              channel credentials — gitignored, never committed
#   ├── sync.json            repo file sync config (core/sync/repo_file_sync.py)
#   ├── memory/              the six sections; only writable via approval
#   ├── pending/             proposals awaiting review
#   ├── sources/             snapshots pulled by repo file sync
#   ├── skills/              topic-local skills (override generic ones)
#   ├── .claude/skills/      symlinks, rebuilt by link-topic-skills.sh
#   └── .agents/skills/      same, for Codex
#
# Idempotent: re-running against an existing topic adds anything missing and
# leaves existing content alone. It will not overwrite bot.env.
#
# What this does NOT do: create the Discord/LINE app (see
# provision-discord-app.sh), or start the agent — that is your launcher's job.
set -euo pipefail

SEED_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-1}"; }
[ $# -ge 1 ] || usage
case "$1" in -h|--help) usage 0;; esac

TOPIC="$1"
case "$TOPIC" in
  *[!a-z0-9-]*|"" ) echo "topic name must be lowercase letters, digits and dashes" >&2; exit 2;;
esac
TOPIC_DIR="${2:-$HOME/workspace/topic-$TOPIC}"

mkdir -p "$TOPIC_DIR"
cd "$TOPIC_DIR"
mkdir -p memory pending sources skills

# Memory sections must match SECTIONS in skills/*/‌*.py — approval rejects an
# unknown section, so a typo here surfaces only at approve time.
for s in background current_understanding decisions open_questions commitments people; do
  [ -f "memory/$s.md" ] || printf '# %s\n' "$s" > "memory/$s.md"
done

if [ ! -f bot.env ]; then
  cat > bot.env <<'EOF'
# Channel credentials. Never commit this file.
# Discord: filled in by tools/provision-discord-app.sh
DISCORD_BOT_TOKEN=
OWNER_DISCORD_USER_ID=
DISCORD_CHANNEL_ID=

# LINE (optional) — see channels/line/README.md
LINE_CHANNEL_SECRET=
LINE_CHANNEL_ACCESS_TOKEN=
OWNER_LINE_USER_ID=
EOF
  chmod 600 bot.env
  echo "  created bot.env (0600) — fill it in before starting the agent"
fi

[ -f sync.json ] || cp "$SEED_DIR/templates/sync.json" sync.json

# bot.env holds live tokens; a topic dir is a git repo because approval commits
# to it, so this must exist before the first commit, not after.
cat > .gitignore <<'EOF'
bot.env
.line-state/
__pycache__/
*.pyc
# skill symlinks point at absolute paths in the agentport checkout —
# machine-specific, and link-topic-skills.sh rebuilds them on every launch
.claude/skills/
.agents/skills/
EOF

if [ ! -d .git ]; then
  git init -q
  git add -A
  git commit -q -m "init topic: $TOPIC"
  echo "  git repo initialised"
fi

"$SEED_DIR/tools/link-topic-skills.sh" "$TOPIC_DIR"

cat <<EOF

topic '$TOPIC' ready at $TOPIC_DIR

next:
  1. fill in $TOPIC_DIR/bot.env
     (Discord: $SEED_DIR/tools/provision-discord-app.sh $TOPIC_DIR)
  2. edit sync.json if you want repo file ingest
  3. start it with your launcher, or the systemd units in $SEED_DIR/deploy/systemd/

memory is written only through the approval flow — see docs/approval-model.md
EOF
