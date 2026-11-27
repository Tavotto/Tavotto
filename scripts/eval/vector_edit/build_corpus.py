"""生成 corpus.json（题库以 JSON 为准入库；本脚本是它的出处，改题改这里再重新生成）。

    python scripts/eval/vector_edit/build_corpus.py          # 写 corpus.json
    python scripts/eval/vector_edit/build_corpus.py --check  # 只核对 corpus.json 与本脚本一致（单测用）

每题是一个小项目：一张合成子图（`FigM.pdf`，脚本见 FIG_SCRIPT，写死坐标轴矩形与坐标范围，数据点的页面毫米位置有闭式解）
加若干画布矢量对象，全部合成，不含任何真实用户内容。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "corpus.json"

PAGE = {"w": 180, "h": 90}
PANEL = {
    "id": "o_panel001",
    "type": "panel",
    "x": 10.0,
    "y": 20.0,
    "w": 80.0,
    "h": 60.0,
    "fileId": "FigM.pdf",
    "fileKind": "pdf",
    "nativeW": 80,
    "nativeH": 60,
    "overrides": [],
}

#: 子图脚本：figsize 80 × 60 mm、坐标轴矩形 [0.15, 0.15, 0.8, 0.75]、x ∈ [0, 10]、y ∈ [0, 1.1]；Sample B = 1 - exp(-x/6)
FIG_SCRIPT = '''\
"""合成子图：两条曲线 + 图例（评测用，坐标轴矩形与范围写死）。"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parent


def main():
    x = np.linspace(0, 10, 12)
    y1 = 1 - np.exp(-x / 3)
    y2 = 1 - np.exp(-x / 6)
    fig = plt.figure(figsize=(80 / 25.4, 60 / 25.4))
    ax = fig.add_axes([0.15, 0.15, 0.8, 0.75])
    ax.plot(x, y1, marker="o", ms=3, lw=1.0, label="Sample A")
    ax.plot(x, y2, ls="--", marker="s", ms=3, lw=1.0, label="Sample B")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 1.1)
    ax.set_xlabel("Time (min)")
    ax.set_ylabel("Conversion (-)")
    ax.legend(loc="lower right", frameon=False)
    fig.savefig(OUT / "FigM.pdf")


if __name__ == "__main__":
    main()
'''
REGISTRY = {"scripts": {"figm.py": {"entry": "main", "cost": "light", "stems": ["FigM"]}}}


def peak_xy() -> tuple[float, float]:
    """Sample B 最右端数据点（x = 10）在画布上的毫米位置（闭式解，不依赖字体）。"""
    y = 1 - math.exp(-10 / 6)
    fx = 0.15 + 0.8 * 1.0
    fy = 0.15 + 0.75 * y / 1.1
    return round(PANEL["x"] + PANEL["w"] * fx, 3), round(PANEL["y"] + PANEL["h"] * (1 - fy), 3)


# ------------------------------------------------------------------ 对象小工具


def arrow(oid, x, y, w=20.0, h=4.0, **kw):
    return {
        "id": oid,
        "type": "arrow",
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "start": {"rx": 0, "ry": 0.5},
        "end": {"rx": 1, "ry": 0.5},
        "color": "#000000",
        "strokePt": 0.5,
        "head": "end",
        **kw,
    }


def text(oid, x, y, t="note", w=12.0, h=4.0, **kw):
    return {
        "id": oid,
        "type": "text",
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "text": t,
        "sizePt": 8,
        "bold": False,
        "align": "left",
        "color": "#000000",
        **kw,
    }


def rect(oid, x, y, w=12.0, h=8.0, **kw):
    return {
        "id": oid,
        "type": "shape",
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "shape": "rect",
        "color": "#000000",
        "fill": None,
        "strokePt": 0.75,
        **kw,
    }


_K = 0.5523


def circle(oid, cx, cy, r, **kw):
    """四个平滑锚点的圆（路径）。"""
    nodes = []
    for px, py, tx, ty in ((1, 0.5, 0, 1), (0.5, 1, -1, 0), (0, 0.5, 0, -1), (0.5, 0, 1, 0)):
        k = _K / 2
        nodes.append(
            {
                "p": [px, py],
                "in": [px - tx * k, py - ty * k],
                "out": [px + tx * k, py + ty * k],
                "smooth": True,
            }
        )
    return {
        "id": oid,
        "type": "path",
        "x": cx - r,
        "y": cy - r,
        "w": 2 * r,
        "h": 2 * r,
        "subpaths": [{"closed": True, "nodes": nodes}],
        "strokePt": 1,
        "color": "#112233",
        "fill": "#ff0000",
        **kw,
    }


def line(oid, pts, **kw):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, y0, w, h = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    return {
        "id": oid,
        "type": "path",
        "x": x0,
        "y": y0,
        "w": w,
        "h": h,
        "subpaths": [
            {
                "closed": False,
                "nodes": [{"p": [(x - x0) / (w or 1), (y - y0) / (h or 1)]} for x, y in pts],
            }
        ],
        "strokePt": 2,
        "color": "#0000ff",
        "fill": None,
        **kw,
    }


def named(o: dict, name: str) -> dict:
    return {**o, "name": name}


# ------------------------------------------------------------------ 题目


def task(tid, kind, family, instruction, objects, predicate, reference, **kw):
    return {
        "id": tid,
        "kind": kind,  # add 加对象（0208 类）/ edit 编辑 / control 对照
        "family": family,
        "instruction": instruction,
        "canvas": {"page": PAGE, "objects": [PANEL, *objects]},
        "predicate": predicate,
        "reference": reference,
        **kw,
    }


def ops(*items):
    return {"kind": "ops", "ops": list(items)}


def build() -> list[dict]:
    px, py = peak_xy()
    t: list[dict] = []

    # ---- 0208 类：加对象（A 组按设计不能新增，作对照；B 组有加标注工具）
    add_note = "A 组按设计不能新增对象（系统提示写明），应进 unhandled、画布不动"
    t.append(
        task(
            "add-arrow-peak",
            "add",
            "加箭头",
            "在子图 P1 里 Sample B 那条曲线最右端的数据点上加一支箭头，箭头尖对准那个点。",
            [],
            [{"t": "new_arrow_tip", "xy": [px, py], "r": 3.0}],
            {
                "kind": "items",
                "items": [
                    {
                        "kind": "arrow",
                        "name": "to_peak",
                        "from": {
                            "anchor": {"panel": {"id": "P1", "point": "e"}},
                            "offset_mm": [18, -12],
                        },
                        "to": {
                            "anchor": {
                                "data": {
                                    "panel": "P1",
                                    "axes": "axes_0",
                                    "x": 10,
                                    "y": round(1 - math.exp(-10 / 6), 4),
                                }
                            }
                        },
                    }
                ],
            },
            may_add=True,
            a_note=add_note,
        )
    )
    t.append(
        task(
            "add-label-a",
            "add",
            "加标注",
            "在子图 P1 的左上角外侧加一个文字标注 (a)，字号 8 pt。",
            [],
            [{"t": "new_text", "text": "(a)", "near": [12, 16, 8]}],
            {
                "kind": "items",
                "items": [
                    {
                        "kind": "text",
                        "name": "tag_a",
                        "text": "(a)",
                        "sizePt": 8,
                        "at": {
                            "anchor": {"panel": {"id": "P1", "point": "nw"}},
                            "offset_mm": [2, -3],
                        },
                        "attach": "sw",
                    }
                ],
            },
            may_add=True,
            a_note=add_note,
        )
    )
    t.append(
        task(
            "add-box",
            "add",
            "加外框",
            "给子图 P1 加一个外框，细线框住整张子图。",
            [],
            [{"t": "new_box_around", "rect": [10, 20, 80, 60], "tol": 4.0}],
            {
                "kind": "items",
                "items": [
                    {
                        "kind": "box",
                        "name": "frame",
                        "fit": [{"panel": {"id": "P1"}}],
                    }
                ],
            },
            may_add=True,
            a_note=add_note,
        )
    )
    t.append(
        task(
            "add-bracket",
            "add",
            "加括号",
            "在子图 P1 的上方加一个括号，横着括住子图的整个宽度（从子图左上角到右上角）。",
            [],
            [{"t": "new_near", "types": ["path", "shape"], "rect": [10, 8, 80, 14]}],
            {
                "kind": "items",
                "items": [
                    {
                        "kind": "bracket",
                        "name": "br",
                        "between": [
                            {"anchor": {"panel": {"id": "P1", "point": "nw"}}},
                            {"anchor": {"panel": {"id": "P1", "point": "ne"}}},
                        ],
                        "side": "above",
                    }
                ],
            },
            may_add=True,
            a_note=add_note,
        )
    )
    t.append(
        task(
            "add-text-at-peak",
            "add",
            "加标注",
            "在 Sample B 曲线最右端的数据点旁边加一行文字 plateau。",
            [],
            [{"t": "new_text", "text": "plateau", "near": [px, py, 12]}],
            {
                "kind": "items",
                "items": [
                    {
                        "kind": "text",
                        "name": "plateau",
                        "text": "plateau",
                        "sizePt": 8,
                        "at": {
                            "anchor": {
                                "data": {
                                    "panel": "P1",
                                    "axes": "axes_0",
                                    "x": 10,
                                    "y": round(1 - math.exp(-10 / 6), 4),
                                }
                            },
                            "offset_mm": [0, -4],
                        },
                        "attach": "s",
                    }
                ],
            },
            may_add=True,
            a_note=add_note,
        )
    )
    t.append(
        task(
            "add-box-and-title",
            "add",
            "加外框+标题",
            "给子图 P1 加外框，并在子图正上方居中加一行文字 Kinetics。",
            [],
            [
                {"t": "new_box_around", "rect": [10, 20, 80, 60], "tol": 4.0},
                {"t": "new_text", "text": "Kinetics", "near": [50, 16, 8]},
            ],
            {
                "kind": "items",
                "items": [
                    {
                        "kind": "box",
                        "name": "frame",
                        "fit": [{"panel": {"id": "P1"}}],
                    },
                    {
                        "kind": "text",
                        "name": "title",
                        "text": "Kinetics",
                        "sizePt": 8,
                        "at": {
                            "anchor": {"panel": {"id": "P1", "point": "n"}},
                            "offset_mm": [0, -2],
                        },
                        "attach": "s",
                    },
                ],
            },
            may_add=True,
            a_note=add_note,
        )
    )

    # ---- 编辑类
    a3 = [
        named(arrow("a1", 100, 10, strokePt=0.5), "箭头一"),
        named(arrow("a2", 108, 30, strokePt=0.5), "箭头二"),
        named(arrow("a3", 114, 50, strokePt=0.5), "箭头三"),
    ]
    t.append(
        task(
            "align-left-and-width",
            "edit",
            "对齐+线宽",
            "把右边这三支箭头（箭头一、二、三）左边对齐，线宽都改成 0.75 pt。",
            a3,
            [
                {"t": "same", "ids": ["a1", "a2", "a3"], "field": "x", "tol": 0.3},
                {"t": "field_all", "ids": ["a1", "a2", "a3"], "field": "strokePt", "eq": 0.75},
            ],
            ops(
                {"op": "align", "ids": ["a1", "a2", "a3"], "mode": "left"},
                {"op": "set", "id": "a1", "set": {"strokePt": 0.75}},
                {"op": "set", "id": "a2", "set": {"strokePt": 0.75}},
                {"op": "set", "id": "a3", "set": {"strokePt": 0.75}},
            ),
            selection=["a1", "a2", "a3"],
            may_change=["a1", "a2", "a3"],
        )
    )
    four = [
        named(text("t1", 100, 10, "A"), "文字 A"),
        named(text("t2", 115, 10, "B"), "文字 B"),
        named(text("t3", 140, 10, "C"), "文字 C"),
        named(text("t4", 160, 10, "D"), "文字 D"),
    ]
    t.append(
        task(
            "distribute-h",
            "edit",
            "等距分布",
            "把顶部这四个字母（A、B、C、D）水平方向等间距排开，两头的不动。",
            four,
            [{"t": "gaps_equal", "ids": ["t1", "t2", "t3", "t4"], "tol": 0.1}],
            ops({"op": "distribute", "ids": ["t1", "t2", "t3", "t4"], "axis": "h"}),
            selection=["t1", "t2", "t3", "t4"],
            may_change=["t2", "t3"],
        )
    )
    lines3 = [
        named(line("l1", [(100, 30), (120, 40)], color="#0000ff"), "线一"),
        named(line("l2", [(100, 45), (120, 55)], color="#00aa00"), "线二"),
        named(line("l3", [(100, 60), (120, 70)], color="#aa00aa"), "线三"),
    ]
    t.append(
        task(
            "unify-color",
            "edit",
            "统一颜色",
            "把右边三条线（线一、线二、线三）的颜色统一成红色 #cc0000。",
            lines3,
            [{"t": "field_all", "ids": ["l1", "l2", "l3"], "field": "color", "eq": "#cc0000"}],
            ops(*[{"op": "set", "id": i, "set": {"color": "#cc0000"}} for i in ("l1", "l2", "l3")]),
            selection=["l1", "l2", "l3"],
            may_change=["l1", "l2", "l3"],
        )
    )
    t.append(
        task(
            "font-double",
            "edit",
            "相对改字号",
            "把选中的这行文字字号放大一倍。",
            [
                named(text("t_cap", 100, 10, "Result", w=30, sizePt=7), "说明文字"),
                text("t_other", 100, 30, "keep", sizePt=7),
            ],
            [{"t": "ratio", "id": "t_cap", "field": "sizePt", "ratio": 2}],
            ops({"op": "set", "id": "t_cap", "set": {"sizePt": 14}}),
            selection=["t_cap"],
            may_change=["t_cap"],
        )
    )
    t.append(
        task(
            "merge-circles",
            "edit",
            "合并",
            "把右边两个相交的圆合并成一个整体轮廓（一条封闭路径）。",
            [
                named(circle("c_back", 110, 40, 10), "圆 后"),
                named(circle("c_front", 118, 40, 10, fill="#00aa00"), "圆 前"),
            ],
            [{"t": "replaced_by_closed_path", "ids": ["c_back", "c_front"]}],
            ops({"op": "pathfinder", "ids": ["c_back", "c_front"], "mode": "unite"}),
            selection=["c_back", "c_front"],
            may_change=["c_back", "c_front"],
            may_add=True,
            needs_kernel=True,
        )
    )
    t.append(
        task(
            "offset-out",
            "edit",
            "偏移",
            "给选中的这个圆向外做一个 2 mm 的偏移轮廓（原来的保留）。",
            [named(circle("c_one", 120, 45, 10), "圆")],
            [
                {"t": "present", "ids": ["c_one"]},
                {"t": "new_path_grown", "id": "c_one", "by": 4.0, "tol": 0.4},
            ],
            ops({"op": "offset", "id": "c_one", "delta_mm": 2}),
            selection=["c_one"],
            may_add=True,
            needs_kernel=True,
        )
    )
    t.append(
        task(
            "outline-stroke",
            "edit",
            "轮廓化",
            "把这条粗线的描边轮廓化，变成填充的形状。",
            [named(line("l_thick", [(100, 40), (130, 50)], strokePt=3, color="#334455"), "粗线")],
            [{"t": "outlined", "id": "l_thick"}],
            ops({"op": "outline_stroke", "id": "l_thick"}),
            selection=["l_thick"],
            may_change=["l_thick"],
            may_add=True,
            needs_kernel=True,
        )
    )
    t.append(
        task(
            "delete-note",
            "edit",
            "删除",
            "把「临时备注」那行文字删掉，别的都别动。",
            [
                named(text("t_note", 100, 10, "临时备注", w=20), "备注"),
                text("t_keep", 100, 30, "Result", w=20),
                arrow("a_keep", 100, 50),
            ],
            [{"t": "deleted", "ids": ["t_note"]}, {"t": "present", "ids": ["t_keep", "a_keep"]}],
            ops({"op": "delete", "id": "t_note"}),
            may_change=[],
        )
    )
    t.append(
        task(
            "order-front",
            "edit",
            "层级",
            "把那个绿色的方块移到最前面，别被别的东西盖住。",
            [
                named(rect("s_green", 105, 30, color="#00aa00", fill="#00aa00"), "绿色方块"),
                named(rect("s_red", 110, 33, color="#aa0000", fill="#aa0000"), "红色方块"),
                named(rect("s_blue", 115, 36, color="#0000aa", fill="#0000aa"), "蓝色方块"),
            ],
            [{"t": "front", "id": "s_green"}],
            ops({"op": "order", "id": "s_green", "to": "front"}),
            may_change=["s_green", "s_red", "s_blue"],
        )
    )
    t.append(
        task(
            "group-two",
            "edit",
            "编组",
            "把方框和它旁边的文字「Control」编成一组，别的不要动。",
            [
                named(rect("r_box", 100, 30, w=14, h=8), "方框"),
                named(text("t_label", 118, 32, "Control", w=18), "标签"),
                text("t_far", 100, 60, "other", w=14),
            ],
            [{"t": "grouped", "ids": ["r_box", "t_label"]}],
            ops({"op": "group", "ids": ["r_box", "t_label"]}),
            may_change=["r_box", "t_label"],
        )
    )
    t.append(
        task(
            "move-below-panel",
            "edit",
            "挪位置",
            "把选中的这行说明文字挪到子图 P1 的正下方，水平居中，紧贴子图下沿。",
            [named(text("t_cap2", 120, 10, "Fig. 1 Kinetics", w=30, h=4), "图注")],
            [
                {"t": "center_x", "id": "t_cap2", "eq": 50.0, "tol": 1.5},
                {"t": "top_between", "id": "t_cap2", "between": [79.0, 86.0]},
            ],
            ops(
                {
                    "op": "move",
                    "id": "t_cap2",
                    "to": {"anchor": {"panel": {"id": "P1", "point": "s"}}, "offset_mm": [0, 1]},
                    "attach": "n",
                }
            ),
            selection=["t_cap2"],
            may_change=["t_cap2"],
        )
    )
    t.append(
        task(
            "rotate-arrow",
            "edit",
            "旋转",
            "把选中的这支箭头顺时针转 45 度。",
            [named(arrow("a_rot", 110, 40, w=24, h=4), "箭头")],
            [{"t": "field", "id": "a_rot", "field": "rotationDeg", "eq": 45, "tol": 0.2}],
            ops({"op": "rotate", "id": "a_rot", "deg": 45}),
            selection=["a_rot"],
            may_change=["a_rot"],
        )
    )
    t.append(
        task(
            "text-and-color",
            "edit",
            "改文字+颜色",
            "把标题文字「Group A」改成「Group B」，并把它的颜色改成蓝色 #0000cc。",
            [
                named(text("t_title", 100, 10, "Group A", w=24), "标题"),
                text("t_side", 100, 30, "side", w=14),
            ],
            [
                {"t": "field", "id": "t_title", "field": "text", "eq": "Group B"},
                {"t": "field", "id": "t_title", "field": "color", "eq": "#0000cc"},
            ],
            ops({"op": "set", "id": "t_title", "set": {"text": "Group B", "color": "#0000cc"}}),
            may_change=["t_title"],
        )
    )

    # ---- 对照题：不该动画布
    t.append(
        task(
            "ctrl-curve-color",
            "control",
            "数据曲线",
            "把子图里 Sample B 那条曲线的颜色改成红色。",
            [text("t_side", 100, 10, "side", w=14)],
            [{"t": "unchanged"}],
            {"kind": "none"},
            wrong_ops=[{"op": "set", "id": "t_side", "set": {"color": "#ff0000"}}],
            note="曲线是脚本画的数据元素，不在画布矢量对象里改：应进 unhandled、画布不动（B 组可走 apply_overrides，但不写进排版）",
        )
    )
    t.append(
        task(
            "ctrl-vague",
            "control",
            "含糊指令",
            "把那个东西弄好看一点。",
            [
                named(text("t_one", 100, 10, "A", w=10), "文字甲"),
                named(arrow("a_one", 100, 30), "箭头"),
                named(rect("s_one", 100, 50), "方块"),
            ],
            [{"t": "unchanged"}],
            {"kind": "none"},
            wrong_ops=[{"op": "set", "id": "t_one", "set": {"sizePt": 12}}],
            note="没有选区、指的是哪个东西不明：应进 unhandled（或问用户）、画布不动",
        )
    )
    return t


def corpus() -> dict:
    return {
        "version": 1,
        "page": PAGE,
        "figure_script": FIG_SCRIPT,
        "registry": REGISTRY,
        "layout_name": "组图",
        "peak_mm": list(peak_xy()),
        "tasks": build(),
    }


def render() -> str:
    return json.dumps(corpus(), ensure_ascii=False, indent=1) + "\n"


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(
            0
            if OUT.read_text(encoding="utf-8") == render()
            else "corpus.json 与 build_corpus.py 不一致"
        )
    OUT.write_text(render(), encoding="utf-8")
    print(f"写入 {OUT}（{len(build())} 题）")
