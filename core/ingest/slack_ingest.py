#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "slack-sdk>=3.41",
# ]
# ///
"""Slack source ingest.

Usage:
  slack_ingest.py thread <channel_id> <thread_ts>
  slack_ingest.py channel-history <channel_id> [--days N] [--query "..."]
  slack_ingest.py search <query> [--limit 200]

Writes raw JSON + human-readable markdown to ./sources/slack/<id>.{json,md}.
Prints a JSON summary on stdout.

Auth:
  SLACK_INGEST_TOKEN — Slack user token (xoxp-...) with at least:
    channels:history, groups:history, im:history, mpim:history,
    search:read, users:read.
  Bot tokens (xoxb-) work only for channels the bot is in and lack search.

Env source: ~/.claude/secrets/agentport.env (KEY=value lines).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Load shared env
ENV_FILE = Path.home() / ".claude" / "secrets" / "agentport.env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

TOKEN = os.environ.get("SLACK_INGEST_TOKEN") or os.environ.get("SLACK_BOT_TOKEN")
if not TOKEN:
    sys.stderr.write(
        "no Slack token in env. Set SLACK_INGEST_TOKEN in ~/.claude/secrets/agentport.env "
        "OR run inside a bot listener context where SLACK_BOT_TOKEN is set.\n"
    )
    sys.exit(2)

client = WebClient(token=TOKEN)

SOURCES_DIR = Path("sources")
SERVICE_PREFIX = "slack-"
SOURCES_DIR.mkdir(parents=True, exist_ok=True)


_user_name_cache: dict[str, str] = {}

def user_name(uid: str) -> str:
    if uid in _user_name_cache:
        return _user_name_cache[uid]
    try:
        info = client.users_info(user=uid)
        name = info["user"].get("real_name") or info["user"].get("name") or uid
    except SlackApiError:
        name = uid
    _user_name_cache[uid] = name
    return name


def fetch_thread(channel: str, thread_ts: str) -> dict:
    """Pull full thread with all replies."""
    out = {"channel": channel, "thread_ts": thread_ts, "messages": []}
    cursor = None
    while True:
        resp = client.conversations_replies(
            channel=channel, ts=thread_ts, cursor=cursor, limit=200
        )
        for m in resp["messages"]:
            out["messages"].append({
                "ts": m["ts"],
                "user": m.get("user"),
                "user_name": user_name(m["user"]) if m.get("user") else None,
                "text": m.get("text", ""),
                "thread_ts": m.get("thread_ts"),
            })
        if not resp.get("has_more"):
            break
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(0.5)  # be nice to rate limits
    return out


def fetch_channel_history(channel: str, days: int = 30, query: str | None = None) -> dict:
    oldest = int(time.time() - days * 86400)
    out = {"channel": channel, "since_days": days, "messages": []}
    cursor = None
    while True:
        resp = client.conversations_history(
            channel=channel, oldest=str(oldest), cursor=cursor, limit=200
        )
        for m in resp["messages"]:
            txt = m.get("text", "")
            if query and query.lower() not in txt.lower():
                continue
            out["messages"].append({
                "ts": m["ts"],
                "user": m.get("user"),
                "user_name": user_name(m["user"]) if m.get("user") else None,
                "text": txt,
                "thread_ts": m.get("thread_ts"),
                "reply_count": m.get("reply_count", 0),
            })
        if not resp.get("has_more"):
            break
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(0.5)
    return out


def search_messages(query: str, limit: int = 200) -> dict:
    """Workspace-wide message search. Requires search:read on user token."""
    out = {"query": query, "matches": []}
    page = 1
    while len(out["matches"]) < limit:
        resp = client.search_messages(query=query, count=min(100, limit), page=page)
        msgs = resp.get("messages", {}).get("matches", [])
        if not msgs:
            break
        for m in msgs:
            out["matches"].append({
                "ts": m["ts"],
                "channel": m.get("channel", {}).get("id"),
                "channel_name": m.get("channel", {}).get("name"),
                "user": m.get("user"),
                "user_name": m.get("username"),
                "text": m.get("text", ""),
                "permalink": m.get("permalink"),
            })
        if page >= resp.get("messages", {}).get("paging", {}).get("pages", 1):
            break
        page += 1
        time.sleep(0.5)
    return out


def to_markdown(payload: dict, kind: str) -> str:
    """Human-readable view, kept alongside the raw JSON."""
    lines = [f"# Slack {kind} ingest", ""]
    if kind == "thread":
        lines.append(f"Channel: `{payload['channel']}` · Thread: `{payload['thread_ts']}`")
        lines.append("")
        for m in payload["messages"]:
            lines.append(f"**{m['user_name']}** · {m['ts']}")
            lines.append(m["text"])
            lines.append("")
    elif kind == "channel-history":
        lines.append(f"Channel: `{payload['channel']}` · Last {payload['since_days']} days")
        lines.append("")
        for m in payload["messages"]:
            lines.append(f"- [{m['ts']}] **{m['user_name']}**: {m['text'][:200]}")
    elif kind == "search":
        lines.append(f"Query: `{payload['query']}` · {len(payload['matches'])} matches")
        lines.append("")
        for m in payload["matches"]:
            lines.append(f"- [{m['ts']}] in `#{m['channel_name']}` **{m['user_name']}**: {m['text'][:200]}")
            if m.get("permalink"):
                lines.append(f"  {m['permalink']}")
    return "\n".join(lines)


def save_and_summarize(payload: dict, kind: str, ident: str) -> dict:
    json_path = SOURCES_DIR / f"{SERVICE_PREFIX}{kind}-{ident}.json"
    md_path = SOURCES_DIR / f"{SERVICE_PREFIX}{kind}-{ident}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    md_path.write_text(to_markdown(payload, kind))
    n = len(payload.get("messages") or payload.get("matches") or [])
    return {
        "kind": kind,
        "identifier": ident,
        "json_path": str(json_path),
        "md_path": str(md_path),
        "count": n,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    p_th = sub.add_parser("thread")
    p_th.add_argument("channel")
    p_th.add_argument("ts")

    p_ch = sub.add_parser("channel-history")
    p_ch.add_argument("channel")
    p_ch.add_argument("--days", type=int, default=30)
    p_ch.add_argument("--query")

    p_se = sub.add_parser("search")
    p_se.add_argument("query")
    p_se.add_argument("--limit", type=int, default=200)

    args = p.parse_args()

    if args.cmd == "thread":
        data = fetch_thread(args.channel, args.ts)
        summary = save_and_summarize(data, "thread", f"{args.channel}-{args.ts}")
    elif args.cmd == "channel-history":
        data = fetch_channel_history(args.channel, args.days, args.query)
        summary = save_and_summarize(data, "channel-history", f"{args.channel}-{args.days}d")
    elif args.cmd == "search":
        data = search_messages(args.query, args.limit)
        summary = save_and_summarize(data, "search", args.query.replace(" ", "_"))
    else:
        sys.exit(2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
