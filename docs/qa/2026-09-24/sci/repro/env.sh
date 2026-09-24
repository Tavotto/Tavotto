# QA §5 (SCI) 共用环境。用法：从 worktree 根目录 `source docs/qa/2026-09-24/sci/repro/env.sh`
# 所有路径可用环境变量覆盖；默认值是本次 QA 执行机上的取值。
export ROOT="${ROOT:-$(git rev-parse --show-toplevel)}"
export SCI_SCRATCH="${SCI_SCRATCH:-/private/tmp/claude-501/-Volumes-Projects-Tavotto/47aff04a-e334-4d42-be51-2f032b92dc64/scratchpad/qa/sci}"
export PY="${PY:-/Volumes/Projects/Tavotto/.venv/bin/python}"
export WORKER_PY="${WORKER_PY:-/opt/homebrew/opt/python@3.13/libexec/bin/python3}"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
export TAVOTTO_NO_TELEMETRY=1
export TAVOTTO_DATA_DIR="$SCI_SCRATCH/data"
export TAVOTTO_CONFIG_DIR="$SCI_SCRATCH/config"
export SCI_PORT=5305
mkdir -p "$SCI_SCRATCH/data" "$SCI_SCRATCH/config"
