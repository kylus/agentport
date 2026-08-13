#!/usr/bin/env python3
"""propose.py — mechanical half of the propose-memory-update skill.

Contributor-safe by construction: it only ever creates one new file under
pending/ (frontmatter built here, not by the LLM), deletes the draft it
consumed, and commits exactly those paths. The PreToolUse role hook
whitelists this exact invocation shape for contributors.

Usage (single line — contributor bash cannot chain commands):

  python3 .claude/skills/propose-memory-update/propose.py \
      --author 65846... --section decisions \
      --draft pending/.draft.md --source https://... [--source ...] \
      [--why "rationale"] [--conflicts "analysis"]

The LLM's remaining jobs: pick the section, write the draft content to
pending/.draft.md (Write tool — allowed on pending/), and relay the
notification line this script prints.
"""
import argparse
import datetime
import os
import pathlib
import re
import subprocess
import sys

SECTIONS = {"background", "current_understanding", "decisions",
            "open_questions", "commitments", "people"}


def die(msg: str):
    sys.exit(f"propose.py: {msg}")


def sh(*args: str) -> str:
    # returncode 由下面自行判斷並給出可讀訊息，所以不讓 subprocess 自己丟
    r = subprocess.run(args, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        die(f"{' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def require_topic_dir():
    """兩支腳本都必須在 topic 目錄裡執行（要有 pending/ 與 memory/）。

    少了這一段，從別的地方跑會得到 FileNotFoundError: 'pending'——
    對第一次用的人完全沒有指向性。
    """
    missing = [d for d in ("pending", "memory") if not os.path.isdir(d)]
    if missing:
        die(f"必須在 topic 目錄裡執行（缺少 {', '.join(d + '/' for d in missing)}）。"
            f"目前在 {os.getcwd()}；用 tools/create-topic.sh 建立一個。")


def main():
    ap = argparse.ArgumentParser(prog="propose.py")
    ap.add_argument("--author", required=True,
                    help="contributor id from the inbound meta (U… / snowflake)")
    ap.add_argument("--section", required=True, choices=sorted(SECTIONS))
    ap.add_argument("--draft", required=True,
                    help="path under pending/ holding the proposed text")
    ap.add_argument("--source", action="append", default=[], required=True)
    ap.add_argument("--why", default="")
    ap.add_argument("--conflicts", default="")
    a = ap.parse_args()
    require_topic_dir()

    author = re.sub(r"[^A-Za-z0-9_@.-]", "", a.author)
    if not author:
        die("author id 淨化後為空")

    draft = os.path.normpath(a.draft)
    if not draft.startswith("pending" + os.sep) or ".." in draft:
        die(f"draft 必須在 pending/ 底下: {a.draft!r}")
    if not os.path.isfile(draft):
        die(f"draft 不存在: {draft}")
    content = pathlib.Path(draft).read_text(encoding="utf-8").strip()
    if not content:
        die("draft 內容為空")
    for u in a.source:
        if not re.match(r"^(https?://|sources/|memory/)", u):
            die(f"來源必須是 URL 或 repo 內引用: {u!r}")

    now = datetime.datetime.now().astimezone()
    fname = f"{now.strftime('%Y-%m-%dT%H-%M-%S')}-{author}-{a.section}.md"
    path = os.path.join("pending", fname)
    if os.path.exists(path):
        die(f"{path} 已存在")

    src_lines = "\n".join(f"  - {u}" for u in a.source)
    body = (f"---\nproposed_by: <@{author}>\n"
            f"proposed_at: {now.isoformat(timespec='seconds')}\n"
            f"section: {a.section}\nstatus: pending\nsources:\n{src_lines}\n---\n\n"
            f"## What to add / change\n\n{content}\n")
    if a.why:
        body += f"\n## Why\n\n{a.why}\n"
    if a.conflicts:
        body += f"\n## Conflicts (if any)\n\n{a.conflicts}\n"
    pathlib.Path(path).write_text(body, encoding="utf-8")
    os.remove(draft)

    summary = next((ln.strip().lstrip('#-* ')[:60]
                    for ln in content.splitlines() if ln.strip()), fname)
    sh("git", "add", path)
    # draft may or may not have been staged; stage its deletion if tracked
    # draft 不一定被 track，add 失敗是可接受的
    subprocess.run(["git", "add", draft], capture_output=True, check=False)
    sh("git", "commit", "-m", f"propose: {summary}")
    sha = sh("git", "rev-parse", "--short", "HEAD")

    print(f"file: {fname}")
    print(f"commit: {sha}")
    print("notify_owner: 📥 新提案 " + fname + f"：{summary}，"
          f"回 'approve {fname}' 或 'reject {fname} <理由>'")


if __name__ == "__main__":
    main()
