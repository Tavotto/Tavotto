#!/usr/bin/env bash
# STATE 节 QA 的统一跑法：从 worktree 根目录起跑。
#
#   docs/qa/2026-09-24/state/repro/run.sh vitest <file...>      # jsdom 纯状态/事务
#   docs/qa/2026-09-24/state/repro/run.sh e2e <spec> [grep]     # 真浏览器（chromium）
#   docs/qa/2026-09-24/state/repro/run.sh pytest <targets...>   # 后端
#
# 环境（按本机实测，改机器请改这三行）：
#   QA_VENV_PY    共享开发 venv 的解释器（editable 指向主工作区，所以必须带 PYTHONPATH=src）
#   QA_WORKER_PY  带 matplotlib/numpy 的解释器（给真渲染的 worker 用）
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
QA_VENV_PY="${QA_VENV_PY:-/Volumes/Projects/Tavotto/.venv/bin/python}"
QA_WORKER_PY="${QA_WORKER_PY:-/opt/homebrew/opt/python@3.13/libexec/bin/python3}"
kind="$1"; shift
case "$kind" in
  vitest)
    cd "$ROOT/web"
    NODE_OPTIONS=--no-experimental-webstorage exec pnpm exec vitest run "$@"
    ;;
  vitest-repro)
    # 预期红的复现用例不进测试集：临时拷进 web/src/__qa_repro__/ 跑完即删
    dst="$ROOT/web/src/__qa_repro__"
    mkdir -p "$dst"
    trap 'rm -rf "$dst"' EXIT
    files=()
    for f in "$@"; do cp "$ROOT/docs/qa/2026-09-24/state/repro/$f" "$dst/"; files+=("src/__qa_repro__/$f"); done
    cd "$ROOT/web"
    NODE_OPTIONS=--no-experimental-webstorage pnpm exec vitest run "${files[@]}"
    ;;
  e2e-repro)
    # 同上，Playwright 版：临时拷进 web/e2e/ 跑完即删
    spec="$1"; shift
    cp "$ROOT/docs/qa/2026-09-24/state/repro/$spec" "$ROOT/web/e2e/$spec"
    trap 'rm -f "$ROOT/web/e2e/$spec"' EXIT
    cd "$ROOT/web"
    export TAVOTTO_PYTHON="$QA_VENV_PY" TAVOTTO_WORKER_PYTHON="$QA_WORKER_PY" PYTHONPATH="$ROOT/src"
    npx playwright test "e2e/$spec" --project=chromium "$@"
    ;;
  e2e)
    spec="$1"; shift
    cd "$ROOT/web"
    export TAVOTTO_PYTHON="$QA_VENV_PY" TAVOTTO_WORKER_PYTHON="$QA_WORKER_PY" PYTHONPATH="$ROOT/src"
    if [ $# -gt 0 ]; then
      exec npx playwright test "e2e/$spec" --project=chromium --grep "$1"
    else
      exec npx playwright test "e2e/$spec" --project=chromium
    fi
    ;;
  pytest)
    cd "$ROOT"
    PYTHONPATH="$ROOT/src" PYTHONDONTWRITEBYTECODE=1 exec "$QA_VENV_PY" -m pytest "$@" -p no:cacheprovider
    ;;
  *) echo "unknown kind: $kind" >&2; exit 2 ;;
esac
