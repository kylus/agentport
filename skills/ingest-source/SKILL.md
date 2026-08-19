---
name: ingest-source
description: >
  Owner-only — pull a single GitHub/GitLab item, Google Doc, local file,
  or (if the channel is enabled) Slack thread into `sources/`, then stage
  a memory-update proposal that the owner can refine and commit.
  Triggers: "ingest <url>", "吸收這個", "/ingest", URL paste from owner.
---

# ingest-source

Pulls one external source into the topic's `sources/` cache and prepares a draft memory update.

## Behavior

1. Verify owner role. Contributors cannot trigger ingest.

2. Parse the URL / path → service + identifier:
   - `github.com/<owner>/<repo>/{issues|pull|discussions|wiki}/<n>` → GitHub item
   - `gitlab.com/<group>/<repo>/-/{issues|merge_requests|wikis}/<n>` → GitLab item
   - `docs.google.com/document/d/<id>` → Google Doc
   - `*.slack.com/archives/<chan>/p<ts>` → Slack thread (only when this
     topic has the Slack channel enabled)
   - a local file path the owner points at → local file (no script; copy
     into `sources/local/` and summarize with Read)

3. Invoke the matching ingest script via Bash (each script is a PEP 723
   self-contained `uv run --script`, so just execute the file directly):
   - `core/ingest/github_ingest.py <issue|pr|discussion|wiki|repo-list> <owner/repo> [<n>]`
   - `core/ingest/gitlab_ingest.py <issue|mr|wiki|repo-list> <group/repo> [<n>]`
   - `core/ingest/drive_ingest.py doc <id>`
   - `core/ingest/slack_ingest.py thread <chan> <ts>`

   Each script:
   - fetches the source via SDK (auth via `~/.claude/secrets/agentport.env`)
   - writes raw content to `sources/<service>/<id>.json` + `sources/<service>/<id>.md` (human-readable)
   - returns a JSON summary on stdout

4. Read the ingest result. Summarize:
   - **What is this source about?**
   - **Key claims / decisions / questions raised**
   - **Cross-references to other sources** (links inside the doc/thread)
   - **Confidence**: single source = `來源單一未確認` unless other sources corroborate
   - **Conflict with existing memory?** (use read-memory output to check)

5. Generate a draft memory update — a markdown block ready to drop into one of the memory sections. Show the draft to the owner.

6. Ask: "commit as memory? / save as pending proposal / discard"
   - **commit** → append to `memory/<section>.md`, commit with `source: <url>`
   - **save as pending** → write to `pending/`
   - **discard** → leave `sources/` as is (already cached), no memory change

7. If the source contains links to other sources, **list** them but do **not** auto-recurse. Recursive ingest needs explicit owner approval (see next section).

## Recursive ingest (owner re-prompts to recurse)

If owner says "recurse" or "follow the links":

1. For each found URL:
   - If already in `ingest_state.json` → skip (mark as processed)
   - If outside owner's authorized scope (configured in `bot.env`) → mark `out-of-scope`
   - If no permission to access → mark `no-permission`
   - Otherwise → enqueue for ingest
2. Process queue serially, max depth = 3 unless owner overrides.
3. Update `ingest_state.json` with: processed URLs, skipped (reason), pending queue, depth.
4. At any point owner can say "stop recurse" — drain queue, write state, report.

## When to invoke

- Owner pastes a URL with no other instructions → ingest that URL
- Owner says "ingest <url>", "吸收這個", "/ingest"
- Owner says "recurse" after a prior ingest

## What NOT to do

- Never run if `[user=… role=contributor]`
- Never recurse without explicit owner consent
- Never silently overwrite a previously ingested source — show diff first
- Never write to `memory/` without showing the draft and getting "commit"
