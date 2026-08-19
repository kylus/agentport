#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "python-gitlab>=8.3",
# ]
# ///
"""GitLab source ingest.

Usage:
  gitlab_ingest.py issue       <group/repo> <iid>
  gitlab_ingest.py mr          <group/repo> <iid>
  gitlab_ingest.py wiki        <group/repo> [<slug>]
  gitlab_ingest.py repo-list   <group/repo>      # list issues + MRs (metadata only)

Writes sources/gitlab/<kind>-<group_repo>-<id>.{json,md}.

Auth env (~/.claude/secrets/agentport.env):
  GITLAB_URL    https://gitlab.com (default)
  GITLAB_TOKEN  personal access token, read_api + read_repository scopes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import gitlab

ENV_FILE = Path.home() / ".claude" / "secrets" / "agentport.env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

URL = os.environ.get("GITLAB_URL", "https://gitlab.com")
TOKEN = os.environ.get("GITLAB_TOKEN")
if not TOKEN:
    sys.stderr.write("missing GITLAB_TOKEN in ~/.claude/secrets/agentport.env\n")
    sys.exit(2)

gl = gitlab.Gitlab(URL, private_token=TOKEN)
SOURCES_DIR = Path("sources")
SERVICE_PREFIX = "gitlab-"
SOURCES_DIR.mkdir(parents=True, exist_ok=True)


def issue_to_dict(it) -> dict:
    return {
        "iid": it.iid,
        "title": it.title,
        "state": it.state,
        "author": it.author.get("username"),
        "created_at": it.created_at,
        "updated_at": it.updated_at,
        "labels": list(it.labels),
        "description": it.description,
        "web_url": it.web_url,
        "notes": [
            {"author": n.author.get("username"), "created_at": n.created_at, "body": n.body}
            for n in it.notes.list(all=True)
        ],
    }


def fetch_issue(repo: str, iid: int) -> dict:
    proj = gl.projects.get(repo)
    issue = proj.issues.get(iid)
    return issue_to_dict(issue)


def fetch_mr(repo: str, iid: int) -> dict:
    proj = gl.projects.get(repo)
    mr = proj.mergerequests.get(iid)
    out = issue_to_dict(mr)
    out.update({
        "source_branch": mr.source_branch,
        "target_branch": mr.target_branch,
        "diff_refs": getattr(mr, "diff_refs", None),
    })
    return out


def fetch_wiki(repo: str, slug: str | None = None) -> dict:
    proj = gl.projects.get(repo)
    if slug:
        page = proj.wikis.get(slug)
        return {"slug": page.slug, "title": page.title, "content": page.content}
    pages = proj.wikis.list(all=True)
    return {"pages": [{"slug": p.slug, "title": p.title} for p in pages]}


def fetch_repo_list(repo: str) -> dict:
    proj = gl.projects.get(repo)
    issues = proj.issues.list(state="all", all=True)
    mrs = proj.mergerequests.list(state="all", all=True)
    return {
        "issues": [{"iid": i.iid, "title": i.title, "state": i.state, "labels": list(i.labels), "url": i.web_url} for i in issues],
        "mrs": [{"iid": m.iid, "title": m.title, "state": m.state, "labels": list(m.labels), "url": m.web_url} for m in mrs],
    }


def to_markdown(payload: dict, kind: str) -> str:
    lines = [f"# GitLab {kind} ingest", ""]
    if kind in ("issue", "mr"):
        lines += [
            f"## {payload.get('title','')}",
            f"- State: {payload.get('state')}",
            f"- Author: @{payload.get('author')}",
            f"- Labels: {', '.join(payload.get('labels', []))}",
            f"- URL: {payload.get('web_url','')}",
            "",
            payload.get("description", "") or "(no description)",
            "",
            "### Notes",
        ]
        for n in payload.get("notes", []):
            lines.append(f"- **@{n['author']}** ({n['created_at']}): {n['body']}")
    elif kind == "wiki":
        if payload.get("pages"):
            lines.append("## Pages")
            for p in payload["pages"]:
                lines.append(f"- {p['slug']}: {p['title']}")
        else:
            lines += [f"## {payload.get('title')}", "", payload.get("content", "")]
    elif kind == "repo-list":
        lines.append(f"## Issues ({len(payload['issues'])})")
        for i in payload["issues"]:
            lines.append(f"- #{i['iid']} [{i['state']}] {i['title']} — {i['url']}")
        lines.append(f"\n## Merge Requests ({len(payload['mrs'])})")
        for m in payload["mrs"]:
            lines.append(f"- !{m['iid']} [{m['state']}] {m['title']} — {m['url']}")
    return "\n".join(lines)


def save_and_summarize(payload: dict, kind: str, ident: str) -> dict:
    safe = ident.replace("/", "_")
    json_path = SOURCES_DIR / f"{SERVICE_PREFIX}{kind}-{safe}.json"
    md_path = SOURCES_DIR / f"{SERVICE_PREFIX}{kind}-{safe}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    md_path.write_text(to_markdown(payload, kind))
    return {"kind": kind, "identifier": ident, "json_path": str(json_path), "md_path": str(md_path)}


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("issue", "mr"):
        sp = sub.add_parser(name)
        sp.add_argument("repo")
        sp.add_argument("iid", type=int)
    sp = sub.add_parser("wiki")
    sp.add_argument("repo")
    sp.add_argument("slug", nargs="?")
    sp = sub.add_parser("repo-list")
    sp.add_argument("repo")
    args = p.parse_args()

    if args.cmd == "issue":
        data = fetch_issue(args.repo, args.iid)
        summary = save_and_summarize(data, "issue", f"{args.repo}-{args.iid}")
    elif args.cmd == "mr":
        data = fetch_mr(args.repo, args.iid)
        summary = save_and_summarize(data, "mr", f"{args.repo}-{args.iid}")
    elif args.cmd == "wiki":
        data = fetch_wiki(args.repo, args.slug)
        summary = save_and_summarize(data, "wiki", f"{args.repo}-{args.slug or 'index'}")
    elif args.cmd == "repo-list":
        data = fetch_repo_list(args.repo)
        summary = save_and_summarize(data, "repo-list", args.repo)
    else:
        sys.exit(2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
