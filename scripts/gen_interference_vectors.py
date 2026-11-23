#!/usr/bin/env python3
"""生成 / 校对 `tests/golden/interference_vectors.json`（V-011）。

干涉检测（`engine/interference.py` 的 `detect`）的判据只有 Python 这一份；画布与导出对话框的预检在
浏览器里求值，`web/src/lib/interference.ts` 是它的逐行移植。两份靠这份向量对齐：**pytest 与 vitest
各跑一遍同一份输入**，比 `detect` 的**逐对**输出（id / severity / message(key+params) / object_ids /
gids / detail，不比只给没有文案表的宿主看的 `text`）。折进预检之后的聚合投影另由
`tests/golden/preflight_vectors.json` 的 `interference-*` 看护。

    python scripts/gen_interference_vectors.py            # 校对（有分歧就非零退出）
    python scripts/gen_interference_vectors.py --write    # 按 Python 侧重新生成

Python 是参考实现：`--write` 之后人工读一遍 diff，再让 vitest 也绿。

几何用**二进制上精确**的数（图幅 128 × 64 mm、分数都是 2 的幂次的倍数），边界值（恰好 0.25 mm、
恰好落在一半上的取整）才真的落在边界上，而不是被浮点噪音推到哪一边。

纯标准库。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tavotto.engine import interference, profiles  # noqa: E402

OUT = ROOT / "tests" / "golden" / "interference_vectors.json"
W_MM, H_MM = 128.0, 64.0
U = 1 / 512  # 0.25 mm 宽 / 0.125 mm 高；全部几何都是它的整数倍


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _el(gid, role, bbox, *, clip=None, geometry=None, visible=None, position=None) -> dict:
    editable = []
    if visible is not None:
        editable.append({"prop": "visible", "type": "bool", "value": visible})
    if position is not None:
        editable.append({"prop": "position", "type": "rect", "value": position})
    el = {"gid": gid, "role": role, "label": gid, "draggable": False, "bbox": bbox}
    if editable:
        el["editable"] = editable
    if clip is not None:
        el["clip_bbox"] = clip
    if geometry is not None:
        el["geometry"] = geometry
    return el


def _poly(*points, closed=False) -> dict:
    return {
        "kind": "polyline",
        "paths": [{"points": [list(p) for p in points], "closed": closed}],
        "fill": False,
        "stroke": True,
    }


def _m(*elements, size=(W_MM, H_MM)) -> dict:
    return {"stem": "Fig1", "size_mm": list(size), "elements": list(elements)}


def _two_axes(*extra) -> dict:
    """左右两个顶层子图（manifest 只给顶层子图发 `position`）。"""
    return _m(
        _el("axes_0", "axes", [0.125, 0.125, 0.25, 0.75], position=[0.125, 0.125, 0.25, 0.75]),
        _el("axes_1", "axes", [0.5, 0.125, 0.25, 0.75], position=[0.5, 0.125, 0.25, 0.75]),
        *extra,
    )


def _legend_axes(*data, legend_bbox=(0.5, 0.125, 0.25, 0.25), legend_gid="axes_0.legend") -> dict:
    return _m(
        _el("axes_0", "axes", [0.125, 0.125, 0.75, 0.75], position=[0.125, 0.125, 0.75, 0.75]),
        _el(legend_gid, "legend", list(legend_bbox)),
        *data,
    )


def cases() -> list[dict]:
    out: list[dict] = []

    def add(name, manifest, *, profile_id="lab-publication-v1", panel_id="p1"):
        out.append(
            {"name": name, "profile_id": profile_id, "panel_id": panel_id, "manifest": manifest}
        )

    # ---------------- 文字互相重叠 ----------------
    # 咬进 8U = 2 mm × 8U = 1 mm
    add(
        "text-overlap-basic",
        _m(
            _el("axes_0.title", "title", [0.25, 0.25, 0.125, 0.0625]),
            _el(
                "axes_0.xlabel",
                "axis_label",
                [0.25 + 0.125 - 8 * U, 0.25 + 0.0625 - 8 * U, 0.125, 0.0625],
            ),
        ),
    )
    # 恰好 0.25 mm × 0.25 mm：含等号，报；格式化 0.25 → Python "0.2"（取偶），JS toFixed 是 "0.3"
    add(
        "text-overlap-exactly-threshold",
        _m(
            _el("axes_0.title", "title", [0.25, 0.25, 0.125, 0.0625]),
            _el("axes_0.xlabel", "axis_label", [0.375 - U, 0.3125 - 2 * U, 0.125, 0.0625]),
        ),
    )
    # 宽向只咬进 0.25 mm 之下一点（U/2 = 0.125 mm）：不报
    add(
        "text-overlap-below-threshold",
        _m(
            _el("axes_0.title", "title", [0.25, 0.25, 0.125, 0.0625]),
            _el("axes_0.xlabel", "axis_label", [0.375 - U / 2, 0.25, 0.125, 0.0625]),
        ),
    )
    # 1.25 mm 与 0.75 mm：两个恰好的半值（取偶后是 "1.2" 与 "0.8"）
    add(
        "text-overlap-half-even-format",
        _m(
            _el("axes_0.title", "title", [0.25, 0.25, 0.125, 0.0625]),
            _el("axes_0.xlabel", "axis_label", [0.375 - 5 * U, 0.3125 - 6 * U, 0.125, 0.0625]),
        ),
    )
    # 刚好贴边（相交面积为 0）：不报
    add(
        "text-overlap-touching-edges",
        _m(
            _el("axes_0.title", "title", [0.25, 0.25, 0.125, 0.0625]),
            _el("axes_0.xlabel", "axis_label", [0.375, 0.25, 0.125, 0.0625]),
        ),
    )
    # 同一个图例的条目之间、条目与自己的图例框：不报；另一个图例的条目压过来：报
    add(
        "text-overlap-same-legend-family-skipped",
        _m(
            _el("axes_0.legend", "legend", [0.25, 0.25, 0.25, 0.25]),
            _el("axes_0.legend.texts_0", "legend_text", [0.25, 0.25, 0.125, 0.0625]),
            _el("axes_0.legend.texts_1", "legend_text", [0.25, 0.28125, 0.125, 0.0625]),
            _el("axes_1.legend_2.texts_0", "legend_text", [0.25, 0.25, 0.125, 0.0625]),
        ),
    )
    # 隐藏的元素不参与；整体藏起来的图例的条目（条目自己仍报 visible=True）也不参与
    add(
        "text-overlap-invisible-and-hidden-legend",
        _m(
            _el("axes_0.title", "title", [0.25, 0.25, 0.125, 0.0625]),
            _el("axes_0.xlabel", "axis_label", [0.25, 0.25, 0.125, 0.0625], visible=False),
            _el("axes_0.legend", "legend", [0.6, 0.6, 0.1, 0.1], visible=False),
            _el("axes_0.legend.texts_0", "legend_text", [0.25, 0.25, 0.125, 0.0625]),
        ),
    )
    # clip_bbox 把它切掉了：画出来的部分不重叠
    add(
        "text-overlap-clip-folded",
        _m(
            _el("axes_0.title", "title", [0.25, 0.25, 0.125, 0.0625]),
            _el(
                "axes_0.texts_0",
                "text",
                [0.25, 0.25, 0.25, 0.0625],
                clip=[0.375, 0.0, 0.5, 1.0],
            ),
        ),
    )
    # 读不懂的几何：bbox 里有布尔 / 字符串 = 不参与；读不懂的 clip_bbox = 不裁
    add(
        "text-overlap-malformed-geometry",
        _m(
            _el("axes_0.title", "title", [0.25, 0.25, 0.125, 0.0625]),
            _el("axes_0.xlabel", "axis_label", [0.25, True, 0.125, 0.0625]),
            _el("axes_0.ylabel", "axis_label", ["0.25", 0.25, 0.125, 0.0625]),
            _el("axes_0.texts_0", "text", [0.25, 0.25, 0.125, 0.0625], clip=[0, 0, "1", 1]),
        ),
    )
    # 排序按 gid 的码点序（`axes_10` 在 `axes_2` 前面），三段两两相交 → 三条
    add(
        "text-overlap-sorted-by-gid",
        _m(
            _el("axes_2.title", "title", [0.25, 0.25, 0.125, 0.0625]),
            _el("axes_10.title", "title", [0.25 + 4 * U, 0.25, 0.125, 0.0625]),
            _el("axes_1.xlabel", "axis_label", [0.25 + 8 * U, 0.25, 0.125, 0.0625]),
        ),
    )
    # 图幅读不出来：什么都不报
    add(
        "text-overlap-no-figure-size",
        _m(
            _el("axes_0.title", "title", [0.25, 0.25, 0.125, 0.0625]),
            _el("axes_0.xlabel", "axis_label", [0.25, 0.25, 0.125, 0.0625]),
            size=(0.0, H_MM),
        ),
    )

    # ---------------- 斜排文字：有向矩形复核（只否决） ----------------
    def _turned(gid, cx, cy, rot, *, clip=None, with_block=True):
        # 32 mm × 4 mm 的文字块；布局框写成它的轴对齐外接框（与真 manifest 一致：45° 时各向半径 12.73 mm）
        hw, hh = 16.0, 2.0
        c, s_ = abs(math.cos(math.radians(rot))), abs(math.sin(math.radians(rot)))
        ex, ey = (hw * c + hh * s_) / W_MM, (hw * s_ + hh * c) / H_MM
        el = _el(gid, "ticklabel", [cx - ex, cy - ey, 2 * ex, 2 * ey], clip=clip)
        if with_block:
            el["text_block"] = {
                "center": [cx, cy],
                "w": 2 * hw / W_MM,
                "h": 2 * hh / H_MM,
                "rotation": rot,
                "text": gid,
            }
        return el

    step = 12.0 / W_MM  # 相邻两段沿横向错开 12 mm：垂直于阅读方向相距 8.5 mm，远大于 4 mm 的字高
    add(
        "rotated-45-parallel-labels-vetoed",
        _m(
            _turned("axes_0.xticklabels_0", 0.3, 0.5, 45.0),
            _turned("axes_0.xticklabels_1", 0.3 + step, 0.5, 45.0),
        ),
    )
    add(
        "rotated-minus-45-parallel-labels-vetoed",
        _m(
            _turned("axes_0.xticklabels_0", 0.3, 0.5, -45.0),
            _turned("axes_0.xticklabels_1", 0.3 + step, 0.5, -45.0),
        ),
    )
    # 只错开 3 mm：墨迹真叠上了，照报（detail / 文案仍是布局框那组数）
    add(
        "rotated-45-dense-labels-still-reported",
        _m(
            _turned("axes_0.xticklabels_0", 0.3, 0.5, 45.0),
            _turned("axes_0.xticklabels_1", 0.3 + 3.0 / W_MM, 0.5, 45.0),
        ),
    )
    # 斜排与横排：横排文字按自己的布局框参与复核——落在阅读方向上（右上）报，落在字的侧面（右下）否决
    add(
        "rotated-45-vs-horizontal-text",
        _m(
            _turned("axes_0.xticklabels_0", 0.3, 0.5, 45.0),
            _el(
                "axes_0.xlabel", "axis_label", [0.3 + 6.0 / W_MM, 0.5 - 10.0 / H_MM, 0.0625, 0.0625]
            ),
            _turned("axes_1.xticklabels_0", 0.7, 0.5, 45.0),
            _el(
                "axes_1.xlabel", "axis_label", [0.7 + 6.0 / W_MM, 0.5 + 6.0 / H_MM, 0.0625, 0.0625]
            ),
        ),
    )
    # 没有 text_block / 有 clip_bbox / 正好 90°：照布局框判（报）
    add(
        "rotated-without-block-or-clipped-or-90-uses-bbox",
        _m(
            _turned("axes_0.xticklabels_0", 0.3, 0.5, 45.0, with_block=False),
            _turned("axes_0.xticklabels_1", 0.3 + step, 0.5, 45.0, with_block=False),
            _turned("axes_1.xticklabels_0", 0.7, 0.5, 45.0, clip=[0, 0, 1, 1]),
            _turned("axes_1.xticklabels_1", 0.7 + step, 0.5, 45.0, clip=[0, 0, 1, 1]),
            _turned("axes_2.xticklabels_0", 0.5, 0.2, -90.0),
            _turned("axes_2.xticklabels_1", 0.5 + 1.0 / W_MM, 0.2, 90.0),
        ),
    )

    # ---------------- 文字压到别的子图 ----------------
    add(
        "text-over-axes-ylabel-into-neighbour",
        _two_axes(
            # 伸进左边子图 1.5 mm / 0.5 mm（两条），宽向都过阈值
            _el("axes_1.ylabel", "axis_label", [0.375 - 6 * U, 0.375, 8 * U, 0.125]),
            _el("axes_1.yticklabels_0", "ticklabel", [0.375 - 2 * U, 0.25, 6 * U, 0.0625]),
        ),
    )
    # 标注（`text`）压到别的子图不算；图例算
    add(
        "text-over-axes-annotation-ignored-legend-counted",
        _two_axes(
            _el("axes_1.texts_0", "text", [0.25, 0.375, 0.0625, 0.0625]),
            _el("axes_1.legend", "legend", [0.25, 0.5, 0.0625, 0.0625]),
        ),
    )
    # 自己的插图（被自己包住的子图）不是干涉；没有 position 的子图（次坐标轴 / 寄生轴）不算别人的绘图区
    add(
        "text-over-axes-inset-and-twin-skipped",
        _m(
            _el("axes_0", "axes", [0.125, 0.125, 0.75, 0.75], position=[0.125, 0.125, 0.75, 0.75]),
            _el("axes_1", "axes", [0.5, 0.25, 0.25, 0.25], position=[0.5, 0.25, 0.25, 0.25]),
            _el("axes_1.title", "title", [0.5, 0.25, 0.0625, 0.0625]),
            _el("axes_0.title", "title", [0.625, 0.4375, 0.0625, 0.0625]),
            _el("axes_2", "axes", [0.125, 0.125, 0.25, 0.25]),
            _el("axes_0.ylabel", "axis_label", [0.125, 0.125, 0.0625, 0.0625]),
        ),
    )
    # 隐藏的子图不是绘图区；figure 级标题压进子图照报
    add(
        "text-over-axes-hidden-axes-and-figure-title",
        _m(
            _el("axes_0", "axes", [0.125, 0.125, 0.25, 0.75], position=[0.125, 0.125, 0.25, 0.75]),
            _el(
                "axes_1",
                "axes",
                [0.5, 0.125, 0.25, 0.75],
                position=[0.5, 0.125, 0.25, 0.75],
                visible=False,
            ),
            _el("axes_0.title", "title", [0.5, 0.25, 0.125, 0.0625]),
            _el("suptitle", "title", [0.25, 0.125, 0.125, 0.0625]),
        ),
    )

    # ---------------- 图例压住数据 ----------------
    # 折线穿过图例框：确定
    add(
        "legend-over-data-line-certain",
        _legend_axes(
            _el(
                "axes_0.lines_0",
                "line",
                [0.125, 0.125, 0.75, 0.75],
                geometry=_poly((0.125, 0.875), (0.875, 0.125)),
            )
        ),
    )
    # 包围盒相交、折线没碰到图例框：不报
    add(
        "legend-over-data-line-misses",
        _legend_axes(
            _el(
                "axes_0.lines_0",
                "line",
                [0.125, 0.125, 0.75, 0.75],
                geometry=_poly((0.125, 0.875), (0.875, 0.875), (0.875, 0.5)),
            )
        ),
    )
    # 误差棒带着 geometry 也只按包围盒（风险）；图像没有 geometry（风险）
    add(
        "legend-over-data-errorbar-and-image-risk",
        _legend_axes(
            _el(
                "axes_0.errorbar_0",
                "errorbar",
                [0.125, 0.125, 0.75, 0.75],
                geometry=_poly((0.125, 0.875), (0.875, 0.125)),
            ),
            _el("axes_0.images_0", "image", [0.125, 0.125, 0.75, 0.75]),
        ),
    )
    # 确定与风险并存：targets 只列确定的，风险的进 risk_only
    add(
        "legend-over-data-certain-with-risk",
        _legend_axes(
            _el("axes_0.collections_0", "collection", [0.125, 0.125, 0.75, 0.75]),
            _el(
                "axes_0.lines_0",
                "line",
                [0.125, 0.125, 0.75, 0.75],
                geometry=_poly((0.125, 0.875), (0.875, 0.125)),
            ),
        ),
    )
    # 单点路径落在框里；闭合路径只有回边穿过框；水平线段（dy = 0）穿过 / 平行在框外
    add(
        "legend-over-data-path-edge-cases",
        _legend_axes(
            _el(
                "axes_0.lines_0",
                "line",
                [0.125, 0.125, 0.75, 0.75],
                geometry=_poly((0.625, 0.25)),
            ),
            _el(
                "axes_0.patches_0",
                "patch",
                [0.125, 0.125, 0.75, 0.75],
                geometry=_poly((0.875, 0.375), (0.875, 0.875), (0.375, 0.125), closed=True),
            ),
            _el(
                "axes_0.lines_1",
                "line",
                [0.125, 0.125, 0.75, 0.75],
                geometry=_poly((0.125, 0.25), (0.875, 0.25)),
            ),
            _el(
                "axes_0.lines_2",
                "line",
                [0.125, 0.125, 0.75, 0.75],
                geometry=_poly((0.125, 0.75), (0.875, 0.75)),
            ),
            _el(
                "axes_0.lines_3",
                "line",
                [0.125, 0.125, 0.75, 0.75],
                geometry={"kind": "polyline", "paths": [{"points": [[0.6, True], "x"]}, 3]},
            ),
        ),
    )
    # 不同子图的数据、看不见的数据、figure 级图例：一律不报
    add(
        "legend-over-data-scope",
        _m(
            _el("axes_0", "axes", [0.125, 0.125, 0.75, 0.75], position=[0.125, 0.125, 0.75, 0.75]),
            _el("legend", "legend", [0.0, 0.9375, 0.0625, 0.03125]),
            _el("axes_0.lines_1", "line", [0.0, 0.9375, 0.0625, 0.03125]),
            _el("axes_0.legend", "legend", [0.5, 0.125, 0.25, 0.25]),
            _el("axes_1.lines_0", "line", [0.125, 0.125, 0.75, 0.75]),
            _el("axes_0.lines_0", "line", [0.125, 0.125, 0.75, 0.75], visible=False),
        ),
    )
    # 图例探出图幅：legend_rect_mm 带负数（取整同 Python）；超过 4 个目标时 targets 参数只列前 4 个
    add(
        "legend-over-data-many-targets-negative-rect",
        _legend_axes(
            *(
                _el(f"axes_0.collections_{k}", "collection", [0.125, 0.125, 0.75, 0.75])
                for k in range(5)
            ),
            legend_bbox=(-0.0009765625, -0.0009765625, 0.25, 0.25),
        ),
    )
    # 严重度：随规范；没有规范时取兜底 warn
    add(
        "severity-free-form",
        _legend_axes(_el("axes_0.images_0", "image", [0.125, 0.125, 0.75, 0.75])),
        profile_id="free-form-v1",
    )
    add(
        "severity-no-profile",
        _legend_axes(_el("axes_0.images_0", "image", [0.125, 0.125, 0.75, 0.75])),
        profile_id=None,
        panel_id="figure",
    )
    return out


def evaluate(case: dict) -> list[dict]:
    pid = case.get("profile_id")
    profile = profiles.load(pid) if pid else None
    return [
        {k: i[k] for k in ("id", "severity", "message", "object_ids", "gids", "detail")}
        for i in interference.detect(case["manifest"], profile, panel_id=case["panel_id"])
    ]


def build() -> dict:
    return {
        "comment": "干涉检测向量（V-011）：pytest（engine/interference.py）与 vitest"
        "（web/src/lib/interference.ts）各跑一遍同一份输入，比 detect 的逐对输出"
        "（不比成文 text）。重新生成：python scripts/gen_interference_vectors.py --write",
        "constants": {
            "MIN_OVERLAP_MM": interference.MIN_OVERLAP_MM,
            "DECORATION_ROLES": list(interference.DECORATION_ROLES),
            "TEXT_ROLES": list(interference.TEXT_ROLES),
            "DATA_ROLES": list(interference.DATA_ROLES),
            "BBOX_BASIS_ROLES": list(interference.BBOX_BASIS_ROLES),
            "CHECK_IDS": list(interference.CHECK_IDS),
        },
        "cases": [{**c, "expected": evaluate(c)} for c in cases()],
    }


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="按 Python 侧重新生成")
    args = ap.parse_args(argv)
    fresh = build()
    text = json.dumps(fresh, ensure_ascii=False, indent=1) + "\n"
    if args.write:
        OUT.write_text(text, encoding="utf-8")
        print(f"已写入 {OUT}（{len(fresh['cases'])} 条）")
        return 0
    if not OUT.is_file():
        print(f"缺少 {OUT}，先跑一次 --write", file=sys.stderr)
        return 1
    if OUT.read_text(encoding="utf-8") != text:
        print(
            f"{OUT} 与当前 Python 实现不一致（--write 重新生成后人工过一遍 diff）", file=sys.stderr
        )
        return 1
    print(f"{OUT} 与 Python 实现一致（{len(fresh['cases'])} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
