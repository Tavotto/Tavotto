#!/usr/bin/env bash
# 逐条用例重跑其命令，把真实输出写进 docs/qa/2026-09-24/state/logs/<CASE>.log。
# 每段输出开头写命令、时间、SHA；超过 190 行只留头 40 + 尾 150 并注明截断。
# 前提：已跑过 scripts/build_frontend.py（e2e 测的是包内 src/tavotto/web/）。
set -u
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
R="docs/qa/2026-09-24/state/repro/run.sh"
LOGS="docs/qa/2026-09-24/state/logs"
mkdir -p "$LOGS"
SHA="$(git rev-parse HEAD)"
SCRUB_HOME="${HOME}"

run() { # run <case> <命令...>
  local case="$1"; shift
  local tmp; tmp="$(mktemp)"
  local start; start="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  "$@" >"$tmp" 2>&1
  local rc=$?
  {
    echo "### \$ $*"
    echo "### time=$start sha=$SHA exit=$rc"
    # 本机主目录抹成 ~（日志里不留真实用户路径）
    sed "s#${SCRUB_HOME}#~#g" "$tmp" | sed 's/\x1b\[[0-9;]*[A-Za-z]//g' > "$tmp.clean"
    local n; n=$(wc -l < "$tmp.clean")
    if [ "$n" -gt 190 ]; then
      head -40 "$tmp.clean"
      echo "### …（截断：共 $n 行，保留头 40 行 + 尾 150 行）…"
      tail -150 "$tmp.clean"
    else
      cat "$tmp.clean"
    fi
    echo
  } >>"$LOGS/$case.log"
  rm -f "$tmp" "$tmp.clean"
  echo "$case rc=$rc :: $*"
}

rm -f "$LOGS"/STATE-0*.log

run STATE-01 "$R" vitest src/store/svgPreviewStore.test.ts src/canvas/fakeRealtimeDrag.test.tsx
run STATE-01 "$R" e2e fake-realtime.spec.ts "拖图内元素|STATE-01"

run STATE-02 "$R" vitest src/canvas/fakeRealtimeDrag.test.tsx -t "pointercancel|lostpointercapture"
run STATE-02 "$R" e2e fake-realtime.spec.ts "STATE-02"
run STATE-02 "$R" e2e-repro state_probe.spec.ts --grep "Esc|blur|移出"
run STATE-02 "$R" e2e-repro state_bugs_repro.spec.ts --grep "STATE-02"

run STATE-03 "$R" vitest src/store/svgPreviewStore.test.ts src/canvas/dragReleaseSnapback.test.tsx

run STATE-04 "$R" vitest src/store/geometryAuthority.test.ts src/store/alignAction.test.ts src/canvas/alignUndoConvergence.test.tsx src/diagnostics/authority.test.ts src/canvas/fakeRealtimeDrag.test.tsx
run STATE-04 "$R" vitest-repro state04_undo_while_authority_pending.test.tsx
run STATE-04 "$R" e2e-repro state_bugs_repro.spec.ts --grep "STATE-04"

run STATE-05 "$R" vitest src/store/geometryAuthority.test.ts src/store/renderStore.test.ts src/canvas/alignUndoConvergence.test.tsx src/canvas/fakeRealtimeDrag.test.tsx -t "乱序|晚到|晚于|SVG 与 manifest|在途期间撤销"
run STATE-05 "$R" e2e fake-realtime.spec.ts "STATE-05"

run STATE-06 "$R" vitest src/store/renderStore.test.ts src/store/geometryAuthority.test.ts src/store/projectSwitchWorkspace.test.ts src/store/documentStore.test.ts
run STATE-06 "$R" vitest-repro state06_cross_project_render.test.ts

run STATE-07 "$R" e2e-repro state_probe.spec.ts --grep "视图倍率"
run STATE-07 "$R" e2e-repro state07_dpr_probe.spec.ts

run STATE-08 "$R" vitest src/store/documentStore.test.ts src/lib/autosave/diskWriter.test.ts
run STATE-08 "$R" e2e fake-realtime.spec.ts "STATE-08"

run STATE-09 "$R" pytest tests/test_ai_refresh.py tests/test_ai_revert_atomic.py tests/test_ai_history.py
run STATE-09 "$R" vitest src/hooks/useServerEvents.test.ts src/store/aiStore.test.ts
run STATE-09 "$R" pytest docs/qa/2026-09-24/state/repro/test_state09_ai_revert_stale.py
