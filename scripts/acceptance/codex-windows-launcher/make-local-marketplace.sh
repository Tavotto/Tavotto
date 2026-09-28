#!/bin/sh
# Pack the codex-plugin/ of a git revision as a *local* Codex marketplace (tar), so a test
# machine can `codex plugin marketplace add <dir>` a branch that is not on plugin-stable.
# `git archive` keeps LF and the 100755 mode exactly as committed.
#   make-local-marketplace.sh <rev> <out.tar>
set -eu
rev=${1:-HEAD}
out=${2:?out.tar}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/w266-mkt/.agents/plugins"
git archive --format=tar "$rev" codex-plugin | tar -x -C "$tmp/w266-mkt"
cat > "$tmp/w266-mkt/.agents/plugins/marketplace.json" <<'JSON'
{
  "name": "tavotto",
  "interface": {"displayName": "Tavotto (local #266 acceptance)"},
  "plugins": [
    {
      "name": "tavotto",
      "source": {"source": "local", "path": "./codex-plugin"},
      "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
      "category": "Productivity"
    }
  ]
}
JSON
tar -c -f "$out" -C "$tmp" w266-mkt
echo "packed $(git rev-parse --short "$rev") -> $out"
