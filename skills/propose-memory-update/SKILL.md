---
name: propose-memory-update
description: >
  Contributor flow — write a proposal to `pending/` for the owner to review.
  Contributors cannot edit `memory/*.md` directly. Use when the user requests a
  memory change but is not the topic owner (role=contributor).
  Triggers: "propose update", "建議補上", "我想加進記憶", "/propose-memory-update".
---

# propose-memory-update

Save a contributor's memory-change proposal to `pending/<timestamp>-<author>.md`
so the owner can review and approve.

## Behavior

The mechanical work (frontmatter, filename, git add/commit, notification text)
is all in `propose.py` — the LLM only decides *what* to propose.

1. Identify the user (from the `[user=U… role=contributor]` prefix on this turn).
2. Determine which section the proposal targets: one of `background`,
   `current_understanding`, `decisions`, `open_questions`, `commitments`,
   `people`. If ambiguous, ask the user.
3. Confirm the user has cited at least one source. Refuse if not — proposals
   without sources are a hard no.
4. Write the proposed text (exactly as it would land in `memory/<section>.md`)
   to `pending/.draft.md` with the Write tool.
5. Run the script — **single line, no chaining** (the contributor bash gate
   rejects chained commands but whitelists this exact invocation):

   ```
   python3 .claude/skills/propose-memory-update/propose.py --author <id> --section <section> --draft pending/.draft.md --source <url> [--source <url2>] [--why "<rationale>"] [--conflicts "<analysis vs existing memory>"]
   ```

   The script builds the frontmatter, names the file
   `pending/<ISO>-<author>-<section>.md`, consumes the draft, commits, and
   prints `file:` / `commit:` / `notify_owner:` lines.

   It refuses to run outside a topic directory (one with `pending/` and
   `memory/`), so a wrong cwd fails with a pointed message rather than a
   bare `FileNotFoundError`.

6. Reply to the user with the filename + commit SHA from the script output, and
   note that the owner must approve before it becomes memory.

7. **Notify the owner** so they don't miss the proposal. How depends on the
   channel the proposal arrived on:

   - **Discord / LINE (or TUI)**: mention the owner in your reply on the same
     channel — prefix the script's `notify_owner:` line with
     `<@$OWNER_DISCORD_USER_ID> `. Neither adapter has a
     send-to-arbitrary-channel tool, so an in-channel mention is the
     notification.

   - **Slack** (only when the external Slack adapter is wired in): DM the owner
     directly. Env vars are set by the launcher from `bot.env`:
     `$SLACK_BOT_TOKEN`, `$OWNER_SLACK_USER_ID`. The contributor's id is in the
     inbound MCP notification's `meta.user_id`.

     The role hook restricts curl to a fixed JSON template targeting
     `OWNER_SLACK_USER_ID`. Substitute `<filename>` and `<one-line>` literally
     (no shell substitution other than the env vars above):

     ```
     curl -s -X POST https://slack.com/api/chat.postMessage \
       -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
       -H "Content-Type: application/json; charset=utf-8" \
       -d "{\"channel\":\"$OWNER_SLACK_USER_ID\",\"text\":\"📥 新提案 <filename>：<one-line>\\n來自 <@$SLACK_USER_ID>\\n回 '@<bot> approve <filename>' 或 '@<bot> reject <filename> <理由>'\"}"
     ```

     The hook regex requires exactly this shape — chat.postMessage URL, one or
     more -H headers, a single -d JSON containing `"channel": "<OWNER_ID>"`.
     Any deviation (e.g. python3 -c, --url override, second -d) is blocked.

## When to invoke

- User says "幫我加進記憶" / "memorize this" / "propose update" / "/propose"
- User is contributor (not owner)

## What NOT to do

- Don't write directly to `memory/*.md` even if the user pushes back
- Don't accept proposals without at least one source URL
- Don't squash multiple unrelated changes into one proposal — split per section
  per topic
