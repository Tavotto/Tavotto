#!/usr/bin/env python3
"""重新生成前端样式预览的 matplotlib SVG fixture。

前端的「SVG 局部样式预览」（web/src/lib/svgStyle.ts）判断的是 matplotlib
**实际输出**的属性形状——颜色落在后代 path 的 inline style 上、alpha 是分开的
fill-opacity/stroke-opacity、线宽等于默认值时 stroke-width 根本不输出、文字颜色
在字形组上而默认黑色时那条 style 不存在。手写一份「看起来像 matplotlib」的
fixture 只能验证我们对它的想象，所以这份由真产物生成。

    python scripts/dump_svg_fixture.py            # 就地更新 fixture
    python scripts/dump_svg_fixture.py --check    # 只比对，不写（CI 可用）

需要科学栈：解释器用 `engine.pool.find_worker_python()` 探到的那个
（与 worker 同源），本进程的 .venv 里没有 matplotlib。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "web", "src", "lib", "__fixtures__", "matplotlibSvg.ts")

# 子进程里跑：本脚本所在解释器不一定有 matplotlib
_CHILD = r'''
import sys, os, re, json
sys.path.insert(0, os.path.join(%(root)r, "src", "magplot", "engine"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, PathPatch
from matplotlib.path import Path
import manifest as M, overrides as O

fig, ax = plt.subplots(figsize=(4, 3))
x = np.linspace(0, 6, 25)
ax.plot(x, np.sin(x), color="#1f77b4", lw=1.5, label="sin")
ax.plot(x, np.cos(x), color="#d62728", lw=2.0, ls="--", label="cos")
ax.scatter(x[::5], np.sin(x[::5]), s=40, facecolor="#2ca02c",
           edgecolor="#000000", lw=0.8, label="pts")
# lw=1.0 是默认值：matplotlib 不会输出 stroke-width，线宽适配器必须按 stroke 判断
ax.bar([1, 3, 5], [0.4, 0.6, 0.3], width=0.5, facecolor="#ff7f0e",
       edgecolor="#333333", lw=1.0, label="bars")
ax.errorbar([2, 4], [-0.5, -0.7], yerr=[0.1, 0.2], fmt="o", color="#9467bd",
            lw=1.2, capsize=3, label="err")
# alpha<1 → fill-opacity / stroke-opacity 分开输出（不是一个 opacity）
ax.fill_between(x, np.sin(x) - 0.2, np.sin(x) - 0.5, facecolor="#8c564b",
                edgecolor="#111111", lw=0.5, alpha=0.5)
# "-|>" 的帽是填充的、杆是 fill:none —— 箭头颜色要同时作用于 stroke 与 fill
ax.add_patch(FancyArrowPatch((1, 0.5), (3, 0.8), arrowstyle="-|>",
                             mutation_scale=12, color="#e377c2", lw=1.4))
# 独立形状（role=patch）两种形态，都**落在已有数据范围之内**——伸出去会改
# autoscale，整份 fixture 的坐标全变，真正要看护的样式差异就淹没在噪音里。
# 填充的：fill 与 stroke 各一条，两者互不串味
ax.fill([0.5, 1.5, 1.0], [-1.0, -1.0, -0.6],
        facecolor="#17becf", edgecolor="#5a3286", lw=1.2)
# 空心的（fill=False）：matplotlib 写的是 `fill: none`，facecolor 预览必须
# 认这条规则、绝不把它填实
ax.add_patch(PathPatch(
    Path([[2.5, -1.2], [2.8, -0.8], [3.2, -1.2], [3.5, -0.9]],
         [Path.MOVETO, Path.CURVE4, Path.CURVE4, Path.CURVE4]),
    fill=False, edgecolor="#7f7f0f", lw=1.6))
ax.set_title("Title here")      # 默认黑：字形组上**没有** style
ax.set_xlabel("X label"); ax.set_ylabel("Y label")
ax.text(1, 0.9, "inline text", color="#123456")      # 有颜色 → style="fill: …"
ax.text(2, 0.7, "faded", color="#aa0000", alpha=0.4) # 有 alpha → 再加 opacity
ax.imshow(np.zeros((2, 2)), extent=(4.2, 5.8, 0.55, 0.95))  # gid 落在 <image> 自身
ax.legend(loc="upper right")

state = O.FigState(fig)
M.instrument(state)
mani = M.build_manifest(state, "Fixture")
buf = __import__("io").StringIO()
fig.savefig(buf, format="svg")
print(json.dumps({"svg": buf.getvalue(),
                  "gids": [[e["gid"], e["role"]] for e in mani["elements"]]}))
''' % {"root": ROOT}

HEADER = '''/* eslint-disable */
/**
 * **真实 matplotlib 输出**的 SVG fixture，由 `scripts/dump_svg_fixture.py` 生成
 * （不要手改）。样式适配器的全部断言都打在这份上——手写一份「看起来像
 * matplotlib」的 SVG 只能验证我们对它的想象。
 *
 * 与真产物的唯一差别：字形/曲线的 `d=` 路径数据与内嵌 PNG 的 base64 被压短
 * （与样式无关，只是让 fixture 能读）。**`style=` 属性一个字节都没动**——
 * 适配器判断的正是它们的形状：
 *
 *   line      `<path style="fill: none; stroke: #1f77b4; stroke-width: 1.5">`
 *   bar       `<path style="fill: #ff7f0e; stroke: #333333; …">`（**没有**
 *             stroke-width：线宽等于默认值时 matplotlib 不输出，所以线宽的
 *             判据必须是 stroke 而不是 stroke-width）
 *   scatter   `<defs><path style="stroke…"/></defs>` + `<use style="fill…; stroke…">`
 *             （`<use>` 影子树里被引用元素自带的样式优先，两处都要改）
 *   fill      `<use style="fill: …; fill-opacity: 0.5; stroke: …; stroke-opacity: 0.5">`
 *             ——alpha 是分开的两条，不是一个 opacity
 *   arrow     杆 `fill: none` + 帽 `fill: <色>`，颜色要同时作用于两者
 *   patch     `ax.fill()` 的 Polygon → `fill: <色>; stroke: <色>`；
 *             `fill=False` 的 PathPatch → `fill: none`（facecolor 不许填实它）
 *   text      `<g style="fill: #123456" transform="…">`；**默认黑色时没有这条 style**
 *   image     gid 落在 `<image>` 自身，且自带 transform（alpha 烤进 PNG，改不了）
 *
 * 这些 gid 在 manifest 里有、在 SVG 里**没有**（SeriesGroup / TickSet 伪元素），
 * 只能回退后端：axes_0.errorbar_* / axes_0.barseries_N / axes_0.[xy]ticks /
 * axes_0.[xy]ticklabels_*。
 */
'''


def shrink(svg: str) -> str:
    import re
    svg = svg[svg.index("<svg"):]
    svg = re.sub(r"<metadata>[\s\S]*?</metadata>", "", svg)
    svg = re.sub(r'\sd="[^"]{60,}"', ' d="M 0 0 L 1 1"', svg)
    svg = re.sub(r'xlink:href="data:image/png;base64,[^"]*"',
                 'xlink:href="data:image/png;base64,iVBORw0KGgo="', svg)
    svg = re.sub(r"\n\s*\n", "\n", svg)
    # 行尾空格：matplotlib 的路径数据里每个折点后面都跟一个空格再换行。它对
    # SVG 毫无意义，却让这份文件每一次重生成都往 `git diff --check` 里塞一堆
    # 「trailing whitespace」——真正要看的输出变化就淹没在里面了。
    # **只削行尾，不碰任何 `style=`**（适配器判断的正是那些属性的形状）。
    svg = re.sub(r"[ \t]+\n", "\n", svg)
    # matplotlib 给 clipPath 与 marker 模板发的是随机 id（每次跑都不同）。
    # 不归一化的话 fixture 每生成一次就「变了」，--check 永远红，
    # 真正的输出变化（我们要看护的那种）就淹没在噪音里。
    seen: dict[str, str] = {}
    for raw in re.findall(r'\b([pm][0-9a-f]{10})\b', svg):
        seen.setdefault(raw, "%s%03d" % (raw[0], len(seen)))
    for raw, stable in seen.items():
        svg = re.sub(r"\b%s\b" % raw, stable, svg)
    return svg.strip()


def build() -> str:
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from magplot.engine import pool  # noqa: PLC0415 — 只有这里要

    py = pool.find_worker_python()
    if not py:
        raise SystemExit("找不到带科学栈的解释器（MM_WORKER_PYTHON 可覆盖）")
    out = subprocess.run([py, "-c", _CHILD], capture_output=True, text=True, check=True)
    import json
    data = json.loads(out.stdout.strip().splitlines()[-1])
    svg = shrink(data["svg"])
    if "`" in svg or "${" in svg:
        raise SystemExit("SVG 里出现了反引号 / ${，不能塞进模板字符串")
    return HEADER + "export const MATPLOTLIB_SVG = String.raw`" + svg + "`\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对，不写")
    args = ap.parse_args()
    text = build()
    if args.check:
        cur = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if cur != text:
            print("fixture 与当前 matplotlib 输出不一致：跑一次 "
                  "`python scripts/dump_svg_fixture.py` 更新，并复核 svgStyle 的适配器",
                  file=sys.stderr)
            return 1
        print("fixture 与当前 matplotlib 输出一致")
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("已写入", os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
