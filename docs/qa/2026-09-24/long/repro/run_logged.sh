#!/bin/sh
# 用法：从 worktree 根目录起跑
#   sh docs/qa/2026-09-24/long/repro/run_logged.sh <日志文件> <命令...>
# 在日志开头写命令、时间、SHA，末尾写退出码；退出码原样返回。
log="$1"; shift
mkdir -p "$(dirname "$log")"
sha="$(git rev-parse HEAD 2>/dev/null)"
{
  echo "# cmd: $*"
  echo "# start: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# sha: $sha"
  echo "# cwd: worktree root"
} > "$log"
"$@" >> "$log" 2>&1
rc=$?
{
  echo "# end: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# exit: $rc"
} >> "$log"
exit $rc
