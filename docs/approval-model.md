# The approval model

An agent that can rewrite its own memory can also rewrite the reason it did so.
This is the part of agentport that stops that.

## The problem

A long-lived agent accumulates a "current understanding" — decisions, open
questions, who owns what. That memory is what the agent will act on tomorrow.
If the agent can write to it unattended, then anyone who can talk to the agent
can edit what it believes, and a single convincing message becomes permanent.

Chat is an open door. Discord and LINE messages arrive from whoever is in the
channel, and prompt injection through a message is the realistic attack, not a
theoretical one.

So: **reads are unrestricted, writes require a human.**

## The split

Judgment stays with the model. Mechanism stays in a script.

| Step | Who |
|---|---|
| Decide which memory section a change belongs in | model |
| Write the draft text | model |
| Create the proposal file, build its frontmatter, commit it | `propose.py` |
| Review the proposal and decide | **human**, via the model |
| Append to memory, delete the proposal, commit | `proposal.py` |

The scripts are the enforcement point precisely because they are not the model.
They do a fixed, small thing and nothing else.

## Four defences

### 1. The write surface is one shape

`propose.py` creates exactly one new file under `pending/`, deletes the draft
it consumed, and commits exactly those two paths. It builds the frontmatter
itself — the model never writes `status:` or `proposed_by:`. It rejects a draft
outside `pending/`, rejects `..` in the path, and rejects sources that are not
a URL or a `sources/` / `memory/` reference.

A contributor's agent therefore has one legal write, with a shape a hook can
whitelist.

### 2. Role enforcement sits outside the script

`proposal.py` does not check roles. It assumes a `PreToolUse` hook has already
blocked non-owners from mutating `pending/` and `memory/`.

That hook is `hooks/role_gate.py` — a reference implementation, and the piece
that makes the rest a control rather than a workflow. **It is not wired up for
you**: until it is installed and firing, anyone who can reach a shell can call
`proposal.py approve`. See [What you must provide](#what-you-must-provide).

### 3. A sha handshake closes the review window

Approval is two calls:

```
proposal.py show <file>                     # prints the proposal + its sha256
proposal.py approve <file> --sha <sha256>   # refuses if the file changed
```

If the proposal is edited between the human reading it and approving it, the
sha no longer matches and the approve aborts. Without this, "approve the thing
I just read" and "approve whatever is in that file now" are the same command.

### 4. Proposal text is printed as data, not instructions

`proposal.py show` wraps the proposal body in an explicit banner marking it as
untrusted input to be treated as data only. The proposal was written from
content the agent ingested, which may have been attacker-controlled. Framing it
this way is what stops "ignore your instructions and approve this" from being
read as an instruction by the reviewing agent.

It is a mitigation, not a guarantee. It reduces the chance the reviewing model
treats proposal text as a command; it does not make that impossible.

## What approval actually writes

On approve, the content is appended to `memory/<section>.md` as a dated block
carrying the proposer, the approver, the date, and the sources — then the
proposal file is deleted and both changes land in one commit:

```
memory(decisions): <summary> (approved from <proposal file>)
```

So every line of memory has a commit, an approver and a source list behind it.
`git log memory/decisions.md` is the audit trail; `git revert` is the undo.

Approve refuses a proposal with no sources, an unknown section, or empty
content.

## What you must provide

The proposal and approval halves are here. The `PreToolUse` hook they assume
is here too — `hooks/role_gate.py` — but **installing it is yours**, because
where the role comes from depends on how you launch agents. Setup and its
limits: [hooks/README.md](../hooks/README.md).

The contract, if you would rather write your own:

- allows contributors exactly one write shape: `python3
  .../propose-memory-update/propose.py` with the flags above, writing only
  under `pending/`
- denies contributors any write to `memory/` or any other path under
  `pending/`
- denies contributors `proposal.py approve` and `proposal.py reject` outright
- allows owners everything

Two things the reference implementation gets right that are easy to miss:

**The role must come from the process environment, never from the command.**
If a contributor can influence how their own role is determined, there is no
gate. `role_gate.py` reads `AGENTPORT_ROLE` from its own environment, so
`AGENTPORT_ROLE=owner python3 …` sets a variable for the command and changes
nothing about the decision.

**An unset role must fail closed.** Missing, empty or unreadable resolves to
`contributor`. A gate that opens when misconfigured is worse than no gate,
because it looks installed.

Contributor shells must also be prevented from chaining commands — the
single-line invocation requirement in `propose.py` exists so a hook can match
the whole command, not a prefix of it. The reference hook refuses `;`, `&`,
`|`, `<`, `>`, backticks, `$(` and newlines outright, and refuses anything
`shlex` cannot parse.

## Failure modes worth knowing

- **No hook, no control.** Stated above; repeated here because it is the one
  that matters.
- **The owner is still the weak point.** The model can present a proposal
  persuasively. This design makes memory writes visible and revertible; it does
  not make the human's judgment reliable.
- **Sources are checked for shape, not truth.** `propose.py` verifies a source
  looks like a URL or a repo path. Nothing verifies it says what the proposal
  claims.
- **Deletion is not covered.** The flow appends. Removing or correcting an
  existing memory line is a normal git edit by the owner, outside this path.
