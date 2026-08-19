#!/usr/bin/env bash
# Scaffold a topic directory that the rest of agentport expects.
#
#   tools/create-topic.sh <topic-name> [target-dir]
#
# Produces (target-dir defaults to ~/workspace/topic-<name>):
#
#   topic-<name>/
#   ├── CLAUDE.md            the agent's persona and standing rules
#   ├── bot.env              channel credentials — gitignored, never committed
#   ├── sync.json            repo file sync config (core/sync/repo_file_sync.py)
#   ├── memory/              the six sections; only writable via approval
#   ├── pending/             proposals awaiting review
#   ├── sources/             snapshots pulled by repo file sync
#   ├── skills/              topic-local skills (override generic ones)
#   ├── .claude/settings.json  hook wiring — committed, so the gate travels
#   ├── .claude/skills/      symlinks, rebuilt by link-topic-skills.sh
#   ├── .agents/skills/      same, for Codex
#   └── .git/hooks/post-commit  auto-push, so approved memory reaches origin
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

# Render a template, substituting {{PLACEHOLDERS}} from the environment.
# Placeholders with nothing to fill them are left intact on purpose: a visible
# {{OWNER_DISCORD_USER_ID}} in CLAUDE.md is a to-do the owner will notice,
# whereas an empty string reads as a finished file and silently isn't.
render() {  # <template> <destination>
  TEMPLATE="$1" DEST="$2" TOPIC_NAME="$TOPIC" SEED_DIR="$SEED_DIR" python3 - <<'PY'
import os, pathlib, re
tpl = pathlib.Path(os.environ["TEMPLATE"]).read_text(encoding="utf-8")
def sub(m):
    return os.environ.get(m.group(1)) or m.group(0)
pathlib.Path(os.environ["DEST"]).write_text(
    re.sub(r"\{\{([A-Z_]+)\}\}", sub, tpl), encoding="utf-8")
PY
}

# Memory sections must match SECTIONS in skills/*/‌*.py — approval rejects an
# unknown section, so a typo here surfaces only at approve time.
for s in background current_understanding decisions open_questions commitments people; do
  if [ ! -f "memory/$s.md" ]; then
    if [ -f "$SEED_DIR/templates/memory/$s.md" ]; then
      render "$SEED_DIR/templates/memory/$s.md" "memory/$s.md"
    else
      printf '# %s\n' "$s" > "memory/$s.md"
    fi
  fi
done

# The persona. Without it the agent has no topic, no owner and no rules about
# citing sources — it is a general assistant sitting in a topic directory.
[ -f CLAUDE.md ] || render "$SEED_DIR/templates/CLAUDE.md" CLAUDE.md

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
bot.log
sessions.json
__pycache__/
*.pyc
# Whoever spoke last. Rewritten by the channel adapters on every inbound
# message and read by the hooks — pure local runtime state.
.current-role
# Adapter state: tokens, per-thread sessions, audit logs. Never commit.
.slack-state/
.discord-state/
.line-state/
# Regenerated from bot.env on every launch, and it embeds absolute paths.
.mcp.json
# skill symlinks point at absolute paths in the agentport checkout —
# machine-specific, and link-topic-skills.sh rebuilds them on every launch.
# settings.json is NOT ignored: the hook wiring should travel with the repo.
.claude/skills/
.agents/skills/
EOF

# Hook wiring. This is the file that makes approval a control rather than a
# convention — see hooks/README.md. Committed, so a clone of the topic carries
# its own gate; {{SEED_DIR}} is absolute, so re-run this script after moving
# the agentport checkout.
mkdir -p .claude
if [ ! -f .claude/settings.json ]; then
  render "$SEED_DIR/templates/claude-settings.json" .claude/settings.json
  echo "  wrote .claude/settings.json (role gate + memory-clean hooks)"
fi

if [ ! -d .git ]; then
  git init -q
  git add -A
  git commit -q -m "init topic: $TOPIC"
  echo "  git repo initialised"
fi

# Auto-push on every commit, including the ones the agent makes when approving
# memory. Installed after git init because it lives inside .git/, which means
# it is NOT carried by a clone — re-run this script on a fresh checkout.
if [ -f "$SEED_DIR/templates/hooks/post-commit" ]; then
  cp "$SEED_DIR/templates/hooks/post-commit" .git/hooks/post-commit
  chmod +x .git/hooks/post-commit
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
