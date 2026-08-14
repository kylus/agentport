#!/usr/bin/env python3
"""role_gate.py — the PreToolUse hook that docs/approval-model.md assumes.

Without this, the approval flow is a convention: `proposal.py` deliberately
does not check roles, so anyone who can reach a shell can approve their own
proposal. This is the piece that turns it into a control.

    owner        → every tool passes through untouched
    contributor  → default-deny, with one legal write shape:

        Write  pending/.draft*.md
        Bash   python3 <…>/propose-memory-update/propose.py --author … \
                   --section … --draft pending/.draft.md --source … [--why …]

    Read / Grep / Glob are unrestricted for both — reads were never the
    problem.

Two decisions worth understanding before you adapt this:

**The role comes from the process environment, never from the command.**
`AGENTPORT_ROLE` is read out of the hook's own environment, which it inherits
from whatever launched the agent. A model that writes `AGENTPORT_ROLE=owner
python3 …` sets that variable for the command, not for this process, so it
changes nothing. Never resolve the role from anything the model can type.

**Unset means contributor.** A missing, empty or unreadable role fails closed.
A gate that defaults to "allow everything" when misconfigured is worse than no
gate, because it looks installed.

Install: see hooks/README.md. Wire it as a `PreToolUse` hook with matcher `*`
so it sees every tool call — an allowlist that only runs on some tools is not
an allowlist.

Then check it is actually running, because a hook that is installed but not
firing looks exactly like one that is working:

    python3 role_gate.py --self-test

That reports the resolved role, the wiring it can find, all the rules run
through the same `decide()` the hook uses, and — the only real evidence —
when the gate last fired.
"""
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time

SECTIONS = {"background", "current_understanding", "decisions",
            "open_questions", "commitments", "people"}

# 唯讀工具：approval model 明講「讀不設限，寫才需要人」
READ_TOOLS = {"Read", "Grep", "Glob", "NotebookRead"}

PROPOSE_FLAGS = {"--author", "--section", "--draft", "--source",
                 "--why", "--conflicts"}

# 這些字元讓一行指令變成兩件事。整個 propose.py 要求單行呼叫，就是為了讓
# 這裡能比對「一整條指令」而不是「開頭長得像」——留下任何一個都等於白做。
CHAIN_CHARS = ";&|<>`\n\r"

AUTHOR_RE = re.compile(r"^[A-Za-z0-9_@.-]+$")
SOURCE_RE = re.compile(r"^(https?://|sources/|memory/)")
# 只准 .draft*.md：直接寫 pending/2026-…-decisions.md 就等於自己捏造
# frontmatter（proposed_by、sources 全由寫的人決定），完全繞過 propose.py。
# 而 proposal.py 的 pending_path() 拒絕以 . 開頭的檔名，所以草稿也不可能
# 被誤當成提案批准。
DRAFT_RE = re.compile(r"^\.draft[A-Za-z0-9_.-]*\.md$")
# venv 給的直譯器叫 python3.11 / python3.14，寫死 "python3" 會讓合法呼叫被擋。
# 放寬直譯器名字不會鬆掉什麼——真正被把關的是後面那個腳本路徑。
PYTHON_RE = re.compile(r"^python(3(\.\d+)?)?$")

# 放進任何一個工具參數裡就會被擋下，兩種角色都一樣。用途只有一個：
# 從 agent 自己的工具迴圈裡證明這個 hook 真的在線上。
CANARY = "AGENTPORT_GATE_CANARY"


def respond(decision: str | None, reason: str = "") -> None:
    """Emit the hook verdict and exit.

    None 代表「這不歸我管」——安靜退場，讓使用者原本的權限設定照常決定。
    這裡刻意永遠不回 "allow"：allow 會蓋掉使用者自己的權限規則，一個
    安全閘門不該順手幫忙放寬別的東西。
    """
    if decision is not None:
        json.dump({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": f"agentport role gate: {reason}",
        }}, sys.stdout)
    sys.exit(0)


def deny(reason: str) -> None:
    respond("deny", reason)


def beacon_path() -> str:
    """證據放在 topic 目錄外面：那是個 git repo，多一個每次工具呼叫都在變的
    檔案只會變成雜訊，而且草稿之外的東西本來就不該出現在那裡。"""
    state = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(state, "agentport", "gate-last-fired")


def touch_beacon() -> None:
    """每次被呼叫就留一個時間戳——「它到底有沒有在跑」的唯一直接證據。

    盡力而為：寫不進去也絕不讓 hook 失敗（那會把 agent 卡死）。代價是
    liveness 會低報成「從沒觸發」，那個方向是安全的——看起來壞掉，不會
    看起來沒事。
    """
    try:
        p = beacon_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(f"{time.time():.0f}\n")
    except OSError:
        pass


def resolve_role() -> str:
    """owner / contributor，取自環境，不取自模型講的話。

    順序：AGENTPORT_ROLE → AGENTPORT_ROLE_FILE → contributor（fail closed）。
    """
    role = (os.environ.get("AGENTPORT_ROLE") or "").strip().lower()
    if not role:
        path = os.environ.get("AGENTPORT_ROLE_FILE") or ""
        if path:
            try:
                with open(path, encoding="utf-8") as fh:
                    role = fh.read().strip().lower()
            except OSError:
                role = ""
    return role if role in ("owner", "contributor") else "contributor"


def topic_root(cwd: str) -> str | None:
    """topic 目錄＝同時有 pending/ 與 memory/ 的那個目錄。

    不往上找父目錄：兩支腳本都要求「就在 topic 目錄裡執行」，而 contributor
    不能 cd（cd 需要串接，上面已經擋掉），所以 agent 本來就得從這裡啟動。
    """
    if os.path.isdir(os.path.join(cwd, "pending")) and \
       os.path.isdir(os.path.join(cwd, "memory")):
        return os.path.realpath(cwd)
    return None


def draft_target(cwd: str, raw: str) -> str | None:
    """把要寫入的路徑解析成 pending/ 底下的草稿檔名，不合法回 None。

    比對 realpath 而不是字串前綴：pending/x -> ../../etc 這種 symlink
    在字串上看起來完全合法。父目錄用 realpath 是因為草稿檔本身還不存在。
    """
    root = topic_root(cwd)
    if not root or not raw:
        return None
    target = raw if os.path.isabs(raw) else os.path.join(cwd, raw)
    parent = os.path.realpath(os.path.dirname(target))
    if parent != os.path.join(root, "pending"):
        return None
    name = os.path.basename(target)
    return name if DRAFT_RE.match(name) else None


def check_propose_command(cwd: str, command: str) -> str | None:
    """回傳拒絕理由；None 表示這確實是那唯一一種合法呼叫。"""
    if not topic_root(cwd):
        return (f"not a topic directory: {cwd} has no pending/ + memory/. "
                "Start the agent inside the topic directory.")
    bad = sorted({c for c in CHAIN_CHARS if c in command})
    if bad:
        return ("command chaining is not allowed for contributors "
                f"(found {' '.join(repr(c) for c in bad)}); "
                "propose.py must be a single, unchained invocation")
    if "$(" in command:
        return "command substitution is not allowed for contributors"

    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return f"could not parse command ({exc}) — refusing on principle"
    if len(tokens) < 2:
        return "the only Bash command available to contributors is propose.py"

    # tokens[0] 必須就是直譯器。FOO=bar python3 … 會讓第一個 token 帶 =，
    # 那是在給指令塞環境變數，不是在跑 propose.py。
    if not PYTHON_RE.match(os.path.basename(tokens[0])):
        return (f"contributors may only run propose.py via python3, not "
                f"{tokens[0]!r}")

    script = tokens[1]
    if os.path.basename(script) != "propose.py" or ".." in script.split(os.sep):
        return "the only Bash command available to contributors is propose.py"
    real = os.path.realpath(script if os.path.isabs(script)
                            else os.path.join(cwd, script))
    pinned = os.environ.get("AGENTPORT_PROPOSE_SCRIPT")
    if pinned:
        # 有釘就以釘的為準：skills 是 symlink 進來的，比 realpath 才有意義
        if real != os.path.realpath(pinned):
            return f"{script} is not the pinned propose.py"
    elif os.path.basename(os.path.dirname(real)) != "propose-memory-update":
        return f"{script} does not resolve to the propose-memory-update skill"
    if not os.path.isfile(real):
        return f"{script} does not exist"

    args: dict[str, list[str]] = {}
    rest = tokens[2:]
    i = 0
    while i < len(rest):
        tok = rest[i]
        if "=" in tok and tok.startswith("--"):
            flag, value = tok.split("=", 1)
            i += 1
        else:
            flag = tok
            if flag not in PROPOSE_FLAGS:
                return f"unexpected argument {tok!r}"
            if i + 1 >= len(rest):
                return f"{flag} is missing its value"
            value = rest[i + 1]
            i += 2
        if flag not in PROPOSE_FLAGS:
            return f"unexpected argument {flag!r}"
        args.setdefault(flag, []).append(value)

    for flag in ("--author", "--section", "--draft", "--source"):
        if flag not in args:
            return f"{flag} is required"
    for flag in ("--author", "--section", "--draft"):
        if len(args[flag]) != 1:
            return f"{flag} may only be given once"

    if not AUTHOR_RE.match(args["--author"][0]):
        return f"author id {args['--author'][0]!r} has characters that are not allowed"
    if args["--section"][0] not in SECTIONS:
        return (f"unknown section {args['--section'][0]!r} — "
                f"one of: {', '.join(sorted(SECTIONS))}")
    if not draft_target(cwd, args["--draft"][0]):
        return (f"--draft {args['--draft'][0]!r} must be a .draft*.md file "
                "directly under pending/")
    for src in args["--source"]:
        if not SOURCE_RE.match(src):
            return f"source {src!r} must be a URL or a sources/ / memory/ reference"
    return None


def decide(event: dict, role: str) -> tuple[str | None, str]:
    """The whole verdict, as a value. ("deny", reason) or (None, "").

    Pure on purpose: --self-test runs the same function the hook runs, rather
    than a second copy of the rules that can drift from it.
    """
    tool = event.get("tool_name") or ""
    tool_input = event.get("tool_input") or {}
    cwd = event.get("cwd") or os.getcwd()

    # canary 對 owner 也擋。它唯一的用途就是證明這個 hook 真的在迴圈裡，
    # 沒有人需要真的執行它，所以擋掉不花任何代價。
    if CANARY in json.dumps(tool_input, ensure_ascii=False):
        return "deny", (f"{CANARY}: the gate is installed and firing "
                        f"(role={role}). Nothing was wrong with that command.")

    if role == "owner":
        return None, ""
    if tool in READ_TOOLS:
        return None, ""

    if tool == "Write":
        if not draft_target(cwd, tool_input.get("file_path") or ""):
            return "deny", ("contributors may only write pending/.draft*.md — "
                            "memory/ and pending/ proposals are owner-only. "
                            "Write the draft, then run propose.py.")
        return None, ""

    if tool == "Bash":
        reason = check_propose_command(cwd, tool_input.get("command") or "")
        return ("deny", reason) if reason else (None, "")

    return "deny", (f"{tool or 'this tool'} is not available to contributors; "
                    "the only write path is propose.py")


SETTINGS_CANDIDATES = (
    ".claude/settings.json",
    ".claude/settings.local.json",
    "~/.claude/settings.json",
)


def wiring_report() -> list[str]:
    """Where this hook appears to be wired in, and whether it looks right.

    Deliberately a hint, not a verdict: Claude Code decides which settings
    files it loads and in what order, and this only reads the usual ones.
    The beacon below is the evidence — this is here to explain a "never
    fired" rather than to prove a "working".
    """
    me = os.path.realpath(__file__)
    lines, found = [], False
    for cand in SETTINGS_CANDIDATES:
        path = os.path.expanduser(cand)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            lines.append(f"  ! {cand}: unreadable ({exc})")
            continue
        for entry in (data.get("hooks") or {}).get("PreToolUse") or []:
            matcher = entry.get("matcher", "")
            for hook in entry.get("hooks") or []:
                cmd = hook.get("command", "")
                if "role_gate" not in cmd:
                    continue
                found = True
                lines.append(f"  · {cand}: matcher={matcher!r}")
                if matcher not in ("", "*"):
                    lines.append("    ! matcher is not '*' — the gate will not see "
                                 "every tool, and Write reaches memory/ without a shell")
                ref = next((os.path.expanduser(t) for t in shlex.split(cmd)
                            if "role_gate" in t), "")
                if ref and not os.path.isfile(ref):
                    lines.append(f"    ! {ref} does not exist")
                elif ref and os.path.realpath(ref) != me:
                    lines.append(f"    ! points at a different copy: {os.path.realpath(ref)}")
    if not found:
        lines.append("  ! no PreToolUse entry referencing role_gate in any of: "
                     + ", ".join(SETTINGS_CANDIDATES))
    return lines


def _scenarios(topic: str, propose: str):
    legal = (f"python3 {propose} --author U1 --section decisions "
             f"--draft pending/.draft.md --source https://example.com/a")
    ev = lambda tool, ti: {"tool_name": tool, "tool_input": ti, "cwd": topic}  # noqa: E731
    return [
        ("owner may write memory/", "owner",
         ev("Write", {"file_path": "memory/decisions.md"}), None),
        ("owner may approve", "owner",
         ev("Bash", {"command": "python3 proposal.py approve x.md"}), None),
        ("contributor may not write memory/", "contributor",
         ev("Write", {"file_path": "memory/decisions.md"}), "deny"),
        ("contributor may not forge a proposal", "contributor",
         ev("Write", {"file_path": "pending/2026-01-01-U1-decisions.md"}), "deny"),
        ("contributor may write a draft", "contributor",
         ev("Write", {"file_path": "pending/.draft.md"}), None),
        ("contributor may not approve", "contributor",
         ev("Bash", {"command": "python3 proposal.py approve x.md --sha a"}), "deny"),
        ("contributor may propose", "contributor", ev("Bash", {"command": legal}), None),
        ("contributor may not chain", "contributor",
         ev("Bash", {"command": legal + " ; id"}), "deny"),
        ("contributor gets no other tool", "contributor", ev("WebFetch", {}), "deny"),
        ("canary is denied for owners too", "owner",
         ev("Bash", {"command": f"echo {CANARY}"}), "deny"),
    ]


def self_test() -> int:
    """Answer the question a silently-missing hook makes hard: is this thing on?"""
    ok = True
    print("role_gate self-test\n")

    print("role:")
    src = ("AGENTPORT_ROLE" if os.environ.get("AGENTPORT_ROLE") else
           "AGENTPORT_ROLE_FILE" if os.environ.get("AGENTPORT_ROLE_FILE") else
           "nothing set — failing closed")
    print(f"  {resolve_role()}  (from {src})\n")

    print("wiring (a hint, not proof — see liveness):")
    for line in wiring_report():
        print(line)
        if line.lstrip().startswith("!"):
            ok = False
    print()

    print("behaviour:")
    topic = tempfile.mkdtemp(prefix="agentport-selftest-")
    try:
        os.makedirs(os.path.join(topic, "pending"))
        os.makedirs(os.path.join(topic, "memory"))
        skills = os.path.join(topic, ".claude", "skills")
        os.makedirs(skills)
        real = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                            "skills", "propose-memory-update")
        propose = ".claude/skills/propose-memory-update/propose.py"
        if os.path.isdir(real):
            os.symlink(real, os.path.join(skills, "propose-memory-update"))
        else:
            print(f"  ! cannot find the skills directory next to {__file__}; "
                  "propose.py scenarios will be reported as failures")
            ok = False
        for name, role, event, want in _scenarios(topic, propose):
            got, reason = decide(event, role)
            good = got == want
            ok = ok and good
            print(f"  {'✓' if good else '✗'} {name}"
                  + ("" if good else f"  → expected {want or 'allow'}, got "
                                     f"{got or 'allow'} ({reason})"))
    finally:
        shutil.rmtree(topic, ignore_errors=True)
    print()

    print("liveness:")
    try:
        with open(beacon_path(), encoding="utf-8") as fh:
            age = time.time() - float(fh.read().strip())
        print(f"  last fired {age:.0f}s ago  ({beacon_path()})")
        if age > 3600:
            print("  ! over an hour — if an agent has run since, the gate is not in its loop")
    except (OSError, ValueError):
        print(f"  ! never fired, or the beacon is unwritable ({beacon_path()})")
        print("    A gate that is installed but not firing looks exactly like one")
        print("    that is working. Prove it end to end from inside the agent:")
        print(f"      ask it to run:  echo {CANARY}")
        print("    That must come back denied. If it runs, the gate is not wired in.")

    print("\n" + ("PASS — the rules hold and the wiring looks right" if ok
                  else "FAIL — see the ! lines above"))
    return 0 if ok else 1


def main() -> None:
    if "--self-test" in sys.argv[1:]:
        sys.exit(self_test())

    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # 讀不懂事件就不能宣稱自己在把關，但也不該把 owner 鎖在門外
        touch_beacon()
        deny("could not parse the hook payload")
        return

    touch_beacon()
    respond(*decide(event, resolve_role()))


if __name__ == "__main__":
    main()
