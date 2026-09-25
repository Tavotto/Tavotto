#!/usr/bin/env bash
# 用法：bash docs/qa/2026-09-24/sci/repro/pytest.sh <pytest 参数...>
# 共享 .venv（无 matplotlib）跑父进程；worker 用装了 matplotlib 的解释器。
# 数据目录交给 tests/conftest.py 自建临时目录（不继承 env.sh 的 scratch 数据目录）。
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/env.sh"; cd "$ROOT"
unset TAVOTTO_DATA_DIR TAVOTTO_CONFIG_DIR
export TAVOTTO_WORKER_PYTHON="$WORKER_PY"
exec "$PY" -m pytest -p no:cacheprovider -rsxX "$@"
