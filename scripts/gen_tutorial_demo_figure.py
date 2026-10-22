#!/usr/bin/env python3
"""生成 / 校对 `web/src/lib/onboarding/demoFigure.json`——新手教程演示图的几何。

新手教程（ADR 0136）**不执行任何 Python**：教程里那张「反应动力学」图由前端按这份
JSON 画成 SVG，用户在图上拖图例、改标题字号，改的是教程自己的演示状态。为了让那张图
与随包分发的示例脚本说的是**同一张图**，几何不手写，而是开发期用真 matplotlib 跑一遍
`resources/tutorial_project/fig1_kinetics.py`、把画出来的东西逐项量下来：

* 图幅、坐标轴框、刻度（位置 / 文字 / 长度 / 朝向）、轴标签、标题锚点与字号；
* 两条曲线的全部采样点（与脚本里 `np.linspace(0, 60, 200)` 一一对应）、颜色、线宽、虚线；
* 图例的外框与每一行（句柄线段 + 文字）相对外框的位置。

单位一律是 pt（1/72 in），原点在图幅左上（SVG 方向）。

    python scripts/gen_tutorial_demo_figure.py            # 校对（有分歧就非零退出）
    python scripts/gen_tutorial_demo_figure.py --write    # 按当前脚本重新生成

**脚本是唯一权威**，这份 JSON 是它的镜像：改了 `fig1_kinetics.py` / `paper_style.py`
而没重新生成，`tests/test_tutorial_demo_figure.py` 红。校对按容差比数值（不同 matplotlib
版本的文字外框有亚 pt 级差异），文字与颜色逐字比。

欢迎页展示的代码片段（`snippet`）也放在这里：每一行都必须是脚本里**逐字存在**的一行，
同一个用例看护——欢迎页上不许出现一行脚本里没有的代码。
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESOURCES = ROOT / "src" / "tavotto" / "resources" / "tutorial_project"
SCRIPT = RESOURCES / "fig1_kinetics.py"
OUT = ROOT / "web" / "src" / "lib" / "onboarding" / "demoFigure.json"

#: 欢迎页「脚本 → 图」里展示的几行。分组之间是省略（界面画一条省略行，不冒充连续代码）。
SNIPPET: list[list[str]] = [
    ["import matplotlib.pyplot as plt"],
    [
        "t = np.linspace(0, 60, 200)",
        "fast = 1 - np.exp(-t / 8)",
        "slow = 1 - np.exp(-t / 24)",
    ],
    [
        'ax.set_title("Reaction kinetics at 25 °C")',
        "ax.legend(loc=(0.22, 0.42), fontsize=7.2)",
    ],
]

#: 校对容差（pt）：文字外框随 matplotlib 版本有亚 pt 级差异，几何本身不该漂。
TOLERANCE_PT = 0.75


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _capture_figure():
    """跑 `fig1_kinetics.main()`，把 `save()` 拦下来拿到内存里的 Figure（不写任何文件）。"""
    import matplotlib

    matplotlib.use("Agg")
    sys.path.insert(0, str(RESOURCES))
    try:
        for name in ("fig1_kinetics", "paper_style"):
            sys.modules.pop(name, None)
        paper_style = importlib.import_module("paper_style")
        captured: dict = {}

        def save(fig, stem, outdir=None):  # noqa: ARG001 — 与原签名一致
            captured["fig"] = fig

        paper_style.save = save
        fig1 = importlib.import_module("fig1_kinetics")
        fig1.main()
        return captured["fig"]
    finally:
        sys.path.remove(str(RESOURCES))


def _r(v: float) -> float:
    return round(float(v), 2)


def build() -> dict:
    """在 matplotlib 默认 rcParams 上量（= 新进程里跑脚本），量完还原调用方的 rcParams。

    同一进程里先跑过的代码（pytest 的其它用例、调用方自己的样式）改过的 rcParams 会漏进
    脚本没显式设的那几项（`text.hinting`、`legend.labelspacing`……），把文字外框量偏几个 pt。
    """
    import matplotlib

    with matplotlib.rc_context():
        matplotlib.rcdefaults()
        return _build()


def _build() -> dict:
    import matplotlib.colors as mcolors
    import numpy as np

    fig = _capture_figure()
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    ax = fig.axes[0]
    k = 72.0 / fig.dpi  # 显示像素 → pt
    width, height = (float(v) * 72.0 for v in fig.get_size_inches())

    def pt(x: float, y: float) -> list[float]:
        return [_r(x * k), _r(height - y * k)]

    def box(extent) -> list[float]:
        """显示坐标的外框 → [左, 上, 宽, 高]（pt，SVG 方向）"""
        x0, y0 = extent.x0 * k, height - extent.y1 * k
        return [_r(x0), _r(y0), _r(extent.width * k), _r(extent.height * k)]

    def text(t, *, rotation: float = 0.0) -> dict:
        return {
            "text": t.get_text(),
            "box": box(t.get_window_extent(renderer)),
            # matplotlib 放这段文字时的对齐锚点（位置经它自己的变换）：外框随字形度量伸缩，锚点不动
            "anchor": pt(*t.get_transform().transform(t.get_position())),
            "sizePt": _r(t.get_fontsize()),
            "bold": t.get_fontweight() in ("bold", 700, "heavy"),
            "rotation": rotation,
        }

    def ticks(axis, which: str) -> list[dict]:
        lo, hi = sorted(getattr(ax, f"get_{which}lim")())
        out = []
        for tick in axis.get_major_ticks():
            loc = tick.get_loc()
            if not (lo - 1e-9 <= loc <= hi + 1e-9) or not tick.label1.get_visible():
                continue
            disp = ax.transData.transform((loc, 0) if which == "x" else (0, loc))
            out.append(
                {
                    "at": _r(disp[0] * k if which == "x" else height - disp[1] * k),
                    "label": text(tick.label1),
                }
            )
        return out

    ax_box = box(ax.get_window_extent(renderer))
    major = ax.xaxis.get_major_ticks()[0]
    tick_style = {
        "lengthPt": _r(major.tick1line.get_markersize()),
        "widthPt": _r(major.tick1line.get_markeredgewidth()),
        "inward": major._tickdir == "in",  # noqa: SLF001 — 公开 API 只在 3.7+ 且不含默认值
    }

    lines = []
    for line in ax.get_lines():
        xy = line.get_transform().transform(np.column_stack(line.get_data()))
        offset, dashes = line._dash_pattern  # noqa: SLF001 — 已按线宽缩放、单位 pt
        lines.append(
            {
                "label": line.get_label(),
                "color": mcolors.to_hex(line.get_color()),
                "widthPt": _r(line.get_linewidth()),
                "dash": [_r(d) for d in dashes] if dashes else None,
                "points": [pt(x, y) for x, y in xy],
            }
        )

    legend = ax.get_legend()
    lg_box = box(legend.get_window_extent(renderer))
    entries = []
    handles = getattr(legend, "legend_handles", None) or legend.legendHandles
    for handle, label in zip(handles, legend.get_texts(), strict=True):
        hx, hy = handle.get_transform().transform(np.column_stack(handle.get_data())).T
        seg = [pt(hx.min(), hy.mean()), pt(hx.max(), hy.mean())]
        lb = text(label)
        entries.append(
            {
                # 句柄与文字都相对图例外框左上角：图例整体挪动时只换外框的位置
                "handle": [[_r(p[0] - lg_box[0]), _r(p[1] - lg_box[1])] for p in seg],
                "text": lb["text"],
                "box": [
                    _r(lb["box"][0] - lg_box[0]),
                    _r(lb["box"][1] - lg_box[1]),
                    lb["box"][2],
                    lb["box"][3],
                ],
                "sizePt": lb["sizePt"],
            }
        )

    title = ax.title
    anchor = title.get_transform().transform(title.get_position())
    return {
        "schema": 1,
        "comment": (
            "生成物：新手教程演示图的几何（pt，原点左上）。唯一产生者 "
            "scripts/gen_tutorial_demo_figure.py，权威是 resources/tutorial_project/fig1_kinetics.py；手改无效。"
        ),
        "script": SCRIPT.name,
        "stem": "Fig1_kinetics",
        "figure": {"width": _r(width), "height": _r(height)},
        "fontFamily": "STIX",
        # 生成时的 matplotlib 主.次版本：字形度量（文字外框高度、图例行距）随版本差几个 pt，
        # 校对在版本不同的环境里只放宽这些量，其余照样逐项比（见 compare）
        "matplotlib": _mpl_minor(),
        "axes": {"box": ax_box, "spineWidthPt": _r(ax.spines["left"].get_linewidth())},
        "ticks": {"style": tick_style, "x": ticks(ax.xaxis, "x"), "y": ticks(ax.yaxis, "y")},
        "xlabel": text(ax.xaxis.label),
        "ylabel": text(ax.yaxis.label, rotation=-90.0),
        "title": {
            "text": title.get_text(),
            "sizePt": _r(title.get_fontsize()),
            # matplotlib 的标题是「水平居中、基线对齐」到这一点（已含 titlepad）：字号变大往上长
            "anchor": pt(*anchor),
        },
        "lines": lines,
        "legend": {"box": lg_box, "entries": entries},
        "snippet": SNIPPET,
    }


def _mpl_minor() -> str:
    import matplotlib

    return ".".join(matplotlib.__version__.split(".")[:2])


# 版本不同时怎么比（实测 3.10 vs 3.11：文字外框的宽高、图例的行高随字形度量变几个 pt，
# 但文字外框的锚点（按对齐方式：顶边 / 中心……）、图例的**左下角**（脚本 loc=(x, y) 元组钉的那一角）两版一致）：
#   * 文字 → 比 matplotlib 实际的对齐锚点 `anchor`（严格容差），外框放过；
#   * 图例外框 → 比左边与下边（锚定的那个角），宽高放过；
#   * 图例条目内部（每行外框、句柄位置）→ 行距随字高变，用有界的 METRIC_TOLERANCE_PT；
#   * 其余（坐标轴框、曲线、刻度位置、标题锚点、文字内容、颜色）照样严格。
METRIC_TOLERANCE_PT = 3.0
_LEGEND_ENTRY = re.compile(r"^\$\.legend\.entries\[\d+\]\.(box|handle)")


def _mpl_minor() -> str:
    import matplotlib

    return ".".join(matplotlib.__version__.split(".")[:2])


def compare(built: dict, on_disk: dict) -> list[str]:
    """按版本选比法：生成物的 matplotlib 主.次版本与此刻相同 → 逐项严格比；不同 → 只放宽字形度量量。"""
    loose = built.get("matplotlib") != on_disk.get("matplotlib")
    return _diff(built, on_disk, loose=loose)


def _num_diff(want, got, path: str, tol: float) -> list[str]:
    return [] if abs(want - got) <= tol else [f"{path}: {want} ≠ {got}（容差 {tol} pt）"]


def _loose_box(want: list, got: list, path: str) -> list[str] | None:
    """版本不同时的外框比法；不是外框（或是坐标轴框）回 None，交给通用比法。"""
    if not path.endswith(".box") or path.startswith("$.axes."):
        return None
    if len(want) != 4 or len(got) != 4:
        return None
    if path == "$.legend.box":
        # 脚本里 `loc=(x, y)` 元组钉的是图例左下角：跨版本只有左边与下边不随字宽变
        return _num_diff(want[0], got[0], f"{path} 左边", TOLERANCE_PT) + _num_diff(
            want[1] + want[3], got[1] + got[3], f"{path} 下边", TOLERANCE_PT
        )
    # 文字外框：版本不同时不比（宽高、甚至位置都随字形度量变）；由同一条目里的 `anchor` 严格比
    return None


def _diff(want, got, path: str = "$", *, loose: bool = False) -> list[str]:
    if isinstance(want, dict) and isinstance(got, dict):
        out: list[str] = []
        for key in sorted(set(want) | set(got)):
            if key in ("comment", "matplotlib"):
                continue
            # 版本不同：带锚点的文字只比锚点，不比外框
            if loose and key == "box" and "anchor" in want and "anchor" in got:
                continue
            if key not in want or key not in got:
                out.append(f"{path}.{key}: 只在一侧")
                continue
            out += _diff(want[key], got[key], f"{path}.{key}", loose=loose)
        return out
    if isinstance(want, list) and isinstance(got, list):
        if loose and (boxed := _loose_box(want, got, path)) is not None:
            return boxed
        if len(want) != len(got):
            return [f"{path}: 长度 {len(want)} ≠ {len(got)}"]
        out = []
        for i, (a, b) in enumerate(zip(want, got, strict=True)):
            out += _diff(a, b, f"{path}[{i}]", loose=loose)
        return out
    if isinstance(want, bool) or isinstance(got, bool) or not isinstance(want, int | float):
        return [] if want == got else [f"{path}: {want!r} ≠ {got!r}"]
    if not isinstance(got, int | float):
        return [f"{path}: {want!r} ≠ {got!r}"]
    if loose and _LEGEND_ENTRY.match(path):
        return _num_diff(want, got, path, METRIC_TOLERANCE_PT)
    return _num_diff(want, got, path, TOLERANCE_PT)


def check() -> list[str]:
    """磁盘上的生成物与按当前脚本重新量出来的比；回分歧列表（空 = 一致）。"""
    try:
        on_disk = json.loads(OUT.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"{OUT} 不存在：先 --write"]
    return compare(build(), on_disk)


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="重新生成并写盘")
    args = ap.parse_args()
    if args.write:
        OUT.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"写入 {OUT.relative_to(ROOT)}")
        return 0
    problems = check()
    for p in problems[:40]:
        print(p)
    if problems:
        print(f"共 {len(problems)} 处分歧；脚本改过就 --write 重新生成")
        return 1
    print("一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
