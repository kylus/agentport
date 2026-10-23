# Marketplace packages

Standalone skills extracted from agentport for skill marketplaces (Agensi).
Each subdirectory is self-contained: it must not reference `core/`, `hooks/`,
`memory/` or any agentport tool, so it works in Claude Code, Codex, Cursor
and other SKILL.md agents without the rest of this repo.

Build the upload zip (contents: the skill directory plus LICENSE):

```bash
tools/build-agensi-zip.sh support-thread-triage   # → dist/support-thread-triage.zip
```

| Package | Source skill | Listing |
|---|---|---|
| `support-thread-triage` | `skills/support-thread` | free; links back to this repo |

When the source skill changes, port the change by hand; the standalone copy
deliberately drops the memory and approval-flow steps.
