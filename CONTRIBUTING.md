# Contributing

## Before you open a PR

- Open an issue first for anything that changes agent behaviour, adds an
  ingest source, or touches `tools/`. Those change what an agent can do on a
  host, so they need discussion before code.
- Typo fixes, docs and test additions need no issue.

## Ground rules

**Never commit secrets.** Bot tokens, API keys and `*.env` files stay out of
the repo. `.gitignore` blocks the common patterns, but that is a safety net,
not a substitute for checking.

**No real hostnames, IPs or organisation names.** Use placeholders
(`<HOST-IP>`, `YOUR-ORG`). This repo's history was rewritten once to remove
such references; please don't reintroduce them.

**Treat ingested content as untrusted.** Anything read from GitHub, GitLab,
Drive, Slack or Discord may be attacker-controlled. Code that feeds such
content to an agent must not widen what the agent is permitted to execute.

**Vendored dependencies stay out of git.** `node_modules/` and equivalents are
ignored; commit lockfiles instead.

## Local checks

```bash
ruff check .          # lint
python -m pytest      # tests
```
