#!/usr/bin/env bash
# 依次跑 mutation_check.py 里的全部定点反证（每条都先验落点、变异后跑、还原后再跑）。
set -u
cd "$(dirname "$0")/../../../../.." || exit 97
PY=/Volumes/Projects/Tavotto/.venv/bin/python
RC=0
for m in M1-root-as-scriptdir M2-ambiguity-suppressed M3-fallback-off M4-fallback-follows-escaping-symlink M5-project-mode-at-root M6-absolute-remapped; do
  "$PY" docs/qa/2026-09-24/path/repro/mutation_check.py "$m" 2>&1 | grep '^\[M'
  [ "${PIPESTATUS[0]}" -eq 0 ] || RC=1
done
git status --porcelain src
exit $RC
