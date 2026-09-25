#!/usr/bin/env bash
# 把 mutate_all.sh 写在 $1 里的 mut_*.log 合并成 logs/MUTATIONS.log（抹掉主目录）
set -eu
ROOT="$(git rev-parse --show-toplevel)"
SRC="$1"
OUT="$ROOT/docs/qa/2026-09-24/state/logs/MUTATIONS.log"
{
  echo "### §9 定点反证原始输出（mutate.py 过滤后的行）；变异基于提交 4a95b4d8（本分支 WIP，产品代码 = 62af729c）"
  for f in "$SRC"/mut_*.log; do
    echo
    echo "=================== $(basename "$f") ==================="
    sed "s#${HOME}#~#g" "$f" | sed 's/\x1b\[[0-9;]*[A-Za-z]//g'
  done
} > "$OUT"
echo "wrote $OUT"
