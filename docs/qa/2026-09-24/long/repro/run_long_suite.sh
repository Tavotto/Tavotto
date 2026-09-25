#!/bin/sh
# 按比例缩短档的 LONG-01 / LONG-02 / LONG-03 顺序跑（同一端口不能并行）。
# 用法（worktree 根目录）：sh docs/qa/2026-09-24/long/repro/run_long_suite.sh <scratch 目录> <LONG01 分钟> <LONG02 分钟> <LONG03 分钟>
# 需要：TAVOTTO_WORKER_PYTHON（装了 matplotlib 的解释器）、TAVOTTO_FONTS_DIR（批准字体，fetch_fonts.py --check 通过）。
set -u
S="$1"; M1="$2"; M2="$3"; M3="$4"
WT="$(pwd)"
PY=/Volumes/Projects/Tavotto/.venv/bin/python
R=docs/qa/2026-09-24/long/repro
rm -rf "$S/fx"
"$PY" "$R/make_fixtures.py" "$S/fx"
for p in projA projB; do "$TAVOTTO_WORKER_PYTHON" "$S/fx/$p/fig.py"; done
export PYTHONPATH="$WT/src" PYTHONDONTWRITEBYTECODE=1
sh "$R/run_logged.sh" "$S/raw/LONG-01.log" "$PY" "$R/long_probe.py" --mode long01 --minutes "$M1" --fixtures "$S/fx" --out "$S/run-long01" --keep-going
echo "LONG-01 exit=$?"
sh "$R/run_logged.sh" "$S/raw/LONG-02.log" "$PY" "$R/long_probe.py" --mode long02 --minutes "$M2" --fixtures "$S/fx" --out "$S/run-long02" --keep-going
echo "LONG-02 exit=$?"
sh "$R/run_logged.sh" "$S/raw/LONG-03.log" "$PY" "$R/long_probe.py" --mode long03 --minutes "$M3" --fixtures "$S/fx" --out "$S/run-long03" --keep-going
echo "LONG-03 exit=$?"
