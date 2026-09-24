#!/usr/bin/env bash
# 用法：bash docs/qa/2026-09-24/sci/repro/runpy.sh <探针.py> [参数...]
# 以 .venv 解释器（Flask 父进程）跑探针；worker 用装了 matplotlib 的解释器；数据目录在 scratch。
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/env.sh"; cd "$ROOT"
export TAVOTTO_WORKER_PYTHON="$WORKER_PY"
export TAVOTTO_WORKERD=0
exec "$PY" "$@"
