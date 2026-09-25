#!/usr/bin/env bash
# 依次跑本节全部定点反证；每条的原始输出写到 $1/mut_<id>.log（默认 ./mut-logs）
set -u
ROOT="$(git rev-parse --show-toplevel)"
OUT="${1:-$ROOT/mut-logs}"
mkdir -p "$OUT"
for m in M2b-no-lostpointercapture M3a-reattach-ignores-domIntact M3b-html-markup-not-memoized M4-authority-falls-back-to-display; do
  python3 "$ROOT/docs/qa/2026-09-24/state/repro/mutate.py" "$m" > "$OUT/mut_$m.log" 2>&1
  echo "$m rc=$?"
done
for m in M1-preview-accumulates M2a-cancel-still-commits M5-stale-response-advances-latest; do
  python3 "$ROOT/docs/qa/2026-09-24/state/repro/mutate.py" "$m" --e2e > "$OUT/mut_$m.log" 2>&1
  echo "$m rc=$?"
done
git -C "$ROOT" status --short
