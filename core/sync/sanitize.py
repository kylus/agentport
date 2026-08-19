#!/usr/bin/env python3
"""Strip credentials out of text that is about to be committed.

The digest pipeline copies excerpts of Claude Code session transcripts into a
topic's git repo. Transcripts are where secrets actually get said out loud —
"here is the decryption passphrase, save it somewhere, I won't keep it" — and a
git repo is where they must never land, because removing one afterwards means
rewriting history.

Two layers, because they fail differently:

**Known shapes.** Tokens with a recognisable format (JWT, Slack, GitHub,
Telegram, AWS, private keys) and `key: value` pairs whose key names a secret.
Cheap, precise, and blind to anything unformatted.

**Cued values.** A line that announces a secret and hands it over on the next
line or in a fenced block. This is the shape that matters here, and the one a
format-matching sanitizer cannot see: the giveaway is in the prose, not in the
value. A real digest was checked against a mature format-only sanitizer, which
masked zero of it and left a 32-character passphrase untouched.

The cue list is bilingual on purpose. The prose in these transcripts is Chinese;
an English-only keyword list reads them as ordinary text.

Nothing here is a guarantee. `residual_secret_lines` exists because of that: it
re-reads the masked text and reports what still looks like a handed-over secret,
so the caller can refuse to write rather than commit something it half-cleaned.
"""
import re

MASK = "***MASKED***"

# Bumped whenever the rules change, so a caller can tell which version produced
# a given file — an old digest is not evidence that today's rules were applied.
SANITIZER_VERSION = "2026-08-19-v1"

_CUE_WORDS = (
    r"pass(?:word|phrase)|passwd|secret|token|api[_-]?key|apikey|credential"
    r"|private[_-]?key|access[_-]?key|auth"
    r"|密碼|密语|密語|通行碼|金鑰|密鑰|權杖|憑證|口令"
)

# A key and its value on one line: api_key: abc123, PASSWORD="hunter2".
KEY_VALUE_RE = re.compile(
    r'(?i)\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|secret'
    r'|passwd|password|passphrase|authorization|client[_-]?secret)\b'
    r'(\s*[:=]\s*["\']?)(?!\*{3}MASKED\*{3})([^\s"\';,]{6,})'
)

# Formats distinctive enough to recognise on sight, wherever they appear.
KNOWN_SHAPES = [
    re.compile(r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----'),
    re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}'),
    re.compile(r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b'),                    # Telegram bot token
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),                              # AWS access key
    re.compile(r'\bASIA[0-9A-Z]{16}\b'),                              # AWS temporary key
    re.compile(r'\bglpat-[A-Za-z0-9_-]{15,}\b'),                      # GitLab PAT
    re.compile(r'\bgh[pousr]_[A-Za-z0-9_]{20,}\b'),                   # GitHub token
    re.compile(r'\bsk-(?:ant-|proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b'),  # Anthropic / OpenAI
    re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{10,}\b'),                  # Slack
    re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b'),  # JWT
]

# "…the decryption password is:" — a cue word, then a colon at the end of the
# line. Trailing markdown emphasis and closing brackets are allowed between
# them, because that is how these lines are actually written.
CUE_LINE_RE = re.compile(rf'(?i)(?:{_CUE_WORDS})[^\n]*?[:：]\s*[*_`）)】」]*\s*$')

FENCE_RE = re.compile(r'^\s*(?:```|~~~)')

# What a handed-over value looks like on its own line: one opaque run of
# characters, no spaces, long enough to be worth protecting. Prose fails this
# because prose has spaces; a URL is excluded because a link is not a secret and
# these transcripts are full of them.
OPAQUE_VALUE_RE = re.compile(r'^[^\s]{8,}$')
_NOT_A_SECRET = re.compile(r'(?i)^(?:https?://|[a-z]+://|<|\*{3}MASKED\*{3}$|[-*+]\s)')


def _is_opaque_value(line: str) -> bool:
    s = line.strip().strip('`"\'')
    if not s or not OPAQUE_VALUE_RE.match(s) or _NOT_A_SECRET.match(s):
        return False
    # A path or a sentence-with-punctuation is not a handed-over credential.
    if s.startswith(("/", "~", "./")) or s.endswith((".", "。", "，", ",")):
        return False
    # Require some mix of character classes; a single long word is usually prose
    # (a Chinese sentence has no spaces either, which is exactly the trap).
    return bool(re.search(r'[A-Za-z]', s) and re.search(r'[0-9!-/:-@\[-`{-~]', s))


def _mask_cued_values(lines: list[str]) -> tuple[list[str], int]:
    """Redact the value a cue line hands over: a fenced block, or the next line."""
    out = list(lines)
    count = 0
    i = 0
    while i < len(out):
        if not CUE_LINE_RE.search(out[i]):
            i += 1
            continue
        j = i + 1
        while j < len(out) and not out[j].strip():
            j += 1
        if j >= len(out):
            break
        if FENCE_RE.match(out[j]):
            k = j + 1
            body = []
            while k < len(out) and not FENCE_RE.match(out[k]):
                if out[k].strip():
                    body.append(k)
                k += 1
            # Only a block that IS the value gets redacted. A cue line can also
            # be ordinary prose that happens to discuss passwords and end in a
            # colon — "we just turned password auth off; here are 14 days of
            # numbers:" — and the block under it is evidence, not a credential.
            # The difference is legible: a handed-over secret is one opaque run
            # of characters, while a table has spaces and commentary in it.
            if len(body) == 1 and _is_opaque_value(out[body[0]]):
                out[body[0]] = MASK
                count += 1
            i = k + 1
            continue
        if _is_opaque_value(out[j]):
            out[j] = MASK
            count += 1
            i = j + 1
            continue
        i += 1
    return out, count


def mask_secrets(text: str) -> tuple[str, int]:
    """Return (masked text, number of redactions)."""
    count = 0

    def kv(m):
        nonlocal count
        count += 1
        return m.group(1) + m.group(2) + MASK

    text = KEY_VALUE_RE.sub(kv, text)
    for pattern in KNOWN_SHAPES:
        text, n = pattern.subn(MASK, text)
        count += n
    lines, n = _mask_cued_values(text.split("\n"))
    return "\n".join(lines), count + n


def residual_secret_lines(text: str) -> list[tuple[int, str]]:
    """Lines that still look like a handed-over secret after masking.

    Reported as (1-indexed line number, reason) and never as content — the
    caller's job is to refuse and tell a human where to look, not to print the
    thing again into a log.
    """
    findings = []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not CUE_LINE_RE.search(line):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            continue
        if lines[j].strip() == MASK:
            continue
        if FENCE_RE.match(lines[j]):
            k = j + 1
            body = [x for x in range(k, len(lines))
                    if not FENCE_RE.match(lines[x]) and lines[x].strip()]
            end = next((x for x in range(k, len(lines)) if FENCE_RE.match(lines[x])),
                       len(lines))
            body = [x for x in body if x < end]
            if len(body) == 1 and _is_opaque_value(lines[body[0]]):
                findings.append((body[0] + 1,
                                 "a cue line hands over an unmasked value in a code block"))
            continue
        if _is_opaque_value(lines[j]):
            findings.append((j + 1, "a cue line hands over an unmasked value"))
    return findings
