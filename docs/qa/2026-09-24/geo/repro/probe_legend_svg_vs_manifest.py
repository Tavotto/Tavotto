"""探针：图例 loc_frac 平移后，**同一次响应**里的 SVG 与 manifest 对图例位置是否说的是同一件事。

浏览器腿里图例多走了 ~1.47 CSS px（Fig1_kinetics，zoom 112%，Δs=-33 px），而
probe_legend_shift.py 显示 manifest 的 anchor / bbox 恰好平移 Δ。这里读 inline SVG 里图例内
第一个图元的坐标，与 manifest bbox 的平移量对比。

用法（从 worktree 根目录）::

    TAVOTTO_DATA_DIR=<scratch>/data TAVOTTO_WORKER_PYTHON=/opt/homebrew/opt/python@3.13/libexec/bin/python3 \\
    PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python \\
        docs/qa/2026-09-24/geo/repro/probe_legend_svg_vs_manifest.py <scratch_dir> [fig1|g1]
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from tavotto.engine import pool

ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).resolve().parent


def first_xy(svg: str) -> tuple[float, float]:
    i = svg.index('<g id="axes_0.legend">')
    seg = svg[i : i + 40000]
    m = re.search(r'd="M ([-\d.]+) ([-\d.]+)', seg)
    return float(m.group(1)), float(m.group(2))


def main() -> int:
    which = sys.argv[2] if len(sys.argv) > 2 else "fig1"
    work = Path(sys.argv[1]).resolve() / f"legend-svg-{which}"
    if work.exists():
        shutil.rmtree(work)
    if which == "fig1":
        shutil.copytree(ROOT / "examples" / "figures", work)
        script, stem = "fig1_kinetics.py", "Fig1_kinetics"
    else:
        work.mkdir(parents=True)
        shutil.copy(HERE / "geo_library.py", work / "geo_library.py")
        script, stem = "geo_library.py", "G1"
    w = pool.one_shot(script, str(work), "main")
    rows = []
    try:
        w.ensure_built()
        r0 = w.override(stem, [], inline_svg=True)
        leg0 = next(e for e in r0["manifest"]["elements"] if e["gid"] == "axes_0.legend")
        vbw, vbh = map(float, re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', r0["svg"]).groups())
        x0, y0 = first_xy(r0["svg"])
        for dfx, dfy in ((-0.1013, 0.0), (-0.05, 0.0), (0.03, 0.0), (0.0, 0.05), (-0.1013, 0.0583)):
            t = [round(leg0["anchor"][0] + dfx, 4), round(leg0["anchor"][1] + dfy, 4)]
            patch = {"gid": "axes_0.legend", "prop": "loc_frac", "value": t}
            r = w.override(stem, [patch], inline_svg=True)
            leg = next(e for e in r["manifest"]["elements"] if e["gid"] == "axes_0.legend")
            x1, y1 = first_xy(r["svg"])
            mdx = leg["bbox"][0] - leg0["bbox"][0]
            mdy = leg["bbox"][1] - leg0["bbox"][1]
            row = {
                "stem": stem,
                "requested": [dfx, dfy],
                "manifest_bbox_d": [mdx, mdy],
                "svg_content_d": [(x1 - x0) / vbw, (y1 - y0) / vbh],
                "svg_minus_manifest_frac": [(x1 - x0) / vbw - mdx, (y1 - y0) / vbh - mdy],
                "svg_minus_manifest_pt": [(x1 - x0) - mdx * vbw, (y1 - y0) - mdy * vbh],
            }
            rows.append(row)
            print(json.dumps(row))
    finally:
        pool.discard(w)
    (work.parent / f"legend_svg_probe_{which}.json").write_text(json.dumps(rows, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
