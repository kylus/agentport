#!/usr/bin/env python3
"""Discord REST polling bridge for the Codex app-server PoC.

Only owner-authored messages beginning with ``!codex-poc`` are handled. On the
first poll the bridge records the newest message and does not replay history.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

API = "https://discord.com/api/v10"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic", help="topic name under ~/workspace/topic-<name>")
    parser.add_argument("channel_id", help="Discord channel id to poll")
    parser.add_argument("--prefix", default="!codex-poc")
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify credentials/channel access without changing the cursor",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args()


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")
    return values


def write_cursor(path: Path, message_id: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps({"last_message_id": message_id}) + "\n")
    temp.chmod(0o600)
    temp.replace(path)


def newest_message(
    session: requests.Session, channel_id: str
) -> dict[str, Any] | None:
    response = session.get(
        f"{API}/channels/{channel_id}/messages", params={"limit": 1}, timeout=20
    )
    response.raise_for_status()
    messages = response.json()
    return messages[0] if messages else None


def fetch_after(
    session: requests.Session, channel_id: str, after: str
) -> list[dict[str, Any]]:
    response = session.get(
        f"{API}/channels/{channel_id}/messages",
        params={"after": after, "limit": 50},
        timeout=20,
    )
    response.raise_for_status()
    return sorted(response.json(), key=lambda message: int(message["id"]))


def run_codex(script: Path, cwd: Path, prompt: str, timeout: float) -> str:
    result = subprocess.run(
        [
            str(script),
            "--cwd",
            str(cwd),
            "--timeout",
            str(timeout),
            prompt,
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=timeout + 10,
    )
    payload = json.loads(result.stdout)
    if payload.get("status") != "completed":
        raise RuntimeError(f"Codex turn did not complete: {payload}")
    output = payload.get("output", "").strip()
    return output or "(Codex completed without a text response.)"


def post_reply(
    session: requests.Session, channel_id: str, message_id: str, content: str
) -> str:
    response = session.post(
        f"{API}/channels/{channel_id}/messages",
        json={
            "content": content[:2000],
            "message_reference": {
                "message_id": message_id,
                "channel_id": channel_id,
                "fail_if_not_exists": False,
            },
            "allowed_mentions": {"parse": []},
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["id"]


def main() -> int:
    args = parse_args()
    topic_dir = Path.home() / "workspace" / f"topic-{args.topic}"
    env_path = topic_dir / "bot.env"
    if not env_path.is_file():
        raise RuntimeError(f"missing topic env: {env_path}")

    env = read_env(env_path)
    token = env.get("DISCORD_BOT_TOKEN", "")
    owner_id = env.get("OWNER_DISCORD_USER_ID", "")
    if not token or not owner_id:
        raise RuntimeError("DISCORD_BOT_TOKEN or OWNER_DISCORD_USER_ID is missing")

    session = requests.Session()
    session.headers["Authorization"] = f"Bot {token}"
    identity = session.get(f"{API}/users/@me", timeout=20)
    identity.raise_for_status()
    channel = session.get(f"{API}/channels/{args.channel_id}", timeout=20)
    channel.raise_for_status()
    if args.check:
        print(
            json.dumps(
                {
                    "bot": identity.json()["username"],
                    "bot_id": identity.json()["id"],
                    "channel_id": channel.json()["id"],
                    "channel_name": channel.json().get("name"),
                },
                ensure_ascii=False,
            )
        )
        return 0

    cursor_path = (
        topic_dir
        / ".discord-state"
        / f"codex-poc-cursor-{args.channel_id}.json"
    )
    if cursor_path.exists():
        cursor = json.loads(cursor_path.read_text()).get("last_message_id")
    else:
        latest = newest_message(session, args.channel_id)
        if latest is None:
            raise RuntimeError("channel has no messages; cannot initialize cursor")
        write_cursor(cursor_path, latest["id"])
        print(f"armed after message {latest['id']}; history was not replayed")
        return 0 if args.once else poll(args, session, topic_dir, owner_id, cursor_path)

    if not cursor:
        raise RuntimeError(f"invalid cursor file: {cursor_path}")
    return poll(args, session, topic_dir, owner_id, cursor_path)


def poll(
    args: argparse.Namespace,
    session: requests.Session,
    topic_dir: Path,
    owner_id: str,
    cursor_path: Path,
) -> int:
    probe = Path(__file__).with_name("codex-app-server-poc.py")
    cursor = json.loads(cursor_path.read_text())["last_message_id"]
    while True:
        messages = fetch_after(session, args.channel_id, cursor)
        for message in messages:
            cursor = message["id"]
            author = message.get("author", {})
            content = message.get("content", "")
            if (
                author.get("id") == owner_id
                and not author.get("bot", False)
                and (
                    content == args.prefix
                    or content.startswith(f"{args.prefix} ")
                )
            ):
                prompt = content[len(args.prefix) :].strip()
                if not prompt:
                    output = f"Usage: `{args.prefix} <message>`"
                else:
                    output = run_codex(probe, topic_dir, prompt, args.timeout)
                reply_id = post_reply(
                    session, args.channel_id, message["id"], output
                )
                print(f"handled {message['id']} -> {reply_id}")
            write_cursor(cursor_path, cursor)

        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, requests.RequestException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
