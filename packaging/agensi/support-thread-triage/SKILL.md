---
name: support-thread-triage
description: >
  Turn a messy support or incident chat thread into a structured triage —
  problem, environment, what has been tried, current hypothesis, what the
  requester expects, next step with owner, risks — and flag every sentence
  that could be read as a promise (confirmed / proposed / internal-only /
  unclear). Use when the user pastes or points at a support thread, ticket
  conversation, or incident channel and asks to summarise, triage, or hand
  it over. Triggers: "triage this thread", "summarise this support case",
  "what did we promise", "整理 support thread", "客服案件整理", "/support".
---

# support-thread-triage

Extract actionable structure from a support or incident thread, and separate
what was actually promised from what merely sounded like a promise.

"Requester" means whoever reported the problem: an external customer,
another team, or the user filing against their own infrastructure.

## Input

The thread itself, in any of these forms:

- pasted text or an exported file;
- a thread the agent can read with a chat tool it already has (Slack,
  Discord, Teams, a ticket system) — use that tool, do not ask the user to
  copy it;
- optionally, the original ticket or earlier thread if one is linked.

If no thread is available, ask for it. Do not triage from a summary of a
thread; the commitment check needs the exact wording.

## Output

Write the labels and content in the thread's main language. Keep facts,
names and quotes exactly as they appear.

```
Case: <one-line problem>
Requester: <name / org, or "unknown">
Source: <thread link or file name>

Problem:
- <what the requester reports — facts only, no speculation>

Environment:
- Product / version: <X>
- System: <OS / browser / cluster / region>
- Scale: <users / data / load, if relevant>
(or: unknown)

Already tried:
- <step, who did it, outcome>

Current hypothesis:
- <best explanation so far> — confidence: low / medium / high, and why

Requester expects:
- <what "solved" looks like from their side>
- <the timeline they expect, quoted if stated>

Next step:
- <action> · owner: <who> · by: <when, or "not set">

Risks:
- <SLA breach / churn / promise leakage / blast radius>

Related:
- <issues, PRs, earlier threads, runbooks mentioned in the thread>

Commitments:
- [confirmed] "<exact quote>" — <who>, <date>
- [proposed] "<exact quote>" — <who>
- [internal-only] "<exact quote>" — <who>
- [unclear] "<exact quote>" — <who>: <how it could be misread>
```

## Commitment classification

Classify every statement aimed at the requester, plus team statements about
what the requester will be told:

| Class | Meaning | What to do |
|---|---|---|
| `confirmed` | Both sides agreed on a deliverable, with an owner and a date | List it; suggest recording it in the team's tracker |
| `proposed` | Offered but not accepted, or no owner or date | List it; flag for the owner to confirm or withdraw |
| `internal-only` | Said inside the team, not yet relayed to the requester | List it so nobody relays it as a promise by accident |
| `unclear` | Wording a requester could hear as a promise ("we'll look into it", "should be fixed soon") | Quote it exactly and say how it could be misread |

When in doubt between two classes, pick the weaker one and say why.

## After the triage

Suggest, but do not perform, follow-up writes:

- confirmed commitments → the team's tracker or ticket;
- questions the team could not answer → an open-questions list;
- anything that changes how the product is understood → the team's notes.

Write to those places only if the user explicitly asks in this conversation.
A triage that silently edits records is the failure this skill exists to
prevent.

## What not to do

- Do not invent environment details. Write "unknown".
- Do not upgrade `proposed` to `confirmed` without the user saying so.
- Do not paraphrase inside the Commitments section. Quote.
- Do not reply to the requester. This skill produces an internal triage only.

---

Extracted from [agentport](https://github.com/kylus/agentport), where
memory writes like these go through a human approval step before anything
is saved. MIT licensed.
