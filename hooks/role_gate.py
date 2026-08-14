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

Install: see hooks/README.md. Wire it as a PreToolUse hook with an empty
matcher so it sees every tool call — an allowlist that only runs on some tools
is not an allowlist.
"""
import json
import os
import re
import shlex
import sys

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
            "permissionDecisionReason": reason,
        }}, sys.stdout)
    sys.exit(0)


def deny(reason: str) -> None:
    respond("deny", f"agentport role gate: {reason}")


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


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # 讀不懂事件就不能宣稱自己在把關，但也不該把 owner 鎖在門外
        deny("could not parse the hook payload")
        return

    if resolve_role() == "owner":
        respond(None)

    tool = event.get("tool_name") or ""
    tool_input = event.get("tool_input") or {}
    cwd = event.get("cwd") or os.getcwd()

    if tool in READ_TOOLS:
        respond(None)

    if tool == "Write":
        name = draft_target(cwd, tool_input.get("file_path") or "")
        if not name:
            deny("contributors may only write pending/.draft*.md — "
                 "memory/ and pending/ proposals are owner-only. "
                 "Write the draft, then run propose.py.")
        respond(None)

    if tool == "Bash":
        reason = check_propose_command(cwd, tool_input.get("command") or "")
        if reason:
            deny(reason)
        respond(None)

    deny(f"{tool or 'this tool'} is not available to contributors; "
         "the only write path is propose.py")


if __name__ == "__main__":
    main()
