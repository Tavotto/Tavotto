#!/bin/sh
# Pack src/tavotto of a git revision so the Windows acceptance machine can run this branch's
# `tavotto codex install` with PYTHONPATH=<root>\src (the codex subcommand is pure stdlib; no
# wheel, no frontend build, no git on the target machine).
#   make-src-tar.sh <rev> <out.tar>
set -eu
rev=${1:-HEAD}
out=${2:?out.tar}
git archive --format=tar "$rev" src/tavotto > "$out"
echo "packed src/tavotto @ $(git rev-parse --short "$rev") -> $out"
