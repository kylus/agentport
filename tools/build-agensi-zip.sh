#!/usr/bin/env bash
# Build a marketplace upload zip for one standalone skill under packaging/agensi/.
# Zip layout: <name>/SKILL.md, <name>/examples/..., <name>/LICENSE
# Idempotent: rebuilds dist/<name>.zip from scratch each run.
set -euo pipefail
name="${1:?usage: $0 <package-name>}"
root="$(cd "$(dirname "$0")/.." && pwd)"
src="$root/packaging/agensi/$name"
[[ -f "$src/SKILL.md" ]] || { echo "no SKILL.md in $src" >&2; exit 1; }

# The standalone copy must not lean on agentport internals.
if grep -nE '(^|[^a-z])(core|hooks|memory|pending)/|mcp__discord__|approve-proposal|propose-memory-update' -r "$src"; then
  echo "standalone package references agentport internals (lines above)" >&2; exit 1
fi

mkdir -p "$root/dist"
out="$root/dist/$name.zip"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cp -r "$src" "$tmp/$name"
cp "$root/LICENSE" "$tmp/$name/LICENSE"
rm -f "$out"
(cd "$tmp" && zip -qr "$out" "$name")
echo "$out"
unzip -l "$out"
