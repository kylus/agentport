#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "google-api-python-client>=2.196",
#   "google-auth>=2.52",
#   "google-auth-oauthlib>=1.4",
# ]
# ///
"""Google Drive (Docs) source ingest.

Usage:
  drive_ingest.py doc <file_id_or_url>
  drive_ingest.py folder <folder_id_or_url>   # lists docs, doesn't recurse content

Auth options (~/.claude/secrets/agentport.env):
  GOOGLE_SERVICE_ACCOUNT_JSON   absolute path to service-account JSON key
  GOOGLE_OAUTH_REFRESH_TOKEN    refresh token (+ GOOGLE_OAUTH_CLIENT_ID / SECRET)

Writes sources/drive/<id>.{json,md}.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Lazy imports so missing deps fail with a useful message.
ENV_FILE = Path.home() / ".claude" / "secrets" / "agentport.env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


SOURCES_DIR = Path("sources")
SERVICE_PREFIX = "drive-"
SOURCES_DIR.mkdir(parents=True, exist_ok=True)
SOURCES_REAL = SOURCES_DIR.resolve()

# Google Drive file IDs are URL-safe base64 (alnum + `-` + `_`), >= 25 chars.
_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def _safe_filename(stem: str) -> str:
    """Reject path-traversal-y identifiers; only the canonical id charset is allowed."""
    if not _DRIVE_ID_RE.match(stem):
        raise ValueError(f"refusing to use untrusted identifier as filename: {stem!r}")
    return stem


def _safe_under(path: Path) -> Path:
    """Resolve the path and ensure it stays under SOURCES_DIR — defense in depth."""
    resolved = path.resolve()
    if not str(resolved).startswith(str(SOURCES_REAL) + os.sep) and resolved != SOURCES_REAL:
        raise ValueError(f"path {resolved} escapes {SOURCES_REAL}")
    return resolved


DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
]


def _build_creds():
    sa_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_path:
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_file(sa_path, scopes=DRIVE_SCOPES)
    refresh = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")
    if refresh:
        from google.oauth2.credentials import Credentials
        return Credentials(
            None,
            refresh_token=refresh,
            client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            token_uri="https://oauth2.googleapis.com/token",
        )
    sys.stderr.write("missing GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_OAUTH_REFRESH_TOKEN\n")
    sys.exit(2)


def _extract_id(s: str) -> str:
    """Extract a Drive file/folder id from a URL or bare id.

    Raises ValueError if the input doesn't yield a canonical id, rather than
    silently passing through user input that could become a file path.
    """
    m = re.search(r"/d/([A-Za-z0-9_-]{20,})", s) or re.search(r"folders/([A-Za-z0-9_-]{20,})", s)
    if m:
        return m.group(1)
    if _DRIVE_ID_RE.match(s):
        return s
    raise ValueError(f"not a recognisable Drive id or URL: {s!r}")


def fetch_doc(file_id: str) -> dict:
    from googleapiclient.discovery import build
    creds = _build_creds()
    docs = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    meta = drive.files().get(fileId=file_id, fields="id,name,mimeType,modifiedTime,owners,webViewLink").execute()
    body = docs.documents().get(documentId=file_id).execute()

    # Flatten doc content to plain text for markdown.
    text_lines = []
    for element in body.get("body", {}).get("content", []):
        para = element.get("paragraph")
        if not para:
            continue
        line = "".join(
            (r.get("textRun", {}).get("content", "")) for r in para.get("elements", [])
        )
        text_lines.append(line.rstrip())
    text = "\n".join(text_lines)

    return {
        "id": meta["id"],
        "name": meta.get("name"),
        "mime_type": meta.get("mimeType"),
        "modified_time": meta.get("modifiedTime"),
        "owners": [o.get("emailAddress") for o in meta.get("owners", [])],
        "web_view_link": meta.get("webViewLink"),
        "text": text,
    }


def fetch_folder(folder_id: str) -> dict:
    from googleapiclient.discovery import build
    creds = _build_creds()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    q = f"'{folder_id}' in parents and trashed=false"
    files = []
    page = None
    while True:
        resp = drive.files().list(q=q, fields="files(id,name,mimeType,modifiedTime,webViewLink),nextPageToken", pageToken=page).execute()
        files.extend(resp.get("files", []))
        page = resp.get("nextPageToken")
        if not page:
            break
    return {"folder_id": folder_id, "files": files}


def to_markdown(payload: dict, kind: str) -> str:
    lines = [f"# Google {kind} ingest", ""]
    if kind == "doc":
        lines += [
            f"## {payload.get('name')}",
            f"- ID: `{payload['id']}`",
            f"- Modified: {payload.get('modified_time')}",
            f"- URL: {payload.get('web_view_link')}",
            "",
            "---",
            "",
            payload.get("text", ""),
        ]
    elif kind == "folder":
        lines.append(f"Folder: `{payload['folder_id']}` · {len(payload['files'])} files")
        for f in payload["files"]:
            lines.append(f"- [{f['mimeType']}] {f['name']} — {f.get('webViewLink','')}")
    return "\n".join(lines)


def save_and_summarize(payload: dict, kind: str, ident: str) -> dict:
    safe = _safe_filename(ident)
    json_path = _safe_under(SOURCES_DIR / f"{SERVICE_PREFIX}{kind}-{safe}.json")
    md_path = _safe_under(SOURCES_DIR / f"{SERVICE_PREFIX}{kind}-{safe}.md")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    md_path.write_text(to_markdown(payload, kind))
    return {"kind": kind, "identifier": safe, "json_path": str(json_path), "md_path": str(md_path)}


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("doc")
    sp.add_argument("ident")
    sp = sub.add_parser("folder")
    sp.add_argument("ident")
    args = p.parse_args()

    ident = _extract_id(args.ident)
    if args.cmd == "doc":
        data = fetch_doc(ident)
        summary = save_and_summarize(data, "doc", ident)
    elif args.cmd == "folder":
        data = fetch_folder(ident)
        summary = save_and_summarize(data, "folder", ident)
    else:
        sys.exit(2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
