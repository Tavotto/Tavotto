#!/usr/bin/env bash
# 跑一个 repro 脚本并把输出（带命令、时间、SHA、退出码头尾）写进 logs/<LOG_NAME>.log。
# 用法：bash docs/qa/2026-09-24/path/repro/qa_repro.sh <LOG_NAME> <repro 脚本> [参数...]
set -u
ROOT="$(cd "$(dirname "$0")/../../../../.." && pwd)"
cd "$ROOT" || exit 97
LOG="$1"; shift
PY="${QA_APP_PYTHON:-/Volumes/Projects/Tavotto/.venv/bin/python}"
WPY="${QA_WORKER_PYTHON:-/opt/homebrew/opt/python@3.13/libexec/bin/python3}"
SCRATCH="${QA_SCRATCH:-/private/tmp/claude-501/-Volumes-Projects-Tavotto/47aff04a-e334-4d42-be51-2f032b92dc64/scratchpad/qa/path}"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
export TAVOTTO_NO_TELEMETRY=1
export TMPDIR="$SCRATCH/"
OUT="$ROOT/docs/qa/2026-09-24/path/logs/$LOG.log"
{
  echo "# command: bash docs/qa/2026-09-24/path/repro/qa_repro.sh $LOG $*"
  echo "# expanded: PYTHONPATH=<worktree>/src PYTHONDONTWRITEBYTECODE=1 TMPDIR=$SCRATCH/ $PY $* $WPY"
  echo "# time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# sha: $(git rev-parse HEAD)"
} > "$OUT"
"$PY" "$@" "$WPY" >> "$OUT" 2>&1
RC=$?
echo "# exit_code: $RC" >> "$OUT"
tail -3 "$OUT"
exit $RC
