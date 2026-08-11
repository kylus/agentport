#!/usr/bin/env python3
"""Minimal Codex app-server protocol probe.

This intentionally uses the stdio transport so it also works with npm-installed
Codex CLI builds, whose managed daemon command is unavailable.
"""

from __future__ import annotations

import argparse
import json
import os
import selectors
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


class ProtocolError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Reply with exactly APP_SERVER_POC_OK. Do not use tools.",
    )
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--codex", default="codex", help="Codex CLI executable")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every app-server message to stderr",
    )
    return parser.parse_args()


def send(proc: subprocess.Popen[str], message: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise ProtocolError("app-server stdin is unavailable")
    proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    proc.stdin.flush()


def main() -> int:
    args = parse_args()
    codex = shutil.which(args.codex)
    if codex is None:
        raise ProtocolError(f"Codex CLI not found: {args.codex}")

    cwd = args.cwd.expanduser().resolve()
    if not cwd.is_dir():
        raise ProtocolError(f"working directory does not exist: {cwd}")

    proc = subprocess.Popen(
        [codex, "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=cwd,
        env=os.environ.copy(),
    )
    if proc.stdout is None or proc.stderr is None:
        raise ProtocolError("app-server output pipes are unavailable")

    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + args.timeout
    responses: dict[int, dict[str, Any]] = {}
    thread_id: str | None = None
    turn_id: str | None = None
    deltas: list[str] = []
    completed: dict[str, Any] | None = None

    send(
        proc,
        {
            "method": "initialize",
            "id": 0,
            "params": {
                "clientInfo": {
                    "name": "agentport_app_server_poc",
                    "title": "agentport app-server PoC",
                    "version": "0.1.0",
                }
            },
        },
    )

    try:
        while completed is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"app-server probe exceeded {args.timeout:g}s")

            events = selector.select(timeout=min(remaining, 1.0))
            if not events:
                if proc.poll() is not None:
                    raise ProtocolError(
                        f"app-server exited early with status {proc.returncode}"
                    )
                continue

            for key, _ in events:
                line = key.fileobj.readline()
                if not line:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stderr":
                    if args.verbose:
                        print(f"app-server stderr: {line.rstrip()}", file=sys.stderr)
                    continue

                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ProtocolError(f"invalid app-server JSON: {line!r}") from exc

                if args.verbose:
                    print(json.dumps(message, ensure_ascii=False), file=sys.stderr)

                request_id = message.get("id")
                if request_id is not None and "method" in message:
                    # A read-only probe must never approve a server-initiated action.
                    send(proc, {"id": request_id, "result": {"decision": "decline"}})
                    continue
                if request_id is not None:
                    if "error" in message:
                        raise ProtocolError(
                            f"request {request_id} failed: {message['error']}"
                        )
                    responses[request_id] = message.get("result", {})

                if 0 in responses and 1 not in responses and thread_id is None:
                    send(proc, {"method": "initialized", "params": {}})
                    send(
                        proc,
                        {
                            "method": "thread/start",
                            "id": 1,
                            "params": {
                                "cwd": str(cwd),
                                "approvalPolicy": "never",
                                "sandbox": "read-only",
                                "ephemeral": True,
                            },
                        },
                    )
                    # Prevent duplicate sends while waiting for response id 1.
                    thread_id = ""

                if 1 in responses and thread_id == "":
                    thread_id = responses[1].get("thread", {}).get("id")
                    if not thread_id:
                        raise ProtocolError("thread/start response has no thread id")
                    send(
                        proc,
                        {
                            "method": "turn/start",
                            "id": 2,
                            "params": {
                                "threadId": thread_id,
                                "input": [{"type": "text", "text": args.prompt}],
                            },
                        },
                    )

                if 2 in responses and turn_id is None:
                    turn_id = responses[2].get("turn", {}).get("id")
                    if not turn_id:
                        raise ProtocolError("turn/start response has no turn id")

                method = message.get("method")
                params = message.get("params", {})
                if method == "item/agentMessage/delta":
                    deltas.append(params.get("delta", ""))
                elif method == "turn/completed":
                    completed = params
    finally:
        selector.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    status = completed.get("turn", {}).get("status")
    output = "".join(deltas)
    print(
        json.dumps(
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "status": status,
                "output": output,
            },
            ensure_ascii=False,
        )
    )
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ProtocolError, TimeoutError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
