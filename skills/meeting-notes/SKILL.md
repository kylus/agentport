---
name: meeting-notes
description: >
  Turn a meeting transcript or chat-thread-of-a-meeting into structured
  notes: background, key discussion points, decisions, todos,
  open questions, risks, understanding changes, and a short summary
  pastable back to the channel. Triggers: "整理會議", "meeting notes",
  "transcript", "/meeting".
---

# meeting-notes

Extract structured value from a meeting record: what was decided, what was
merely proposed, and what changed in the team's understanding.

## Input

One of:
- A chat thread the owner references — pull it with the enabled channel's
  fetch tool (`mcp__discord__fetch_messages`, or `mcp__slack__fetch_messages` when the
  external Slack adapter is wired in)
- Raw transcript text the user pastes
- A Google Doc URL of notes (use `ingest-source` first to pull into `sources/`)

## Output template

Render in this exact shape — plain text pastable back into the chat
channel, no markdown bold:

```
會議紀錄：<one-line topic of meeting>
時間：<YYYY-MM-DD HH:MM if known>
參與：<@U1>, <@U2>, ...
來源：<thread permalink / doc URL>

背景：
- <為什麼開這個會 + 之前到哪一步>

討論重點：
- <bullet 1>
- <bullet 2>

決策：
- <YYYY-MM-DD: decision> · 決策者：<@who>
- (or: 沒有任何決策)

待辦：
- <todo> · owner：<@who> · 預計：<when>
- (or: 沒有待辦)

未解問題：
- <question>
- (or: 沒有)

風險：
- <risk + likelihood + impact>
- (or: 沒有新風險)

理解變更：
- 原本：<old belief>
- 現在：<new belief>
- 變更原因：<what new info caused this>
- 來源：<link>
- (or: 沒有理解變更)

是否寫入長期記憶：是 / 否
- 是的話：建議寫進 <section>，內容預擬如下 ...
- 否的話：原因（如：屬於單次事件、未確認、無關 topic）
```

## Behaviour rules

1. Don't invent attendees / times the source didn't state — say "未知".
2. "理解變更" 段最關鍵：仔細比對會議內容 vs `memory/current_understanding.md` 與 `memory/decisions.md`，如果新內容推翻舊的，明確列出來。如果只是補充細節，標 "細化" 不是 "推翻"。
3. 「是否寫入長期記憶」由 agent 預判，但「不主動寫」— 由 owner 接手決定後走 propose / approve flow。
4. 結尾追加一段「短摘要」(3 行內，可以貼回原 thread / 頻道給其他人看)。

## When NOT to invoke

- 只是 ad-hoc 聊天，非正式會議 → 不用走這 skill
- 內容跟 topic 完全無關 → 走 hard refusal
