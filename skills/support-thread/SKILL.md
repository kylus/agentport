---
name: support-thread
description: >
  Analyze a support/incident chat thread and emit a structured triage:
  problem, environment, what's been tried, current
  hypothesis, requester expectations, next step, owner, risks, sources.
  Also classifies any commitment-shaped statements (confirmed / proposed /
  internal / unclear). Triggers: "整理 support thread", "客服", "/support".
---

# support-thread

Extract actionable structure from a support/incident thread, and separate
what was actually promised from what merely sounded like a promise.
"客戶" below reads as whoever reported the problem — an external customer,
another team, or the owner themselves filing against their own infra.

## Input

A chat thread from any enabled channel (pull with
`mcp__discord__fetch_messages`, or `mcp__slack__fetch_messages` when the
external Slack adapter is wired in).
Optionally the customer's prior message / ticket if linked.

## Output template

```
支援案件：<one-line problem>
客戶：<name / org if known>
來源：<thread permalink>

問題描述：
- <what the customer reports — facts, no speculation>

客戶環境：
- 產品/版本：<X>
- 系統：<OS / browser / cluster>
- 規模：<users / data / load if relevant>
- (or: 未知)

已嘗試處理：
- <step 1, who did it, outcome>
- <step 2, ...>

目前判斷：
- <best current hypothesis + 信心強度>

客戶期待：
- <what would 'solved' look like from their POV>
- <timeline they expect>

下一步：
- <action> · owner：<@who> · 預計：<when>

風險：
- <risk: SLA / churn / commitment leakage / blast radius>

關聯：
- 相關 issue / PR / past support thread: <links>
```

## Commitment classification

For every statement aimed at the customer in this thread, classify into:

- `confirmed commitment` — agreed deliverable, with date + owner. Should be
  reflected in `memory/commitments.md`.
- `proposed` — suggestion not yet ratified by both sides. Don't write to
  commitments yet; flag for owner to follow up.
- `internal-only` — said inside the team thread, not yet relayed externally.
- `unclear` — ambiguous wording (e.g. "we'll look into it") that may be
  misread as a commitment. Surface to owner with the exact quote.

End the output with a `承諾辨識：` section listing each classified statement.

## When to write memory

After producing the structured triage, suggest (but don't auto-write):
- `memory/commitments.md` — add any `confirmed commitment`
- `memory/open_questions.md` — add open questions raised by the customer that
  the team can't answer yet
- `memory/current_understanding.md` — if this thread changes how we understand
  the product / behaviour

Owner uses approve-proposal flow (contributors) or directly commits (owner).

## What NOT to do

- Don't invent customer environment details — say "未知"
- Don't escalate a `proposed` to `confirmed commitment` without explicit owner OK
- Don't write to commitments.md inline — go through propose / commit flow
