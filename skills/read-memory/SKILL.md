---
name: read-memory
description: >
  Dump the current state of this topic's section-versioned memory (background,
  current_understanding, decisions, open_questions, commitments, people). Use at
  the start of every Q&A turn so the agent answers from memory, not training data.
  Triggers: "what do we know", "current state", "read memory", "目前理解",
  "現在進度", "/read-memory".
---

# read-memory

Loads the topic's memory sections so the agent can answer with cited facts.

## Behavior

Run the dump script — **one Bash call, do not read the files individually**:

```
bash .claude/skills/read-memory/dump.sh
```

It prints all six sections (`background`, `current_understanding`, `decisions`,
`open_questions`, `commitments`, `people`) under `## <section>` headers, each
with its last-commit timestamp, and `(尚未有任何條目)` for empty sections.

## When to invoke

- Every Q&A turn (auto-load at start of conversation)
- When user asks "what do we know about X"
- Before writing memory (to avoid duplicating existing entries)
- Before answering — never speak from training data alone

## What NOT to do

- Don't read memory/*.md one file at a time — that's 12 tool calls for what
  the script does in one
- Don't paraphrase — use the raw markdown so citations stay verbatim
- Don't filter by relevance — let the agent decide what to use
- Don't fetch sources/ — that's for `ingest-source`, not for normal Q&A
