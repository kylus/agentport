#!/usr/bin/env python3
"""
Extract topic-relevant messages from Claude Code session JSONL files
and write a dated digest to sources/sessions/<YYYYMMDD-HHMM>.md.

Which messages count as relevant comes from the topic's sync.json —
"session_digest.keywords" is a list of regex fragments OR-joined into one
case-insensitive pattern (see templates/sync.json). No keywords configured
means this topic opted out of session digests.

Keywords alone are not enough of a filter. They match text, and the same words
turn up in work that has nothing to do with the topic — a session about the
agent framework mentions the same hostnames as a session about the network it
manages. On one real五-week window, 52% of the extracted payload came from an
unrelated project and only 28% from the topic's actual subject. Everything
extracted is read by an agent, so noise costs tokens on the way in and dilutes
memory on the way out. Two more filters, both configurable under
"session_digest" in sync.json:

    "include_projects": ["network-mgmt"]     only these (substring match)
    "exclude_projects": ["kylus-site"]       never these

The topic's own session directory and subagent transcripts are always excluded,
with no way to opt in. A topic that reads its own output back turns its own
speculation into a cited source, and does it again every night.

Everything written is passed through sanitize.mask_secrets first. Transcripts
are where credentials get said out loud — "here is the passphrase, save it, I
won't keep it" — and this writes into a git repo, where a leaked secret cannot
be taken back without rewriting history. If anything still looks like a
handed-over secret after masking, no digest is written at all: a partially
cleaned digest committed automatically is worse than a sync that stopped and
said why.

Reads last_session_sync from ingest_state.json; updates it on success.
No external dependencies — stdlib only.

Usage:
    python3 core/sync/session_digest.py --topic-dir ~/workspace/topic-<name>
"""

import json
import pathlib
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sanitize import SANITIZER_VERSION, mask_secrets, residual_secret_lines


def load_digest_config(topic_dir: pathlib.Path) -> dict:
    config_file = topic_dir / 'sync.json'
    try:
        config = json.loads(config_file.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        print(f'error: {config_file} is not valid JSON: {e}', file=sys.stderr)
        raise SystemExit(1)
    return config.get('session_digest', {}) or {}


def project_filter(topic_dir: pathlib.Path, config: dict):
    """Return a predicate: may this session-project directory be read?

    Claude Code names a project directory after its cwd with slashes turned
    into dashes, so the topic's own directory is derivable rather than
    configurable — which is what makes the self-exclusion unconditional.
    """
    own = str(topic_dir).replace('/', '-')
    include = [x for x in config.get('include_projects', []) if x]
    exclude = [x for x in config.get('exclude_projects', []) if x]

    def allowed(project_dir_name: str) -> bool:
        if project_dir_name == own or project_dir_name == 'subagents':
            return False
        if any(x in project_dir_name for x in exclude):
            return False
        if include:
            return any(x in project_dir_name for x in include)
        return True

    return allowed


def load_keywords(topic_dir: pathlib.Path) -> 're.Pattern | None':
    config = load_digest_config(topic_dir)
    fragments = config.get('keywords', [])
    if not fragments:
        return None
    return re.compile('(?:' + '|'.join(fragments) + ')', re.IGNORECASE)


def extract_text(msg_obj: dict) -> str:
    content = msg_obj.get('content', '')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get('type') == 'text':
                parts.append(item.get('text', ''))
        return '\n'.join(parts)
    return ''


def parse_ts(ts_str: str) -> datetime | None:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except ValueError:
        return None


def process_session(fpath: pathlib.Path, since: datetime, keywords: 're.Pattern') -> list[tuple[str, str]]:
    hits = []
    try:
        with open(fpath, encoding='utf-8', errors='replace') as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg_type = obj.get('type')
                if msg_type not in ('user', 'assistant'):
                    continue
                ts = parse_ts(obj.get('timestamp', ''))
                if ts and ts <= since:
                    continue
                msg = obj.get('message', {})
                role = msg.get('role', msg_type)
                text = extract_text(msg)
                if text and keywords.search(text):
                    # Cap each hit to keep digest readable
                    hits.append((role, text[:1500]))
    except OSError as e:
        print(f'warn: {fpath}: {e}', file=sys.stderr)
    return hits


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--topic-dir', required=True, help='Path to topic-<name>/')
    args = ap.parse_args()

    topic_dir = pathlib.Path(args.topic_dir).expanduser().resolve()

    keywords = load_keywords(topic_dir)
    if keywords is None:
        print('session_digest not configured in sync.json — skipping')
        return 0

    ingest_state_file = topic_dir / 'ingest_state.json'

    try:
        state = json.loads(ingest_state_file.read_text())
    except (OSError, json.JSONDecodeError):
        # 檔案不存在或內容損毀都視為「從頭開始」；其他例外應該往上拋
        state = {}

    last_sync_str = state.get('last_session_sync')
    if last_sync_str:
        last_sync = datetime.fromisoformat(last_sync_str)
    else:
        # First run: look back 30 days
        from datetime import timedelta
        last_sync = datetime.now(timezone.utc) - timedelta(days=30)

    now = datetime.now(timezone.utc)

    # Discover session files modified after last_sync
    projects_dir = pathlib.Path.home() / '.claude' / 'projects'
    allowed = project_filter(topic_dir, load_digest_config(topic_dir))
    candidates: list[tuple[datetime, pathlib.Path]] = []
    skipped = 0
    for f in projects_dir.rglob('*.jsonl'):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime <= last_sync:
            continue
        if not allowed(f.parent.name):
            skipped += 1
            continue
        candidates.append((mtime, f))
    candidates.sort()
    if skipped:
        print(f'skipped {skipped} session file(s) outside this topic\'s scope')

    if not candidates:
        print('no sessions modified since last sync')
        state['last_session_sync'] = now.isoformat()
        ingest_state_file.write_text(json.dumps(state, indent=2))
        return 0

    # Extract relevant content per session
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    for _mtime, fpath in candidates:
        hits = process_session(fpath, last_sync, keywords)
        if hits:
            sections.append((fpath.stem[:8], hits))

    state['last_session_sync'] = now.isoformat()
    state.setdefault('processed', [])

    if not sections:
        print('no topic-relevant content in new sessions')
        ingest_state_file.write_text(json.dumps(state, indent=2))
        return 0

    # Write digest
    date_str = now.strftime('%Y%m%d-%H%M')
    sources_dir = topic_dir / 'sources' / 'sessions'
    sources_dir.mkdir(parents=True, exist_ok=True)
    digest_file = sources_dir / f'{date_str}.md'

    lines: list[str] = [
        f'# Session Digest — {now.strftime("%Y-%m-%d %H:%M")} UTC\n\n',
        '> 自動從 Claude Code session 提取的 topic 相關對話片段（關鍵字見 sync.json）。\n',
        '> 由 `tools/sync-session-memory.sh` 產生。\n',
        '> owner 可對 topic agent 說「ingest 今天的 session digest」以更新 memory。\n\n',
    ]
    total_hits = 0
    for session_id, hits in sections:
        lines.append(f'## Session `{session_id}…`\n\n')
        for role, text in hits:
            lines.append(f'**{role}**: {text.strip()}\n\n---\n\n')
            total_hits += 1

    body, redactions = mask_secrets(''.join(lines))

    # Fail closed. Nothing is written and last_session_sync is left alone, so a
    # re-run after the offending session is dealt with picks the same window up
    # again rather than skipping past it.
    leftover = residual_secret_lines(body)
    if leftover:
        print(f'error: {len(leftover)} value(s) still look like credentials after '
              f'masking — refusing to write a digest', file=sys.stderr)
        for lineno, why in leftover[:10]:
            print(f'  line {lineno}: {why}', file=sys.stderr)
        print('  (line numbers refer to the digest that was NOT written; the '
              'content is deliberately not echoed here)', file=sys.stderr)
        return 1

    if redactions:
        print(f'masked {redactions} secret(s) [{SANITIZER_VERSION}]')

    digest_file.write_text(body, encoding='utf-8')

    state['processed'].append({
        'type': 'session_digest',
        'file': str(digest_file.relative_to(topic_dir)),
        'synced_at': now.isoformat(),
        'sessions': len(sections),
        'hits': total_hits,
    })
    ingest_state_file.write_text(json.dumps(state, indent=2))

    print(f'digest: {digest_file}')
    print(f'sessions: {len(sections)}, hits: {total_hits}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
