# Codex app-server PoC

This PoC checks whether a topic agent can be driven through Codex app-server
instead of Claude Code's development-channel notification mechanism.

## Target: reversible provider switching

This is a failover/switching mechanism, not a one-way migration from Claude to
Codex. The production design must support repeated `Claude → Codex → Claude`
switches:

- exactly one provider owns inbound channel traffic at a time;
- Claude keeps its own resumable session id;
- Codex keeps its own resumable app-server thread id;
- switching back resumes that provider's previous native conversation;
- cross-provider context comes from the shared topic repo memory and an
  explicit handoff, because native Claude and Codex transcripts are not
  interchangeable;
- a switch acquires a per-topic lock, stops and verifies the old provider,
  starts and health-checks the new provider, then records the active provider;
- if the new provider fails its health check, the switch automatically rolls
  back to the previous provider.

The current Discord-only PoC validates the Codex backend but is not yet that
control plane. A complete multi-channel switch also requires provider-neutral
Discord/LINE/Slack ingress. Today the Claude channel plugins live inside the
Claude process, while the PoC router only covers Discord; stopping Claude
therefore also stops its LINE and Slack ingress.

## Verified on casaos

- Codex CLI: `0.144.5` (npm installation)
- app-server stdio handshake: `initialize` → `initialized`
- thread and turn lifecycle: `thread/start` → `turn/start`
- streamed output: `item/agentMessage/delta`
- completion: `turn/completed`
- Discord REST authentication, channel lookup, and outbound message posting

The managed command `codex app-server daemon start` does **not** work with the
npm installation. It requires the standalone Codex installation managed by the
official installer. The protocol probe therefore starts the same app-server
binary over stdio and does not replace the existing Codex installation.

## Protocol probe

```bash
tools/codex-app-server-poc.py
```

The probe creates an ephemeral, read-only thread with approvals disabled. It
prints one JSON object containing `threadId`, `turnId`, `status`, and streamed
text output. Pass a prompt as the positional argument to test another turn:

```bash
tools/codex-app-server-poc.py "Reply with exactly hello."
```

## Discord polling bridge

The bridge reads the existing topic's `bot.env`, but only handles messages:

- authored by `OWNER_DISCORD_USER_ID`;
- starting with the explicit `!codex-poc` prefix;
- newer than its per-channel cursor.

It never replays channel history. The first normal run only records the newest
message as the cursor.

Check credentials and channel access without writing state:

```bash
tools/codex-discord-poc.py <topic> <DISCORD_CHANNEL_ID> --check
```

Arm the cursor without waiting:

```bash
tools/codex-discord-poc.py <topic> <DISCORD_CHANNEL_ID> --once
```

Run the bridge:

```bash
tools/codex-discord-poc.py <topic> <DISCORD_CHANNEL_ID>
```

Then send `!codex-poc Reply with exactly DISCORD_POC_OK` as the owner.

Do not leave this PoC bridge running beside the production Claude channel
bridge for general messages. The strict prefix prevents the Codex bridge from
handling ordinary traffic, but the existing Claude agent may still see the
same prefixed owner message. For an isolated live round trip, temporarily stop
`topic-agent@<topic>`, run the PoC bridge, perform the test, then restore the
service.

## Remaining production work

The original PoC creates one ephemeral app-server process/thread per Discord
message. `tools/run-codex-topic.py` now provides the production-shaped Codex
side:

- one supervised app-server subprocess for the lifetime of the provider;
- a persisted thread id at
  `~/.local/state/agentport/<topic>/codex/thread.json`;
- `thread/resume` after process restart or after switching away and back;
- polling of every Discord channel registered in
  `.discord-state/access.json`;
- per-channel cursors, first-run/no-replay arming, owner-only filtering,
  Discord message chunking, and deterministic nonces to reduce duplicate
  replies after retries;
- Codex skill discovery through `.agents/skills`, rebuilt from the same
  generic and topic-local sources as `.claude/skills`;
- a health file and singleton lock;
- `topic-agent-codex@.service`, mutually exclusive with the Claude unit;
- `tools/switch-topic-provider.sh`, with switch locking, health checks,
  boot-time enablement changes, and rollback.

The Codex provider is installed but is not enabled automatically:

```bash
bash deploy/install.sh
tools/run-codex-topic.py check <topic>
tools/run-codex-topic.py self-test <topic>
tools/switch-topic-provider.sh <topic> status
```

Switch to Codex and back:

```bash
tools/switch-topic-provider.sh <topic> codex
tools/switch-topic-provider.sh <topic> claude
```

If the topic has LINE or Slack enabled, switching to the current Discord-only
Codex provider refuses by default. The explicit
`--allow-channel-downtime` flag acknowledges that those channels will be
offline:

```bash
tools/switch-topic-provider.sh <topic> codex --allow-channel-downtime
```

This guard is intentional. The remaining shared work is provider-neutral LINE
and Slack ingress plus contributor role enforcement. Until then, the Codex
provider accepts only `OWNER_DISCORD_USER_ID` and runs with the same
non-interactive full-access posture as the existing owner-operated Claude
agent.
