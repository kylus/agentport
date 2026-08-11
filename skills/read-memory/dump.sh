#!/usr/bin/env bash
# One-shot dump of every memory section + per-section git freshness stamp.
# Replaces ~12 LLM tool calls (6 reads + 6 git logs) with a single Bash call.
set -euo pipefail
cd "${TOPIC_DIR:-$PWD}"

sections=(background current_understanding decisions open_questions commitments people)

for s in "${sections[@]}"; do
  path="memory/${s}.md"
  stamp="$(git log -1 --format='%ai %an' -- "$path" 2>/dev/null || true)"
  echo "## ${s}${stamp:+ · 最後更新 ${stamp}}"
  echo
  # "Real content" = at least one line that is not blank / not an HTML comment.
  if [ -f "$path" ] && grep -qEv '^\s*$|^\s*<!--|^\s*-->|^\s*#' "$path"; then
    cat "$path"
  else
    echo "(尚未有任何條目)"
  fi
  echo
done
