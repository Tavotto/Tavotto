"""探针：GEO-08（注释文字拖动）与 GEO-10（constrained 布局下改图幅/字号后拖过的标题）。

用法（从 worktree 根目录）::

    TAVOTTO_DATA_DIR=<scratch>/data TAVOTTO_WORKER_PYTHON=/opt/homebrew/opt/python@3.13/libexec/bin/python3 \\
    PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python \\
        docs/qa/2026-09-24/geo/repro/probe_geo08_geo10.py <scratch_dir>

脚本内容与 tests/test_geometry_reference.py 的 LIBRARY 相同（从那里读，保证是同一份夹具）。
每个场景：热会话一次 + 全新 worker 一次性重放一次，打印目标锚点与写下的值之差。
"""

from __future__ import annotations

import ast
import json
import shutil
import sys
from pathlib import Path

from tavotto.engine import pool

ROOT = Path(__file__).resolve().parents[5]


def library() -> str:
    tree = ast.parse((ROOT / "tests" / "test_geometry_reference.py").read_text("utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "LIBRARY":
            return node.value.value
    raise SystemExit("LIBRARY 不在 tests/test_geometry_reference.py 里")


def by_gid(resp):
    return {e["gid"]: e for e in resp["manifest"]["elements"]}


def main() -> int:
    work = Path(sys.argv[1]).resolve() / "geo08-10"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / "fig_geo_reference.py").write_text(library(), "utf-8")
    out: dict = {}
    w = pool.one_shot("fig_geo_reference.py", str(work), "main")
    try:
        w.ensure_built()
        # ---- GEO-08：G8 里每段文字的角色 / 可拖 / 写 pos_frac 之后动没动
        b8 = by_gid(w.override("G8", []))
        rows = []
        for g, e in sorted(b8.items()):
            if e["role"] not in ("text", "annotation") or not e.get("drag_prop"):
                continue
            t = [e["anchor"][0] - 0.05, e["anchor"][1] + 0.04]
            m = by_gid(w.override("G8", [{"gid": g, "prop": e["drag_prop"], "value": t}]))[g]
            rows.append(
                {
                    "gid": g,
                    "role": e["role"],
                    "label": e.get("label"),
                    "drag_prop": e["drag_prop"],
                    "anchor0": e["anchor"],
                    "target": t,
                    "anchor_after": m["anchor"],
                    "moved": m["anchor"] != e["anchor"],
                    "err": [m["anchor"][0] - t[0], m["anchor"][1] - t[1]],
                    "warnings": None,
                }
            )
        w.override("G8", [])
        out["geo08"] = rows
        # ---- GEO-10：拖过的标题在改图幅 / 字号之后
        b10 = by_gid(w.override("G10", []))
        t0 = b10["axes_0.title"]["anchor"]
        target = [round(t0[0] - 0.1, 4), round(t0[1] + 0.03, 4)]
        moved = {"gid": "axes_0.title", "prop": "pos_frac", "value": target}
        size = {"gid": "figure", "prop": "size_mm", "value": [150.0, 70.0]}
        fs_x = {"gid": "axes_0.xlabel", "prop": "fontsize", "value": 16.0}
        fs_t = {"gid": "axes_0.title", "prop": "fontsize", "value": 14.0}
        scen = {
            "moved_only": [moved],
            "moved+size": [moved, size],
            "size+moved": [size, moved],
            "moved+title_fs": [moved, fs_t],
            "moved+xlabel_fs": [moved, fs_x],
            "moved+size+fs_x+fs_t": [moved, size, fs_x, fs_t],
        }
        rows10 = []
        for name, patches in scen.items():
            w.override("G10", [])
            hot = by_gid(w.override("G10", patches))["axes_0.title"]["anchor"]
            fresh_w = pool.one_shot("fig_geo_reference.py", str(work), "main")
            try:
                fresh_w.ensure_built()
                fresh = by_gid(fresh_w.override("G10", patches))["axes_0.title"]["anchor"]
            finally:
                pool.discard(fresh_w)
            rows10.append(
                {
                    "scenario": name,
                    "target": target,
                    "hot_anchor": hot,
                    "fresh_anchor": fresh,
                    "hot_err": [hot[0] - target[0], hot[1] - target[1]],
                    "fresh_err": [fresh[0] - target[0], fresh[1] - target[1]],
                }
            )
        out["geo10"] = rows10
    finally:
        pool.discard(w)
    print(json.dumps(out, indent=1, ensure_ascii=False))
    (work.parent / "probe_geo08_geo10.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False), "utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
