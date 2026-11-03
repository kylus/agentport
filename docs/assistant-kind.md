# The assistant kind

A topic agent answers about one subject from approved memory. An assistant is
the other shape people end up wanting from the same plumbing: one long-lived
agent the owner can DM for anything — look something up, run a command, edit
a file, draft a reply. Same launcher, same adapters, different persona and a
much narrower door.

```bash
tools/create-topic.sh assistant --kind assistant      # → ~/workspace/topic-assistant
# fill in bot.env: DISCORD_BOT_TOKEN, OWNER_DISCORD_USER_ID
tools/run-topic.sh assistant
```

## What changes from a topic

| | topic (default) | assistant |
|---|---|---|
| Persona | `templates/CLAUDE.md`: memory-only expert | `templates/CLAUDE.assistant.md`: general assistant, asks before outward actions |
| Channels | any of Slack / Discord / LINE | Discord only |
| Who can talk to it | owner + contributors, per adapter access lists | owner only, DM; enforced at launch |
| `access.json` reload | live, per message | `DISCORD_ACCESS_MODE=static` (read once at boot) |
| `.git/hooks/post-commit` auto-push | installed | not installed |
| Extra working dirs | — | `ASSISTANT_ADD_DIRS` (colon-separated) |

`AGENTPORT_KIND=assistant` in `bot.env` is what `run-topic.sh` keys on. A
`bot.env` without it is a topic, so every topic created before this existed
starts exactly as before — `tests/test_launch.py` pins that with golden files.

## The launch guard

`run-topic.sh` refuses to start an assistant (exit 2, nothing printed for
`--print-argv`) unless all of these hold after `access.json` is seeded:

| Rule | Why |
|---|---|
| Discord on; Slack and LINE off | Their group defaults admit anyone in the group; one access model is easier to get right than three |
| `dmPolicy` is `allowlist` | `pairing` hands codes to strangers |
| top-level `allowFrom` is exactly `[owner]` | That list governs DMs |
| every `groups[id].allowFrom` is exactly `[owner]` | A guild channel is gated by its own list, and an empty or missing list admits every member of the channel. Top-level `allowFrom` does not apply there |
| `.mcp.json` has only `discord`, with `DISCORD_ACCESS_MODE=static` | The agent can edit files; a live-reloaded allowlist is one it could widen mid-session |

Groups are allowed if they are owner-only, but the scaffold creates none: a
reply in a channel is visible to everyone else in it.

## What the guard does not do

Be clear-eyed about this before running one.

> ⚠️ An assistant with `SKIP_PERMS=1` is a shell for whoever can send DMs as
> the owner. The guard narrows who that is; it does not limit what they get.

| Residual risk | Note |
|---|---|
| Owner chat account taken over → shell as the OS user | Turn on 2FA for the chat account. Run the assistant as a separate OS user if the host holds anything you would not hand to that account |
| `ASSISTANT_ADD_DIRS` / `--add-dir` | Convenience scope for file tools, not a boundary. The shell reaches whatever the OS user can |
| "Ask before outward actions" | A persona rule. It catches mistakes, not a determined prompt injection |
| The agent editing its own `CLAUDE.md`, hooks or `bot.env` | Same user, same files. Check `git status` in the topic and this checkout before a relaunch |
| Bot token theft | Reads every channel and DM the bot is in. Rotate on exposure |

The role gate (`hooks/role_gate.py`) stays wired: only owner messages reach the
assistant, so the role file always says `owner`, but the canary
(`echo AGENTPORT_GATE_CANARY`) should still be denied — that is how you know
the hooks are loaded at all.

Attachments: the Discord adapter refuses to send its own state directory as a
reply attachment. Pair the assistant with an adapter build that also refuses
`bot.env`, `.env` files and other `.*-state` directories.
