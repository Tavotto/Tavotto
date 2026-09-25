#!/usr/bin/env python3
"""§9 定点反证：先验落点 → 变异 → 跑用例（看退出码与失败断言）→ git checkout 还原 → 再跑一次确认回绿。

用法（从 worktree 根目录）：
    python3 docs/qa/2026-09-24/state/repro/mutate.py <MUTATION_ID> [--e2e]

变异永远不进提交：脚本开始前要求工作区干净，结束时无论成败都 `git checkout -- <file>`。
`--e2e` 时变异后先 `scripts/build_frontend.py` 重建包内前端（漏了会静默测旧界面），还原后再建一次。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
RUN = str(ROOT / "docs/qa/2026-09-24/state/repro/run.sh")
VENV_PY = os.environ.get("QA_VENV_PY", "/Volumes/Projects/Tavotto/.venv/bin/python")

# id -> (文件, 原串, 变异串, vitest 目标, e2e (spec, grep) 或 None)
MUTATIONS: dict[str, tuple[str, str, str, list[str], tuple[str, str] | None]] = {
    # STATE-01：预览不再从 base 现算，而是在当前 transform 上字符串累加
    "M1-preview-accumulates": (
        "web/src/store/svgPreviewStore.ts",
        "const base = p.baseTransforms.get(op.gid) ?? null",
        "const base = node.getAttribute('transform')",
        ["src/store/svgPreviewStore.test.ts", "src/canvas/fakeRealtimeDrag.test.tsx"],
        ("fake-realtime.spec.ts", "STATE-01"),
    ),
    # STATE-02：取消后仍 commit（图内文字拖动把 cancelled 当成 up）
    "M2a-cancel-still-commits": (
        "web/src/canvas/interactions.ts",
        "      if (!moved || end.cancelled) {\n        cancelElementPreview()\n        recordDiagnosticEvent({\n          type: 'element.drag.cancel',",
        "      if (!moved) {\n        cancelElementPreview()\n        recordDiagnosticEvent({\n          type: 'element.drag.cancel',",
        ["src/canvas/fakeRealtimeDrag.test.tsx"],
        ("fake-realtime.spec.ts", "STATE-02"),
    ),
    # STATE-02：lostpointercapture 不再接到取消路径上
    "M2b-no-lostpointercapture": (
        "web/src/canvas/interactions.ts",
        "  window.addEventListener('lostpointercapture', cancel)\n}",
        "}",
        ["src/canvas/fakeRealtimeDrag.test.tsx"],
        None,
    ),
    # STATE-03：DOM 没换也重采 base（重放叠加位移）
    "M3a-reattach-ignores-domIntact": (
        "web/src/store/svgPreviewStore.ts",
        "  if (domIntact(p, svg)) return\n",
        "",
        ["src/store/svgPreviewStore.test.ts", "src/canvas/dragReleaseSnapback.test.tsx"],
        None,
    ),
    # STATE-03：松手重写 DOM（{__html} 每次新对象）
    "M3b-html-markup-not-memoized": (
        "web/src/lib/useHtmlMarkup.ts",
        "  return useMemo(() => (html == null ? undefined : { __html: html }), [html])",
        "  void useMemo\n  return html == null ? undefined : { __html: html }",
        ["src/canvas/dragReleaseSnapback.test.tsx"],
        None,
    ),
    # STATE-04：几何权威退回显示那份（读旧 manifest 写文档）
    "M4-authority-falls-back-to-display": (
        "web/src/store/renderStore.ts",
        "  return exactOf(state.byKey[renderKeyOf(panel)], panel)\n}",
        "  void exactOf\n  return panelRender(state as Pick<RenderState, 'byKey' | 'latest'>, panel) ?? null\n}",
        [
            "src/store/geometryAuthority.test.ts",
            "src/store/alignAction.test.ts",
            "src/canvas/fakeRealtimeDrag.test.tsx",
        ],
        None,
    ),
    # STATE-05：乱序旧响应推进 latest
    "M5-stale-response-advances-latest": (
        "web/src/store/renderStore.ts",
        "const fresher = seq >= (s.latestSeq[fileId] ?? 0)",
        "const fresher = true || seq >= (s.latestSeq[fileId] ?? 0)",
        [
            "src/store/geometryAuthority.test.ts",
            "src/canvas/alignUndoConvergence.test.tsx",
            "src/canvas/fakeRealtimeDrag.test.tsx",
        ],
        ("fake-realtime.spec.ts", "STATE-05"),
    ),
}


def sh(cmd: list[str], log: list[str]) -> int:
    log.append(f"$ {' '.join(cmd)}")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, env=env)
    out = (p.stdout + p.stderr).splitlines()
    keep = [
        ln
        for ln in out
        if any(
            k in ln
            for k in (
                "✓",
                "×",
                "✘",
                "FAIL",
                "Assertion",
                "Error:",
                "Tests ",
                "passed",
                "failed",
                "expected",
            )
        )
    ]
    log.extend(keep[:80])
    log.append(f"exit={p.returncode}")
    return p.returncode


def build(log: list[str]) -> None:
    env_cmd = ["env", f"PYTHONPATH={ROOT / 'src'}", VENV_PY, "scripts/build_frontend.py"]
    rc = sh(env_cmd, log)
    if rc != 0:
        raise SystemExit("build_frontend 失败")


def main() -> int:
    mid = sys.argv[1]
    with_e2e = "--e2e" in sys.argv
    rel, old, new, vitest, e2e = MUTATIONS[mid]
    path = ROOT / rel
    if subprocess.run(["git", "diff", "--quiet"], cwd=ROOT).returncode != 0:
        raise SystemExit("工作区不干净：先提交再变异")
    log: list[str] = [f"# mutation {mid} on {rel}"]
    src = path.read_text(encoding="utf-8")
    hits = src.count(old)
    log.append(f"target occurrences = {hits}")
    if hits != 1:
        print("\n".join(log))
        raise SystemExit("目标串不唯一或不存在：变异没有落在预期位置")
    try:
        path.write_text(src.replace(old, new), encoding="utf-8")
        log.append("## mutated run (vitest)")
        red_v = sh([RUN, "vitest", *vitest], log)
        red_e = None
        if with_e2e and e2e:
            build(log)
            log.append("## mutated run (e2e)")
            red_e = sh([RUN, "e2e", e2e[0], e2e[1]], log)
    finally:
        subprocess.run(["git", "checkout", "--", rel], cwd=ROOT, check=True)
    log.append("## restored run (vitest)")
    green_v = sh([RUN, "vitest", *vitest], log)
    green_e = None
    if with_e2e and e2e:
        build(log)
        log.append("## restored run (e2e)")
        green_e = sh([RUN, "e2e", e2e[0], e2e[1]], log)
    log.append(
        f"SUMMARY {mid}: mutated vitest exit={red_v} e2e exit={red_e} | restored vitest exit={green_v} e2e exit={green_e}"
    )
    print("\n".join(log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
