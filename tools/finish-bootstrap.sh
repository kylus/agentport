#!/usr/bin/env bash
# Force a topic out of 'bootstrapping' state.
#
# If the bootstrap TUI was interrupted before completion, ingest_state.json
# keeps state='bootstrapping' forever and the bot rejects every mention with
# "正在吸收歷史…". This script flips it to 'ready' so the bot starts answering.
#
# Usage:
#   tools/finish-bootstrap.sh <topic-name>

set -euo pipefail

TOPIC_NAME="${1:?usage: finish-bootstrap.sh <topic-name>}"
TOPIC_DIR="$HOME/workspace/topic-${TOPIC_NAME}"
TOPIC_REAL="$(readlink -f "$TOPIC_DIR" 2>/dev/null || echo "$TOPIC_DIR")"
STATE_FILE="$TOPIC_REAL/ingest_state.json"

[ -r "$STATE_FILE" ] || { echo "error: $STATE_FILE not found" >&2; exit 1; }

python3 - "$STATE_FILE" <<'PY'
import json, sys, datetime
p = sys.argv[1]
s = json.load(open(p))
prev = s.get("state")
s["state"] = "ready"
s["eta"] = None
s.setdefault("log", []).append({
    "ts": datetime.datetime.now().isoformat(),
    "event": f"finish-bootstrap.sh: {prev} → ready",
})
json.dump(s, open(p, "w"), indent=2, ensure_ascii=False)
print(f"state {prev!r} → 'ready'")
PY

# If a listener is running, it'll see the new state on the next mention
# (the file is read per-mention, no caching).
echo "done. bot will now answer mentions for $TOPIC_NAME."
