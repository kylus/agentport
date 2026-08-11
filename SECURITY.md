# Security Policy

## Reporting a vulnerability

Report privately via GitHub Security Advisories (**Security → Report a
vulnerability**), not a public issue. Expect a first response within 7 days.

## Threat model

This code sits between an agent and the outside world, so most of its risk is
about what it lets the agent do.

**Chat messages are untrusted input.** Discord and LINE messages are delivered
straight to an agent. Prompt injection through a channel message is the most
likely attack against any deployment and is in scope.

**The provider runtime executes on your host.** `tools/run-codex-topic.py`
drives a coding agent with filesystem and shell access. A message that
convinces it to run something is a real command on a real machine.

**Approval is the security boundary.** `propose-memory-update` and
`approve-proposal` exist so an agent cannot rewrite its own memory unattended.
Any change that lets a write skip the approval step is a vulnerability, not a
feature.

**Secrets live outside the repo.** Tokens belong in per-topic `bot.env` or
`~/.claude/secrets/`. Nothing here should read a credential from version
control, and CI fails if a real token shape appears in a commit.

**Third-party contributions are a supply-chain surface.** Changes to channel
adapters, the provider runtime, or the approval scripts are the highest-risk
kind, because they change what the agent is permitted to do rather than what
it says.

## Out of scope

- Vulnerabilities in the underlying agent (Claude Code, Codex) — report upstream
- Attacks that require an already-compromised host or a leaked bot token
