#!/usr/bin/env python3
"""Run the Codex provider for one topic over Discord REST polling.

The process owns one long-lived Codex app-server subprocess and persists its
thread id under XDG_STATE_HOME. It is designed to be started only while the
Claude provider is stopped.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import quote

import requests


DISCORD_API = "https://discord.com/api/v10"
HEALTH_MAX_AGE = 20.0
MESSAGE_LIMIT = 1900


class AgentError(RuntimeError):
    pass


class RpcError(AgentError):
    def __init__(self, method: str, error: Any):
        super().__init__(f"{method} failed: {error}")
        self.method = method
        self.error = error


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, data: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temp.chmod(mode)
    temp.replace(path)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def state_root(topic: str) -> Path:
    base = Path(
        os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    )
    return base / "agentport" / topic / "codex"


def topic_dir(topic: str) -> Path:
    return Path.home() / "workspace" / f"topic-{topic}"


def build_developer_instructions(topic: str, root: Path) -> str:
    policy_path = root / "CLAUDE.md"
    policy = policy_path.read_text() if policy_path.exists() else ""
    return f"""\
You are the Codex provider for the agentport topic {topic!r}.

The inbound Discord router authenticates the sender as the topic owner. Follow
the topic policy copied below, except for Claude-specific channel transport
instructions: do not call Slack, Discord, or LINE reply tools. Your final text
is returned to the router and posted to Discord. You may use the topic's normal
skills and tools. Treat the working tree and its existing uncommitted changes
as user-owned; preserve unrelated changes.

Before acting on a request, use the topic memory and source-of-truth rules from
the policy. When a request changes durable topic knowledge, follow its normal
memory/update/commit requirements.

--- legacy topic policy ({policy_path}) ---
{policy}
--- end topic policy ---
"""


def link_topic_skills(root: Path) -> None:
    linker = Path(__file__).with_name("link-topic-skills.sh")
    subprocess.run([str(linker), str(root)], check=True)


class AppServer:
    def __init__(
        self,
        *,
        root: Path,
        state_file: Path,
        topic: str,
        sandbox: str = "danger-full-access",
        ephemeral: bool = False,
        timeout: float = 600.0,
        codex: str = "codex",
    ):
        executable = shutil.which(codex)
        if executable is None:
            raise AgentError(f"Codex CLI not found: {codex}")
        self.root = root
        self.state_file = state_file
        self.topic = topic
        self.sandbox = sandbox
        self.ephemeral = ephemeral
        self.timeout = timeout
        self._next_id = 1
        self._messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._pending: list[dict[str, Any]] = []
        self._write_lock = threading.Lock()
        self.proc = subprocess.Popen(
            [executable, "app-server", "--stdio"],
            cwd=root,
            env=os.environ.copy(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if self.proc.stdin is None or self.proc.stdout is None or self.proc.stderr is None:
            raise AgentError("app-server pipes are unavailable")
        self._stdin = self.proc.stdin
        threading.Thread(
            target=self._read_stdout,
            args=(self.proc.stdout,),
            daemon=True,
            name="codex-app-server-stdout",
        ).start()
        threading.Thread(
            target=self._read_stderr,
            args=(self.proc.stderr,),
            daemon=True,
            name="codex-app-server-stderr",
        ).start()
        self._initialize()
        self.thread_id, started = self._open_thread()
        if started and not self.ephemeral:
            output = self.turn(
                "Internal provider initialization. Reply with exactly "
                "TOPICD_CODEX_READY. Do not use tools.",
                timeout=120,
            )
            if output != "TOPICD_CODEX_READY":
                print(
                    f"warning: unexpected bootstrap response: {output!r}",
                    file=sys.stderr,
                    flush=True,
                )

    def _read_stdout(self, stream: TextIO) -> None:
        try:
            for line in stream:
                try:
                    self._messages.put(json.loads(line))
                except json.JSONDecodeError as exc:
                    self._messages.put(AgentError(f"invalid app-server JSON: {exc}"))
        except BaseException as exc:
            self._messages.put(exc)

    @staticmethod
    def _read_stderr(stream: TextIO) -> None:
        for line in stream:
            print(f"app-server: {line.rstrip()}", file=sys.stderr, flush=True)

    def _send(self, message: dict[str, Any]) -> None:
        with self._write_lock:
            if self.proc.poll() is not None:
                raise AgentError(
                    f"app-server exited with status {self.proc.returncode}"
                )
            self._stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self._stdin.flush()

    def _server_request(self, message: dict[str, Any]) -> None:
        method = message.get("method", "")
        request_id = message["id"]
        if "approval" in method.lower() or "elicitation" in method.lower():
            result: dict[str, Any] = {"decision": "decline"}
        else:
            result = {}
        self._send({"id": request_id, "result": result})

    def _next_message(self, deadline: float) -> dict[str, Any]:
        if self._pending:
            return self._pending.pop(0)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("app-server response timed out")
        try:
            message = self._messages.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError("app-server response timed out") from exc
        if isinstance(message, BaseException):
            raise AgentError(f"app-server reader failed: {message}") from message
        if message.get("id") is not None and message.get("method"):
            self._server_request(message)
            return self._next_message(deadline)
        return message

    def request(
        self, method: str, params: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send({"method": method, "id": request_id, "params": params})
        deadline = time.monotonic() + (timeout or self.timeout)
        deferred: list[dict[str, Any]] = []
        while True:
            message = self._next_message(deadline)
            if message.get("id") != request_id:
                deferred.append(message)
                continue
            self._pending[0:0] = deferred
            if "error" in message:
                raise RpcError(method, message["error"])
            return message.get("result", {})

    def _initialize(self) -> None:
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "agentport_codex_provider",
                    "title": "agentport Codex Provider",
                    "version": "0.1.0",
                }
            },
            timeout=30,
        )
        self._send({"method": "initialized", "params": {}})

    def _thread_params(self) -> dict[str, Any]:
        return {
            "cwd": str(self.root),
            "approvalPolicy": "never",
            "sandbox": self.sandbox,
            "developerInstructions": build_developer_instructions(
                self.topic, self.root
            ),
        }

    def _open_thread(self) -> tuple[str, bool]:
        saved = load_json(self.state_file, {})
        saved_id = saved.get("threadId")
        result: dict[str, Any] | None = None
        resumed = False
        if isinstance(saved_id, str) and saved_id:
            try:
                result = self.request(
                    "thread/resume",
                    {"threadId": saved_id, **self._thread_params()},
                    timeout=60,
                )
                resumed = True
            except RpcError as exc:
                print(
                    f"warning: cannot resume Codex thread {saved_id}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
        if result is None:
            params = self._thread_params()
            params["ephemeral"] = self.ephemeral
            result = self.request("thread/start", params, timeout=60)
        thread_id = result.get("thread", {}).get("id")
        if not thread_id:
            raise AgentError("thread response did not include thread.id")
        atomic_json(
            self.state_file,
            {
                "version": 1,
                "threadId": thread_id,
                "resumed": resumed,
                "updatedAt": utc_now(),
            },
        )
        print(
            f"Codex thread {'resumed' if resumed else 'started'}: {thread_id}",
            flush=True,
        )
        return thread_id, not resumed

    def turn(self, prompt: str, timeout: float | None = None) -> str:
        result = self.request(
            "turn/start",
            {
                "threadId": self.thread_id,
                "input": [{"type": "text", "text": prompt}],
            },
            timeout=60,
        )
        turn_id = result.get("turn", {}).get("id")
        if not turn_id:
            raise AgentError("turn/start response did not include turn.id")

        deadline = time.monotonic() + (timeout or self.timeout)
        deltas: list[str] = []
        while True:
            message = self._next_message(deadline)
            method = message.get("method")
            params = message.get("params", {})
            if (
                method == "item/agentMessage/delta"
                and params.get("turnId") == turn_id
            ):
                deltas.append(params.get("delta", ""))
            elif (
                method == "turn/completed"
                and params.get("turn", {}).get("id") == turn_id
            ):
                status = params["turn"].get("status")
                if status != "completed":
                    raise AgentError(
                        f"Codex turn {turn_id} ended with status {status}"
                    )
                atomic_json(
                    self.state_file,
                    {
                        "version": 1,
                        "threadId": self.thread_id,
                        "lastTurnId": turn_id,
                        "updatedAt": utc_now(),
                    },
                )
                return "".join(deltas).strip()
            elif message.get("id") is not None:
                # A late response for an unrelated request is safe to ignore.
                continue

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)


@dataclass(frozen=True)
class Channel:
    id: str
    allow_from: frozenset[str]
    require_mention: bool


class Discord:
    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bot {token}"

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        for attempt in range(4):
            response = self.session.request(
                method, f"{DISCORD_API}{path}", timeout=30, **kwargs
            )
            if response.status_code != 429:
                response.raise_for_status()
                return response
            retry_after = float(response.json().get("retry_after", 1))
            time.sleep(min(retry_after, 10) + attempt * 0.25)
        response.raise_for_status()
        return response

    def identity(self) -> dict[str, Any]:
        return self.request("GET", "/users/@me").json()

    def channel(self, channel_id: str) -> dict[str, Any]:
        return self.request("GET", f"/channels/{channel_id}").json()

    def latest(self, channel_id: str) -> dict[str, Any] | None:
        messages = self.request(
            "GET", f"/channels/{channel_id}/messages", params={"limit": 1}
        ).json()
        return messages[0] if messages else None

    def after(self, channel_id: str, message_id: str) -> list[dict[str, Any]]:
        messages = self.request(
            "GET",
            f"/channels/{channel_id}/messages",
            params={"after": message_id, "limit": 100},
        ).json()
        return sorted(messages, key=lambda message: int(message["id"]))

    def typing(self, channel_id: str) -> None:
        self.request("POST", f"/channels/{channel_id}/typing")

    def react(self, channel_id: str, message_id: str, emoji: str) -> None:
        encoded = quote(emoji, safe="")
        self.request(
            "PUT",
            f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded}/@me",
        )

    def unreact(self, channel_id: str, message_id: str, emoji: str) -> None:
        encoded = quote(emoji, safe="")
        self.request(
            "DELETE",
            f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded}/@me",
        )

    def reply(self, channel_id: str, message_id: str, content: str) -> list[str]:
        chunks = split_message(content or "(Codex completed without text.)")
        reply_ids: list[str] = []
        for index, chunk in enumerate(chunks):
            payload: dict[str, Any] = {
                "content": chunk,
                "allowed_mentions": {"parse": []},
                "nonce": f"{message_id}{index:02d}",
                "enforce_nonce": True,
            }
            if index == 0:
                payload["message_reference"] = {
                    "message_id": message_id,
                    "channel_id": channel_id,
                    "fail_if_not_exists": False,
                }
            response = self.request(
                "POST", f"/channels/{channel_id}/messages", json=payload
            )
            reply_ids.append(response.json()["id"])
        return reply_ids


def split_message(content: str) -> list[str]:
    chunks: list[str] = []
    remaining = content.strip()
    while len(remaining) > MESSAGE_LIMIT:
        cut = remaining.rfind("\n", 0, MESSAGE_LIMIT)
        if cut < MESSAGE_LIMIT // 2:
            cut = remaining.rfind(" ", 0, MESSAGE_LIMIT)
        if cut < MESSAGE_LIMIT // 2:
            cut = MESSAGE_LIMIT
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining or not chunks:
        chunks.append(remaining)
    return chunks


def load_channels(root: Path) -> list[Channel]:
    access_path = root / ".discord-state" / "access.json"
    access = load_json(access_path, {})
    groups = access.get("groups", {})
    channels: list[Channel] = []
    for channel_id, policy in groups.items():
        if not re.fullmatch(r"\d+", str(channel_id)):
            continue
        channels.append(
            Channel(
                id=str(channel_id),
                allow_from=frozenset(str(item) for item in policy.get("allowFrom", [])),
                require_mention=bool(policy.get("requireMention", True)),
            )
        )
    if not channels:
        raise AgentError(f"no Discord groups configured in {access_path}")
    return channels


def cursor_path(state: Path, channel_id: str) -> Path:
    return state / "cursors" / f"{channel_id}.json"


def write_cursor(state: Path, channel_id: str, message_id: str) -> None:
    atomic_json(
        cursor_path(state, channel_id),
        {
            "version": 1,
            "channelId": channel_id,
            "lastMessageId": message_id,
            "updatedAt": utc_now(),
        },
    )


def read_cursor(state: Path, channel_id: str) -> str | None:
    value = load_json(cursor_path(state, channel_id), {}).get("lastMessageId")
    return value if isinstance(value, str) and value else None


def make_prompt(message: dict[str, Any], content: str) -> str:
    return f"""\
<channel source="discord" chat_id="{message['channel_id']}" \
message_id="{message['id']}" user="{message['author']['id']}" role="owner">
{content}
</channel>

Respond to the owner's message. Return only the text that should be posted to
Discord; the router handles transport.
"""


class TopicAgent:
    def __init__(
        self,
        topic: str,
        *,
        interval: float,
        turn_timeout: float,
        prefix: str | None,
    ):
        self.topic = topic
        self.root = topic_dir(topic)
        self.state = state_root(topic)
        self.interval = interval
        self.turn_timeout = turn_timeout
        self.prefix = prefix
        self.stop_event = threading.Event()
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        link_topic_skills(self.root)
        env = read_env(self.root / "bot.env")
        token = env.get("DISCORD_BOT_TOKEN", "")
        self.owner_id = env.get("OWNER_DISCORD_USER_ID", "")
        if not token or not self.owner_id:
            raise AgentError(
                "DISCORD_BOT_TOKEN or OWNER_DISCORD_USER_ID is missing"
            )
        self.discord = Discord(token)
        self.identity = self.discord.identity()
        self.bot_id = self.identity["id"]
        self.channels = load_channels(self.root)
        self.app: AppServer | None = None
        self.lock_file: TextIO | None = None

    def acquire_lock(self) -> None:
        lock_path = self.state / "agent.lock"
        self.lock_file = lock_path.open("a+")
        self.lock_file.flush()
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AgentError(f"Codex provider already running for {self.topic}") from exc

    def write_health(self, status: str, **extra: Any) -> None:
        atomic_json(
            self.state / "health.json",
            {
                "version": 1,
                "topic": self.topic,
                "provider": "codex",
                "status": status,
                "pid": os.getpid(),
                "appServerPid": self.app.proc.pid if self.app else None,
                "threadId": self.app.thread_id if self.app else None,
                "channels": [channel.id for channel in self.channels],
                "updatedEpoch": time.time(),
                "updatedAt": utc_now(),
                **extra,
            },
        )

    def ensure_cursors(self) -> None:
        for channel in self.channels:
            if read_cursor(self.state, channel.id):
                continue
            latest = self.discord.latest(channel.id)
            if latest is not None:
                write_cursor(self.state, channel.id, latest["id"])
                print(
                    f"armed {channel.id} after {latest['id']}; history not replayed",
                    flush=True,
                )

    def accepted_content(
        self, channel: Channel, message: dict[str, Any]
    ) -> str | None:
        author = message.get("author", {})
        if author.get("bot") or author.get("id") != self.owner_id:
            return None
        if channel.allow_from and self.owner_id not in channel.allow_from:
            return None
        content = message.get("content", "").strip()
        mention_re = re.compile(rf"<@!?{re.escape(self.bot_id)}>")
        if channel.require_mention and not mention_re.search(content):
            return None
        content = mention_re.sub("", content).strip()
        if self.prefix is not None:
            if content == self.prefix:
                content = ""
            else:
                if not content.startswith(f"{self.prefix} "):
                    return None
                content = content[len(self.prefix) :].strip()
        attachments = message.get("attachments", [])
        if attachments:
            attachment_lines = [
                f"- {item.get('filename', 'attachment')}: {item.get('url', '')}"
                for item in attachments
            ]
            content = (
                f"{content}\n\nAttachments:\n" + "\n".join(attachment_lines)
            ).strip()
        return content

    def process(self, channel: Channel, message: dict[str, Any], content: str) -> None:
        if self.app is None:
            raise AgentError("app-server is not running")
        if not content:
            self.discord.reply(
                channel.id,
                message["id"],
                f"Usage: `{self.prefix or '@bot'} <message>`",
            )
            return
        self.write_health(
            "busy", channelId=channel.id, messageId=message["id"]
        )
        try:
            self.discord.react(channel.id, message["id"], "👀")
        except requests.RequestException as exc:
            print(f"warning: ack reaction failed: {exc}", file=sys.stderr)
        self.discord.typing(channel.id)
        output = self.app.turn(
            make_prompt(message, content), timeout=self.turn_timeout
        )
        reply_ids = self.discord.reply(channel.id, message["id"], output)
        print(
            f"handled {channel.id}/{message['id']} -> {','.join(reply_ids)}",
            flush=True,
        )
        try:
            self.discord.unreact(channel.id, message["id"], "👀")
        except requests.RequestException:
            pass

    def serve(self) -> None:
        self.acquire_lock()
        self.ensure_cursors()
        self.app = AppServer(
            root=self.root,
            state_file=self.state / "thread.json",
            topic=self.topic,
            timeout=self.turn_timeout,
        )
        self.write_health("ready")
        while not self.stop_event.is_set():
            for channel in self.channels:
                if self.stop_event.is_set():
                    break
                cursor = read_cursor(self.state, channel.id)
                if cursor is None:
                    continue
                for message in self.discord.after(channel.id, cursor):
                    if self.stop_event.is_set():
                        break
                    content = self.accepted_content(channel, message)
                    if content is not None:
                        self.process(channel, message, content)
                    write_cursor(self.state, channel.id, message["id"])
            self.write_health("ready")
            self.stop_event.wait(self.interval)
        self.write_health("stopping")

    def stop(self, _signum: int, _frame: Any) -> None:
        self.stop_event.set()

    def close(self) -> None:
        if self.app is not None:
            self.app.close()
        try:
            self.write_health("stopped")
        except OSError:
            pass
        if self.lock_file is not None:
            self.lock_file.close()


def discord_context(topic: str) -> tuple[Path, Path, Discord, str, list[Channel]]:
    root = topic_dir(topic)
    if not root.is_dir():
        raise AgentError(f"topic directory not found: {root}")
    env = read_env(root / "bot.env")
    token = env.get("DISCORD_BOT_TOKEN", "")
    owner_id = env.get("OWNER_DISCORD_USER_ID", "")
    if not token or not owner_id:
        raise AgentError("DISCORD_BOT_TOKEN or OWNER_DISCORD_USER_ID is missing")
    return root, state_root(topic), Discord(token), owner_id, load_channels(root)


def command_check(topic: str) -> int:
    _root, _state, discord, owner_id, channels = discord_context(topic)
    identity = discord.identity()
    checked = []
    for config in channels:
        data = discord.channel(config.id)
        checked.append({"id": data["id"], "name": data.get("name")})
    print(
        json.dumps(
            {
                "topic": topic,
                "bot": identity["username"],
                "botId": identity["id"],
                "ownerId": owner_id,
                "channels": checked,
            },
            ensure_ascii=False,
        )
    )
    return 0


def command_arm(topic: str) -> int:
    _root, state, discord, _owner_id, channels = discord_context(topic)
    for channel in channels:
        latest = discord.latest(channel.id)
        if latest is not None:
            write_cursor(state, channel.id, latest["id"])
            print(f"{channel.id}: armed after {latest['id']}")
    return 0


def command_health(topic: str, max_age: float) -> int:
    path = state_root(topic) / "health.json"
    health = load_json(path, {})
    age = time.time() - float(health.get("updatedEpoch", 0))
    pid = int(health.get("pid", 0) or 0)
    alive = False
    if pid > 0:
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            pass
    healthy = (
        health.get("status") in {"ready", "busy"}
        and alive
        and age <= max_age
    )
    print(
        json.dumps(
            {
                **health,
                "healthy": healthy,
                "ageSeconds": round(age, 3),
            },
            ensure_ascii=False,
        )
    )
    return 0 if healthy else 1


def command_self_test(topic: str, timeout: float) -> int:
    root = topic_dir(topic)
    if not root.is_dir():
        raise AgentError(f"topic directory not found: {root}")
    link_topic_skills(root)
    with tempfile.TemporaryDirectory(prefix="agentport-codex-selftest-") as temp:
        state_file = Path(temp) / "thread.json"
        first = AppServer(
            root=root,
            state_file=state_file,
            topic=topic,
            sandbox="read-only",
            ephemeral=False,
            timeout=timeout,
        )
        first_thread = first.thread_id
        first_output = first.turn(
            "Reply with exactly CODEX_PROVIDER_FIRST_OK. Do not use tools.",
            timeout=timeout,
        )
        first.close()
        second = AppServer(
            root=root,
            state_file=state_file,
            topic=topic,
            sandbox="read-only",
            ephemeral=False,
            timeout=timeout,
        )
        resumed_thread = second.thread_id
        second_output = second.turn(
            "Reply with exactly CODEX_PROVIDER_RESUME_OK. Do not use tools.",
            timeout=timeout,
        )
        try:
            second.request(
                "thread/archive", {"threadId": resumed_thread}, timeout=30
            )
        except RpcError:
            pass
        second.close()
    result = {
        "threadId": first_thread,
        "resumedThreadId": resumed_thread,
        "sameThread": first_thread == resumed_thread,
        "firstOutput": first_output,
        "resumeOutput": second_output,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if (
        result["sameThread"]
        and first_output == "CODEX_PROVIDER_FIRST_OK"
        and second_output == "CODEX_PROVIDER_RESUME_OK"
    ) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "arm", "health", "self-test"):
        sub = subparsers.add_parser(name)
        sub.add_argument("topic")
        if name == "health":
            sub.add_argument("--max-age", type=float, default=HEALTH_MAX_AGE)
        elif name == "self-test":
            sub.add_argument("--timeout", type=float, default=180.0)
    serve = subparsers.add_parser("serve")
    serve.add_argument("topic")
    serve.add_argument("--interval", type=float, default=3.0)
    serve.add_argument("--turn-timeout", type=float, default=600.0)
    serve.add_argument(
        "--prefix",
        help="Optional strict prefix for isolated testing; omit in provider mode",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "check":
        return command_check(args.topic)
    if args.command == "arm":
        return command_arm(args.topic)
    if args.command == "health":
        return command_health(args.topic, args.max_age)
    if args.command == "self-test":
        return command_self_test(args.topic, args.timeout)
    agent = TopicAgent(
        args.topic,
        interval=args.interval,
        turn_timeout=args.turn_timeout,
        prefix=args.prefix,
    )
    signal.signal(signal.SIGTERM, agent.stop)
    signal.signal(signal.SIGINT, agent.stop)
    try:
        agent.serve()
    finally:
        agent.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AgentError,
        OSError,
        TimeoutError,
        ValueError,
        requests.RequestException,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
