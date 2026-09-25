#!/usr/bin/env bash
# SCI-01：CompatBench PR 档 smoke，强制开启 fidelity（原生 matplotlib vs Tavotto 零 override）。
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/env.sh"; cd "$ROOT"
mkdir -p "$SCI_SCRATCH/compat"
exec "$PY" scripts/ci/compat_matrix.py --smoke --fidelity --gate pr \
  --python "$WORKER_PY" --out "$SCI_SCRATCH/compat/out" --json "$SCI_SCRATCH/compat/compat-report.json"
