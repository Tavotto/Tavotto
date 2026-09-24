#!/usr/bin/env bash
# QA §4 PATH：从 worktree 根目录跑 pytest 的统一入口（逐字可复制）。
# 用法：bash docs/qa/2026-09-24/path/repro/qa_pytest.sh <LOG_NAME|-> <pytest 参数...>
#   LOG_NAME 非 "-" 时把输出（带命令、时间、SHA 头）写进 docs/qa/2026-09-24/path/logs/<LOG_NAME>.log
# 环境：共享 .venv（无 matplotlib）跑 Flask 侧；worker 用装了 matplotlib 的解释器（可用
# QA_WORKER_PYTHON 覆盖）。PYTHONPATH 指向本 worktree 的 src（.venv 是对主工作区的 editable 安装）。
set -u
ROOT="$(cd "$(dirname "$0")/../../../../.." && pwd)"
cd "$ROOT" || exit 97
LOG="$1"; shift
PY="${QA_APP_PYTHON:-/Volumes/Projects/Tavotto/.venv/bin/python}"
WPY="${QA_WORKER_PYTHON:-/opt/homebrew/opt/python@3.13/libexec/bin/python3}"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
export TAVOTTO_WORKER_PYTHON="$WPY"
export TAVOTTO_NO_TELEMETRY=1
SCRATCH="${QA_SCRATCH:-/private/tmp/claude-501/-Volumes-Projects-Tavotto/47aff04a-e334-4d42-be51-2f032b92dc64/scratchpad/qa/path}"
BT=()
if [ "$LOG" != "-" ]; then BT=(--basetemp="$SCRATCH/pytest-$LOG"); fi
CMD=("$PY" -m pytest "$@" -p no:cacheprovider -rs ${BT[@]+"${BT[@]}"})
if [ "$LOG" = "-" ]; then
  "${CMD[@]}"
  exit $?
fi
OUT="$ROOT/docs/qa/2026-09-24/path/logs/$LOG.log"
TMP="$(mktemp)"
{
  echo "# command: bash docs/qa/2026-09-24/path/repro/qa_pytest.sh $LOG $*"
  echo "# expanded: PYTHONPATH=<worktree>/src PYTHONDONTWRITEBYTECODE=1 TAVOTTO_WORKER_PYTHON=$WPY $PY -m pytest $* -p no:cacheprovider -rs ${BT[*]+${BT[*]}}"
  echo "# time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# sha: $(git rev-parse HEAD)  (worktree dirty files: $(git status --porcelain | wc -l | tr -d ' '))"
} > "$TMP"
"${CMD[@]}" > "$TMP.out" 2>&1
RC=$?
N=$(wc -l < "$TMP.out")
if [ "$N" -gt 190 ]; then
  head -40 "$TMP.out" >> "$TMP"
  echo "# ... 截断：共 $N 行，保留头 40 行 + 尾 150 行 ..." >> "$TMP"
  tail -150 "$TMP.out" >> "$TMP"
else
  cat "$TMP.out" >> "$TMP"
fi
echo "# exit_code: $RC" >> "$TMP"
mv "$TMP" "$OUT"
rm -f "$TMP.out"
tail -5 "$OUT"
exit $RC
