# The role hook

`docs/approval-model.md` says the approval scripts assume a `PreToolUse` hook
has already blocked non-owners. This is that hook — a reference
implementation, small enough to read in one sitting and adapt.

Without it, `proposal.py approve` is available to anyone who can reach a
shell, and the approval flow is a convention rather than a control.

## What it enforces

| Role | Reads | Writes |
|---|---|---|
| `owner` | everything | everything |
| `contributor` | `Read` / `Grep` / `Glob` | `Write pending/.draft*.md`, then one `propose.py` invocation — nothing else |

Everything not on the contributor allowlist is denied, including tools that
did not exist when this was written. Default-deny is the point: a blocklist of
known-bad commands is a list of the ones somebody thought of.

## Install

```bash
cp hooks/role_gate.py ~/.local/share/agentport/role_gate.py
```

Then in the topic's `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command",
            "command": "python3 ~/.local/share/agentport/role_gate.py" }
        ]
      }
    ]
  }
}
```

The matcher must be `*`. A hook that only runs on `Bash` is not an allowlist —
the `Write` tool reaches `memory/` without going near a shell.

Set the role where the agent is launched, not in the topic directory:

```ini
# deploy/systemd/topic-agent-*.service
Environment=AGENTPORT_ROLE=contributor
Environment=AGENTPORT_PROPOSE_SCRIPT=%h/agentport/skills/propose-memory-update/propose.py
```

`AGENTPORT_PROPOSE_SCRIPT` is optional but worth setting: it pins the exact
script by resolved path, so a lookalike `propose-memory-update/propose.py`
placed somewhere else does not satisfy the check.

If your role varies per session, point `AGENTPORT_ROLE_FILE` at a file the
launcher writes instead.

**Under herdr, set it on the server, not on the unit.** `herdr agent start`
spawns the agent in a pane owned by the herdr server, so the agent inherits the
server's environment — not `topic-agent-herdr@.service`'s. Putting
`AGENTPORT_ROLE` in that unit sets it for the launcher and never reaches the
agent, which then fails closed to `contributor` and blocks the owner. Export it
before starting the server, or use `AGENTPORT_ROLE_FILE`.

Verify it is live before trusting it:

```bash
printf '{"hook_event_name":"PreToolUse","tool_name":"Write","tool_input":{"file_path":"memory/decisions.md"},"cwd":"'"$PWD"'"}' \
  | AGENTPORT_ROLE=contributor python3 hooks/role_gate.py
```

That must print a `deny`. A hook that is installed but not firing looks
exactly like a hook that is working.

## Why the role comes from the environment

`AGENTPORT_ROLE` is read from the hook process's own environment, inherited
from whatever launched the agent. A model that runs `AGENTPORT_ROLE=owner
python3 …` sets that variable for its own command, not for this process — the
gate never sees it. Nothing the model can type reaches the role decision.

The corollary: an unset, empty or unreadable role resolves to `contributor`.
A gate that opens when misconfigured is worse than no gate, because it looks
installed.

## Why contributors get no general shell

Contributors get exactly one Bash command shape and no way to chain. That is
strict on purpose — command-string matching only works if the string is one
command. `;`, `&`, `|`, `<`, `>`, backticks, `$(`, and newlines are all
refused before parsing, and anything `shlex` cannot parse is refused too.
This is the reason `propose.py` documents a single-line invocation.

Reading is done through the `Read` tool rather than `cat`, so no general shell
is needed to reach unrestricted reads.

## Limits worth stating

- **Same-user setups are soft.** If the agent runs as the same OS user that
  owns the hook file and `.claude/settings.json`, then the *contributor* path
  cannot edit them — both are denied — but any other process running as that
  user can. Separate OS users, or a read-only mount, is what makes this hard.
- **It gates tool calls, not the model.** A contributor can still get a
  persuasive proposal in front of the owner. The gate decides what can be
  written without a human, not what the human should believe. That limit is
  covered in `docs/approval-model.md`.
- **Owners are unrestricted by design.** The contract in
  `docs/approval-model.md` says owners get everything. If you want a gate on
  the owner too, this is the wrong file to change — that is a different
  control.

## Tests

```bash
python3 -m unittest discover -s tests
```

They run the hook as a subprocess fed a real event on stdin, because a hook
that works when imported and fails when executed is still broken.
