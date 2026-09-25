#!/usr/bin/env bash
# vrun.sh CASE LABEL -- cmd...   (run from verify worktree root)
CASE="$1"; LABEL="$2"; shift 3
W=/private/tmp/claude-501/-Volumes-Projects-Tavotto/47aff04a-e334-4d42-be51-2f032b92dc64/scratchpad/qa/verify-sci
export SCI_SCRATCH=/private/tmp/claude-501/-Volumes-Projects-Tavotto/47aff04a-e334-4d42-be51-2f032b92dc64/scratchpad/qa/vsci-scratch
cd "$W"
OUT="$W/docs/qa/2026-09-24/sci/logs/verify/$CASE.$LABEL.log"
{ echo "# verify rerun $CASE/$LABEL"; echo "# cmd: $*"; echo "# head: $(git rev-parse HEAD)"; echo "# start(UTC): $(date -u +%FT%TZ)"; } > "$OUT"
bash -c "$*" >>"$OUT" 2>&1
RC=$?
{ echo "# end(UTC): $(date -u +%FT%TZ)"; echo "# exit_code: $RC"; } >> "$OUT"
echo "$CASE/$LABEL exit=$RC"; tail -4 "$OUT"
