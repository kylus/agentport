#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "PyGithub>=2.5",
# ]
# ///
"""GitHub source ingest.

Usage:
  github_ingest.py issue       <owner/repo> <number>
  github_ingest.py pr          <owner/repo> <number>
  github_ingest.py discussion  <owner/repo> <number>
  github_ingest.py wiki        <owner/repo> [<page>]
  github_ingest.py repo-list   <owner/repo>           # list issues + PRs (metadata)

Writes sources/github/<kind>-<owner_repo>-<n>.{json,md}.

Auth env (~/.claude/secrets/agentport.env):
  GITHUB_TOKEN    Personal access token (classic) — scopes: repo (private) or public_repo.
                  Fine-grained also works; needs 'Read' on Issues / Pull requests / Discussions / Contents.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from github import Github

ENV_FILE = Path.home() / ".claude" / "secrets" / "agentport.env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _resolve_token() -> str:
    """Pick the first available auth method:
    1. GITHUB_TOKEN / GH_TOKEN env (from agentport.env or shell)
    2. `gh auth token` — gh CLI's stored OAuth token
    Returns a token or exits 2.
    """
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok
    import shutil
    import subprocess
    if shutil.which("gh"):
        try:
            r = subprocess.run(
                ["gh", "auth", "token"],
                check=True, capture_output=True, text=True,
            )
            tok = r.stdout.strip()
            if tok:
                return tok
        except subprocess.CalledProcessError as e:
            sys.stderr.write(f"`gh auth token` failed: {e.stderr.strip()}\n")
    sys.stderr.write(
        "no GitHub auth — set GITHUB_TOKEN in ~/.claude/secrets/agentport.env "
        "OR run `gh auth login` once\n"
    )
    sys.exit(2)


TOKEN = _resolve_token()

gh = Github(TOKEN)

SOURCES_DIR = Path("sources")
SERVICE_PREFIX = "github-"
SOURCES_DIR.mkdir(parents=True, exist_ok=True)
SOURCES_REAL = SOURCES_DIR.resolve()

# GitHub owner/repo names: letters/digits/_-., 1..39 chars for owners, 1..100 for repos.
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def _safe_repo(s: str) -> str:
    if not _REPO_RE.match(s):
        raise ValueError(f"not a recognisable owner/repo: {s!r}")
    return s


def _safe_under(path: Path) -> Path:
    resolved = path.resolve()
    if not str(resolved).startswith(str(SOURCES_REAL) + os.sep) and resolved != SOURCES_REAL:
        raise ValueError(f"path {resolved} escapes {SOURCES_REAL}")
    return resolved


def _user(u) -> str:
    return getattr(u, "login", None) or "unknown"


def issue_to_dict(it) -> dict:
    return {
        "number": it.number,
        "title": it.title,
        "state": it.state,
        "author": _user(it.user),
        "created_at": str(it.created_at) if it.created_at else None,
        "updated_at": str(it.updated_at) if it.updated_at else None,
        "labels": [lb.name for lb in it.labels],
        "body": it.body or "",
        "html_url": it.html_url,
        "comments": [
            {"author": _user(c.user), "created_at": str(c.created_at), "body": c.body}
            for c in it.get_comments()
        ],
    }


def fetch_issue(repo: str, number: int) -> dict:
    r = gh.get_repo(_safe_repo(repo))
    issue = r.get_issue(number)
    if issue.pull_request is not None:
        sys.stderr.write(f"warning: #{number} is a PR, not an Issue. Use `pr` subcommand for fuller data.\n")
    return issue_to_dict(issue)


def fetch_pr(repo: str, number: int) -> dict:
    r = gh.get_repo(_safe_repo(repo))
    pr = r.get_pull(number)
    # issue_to_dict(pr) on a PullRequest calls .get_comments() which returns
    # *review* comments only. The PR's main-conversation comments live on the
    # underlying Issue. Fetch both so cold start sees the whole discussion.
    out = issue_to_dict(pr)
    issue = r.get_issue(number)  # cheap; same object underlying the PR
    out["conversation_comments"] = [
        {"author": _user(c.user), "created_at": str(c.created_at), "body": c.body}
        for c in issue.get_comments()
    ]
    out.update({
        "merged": pr.merged,
        "mergeable_state": pr.mergeable_state,
        "draft": pr.draft,
        "head": {"ref": pr.head.ref, "sha": pr.head.sha},
        "base": {"ref": pr.base.ref, "sha": pr.base.sha},
        "review_comments": [
            {"author": _user(c.user), "created_at": str(c.created_at), "path": c.path, "body": c.body}
            for c in pr.get_review_comments()
        ],
        "reviews": [
            {"author": _user(rv.user), "state": rv.state, "submitted_at": str(rv.submitted_at), "body": rv.body or ""}
            for rv in pr.get_reviews()
        ],
    })
    return out


def fetch_discussion(repo: str, number: int) -> dict:
    """Discussions need GraphQL; PyGithub support is limited, so we call the
    GraphQL endpoint directly via requests. Paginates through comments."""
    import requests
    safe = _safe_repo(repo)
    owner, name = safe.split("/", 1)
    base_q = """
    query($owner:String!,$name:String!,$number:Int!,$after:String) {
      repository(owner:$owner,name:$name) {
        discussion(number:$number) {
          number title bodyText createdAt updatedAt
          author{login} url
          category{name}
          comments(first:100, after:$after) {
            pageInfo { hasNextPage endCursor }
            nodes { author{login} bodyText createdAt }
          }
        }
      }
    }
    """
    after: str | None = None
    out: dict | None = None
    all_comments: list[dict] = []
    while True:
        r = requests.post(
            "https://api.github.com/graphql",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"query": base_q, "variables": {"owner": owner, "name": name, "number": number, "after": after}},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            raise RuntimeError(f"GitHub GraphQL errors: {data['errors']}")
        d = data["data"]["repository"]["discussion"]
        if not d:
            raise RuntimeError(f"discussion #{number} not found in {safe}")
        if out is None:
            out = {
                "number": d["number"],
                "title": d["title"],
                "body": d["bodyText"],
                "author": (d.get("author") or {}).get("login"),
                "category": (d.get("category") or {}).get("name"),
                "created_at": d["createdAt"],
                "updated_at": d["updatedAt"],
                "html_url": d["url"],
            }
        comments = d["comments"]
        for c in comments["nodes"]:
            all_comments.append({
                "author": (c.get("author") or {}).get("login"),
                "created_at": c["createdAt"],
                "body": c["bodyText"],
            })
        if not comments["pageInfo"]["hasNextPage"]:
            break
        after = comments["pageInfo"]["endCursor"]
    out["comments"] = all_comments
    return out


# GitHub wiki page slugs use [A-Za-z0-9 _-] in practice. We accept the same
# plus dot for stems like "v1.0". Hyphens/spaces are kept verbatim — GitHub
# stores spaces as `-` in the filename, but accept both for forgiving UX.
_WIKI_PAGE_RE = re.compile(r"^[A-Za-z0-9 ._-]{1,100}$")


def fetch_wiki(repo: str, page: str | None = None) -> dict:
    """GitHub Wiki is a separate git repo (repo.wiki.git). Listing pages via
    API isn't supported; we shallow-clone the wiki repo and read the file(s)."""
    import shutil
    import subprocess
    import tempfile

    safe = _safe_repo(repo)
    # Token-in-URL leaks via /proc/<pid>/cmdline and CalledProcessError.cmd.
    # Use the credential helper protocol over stdin instead — Git never sees
    # the token on argv and won't persist it in the cloned repo's config.
    tmp = Path(tempfile.mkdtemp(prefix="gh-wiki-"))
    wiki_url = f"https://github.com/{safe}.wiki.git"
    try:
        # Avoid leaking the PAT via argv (where /proc/<pid>/cmdline and
        # CalledProcessError.cmd would expose it). Use an inline credential
        # helper that the spawned shell expands at git-credential time,
        # reading the token from this process's env. The script itself
        # contains only `$GH_TOKEN_FOR_WIKI` — the literal token text is
        # never on argv and never persisted to disk.
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GH_TOKEN_FOR_WIKI"] = TOKEN
        helper = '!f() { echo username=x-access-token; echo password=$GH_TOKEN_FOR_WIKI; }; f'
        proc = subprocess.run(
            [
                "git",
                "-c", f"credential.helper={helper}",
                "clone", "--depth", "1", wiki_url, str(tmp),
            ],
            env=env,
            capture_output=True,
            text=True,
            # returncode is handled right below, with a message that explains
            # what a wiki clone failure usually means — don't let it raise.
            check=False,
        )
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            # GitHub returns 404 for repos with wiki disabled or empty.
            if "Repository not found" in stderr or "not found" in stderr.lower() or "could not read" in stderr.lower():
                return {"repo": safe, "pages": [], "note": "wiki disabled or empty"}
            # Token may be in stderr only if Git accidentally echoes it back;
            # PyGithub-style scrub just in case.
            stderr_safe = stderr.replace(TOKEN, "***")
            raise RuntimeError(f"git clone failed (exit {proc.returncode}): {stderr_safe}")

        clone_real = tmp.resolve()

        if page:
            if not _WIKI_PAGE_RE.match(page):
                raise ValueError(f"refusing to use untrusted wiki page slug: {page!r}")
            # Resolve and verify the matched file stays inside the clone dir.
            f = next(clone_real.glob(f"{page}.*"), None)
            if not f:
                # GitHub stores spaces as hyphens in filenames; try that too.
                f = next(clone_real.glob(f"{page.replace(' ', '-')}.*"), None)
            if not f:
                raise FileNotFoundError(f"wiki page {page!r} not found")
            resolved = f.resolve()
            if not str(resolved).startswith(str(clone_real) + os.sep):
                raise ValueError(f"wiki file {resolved} escapes clone dir {clone_real}")
            return {"repo": safe, "page": page, "content": resolved.read_text()}
        pages = []
        for f in sorted(clone_real.glob("*.md")) + sorted(clone_real.glob("*.mediawiki")):
            pages.append({"page": f.stem, "size": f.stat().st_size})
        return {"repo": safe, "pages": pages}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fetch_repo_list(repo: str) -> dict:
    r = gh.get_repo(_safe_repo(repo))
    issues = []
    prs = []
    for it in r.get_issues(state="all"):
        meta = {"number": it.number, "title": it.title, "state": it.state, "labels": [lb.name for lb in it.labels], "url": it.html_url}
        if it.pull_request is None:
            issues.append(meta)
        else:
            prs.append(meta)
    return {"issues": issues, "prs": prs}


def to_markdown(payload: dict, kind: str) -> str:
    lines = [f"# GitHub {kind} ingest", ""]
    if kind in ("issue", "pr", "discussion"):
        lines += [
            f"## {payload.get('title','')}",
            f"- State: {payload.get('state', 'discussion')}",
            f"- Author: @{payload.get('author')}",
            f"- URL: {payload.get('html_url','')}",
            "",
            payload.get("body", "") or "(no body)",
            "",
            "### Comments",
        ]
        for c in payload.get("comments", []):
            lines.append(f"- **@{c['author']}** ({c['created_at']}): {c['body']}")
        if kind == "pr":
            lines.append("\n### Reviews")
            for rv in payload.get("reviews", []):
                lines.append(f"- **@{rv['author']}** [{rv['state']}] ({rv['submitted_at']}): {rv['body']}")
    elif kind == "wiki":
        # `pages` present (possibly empty list) → repo-level listing.
        # Otherwise it's a single-page payload with `page`/`content`.
        if "pages" in payload:
            pages = payload["pages"]
            lines.append(f"## Pages ({len(pages)})")
            if payload.get("note"):
                lines.append(f"_{payload['note']}_")
            for p in pages:
                lines.append(f"- {p['page']} ({p['size']} bytes)")
        else:
            lines += [f"## {payload['page']}", "", payload["content"]]
    elif kind == "repo-list":
        lines.append(f"## Issues ({len(payload['issues'])})")
        for i in payload["issues"]:
            lines.append(f"- #{i['number']} [{i['state']}] {i['title']} — {i['url']}")
        lines.append(f"\n## Pull Requests ({len(payload['prs'])})")
        for p in payload["prs"]:
            lines.append(f"- !{p['number']} [{p['state']}] {p['title']} — {p['url']}")
    return "\n".join(lines)


def save_and_summarize(payload: dict, kind: str, ident: str) -> dict:
    safe = ident.replace("/", "_")
    json_path = _safe_under(SOURCES_DIR / f"{SERVICE_PREFIX}{kind}-{safe}.json")
    md_path = _safe_under(SOURCES_DIR / f"{SERVICE_PREFIX}{kind}-{safe}.md")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    md_path.write_text(to_markdown(payload, kind))
    return {"kind": kind, "identifier": ident, "json_path": str(json_path), "md_path": str(md_path)}


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("issue", "pr", "discussion"):
        sp = sub.add_parser(name)
        sp.add_argument("repo")
        sp.add_argument("number", type=int)
    sp = sub.add_parser("wiki")
    sp.add_argument("repo")
    sp.add_argument("page", nargs="?")
    sp = sub.add_parser("repo-list")
    sp.add_argument("repo")
    args = p.parse_args()

    if args.cmd == "issue":
        data = fetch_issue(args.repo, args.number)
        summary = save_and_summarize(data, "issue", f"{args.repo}-{args.number}")
    elif args.cmd == "pr":
        data = fetch_pr(args.repo, args.number)
        summary = save_and_summarize(data, "pr", f"{args.repo}-{args.number}")
    elif args.cmd == "discussion":
        data = fetch_discussion(args.repo, args.number)
        summary = save_and_summarize(data, "discussion", f"{args.repo}-{args.number}")
    elif args.cmd == "wiki":
        data = fetch_wiki(args.repo, args.page)
        summary = save_and_summarize(data, "wiki", f"{args.repo}-{args.page or 'index'}")
    elif args.cmd == "repo-list":
        data = fetch_repo_list(args.repo)
        summary = save_and_summarize(data, "repo-list", args.repo)
    else:
        sys.exit(2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
