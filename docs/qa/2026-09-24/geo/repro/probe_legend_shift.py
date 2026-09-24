"""探针：示例图 Fig1_kinetics 的图例写 loc_frac = anchor + Δ 之后，墨迹框是否也平移 Δ。

背景：浏览器腿（e2e/geometry-reference.spec.ts 的 GEO-02）里，标题 + 图例整组平移 Δs=(-33, 19) CSS px，
标题误差 0.002 px，图例 x 方向多走了约 1.4 px。这里绕开前端，直接对 worker 发同一类 patch，
看偏差是否出在引擎（anchor 与 bbox 的关系）上。

用法（从 worktree 根目录）::

    TAVOTTO_DATA_DIR=<scratch>/data TAVOTTO_WORKER_PYTHON=/opt/homebrew/opt/python@3.13/libexec/bin/python3 \\
    PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python docs/qa/2026-09-24/geo/repro/probe_legend_shift.py <scratch_dir>
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from tavotto.engine import pool

ROOT = Path(__file__).resolve().parents[5]


def main() -> int:
    work = Path(sys.argv[1]).resolve() / "legend-probe"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(ROOT / "examples" / "figures", work)
    w = pool.one_shot("fig1_kinetics.py", str(work), "main")
    out: dict = {"cases": []}
    try:
        w.ensure_built()
        base = {e["gid"]: e for e in w.override("Fig1_kinetics", [])["manifest"]["elements"]}
        size = w.override("Fig1_kinetics", [])["manifest"]["size_mm"]
        out["size_mm"] = size
        for gid in ("axes_0.legend", "axes_0.title"):
            el = base[gid]
            for dfx, dfy in ((-0.08, 0.0), (0.0, 0.05), (-0.08, 0.05), (0.05, -0.03)):
                target = [round(el["anchor"][0] + dfx, 4), round(el["anchor"][1] + dfy, 4)]
                patch = {"gid": gid, "prop": el["drag_prop"], "value": target}
                man = {
                    e["gid"]: e
                    for e in w.override("Fig1_kinetics", [patch])["manifest"]["elements"]
                }
                m = man[gid]
                row = {
                    "gid": gid,
                    "prop": el["drag_prop"],
                    "delta": [dfx, dfy],
                    "anchor_err": [m["anchor"][0] - target[0], m["anchor"][1] - target[1]],
                    "bbox_shift_err": [
                        m["bbox"][0] - el["bbox"][0] - (target[0] - el["anchor"][0]),
                        m["bbox"][1] - el["bbox"][1] - (target[1] - el["anchor"][1]),
                    ],
                    "bbox_size_change": [
                        m["bbox"][2] - el["bbox"][2],
                        m["bbox"][3] - el["bbox"][3],
                    ],
                    "bbox_shift_err_mm": [
                        (m["bbox"][0] - el["bbox"][0] - (target[0] - el["anchor"][0])) * size[0],
                        (m["bbox"][1] - el["bbox"][1] - (target[1] - el["anchor"][1])) * size[1],
                    ],
                }
                out["cases"].append(row)
                print(json.dumps(row))
            w.override("Fig1_kinetics", [])
        leg = base["axes_0.legend"]
        out["legend_base"] = {k: leg.get(k) for k in ("anchor", "bbox", "drag_prop")}
        print("legend base", out["legend_base"])
    finally:
        pool.discard(w)
    (work.parent / "legend_probe.json").write_text(json.dumps(out, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
