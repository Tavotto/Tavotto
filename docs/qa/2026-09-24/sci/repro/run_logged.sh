#!/usr/bin/env bash
# 用法：bash docs/qa/2026-09-24/sci/repro/run_logged.sh <CASE_ID> <label> -- <命令...>
# 在 worktree 根目录执行命令；完整输出写到 $SCI_SCRATCH/raw/<CASE>.<label>.log，
# 仓库内 logs/<CASE>.log 追加一节（命令、UTC 时间、SHA、退出码；超长保留头 40 + 尾 150 行）。
set -u
CASE="$1"; LABEL="$2"; shift 2
[ "$1" = "--" ] && shift
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/env.sh"
cd "$ROOT"
mkdir -p "$SCI_SCRATCH/raw" "$ROOT/docs/qa/2026-09-24/sci/logs"
RAW="$SCI_SCRATCH/raw/$CASE.$LABEL.log"
OUT="$ROOT/docs/qa/2026-09-24/sci/logs/$CASE.log"
START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
"$@" >"$RAW" 2>&1
RC=$?
N=$(wc -l <"$RAW" | tr -d ' ')
{
  echo "=================================================================="
  echo "## $CASE / $LABEL"
  echo "# cmd: $*"
  echo "# start(UTC): $START   end(UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# source_sha: $(git rev-parse HEAD)  (worktree 相对基线 62af729c 只多出 docs/qa 与新增测试)"
  echo "# exit_code: $RC   raw_lines: $N   raw_sha256: $(shasum -a 256 "$RAW" | cut -d' ' -f1)"
  if [ "$N" -gt 190 ]; then
    head -40 "$RAW"
    echo "# ... [截断：省略中间 $((N-190)) 行；完整输出在 raw 日志] ..."
    tail -150 "$RAW"
  else
    cat "$RAW"
  fi
  echo "# exit_code: $RC"
} >>"$OUT"
echo "$CASE/$LABEL exit=$RC lines=$N"
tail -3 "$RAW"
exit $RC
