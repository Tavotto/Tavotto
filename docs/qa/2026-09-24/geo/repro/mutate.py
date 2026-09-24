"""§9 定点反证的执行器：先验落点 → 变异 → 跑用例 → 还原 → 再跑。

用法（从 worktree 根目录；**先提交**，变异永远不进提交）::

    python3 docs/qa/2026-09-24/geo/repro/mutate.py <MUTATION_ID> <log_path>

每个变异：目标串必须**恰好出现一次**（不在就不算数，退出 3）；变异后跑指定命令，
记录退出码与失败断言；然后 `git checkout -- <file>` 还原，再跑一次记录退出码。
子进程带 PYTHONDONTWRITEBYTECODE=1（见 memory：变异还原留下过期 .pyc）。
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys

ROOT = os.getcwd()
NODE = "NODE_OPTIONS=--no-experimental-webstorage"
VITEST = f"cd web && {NODE} npx vitest run"
PY = (
    "TAVOTTO_WORKER_PYTHON=/opt/homebrew/opt/python@3.13/libexec/bin/python3 "
    f"PYTHONPATH={ROOT}/src PYTHONDONTWRITEBYTECODE=1 "
    "/Volumes/Projects/Tavotto/.venv/bin/python -m pytest -p no:cacheprovider -q"
)

MUTATIONS = {
    # GEO-01：漏 zoom
    "M1-missing-zoom": (
        "web/src/canvas/interactions.ts",
        "return [dx / (layout.width * zoom), dy / (layout.height * zoom)]",
        "return [dx / layout.width, dy / layout.height]",
        [
            f"{VITEST} src/canvas/geometryReference.test.ts",
            f"{VITEST} src/canvas/fakeRealtimeDrag.test.tsx src/canvas/axesCompanionDrag.test.tsx",
        ],
    ),
    # GEO-01：Axes 的 Y 轴方向反了
    "M2-y-flip": (
        "web/src/canvas/interactions.ts",
        "y = clamp(y - dfy, 0, 1 - h) // 屏幕向下",
        "y = clamp(y + dfy, 0, 1 - h) // 屏幕向下",
        [f"{VITEST} src/canvas/geometryReference.test.ts -t 'bottom-origin'"],
    ),
    # GEO-02：多选漏一人
    "M3-group-drops-one": (
        "web/src/canvas/interactions.ts",
        "entries.map((en, i) => en.write(boxes[i])),",
        "entries.map((en, i) => en.write(boxes[i])).slice(1),",
        [
            f"{VITEST} src/canvas/geometryReference.test.ts -t 'GEO-02'",
            f"{VITEST} src/store/alignAction.test.ts src/store/alignSelectedTo.test.ts "
            "src/canvas/alignUndoConvergence.test.tsx src/canvas/fakeRealtimeDrag.test.tsx",
        ],
    ),
    # GEO-07：去掉 2px 抖动阈值
    "M4-no-threshold": (
        "web/src/canvas/interactions.ts",
        "export function trackPointer(e: ReactPointerEvent, { onMove, onEnd, threshold = 2 }",
        "export function trackPointer(e: ReactPointerEvent, { onMove, onEnd, threshold = 0 }",
        [f"{VITEST} src/canvas/geometryReference.test.ts -t 'GEO-07'"],
    ),
    # GEO-06：第二段拖动以 manifest 的旧锚点起算（忽略已写的 override）
    "M5-stale-anchor": (
        "web/src/lib/elementGeom.ts",
        "  if (ov && Array.isArray(ov.value)) {\n    const v = ov.value as number[]\n    return [v[0], v[1]]",
        "  if (ov && Array.isArray(ov.value) && false) {\n    const v = ov.value as number[]\n    return [v[0], v[1]]",
        [f"{VITEST} src/canvas/geometryReference.test.ts -t 'GEO-06'"],
    ),
    # GEO-04：成组缩放西侧手柄的基点错了（左边不再跟着走 = 对面的东边不守恒）
    "M6-group-resize-anchor": (
        "web/src/lib/axesLayout.ts",
        "west ? gx + gw - gw * sx : gx,",
        "gx,",
        [f"{VITEST} src/canvas/geometryReference.test.ts -t 'GEO-04'"],
    ),
    # GEO-01 后端半：文字的 pos_frac 落点偏了 0.01（figure 分数）
    "M7-engine-text-offset": (
        "src/tavotto/engine/overrides.py",
        "    disp = pathgeom.frac_to_display(fig, float(value[0]), float(value[1]))\n"
        '    kind, ax = getattr(t, "_mm_drag", (None, None))',
        "    disp = pathgeom.frac_to_display(fig, float(value[0]), float(value[1]) + 0.01)\n"
        '    kind, ax = getattr(t, "_mm_drag", (None, None))',
        [f"{PY} tests/test_geometry_reference.py"],
    ),
    # GEO-10：拖轴标签时顺手把 x 轴标签钉死在一个绝对坐标上（「无效 override 锁住未移动对象」）
    "M8-engine-pins-unmoved-label": (
        "src/tavotto/engine/overrides.py",
        "        fx, fy = ax.transAxes.inverted().transform(disp)\n"
        "        axis.set_label_coords(float(fx), float(fy))\n"
        "        return\n"
        '    if kind == "title":',
        "        fx, fy = ax.transAxes.inverted().transform(disp)\n"
        "        axis.set_label_coords(float(fx), float(fy))\n"
        '        ax.xaxis.set_label_coords(0.5, -0.12) if kind == "ylabel" else None\n'
        "        return\n"
        '    if kind == "title":',
        [f"{PY} tests/test_geometry_reference.py -k 'moved_label or single_drag'"],
    ),
    # GEO-09 / GEO-01：一次单元素拖动落成两条历史（先写一条原值、再写终值）
    "M9-drag-two-history-entries": (
        "web/src/canvas/interactions.ts",
        "      setOverride(panel.id, element.gid, dragProp, [anchor[0] + dfx, anchor[1] + dfy], true)",
        "      setOverride(panel.id, element.gid, dragProp, [anchor[0], anchor[1] + 1e-3], 'none')\n"
        "      setOverride(panel.id, element.gid, dragProp, [anchor[0] + dfx, anchor[1] + dfy], true)",
        [
            f"{VITEST} src/canvas/geometryReference.test.ts -t 'GEO-09'",
            f"{VITEST} src/canvas/geometryReference.test.ts -t 'GEO-01'",
        ],
    ),
}


def run(cmd: str, log) -> int:
    log.write(f"\n$ {cmd}\n")
    log.flush()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run(cmd, shell=True, cwd=ROOT, env=env, capture_output=True, text=True)
    out = (p.stdout + p.stderr).splitlines()
    keep = (
        out if len(out) <= 190 else out[:40] + [f"... [截断 {len(out) - 190} 行] ..."] + out[-150:]
    )
    log.write("\n".join(keep) + f"\n[exit={p.returncode}]\n")
    log.flush()
    return p.returncode


def main() -> int:
    mid, log_path = sys.argv[1], sys.argv[2]
    path, old, new, cmds = MUTATIONS[mid]
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n===== mutation {mid} · {datetime.datetime.now().isoformat()} · HEAD {sha}\n")
        log.write(f"target: {path}\n- {old!r}\n+ {new!r}\n")
        src = open(os.path.join(ROOT, path), encoding="utf-8").read()
        n = src.count(old)
        log.write(f"target string occurrences: {n}\n")
        if n != 1:
            log.write("落点不唯一 / 不存在：变异不算数\n")
            return 3
        open(os.path.join(ROOT, path), "w", encoding="utf-8").write(src.replace(old, new))
        mutated = [run(c, log) for c in cmds]
        subprocess.run(["git", "checkout", "--", path], cwd=ROOT, check=True)
        log.write(f"\n-- restored {path} (git checkout --) --\n")
        restored = [run(c, log) for c in cmds]
        log.write(f"\nSUMMARY {mid}: mutated_exit={mutated} restored_exit={restored}\n")
        print(f"{mid}: mutated_exit={mutated} restored_exit={restored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
