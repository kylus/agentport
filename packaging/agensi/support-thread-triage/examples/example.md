# Example (fictional)

## Input thread

```
[09:12] Dana (Acme Retail): Since this morning our store app can't sync
        orders. Error says "token expired". About 40 stores affected.
[09:20] Sam (support): Thanks Dana, we'll look into it.
[09:41] Sam: @Lee can you check the auth service? Started after last
        night's deploy I think.
[09:58] Lee: Rolled the auth config back on staging, token refresh works
        there. Can push to prod around 11:00 if nobody objects.
[10:05] Sam: Dana, we've found a likely cause and expect a fix today.
[10:07] Dana: OK, we need orders flowing before the 12:00 lunch rush.
```

## Output

```
Case: Store app order sync fails with "token expired" after last night's deploy
Requester: Dana, Acme Retail
Source: pasted thread

Problem:
- Store app cannot sync orders since this morning; error "token expired"
- About 40 stores affected (requester's figure)

Environment:
- Product / version: store app, version unknown
- System: unknown
- Scale: ~40 stores

Already tried:
- Lee rolled back the auth config on staging; token refresh works there

Current hypothesis:
- Last night's deploy changed auth config and broke token refresh —
  confidence: medium (staging rollback fixes it; prod not yet verified)

Requester expects:
- Orders syncing again
- "before the 12:00 lunch rush"

Next step:
- Roll back auth config in prod · owner: Lee · by: ~11:00 (not yet agreed)

Risks:
- SLA: requester's deadline is 12:00; the prod rollback is planned for 11:00 with no buffer
- Promise leakage: requester was told "a fix today", but they expect one by 12:00

Related:
- last night's deploy (no link in thread)

Commitments:
- [internal-only] "Can push to prod around 11:00 if nobody objects" — Lee
- [unclear] "we expect a fix today" — Sam: requester may hear "before 12:00"; nobody confirmed that time
- [unclear] "we'll look into it" — Sam: acknowledgement, not a promise
```

Suggested follow-ups (not performed): confirm with Lee whether 11:00 is
committed, then tell Dana a specific time; log the deploy as the suspected
cause on the incident ticket.
