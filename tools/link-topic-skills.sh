#!/usr/bin/env bash
# Rebuild a topic's Claude and Codex skill discovery dirs (idempotent).
#
# Usage:
#   tools/link-topic-skills.sh <topic-dir>
#
# Layout produced (the standard topic layout):
#   .claude/skills/<name> -> $SEED_DIR/skills/<name>     Claude generic
#   .agents/skills/<name> -> $SEED_DIR/skills/<name>     Codex generic
#   either skills/<name>  -> ../../skills/<name>          topic-local
#
# Rules:
# - Topic-local skills (in ${TOPIC_DIR}/skills/) override generic ones on
#   name collision — the local pass runs last with ln -sfn.
# - Dangling symlinks (skill deleted from agentport or the topic repo) are
#   pruned so /help never lists a dead skill.
# - A real (non-symlink) entry is left alone with a warning — someone put
#   an unmanaged skill there by hand; we don't own it.
# - Migrates the pre-2026-07 layout where .claude/skills itself was ONE
#   symlink to agentport/skills: that link is replaced by a real directory.
#
# Intended to be called by your own scaffold step and by your launcher on
# EVERY launch (same pattern as the .mcp.json regeneration), so adding a
# generic skill to this repo propagates to each topic at its next restart —
# no manual re-linking.
set -euo pipefail

SEED_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOPIC_DIR="${1:?usage: link-topic-skills.sh <topic-dir>}"
[ -d "$TOPIC_DIR" ] || { echo "error: $TOPIC_DIR not found" >&2; exit 1; }

link_for_runtime() {
  local skills_dir="$1"

  # Migrate old whole-directory symlink → real directory of per-skill links.
  if [ -L "$skills_dir" ]; then
    rm "$skills_dir"
    echo "migrated: $skills_dir whole-dir symlink → per-skill symlinks"
  fi
  mkdir -p "$skills_dir"

  # Pass 1: generic skills from agentport (absolute — the topic repo only lives
  # on this host, and an absolute target survives aliases/symlinks).
  for src in "$SEED_DIR"/skills/*/; do
    [ -d "$src" ] || continue
    name="$(basename "$src")"
    dst="$skills_dir/$name"
    if [ -e "$dst" ] && [ ! -L "$dst" ]; then
      echo "warn: $dst exists and is not a symlink — leaving it alone" >&2
      continue
    fi
    ln -sfn "${SEED_DIR}/skills/${name}" "$dst"
  done

  # Pass 2: topic-local skills. Both discovery dirs are two levels below
  # the topic root, so the same relative target works for Claude and Codex.
  if [ -d "$TOPIC_DIR/skills" ]; then
    for src in "$TOPIC_DIR"/skills/*/; do
      [ -d "$src" ] || continue
      name="$(basename "$src")"
      dst="$skills_dir/$name"
      if [ -e "$dst" ] && [ ! -L "$dst" ]; then
        echo "warn: $dst exists and is not a symlink — leaving it alone" >&2
        continue
      fi
      ln -sfn "../../skills/${name}" "$dst"
    done
  fi

  # Pass 3: prune dangling symlinks (skill removed upstream or locally).
  for link in "$skills_dir"/*; do
    [ -L "$link" ] || continue
    [ -e "$link" ] || {
      rm "$link"
      echo "pruned dead skill link: $(basename "$link")"
    }
  done
}

link_for_runtime "$TOPIC_DIR/.claude/skills"
link_for_runtime "$TOPIC_DIR/.agents/skills"

# Existing topic repos predate the Codex discovery dir and may have unrelated
# dirty .gitignore edits. Keep the machine-local links out of git without
# rewriting their tracked files; newly scaffolded repos also get .gitignore.
if git -C "$TOPIC_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  GIT_DIR="$(git -C "$TOPIC_DIR" rev-parse --absolute-git-dir)"
  EXCLUDE_FILE="$GIT_DIR/info/exclude"
  mkdir -p "$(dirname "$EXCLUDE_FILE")"
  touch "$EXCLUDE_FILE"
  if ! grep -Fqx '/.agents/skills/' "$EXCLUDE_FILE"; then
    printf '%s\n' '/.agents/skills/' >> "$EXCLUDE_FILE"
  fi
fi
