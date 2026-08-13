#!/usr/bin/env python3
"""
Pull configured git repos and copy key files into the topic's sources/.

Which repos and which files come from the topic's sync.json (see
templates/sync.json), so nothing repo-specific is hardcoded here:

    {
      "repo_sync": [
        {
          "name": "network-mgmt",
          "url": "https://${GITHUB_TOKEN}@github.com/kylus/network-mgmt.git",
          "files": ["README.md", "docs/topology.md"]
        }
      ]
    }

`url` may reference ${ENV_VAR}s; they are expanded from the shared secrets
env file (~/.claude/secrets/agentport.env) so tokens never live in sync.json.
Clones on first run; does git pull on subsequent runs.

Usage:
    python3 core/sync/repo_file_sync.py --topic-dir ~/workspace/topic-<name>
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone

CACHE_ROOT = pathlib.Path.home() / ".claude" / "cache" / "repo-sync"


def load_env():
    env_file = pathlib.Path.home() / ".claude" / "secrets" / "agentport.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def sync_repo(entry: dict, topic_dir: pathlib.Path, date_str: str) -> list[str]:
    name = entry["name"]
    url = os.path.expandvars(entry["url"])
    files = entry.get("files", [])
    if not files:
        print(f"warn: repo_sync '{name}' has no files listed — skipping", file=sys.stderr)
        return []

    cache_dir = CACHE_ROOT / name
    if (cache_dir / ".git").exists():
        print(f"pulling {cache_dir}")
        result = run(["git", "-C", str(cache_dir), "pull", "--ff-only"])
        print(result.stdout.strip() or "already up to date")
    else:
        print(f"cloning to {cache_dir}")
        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--depth=1", url, str(cache_dir)])
        print("clone done")

    dest_dir = topic_dir / "sources" / name
    dest_dir.mkdir(parents=True, exist_ok=True)

    copied = []
    for fname in files:
        src = cache_dir / fname
        if not src.exists():
            print(f"warn: {fname} not found in {name}", file=sys.stderr)
            continue
        # Flatten nested paths: a/b/c.md → a-b-c.md
        flat = fname.replace("/", "-")
        dest = dest_dir / f"{date_str}-{flat}"
        shutil.copy2(src, dest)
        # Also keep a "latest" symlink for easy agent access
        latest = dest_dir / flat
        latest.unlink(missing_ok=True)
        latest.symlink_to(dest.name)
        copied.append(f"{name}/{flat}")
        print(f"copied: {name}/{dest.name}")
    return copied


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic-dir", required=True)
    args = ap.parse_args()

    topic_dir = pathlib.Path(args.topic_dir).expanduser().resolve()
    load_env()

    config_file = topic_dir / "sync.json"
    try:
        config = json.loads(config_file.read_text())
    except FileNotFoundError:
        print("repo_sync not configured (no sync.json) — nothing to do")
        return 0
    except json.JSONDecodeError as e:
        print(f"error: {config_file} is not valid JSON: {e}", file=sys.stderr)
        return 1

    entries = config.get("repo_sync", [])
    if not entries:
        print("repo_sync not configured in sync.json — nothing to do")
        return 0

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    copied = []
    for entry in entries:
        try:
            copied += sync_repo(entry, topic_dir, date_str)
        except (subprocess.CalledProcessError, KeyError) as e:
            print(f"warn: repo_sync '{entry.get('name', '?')}' failed: {e}", file=sys.stderr)

    if not copied:
        print("nothing copied")
        return 0

    # Commit to topic repo
    subprocess.run(["git", "add", "sources/"], cwd=topic_dir, check=True)
    # 這裡的 returncode 就是訊號本身（0=無差異，1=有差異），不是錯誤
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=topic_dir, check=False)
    if result.returncode != 0:
        subprocess.run(
            ["git", "commit", "-m", f"sync: repo file snapshot {date_str}"],
            cwd=topic_dir, check=True
        )
        print(f"committed snapshot for {date_str}")
    else:
        print("no changes to commit")

    return 0


if __name__ == "__main__":
    sys.exit(main())
