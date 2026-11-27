"""Agent 往排版里加矢量元素：图的几何 → 锚点 → 求解（ADR 0208 §一–§三；第 1 期的贯通切片）。

**分工**：Agent 只说「画什么、挂在哪、和谁什么关系」，这里确定性地算出毫米坐标（不调模型），产物交给
`engine/agentobjects.validate_new_objects` 校验后才算数。核对（在导出几何上量）在第 1 期下一块。

坐标与真值：
- 图内元素的框与数据点**都取同一次导出绘制**：`export(..., data_points=…)` 回的 `draw_manifest` 与 `data_points`
  （tight / constrained 下 PNG 预览与 PDF 成品差零点几毫米，对齐的真值是成品；见 `wireproto` 的 `data_points`）。
- 图幅分数 → 画布毫米：`panel_point_to_page`，与前端 `canvas/elementGeometry.elementRectOnPage` 逐步同源
  （`panelFullRect` 的裁剪 + `panelContentTransform` 的先翻转后旋转），golden `tests/golden/vectorscene_vectors.json`。

这一版支持的描述（闭集，随第 1 期的真实任务夹具扩）：
- 锚点 `anchor`：`{"data": {"panel", "axes", "x", "y"}}`（数据点）、`{"element": {"panel", "gid", "point"}}`
  （元素框的 9 个方位点）、`{"panel": {"id", "point"}}`（子图框的方位点）、`{"item": {"name", "point"}}`（本批里先声明的元素）；
- 元素：`{"kind": "arrow", "name", "from": 位置, "to": 位置, ...样式}`、`{"kind": "text", "name", "text", "at": 位置,
  "attach": 方位, ...样式}`、`{"kind": "shape", "name", "shape", "at": 位置, "size_mm": [w, h], "attach": 方位, ...样式}`
  （圈出一个点、框一块区域）、`{"kind": "bracket", "name", "between": [位置, 位置], "side": "above"|"below", "gap_mm",
  "tick_mm"}`（显著性括号：开口朝向两个点，横线在较高的那个点之上留 `gap_mm`）、`{"kind": "connector", "name", "from",
  "to", "route": "straight"|"elbow-h"|"elbow-v"}`（连线，直角折线先横后竖 / 先竖后横，末端缺省带箭头）、
  `{"kind": "box", "name", "fit": [目标…], "padding_mm"}`（框住一组：子图 / 图内元素 / 本批元素的并集外扩内边距）；
  位置 = `{"anchor": 锚点, "offset_mm": [dx, dy]}`（偏移可省）；目标 = `{"panel": {"id"}}` / `{"element": {"panel", "gid"}}` /
  `{"item": {"name"}}`。产物只用现有对象类型（箭头 / 文字 / 形状 / 路径），路径只有直线段、锚点最少。
"""

from __future__ import annotations

import math
import re
from typing import Any

PT_TO_MM = 25.4 / 72.0

#: 方位点 → 框内比例（x 向右、y 向下）
POINTS = {
    "nw": (0.0, 0.0),
    "n": (0.5, 0.0),
    "ne": (1.0, 0.0),
    "w": (0.0, 0.5),
    "center": (0.5, 0.5),
    "e": (1.0, 0.5),
    "sw": (0.0, 1.0),
    "s": (0.5, 1.0),
    "se": (1.0, 1.0),
}

#: 求解错误码（闭集）；结果只报码与定位，不回显输入内容
ERROR_CODES = (
    "bad_item",  # 元素描述的形状不对 / kind 不认识
    "duplicate_name",  # 两个元素同名
    "unknown_ref",  # 锚点指向不存在的子图 / 元素 / 本批元素（或引用了后面才声明的）
    "anchor_unresolvable",  # 数据点换算不了（worker 回 unresolvable / 没回）
    "panel_transformed",  # 子图旋转或翻转过：这一版不给子元素锚点
    "bad_point",  # 方位名不认识
    "scene_stale",  # 场景指纹变了（文档被改、脚本重跑出了新图）：方案作废，重新读场景
    "needs_confirmation",  # 统计标注（星号 / n.s. / p 值 / n）没写明来自用户或数据：不由 Agent 编（ADR 0208 §四）
)

#: 统计标注的文字（ADR 0208 §四，维护者 2026-11-24 定）：只能来自用户的话或脚本 / 数据里的结果，
#: 描述里要写 `"provenance": "user" | "data"`；没写的整批不落地，回 `needs_confirmation`。
#: 只作用在 `is_stat_text` 压过空白的文字上（任意空白 → 单个空格、首尾无空白），所以空白一律写成 ` ?`（至多一个空格）：
#: 与原来的 `\s*` 在压过的文字上等价，又没有可回溯的空白重复（CodeQL py/polynomial-redos）。
_STAT_TEXT = re.compile(
    r"^(\*{1,4}|n\.? ?s\.?|ns)$|\bp ?[<>=≤≥]|\bp ?-? ?value|\bn ?= ?\d",
    re.IGNORECASE,
)
PROVENANCES = ("user", "data")


def is_stat_text(text: object) -> bool:
    """是不是统计标注文字。**先把任意空白压成单个空格再匹配**：`_STAT_TEXT` 里 `\\s*` 作用在用户 / 模型给的文字上，
    成千上万个制表符会让回溯正则变成多项式时间；压过之后正则里的空白只写成 ` ?`，匹配是线性的（语义不变——原来的 `\\s` 本来就把各种空白当一回事）。"""
    return bool(_STAT_TEXT.search(" ".join(str(text).split())))


#: 条目词汇的一份说明（给模型 / Agent 看的，**只此一处**）：`vectoredit.OPS_DOC` 的 `add` 从这里取。位置一律写锚点、不写毫米。
ITEMS_DOC = (
    "条目 {kind, name, …}：kind ∈ arrow {from, to, head?} / text {text, at, attach?, sizePt?} / "
    "bracket {between: [位置, 位置], side?: above|below} / box {fit: [目标…], padding_mm?}（框住子图）/ "
    "connector {from, to, route?} / shape {shape, at, size_mm}；name 是本批内的局部名字。"
    "位置 = {anchor: …, offset_mm?}；anchor 只认 panel = {panel: {id: P1, point: nw|n|ne|w|center|e|sw|s|se}}、"
    "item = {item: {name, point}}（本批里先声明的条目）；"
    "data = {data: {panel: P1, axes: 坐标轴的 gid, x, y}}（数据坐标上的点，如曲线的峰）、"
    "element = {element: {panel: P1, gid: 元素的 gid, point}}（图内元素的方位点）——这两种只在场景里 `measured: true` 的子图上能用（gid 取场景 `scene`），没量过的会被拒。"
    "box 的 fit 是目标列表，每个目标是单键对象，如 [{panel: {id: P1}}]。"
    "显著性星号 / n.s. / p 值 / n 的文字必须写 provenance: user，且这段文字必须原样出现在用户的话里，否则不收。"
    "例：外框 {kind: box, name: frame, fit: [{panel: {id: P1}}]}；"
    '子图标注 {kind: text, name: tag, text: "(a)", at: {anchor: {panel: {id: P1, point: nw}}, offset_mm: [2, -3]}, attach: sw}；'
    "括号 {kind: bracket, name: br, between: [{anchor: {panel: {id: P1, point: nw}}}, {anchor: {panel: {id: P1, point: ne}}}], side: above}。"
)


class SceneError(Exception):
    def __init__(self, code: str, item: str | None = None, detail: str | None = None):
        assert code in ERROR_CODES, code
        super().__init__(code)
        self.code, self.item, self.detail = code, item, detail

    def as_dict(self) -> dict:
        out: dict[str, Any] = {"code": self.code}
        if self.item is not None:
            out["item"] = self.item
        if self.detail is not None:
            out["detail"] = self.detail
        return out


# ------------------------------------------------------------------ 图幅分数 → 画布毫米


def panel_rotation(panel: dict) -> int:
    """与前端 `types/document.panelRotation` 同一条：按 90° 取整、归到 0 / 90 / 180 / 270。"""
    r = panel.get("rotation") or 0
    try:
        r = float(r)
    except (TypeError, ValueError):
        r = 0.0
    return int(((round(r / 90) % 4) + 4) % 4) * 90


def _rotate_vec(x: float, y: float, r: int) -> tuple[float, float]:
    """与前端 `rotateVec` 同一条（顺时针为正，y 向下）。"""
    if r == 90:
        return (-y, x)
    if r == 180:
        return (-x, -y)
    if r == 270:
        return (y, -x)
    return (x, y)


def panel_full_rect(panel: dict) -> tuple[float, float, float, float]:
    """与前端 `lib/elementGeom.panelFullRect` 同一条：面板未裁剪时的完整显示矩形（mm，内容坐标系）。"""
    c = panel.get("crop")
    rot = panel_rotation(panel)
    cw, ch = (panel["h"], panel["w"]) if rot in (90, 270) else (panel["w"], panel["h"])
    cx, cy = panel["x"] + panel["w"] / 2, panel["y"] + panel["h"] / 2
    return (
        cx - cw / 2 - ((c["x"] / c["w"]) * cw if c else 0.0),
        cy - ch / 2 - ((c["y"] / c["h"]) * ch if c else 0.0),
        cw / (c["w"] if c else 1.0),
        ch / (c["h"] if c else 1.0),
    )


def panel_rect_to_page(panel: dict, box: list | tuple) -> tuple[float, float, float, float]:
    """manifest 的分数框 → 页面上看得见的外接框（mm）。与前端 `elementRectOnPage` 逐步同源。"""
    fx, fy, fw, fh = (float(v) for v in box)
    full_x, full_y, full_w, full_h = panel_full_rect(panel)
    rot = panel_rotation(panel)
    sx = -1.0 if panel.get("flipH") else 1.0
    sy = -1.0 if panel.get("flipV") else 1.0
    cx, cy = panel["x"] + panel["w"] / 2, panel["y"] + panel["h"] / 2
    w, h = fw * full_w, fh * full_h
    ox, oy = _rotate_vec(
        (full_x + fx * full_w + w / 2 - cx) * sx, (full_y + fy * full_h + h / 2 - cy) * sy, rot
    )
    rw, rh = (h, w) if rot in (90, 270) else (w, h)
    return (cx + ox - rw / 2, cy + oy - rh / 2, rw, rh)


def panel_point_to_page(panel: dict, fx: float, fy: float) -> tuple[float, float]:
    """图幅分数点 → 画布毫米（零尺寸框的中心）。"""
    x, y, _w, _h = panel_rect_to_page(panel, (fx, fy, 0.0, 0.0))
    return (x, y)


def _box_point(box: tuple[float, float, float, float], point: str, item: str | None):
    if point not in POINTS:
        raise SceneError("bad_point", item, point)
    px, py = POINTS[point]
    return (box[0] + px * box[2], box[1] + py * box[3])


# ------------------------------------------------------------------ 锚点


def required_data_points(items: list) -> dict[str, list[dict]]:
    """这批描述要问 worker 的数据点，按子图分组（调用方对每个子图调一次 `export(..., data_points=…)`）。
    顺序稳定、去重；回包按同一顺序对上（`solve` 的 `panels[id]["data_points"]`）。"""
    out: dict[str, list[dict]] = {}
    for it in items if isinstance(items, list) else []:
        for anchor in _anchors_of(it):
            d = anchor.get("data") if isinstance(anchor, dict) else None
            if not isinstance(d, dict) or not isinstance(d.get("panel"), str):
                continue
            q = {"axes": d.get("axes"), "x": d.get("x"), "y": d.get("y")}
            reqs = out.setdefault(d["panel"], [])
            if q not in reqs:
                reqs.append(q)
    return out


def _anchors_of(it: object) -> list:
    if not isinstance(it, dict):
        return []
    found = []
    positions = [it.get(k) for k in ("from", "to", "at")]
    if isinstance(it.get("between"), list):
        positions += it["between"]
    for pos in positions:
        if isinstance(pos, dict) and isinstance(pos.get("anchor"), dict):
            found.append(pos["anchor"])
    return found


#: 没写 `fontFamily` 的画布文字按它渲染：与前端 `web/src/lib/typography.ts` 的 `CANVAS_TEXT_DEFAULT_FAMILY` 同一个值（同源对，
#: `tests/test_vector_edit_style_check.py` 逐字核对），核对 / 求解里不再各处手写 "serif"。
CANVAS_TEXT_DEFAULT_FAMILY = "serif"


def family_class(name: object) -> str:
    """图里的字体名 → 画布文字的三类族（`TextObject.fontFamily`）：含 mono → 等宽；含 serif 且不含 sans → 衬线；其余无衬线。"""
    n = str(name or "").lower()
    if "mono" in n or "courier" in n:
        return "monospace"
    if (
        "serif" in n
        and "sans" not in n
        or n in ("times", "times new roman", "stix", "cmr10", "dejavu serif")
    ):
        return "serif"
    return "sans-serif"


def _figure_family(panels: dict) -> str:
    """这批子图里最常见的文字字体族（三类之一）；没有文字就无衬线。"""
    counts: dict[str, int] = {}
    for p in panels.values():
        for el in (p.get("draw_manifest") or {}).get("elements", []):
            fam = _field(el, "fontfamily")
            if isinstance(fam, str):
                k = family_class(fam)
                counts[k] = counts.get(k, 0) + 1
    return max(counts, key=counts.get) if counts else "sans-serif"


class _Ctx:
    def __init__(self, panels: dict):
        self.family = _figure_family(panels)
        self.panels = panels  # id -> {"panel": PanelObject, "draw_manifest": …, "data_points": […], "requests": […]}
        self.items: dict[str, tuple[float, float, float, float]] = {}  # 已解出的本批元素的外接框


def _panel_of(ctx: _Ctx, pid: object, item: str) -> dict:
    if not isinstance(pid, str) or pid not in ctx.panels:
        raise SceneError("unknown_ref", item, "panel")
    return ctx.panels[pid]


def _require_untransformed(p: dict, item: str) -> None:
    panel = p["panel"]
    if panel_rotation(panel) or panel.get("flipH") or panel.get("flipV"):
        raise SceneError("panel_transformed", item)


#: 元素框本身就贴着墨迹的角色：文字类（`_text_ink_extent`）、坐标轴（绘图区）、图例、色条。
#: 其余元素的 `bbox` 是撑大过的点击热区（`manifest._padded_bbox`）或只是数据范围（散点不含标记大小），
#: 锚上去会差出零点几毫米——它们只认真实轮廓 `geometry.paths`。
INK_BOX_ROLES = (
    "axes",
    "title",
    "axis_label",
    "ticklabel",
    "legend",
    "legend_text",
    "text",
    "colorbar",
    "suptitle",
)


def element_ink_box(p: dict, gid: object, item: str) -> list[float]:
    """图内元素的**墨迹**框（图幅分数 [x, y, w, h]）：有真实轮廓取轮廓点的范围，文字 / 坐标轴类用 `bbox`，其余拒绝。"""
    el = next(
        (e for e in (p.get("draw_manifest") or {}).get("elements", []) if e.get("gid") == gid),
        None,
    )
    if el is None:
        raise SceneError("unknown_ref", item, "element")
    pts = [
        q
        for path in ((el.get("geometry") or {}).get("paths") or [])
        if isinstance(path, dict)
        for q in (path.get("points") or [])
        if isinstance(q, (list, tuple)) and len(q) == 2
    ]
    if pts:
        xs, ys = [float(q[0]) for q in pts], [float(q[1]) for q in pts]
        return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
    if el.get("role") in INK_BOX_ROLES and isinstance(el.get("bbox"), list):
        return [float(v) for v in el["bbox"]]
    raise SceneError("anchor_unresolvable", item, "no_ink_geometry")


def resolve_anchor(anchor: dict, ctx: _Ctx, item: str) -> tuple[float, float]:
    """锚点 → 画布毫米。数据点只认 worker 在导出绘制上换算的结果，不在这里重算。"""
    if not isinstance(anchor, dict) or len(anchor) != 1:
        raise SceneError("bad_item", item, "anchor")
    kind, spec = next(iter(anchor.items()))
    if not isinstance(spec, dict):
        raise SceneError("bad_item", item, "anchor")
    if kind == "data":
        p = _panel_of(ctx, spec.get("panel"), item)
        _require_untransformed(p, item)
        q = {"axes": spec.get("axes"), "x": spec.get("x"), "y": spec.get("y")}
        reqs, results = p.get("requests") or [], p.get("data_points") or []
        if q not in reqs or len(results) != len(reqs):
            raise SceneError("anchor_unresolvable", item, "not_measured")
        r = results[reqs.index(q)]
        if not isinstance(r, dict) or r.get("status") != "ok":
            raise SceneError("anchor_unresolvable", item, str((r or {}).get("reason", "unknown")))
        return panel_point_to_page(p["panel"], *r["xy"])
    if kind == "element":
        p = _panel_of(ctx, spec.get("panel"), item)
        _require_untransformed(p, item)
        box = element_ink_box(p, spec.get("gid"), item)
        return _box_point(panel_rect_to_page(p["panel"], box), spec.get("point", "center"), item)
    if kind == "panel":
        p = _panel_of(ctx, spec.get("id"), item)
        pn = p["panel"]
        return _box_point((pn["x"], pn["y"], pn["w"], pn["h"]), spec.get("point", "center"), item)
    if kind == "item":
        box = ctx.items.get(spec.get("name"))  # type: ignore[arg-type]
        if box is None:
            raise SceneError("unknown_ref", item, "item")
        return _box_point(box, spec.get("point", "center"), item)
    raise SceneError("bad_item", item, "anchor")


def _position(pos: object, ctx: _Ctx, item: str) -> tuple[float, float]:
    if not isinstance(pos, dict) or not isinstance(pos.get("anchor"), dict):
        raise SceneError("bad_item", item, "position")
    x, y = resolve_anchor(pos["anchor"], ctx, item)
    off = pos.get("offset_mm", [0.0, 0.0])
    if (
        not isinstance(off, list)
        or len(off) != 2
        or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in off
        )
    ):
        raise SceneError("bad_item", item, "offset_mm")
    return (x + float(off[0]), y + float(off[1]))


# ------------------------------------------------------------------ 元素


def _arrow(it: dict, ctx: _Ctx, name: str) -> dict:
    ax, ay = _position(it.get("from"), ctx, name)
    bx, by = _position(it.get("to"), ctx, name)
    x0, y0 = min(ax, bx), min(ay, by)
    w, h = abs(bx - ax), abs(by - ay)

    def rel(v: float, lo: float, span: float) -> float:
        return (v - lo) / span if span > 1e-9 else 0.5

    return {
        "id": name,
        "type": "arrow",
        "x": x0,
        "y": y0,
        "w": w,
        "h": h,
        "start": {"rx": rel(ax, x0, w), "ry": rel(ay, y0, h)},
        "end": {"rx": rel(bx, x0, w), "ry": rel(by, y0, h)},
        "strokePt": float(it.get("strokePt", 0.75)),
        "color": it.get("color", "#000000"),
        "head": it.get("head", "end"),
        **({"headEnd": it["headEnd"]} if "headEnd" in it else {}),
    }


def _text(it: dict, ctx: _Ctx, name: str, faces) -> dict:
    """文字框按它真实的排版落位：宽 = 字形宽（与前端「贴合文字」同一个余量），高 = 一行的行框。
    `attach` 是框上的哪个方位点对到 `at`（缺省 `w`：左边中点）。高度在桌面打开时会按内容重新量（`TextView`），
    这里给的是同一行框的值，量出来一致。"""
    text = it.get("text")
    if not isinstance(text, str) or not text:
        raise SceneError("bad_item", name, "text")
    if is_stat_text(text) and it.get("provenance") not in PROVENANCES:
        raise SceneError("needs_confirmation", name, "statistical_text")
    size = float(it.get("sizePt", 8))
    bold, italic = bool(it.get("bold", False)), bool(it.get("italic", False))
    family = it.get("fontFamily", ctx.family)
    # 多行（`\n`）：宽取最宽的一行，高按行数——只给一行高时，导出会画出全部行、桌面打开后又把框撑高而不重新落位（Codex #2201）
    lines = text.split("\n")
    natural_mm = max(faces(line, size, bold, italic, family) for line in lines) * PT_TO_MM
    em = size * PT_TO_MM
    w = natural_mm * 1.04 + 0.5 * em  # 与前端 `textFit.fitWidthMm` 同一个余量
    h = (
        len(lines) * size * 1.25 * PT_TO_MM
    )  # 行高缺省 1.25（`TextView` / `typography.layout_text`）
    px, py = _position(it.get("at"), ctx, name)
    attach = it.get("attach", "w")
    if attach not in POINTS:
        raise SceneError("bad_point", name, attach)
    ax_, ay_ = POINTS[attach]
    return {
        "id": name,
        "type": "text",
        "x": px - ax_ * w,
        "y": py - ay_ * h,
        "w": w,
        "h": h,
        "text": text,
        "sizePt": size,
        "bold": bold,
        **({"italic": True} if italic else {}),
        "color": it.get("color", "#000000"),
        "align": {
            "w": "left",
            "nw": "left",
            "sw": "left",
            "e": "right",
            "ne": "right",
            "se": "right",
        }.get(attach, "center"),
        "fontFamily": family,
    }


def _shape(it: dict, ctx: _Ctx, name: str) -> dict:
    size = it.get("size_mm")
    if (
        not isinstance(size, list)
        or len(size) != 2
        or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in size)
    ):
        raise SceneError("bad_item", name, "size_mm")
    px, py = _position(it.get("at"), ctx, name)
    attach = it.get("attach", "center")
    if attach not in POINTS:
        raise SceneError("bad_point", name, attach)
    ax_, ay_ = POINTS[attach]
    w, h = float(size[0]), float(size[1])
    obj = {
        "id": name,
        "type": "shape",
        "x": px - ax_ * w,
        "y": py - ay_ * h,
        "w": w,
        "h": h,
        "shape": it.get("shape", "ellipse"),
        "strokePt": float(it.get("strokePt", 0.75)),
        "color": it.get("color", "#000000"),
        "fill": it.get("fill"),
    }
    return obj


def _measure_text_mm(text: str, size_pt: float, bold: bool, italic: bool, family: str) -> float:
    from ..rendercore import facade

    fam = {"sans-serif": "sans-serif", "serif": "serif", "monospace": "monospace"}.get(
        family, "sans-serif"
    )
    return float(facade.text_width(text, size_pt, bold=bold, italic=italic, family=fam))


def _num_arg(it: dict, key: str, default: float, name: str, lo: float = 0.0) -> float:
    v = it.get(key, default)
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < lo:
        raise SceneError("bad_item", name, key)
    return float(v)


def _path_from_points(name: str, it: dict, pts: list[tuple[float, float]], **extra) -> dict:
    """画布上的一条折线 → `path` 对象（比例坐标、包围盒 = 折线范围；零宽 / 零高的那一维放在 0.5）。"""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, y0 = min(xs), min(ys)
    w, h = max(xs) - x0, max(ys) - y0

    def rel(v: float, lo: float, span: float) -> float:
        return (v - lo) / span if span > 1e-9 else 0.5

    return {
        "id": name,
        "type": "path",
        "x": x0,
        "y": y0,
        "w": w,
        "h": h,
        "subpaths": [
            {"closed": False, "nodes": [{"p": [rel(x, x0, w), rel(y, y0, h)]} for x, y in pts]}
        ],
        "strokePt": float(it.get("strokePt", 0.75)),
        "color": it.get("color", "#000000"),
        "fill": None,
        "lineJoin": "miter",
        **extra,
    }


def _node_records(name: str, pts, refs) -> list[dict]:
    """路径节点 k 应该落在 锚点 + 位移：位移 = 节点 − 锚点（求解时已知），核对在导出几何上重新解锚点再比。"""
    out = []
    for k, (pos, anchor_xy) in refs:
        out.append(
            {
                "item": name,
                "role": "node",
                "node": k,
                "anchor": pos["anchor"],
                # 节点相对锚点本身（不含位置偏移）的位移：位置偏移已经算进节点里了
                "offset_mm": [pts[k][0] - anchor_xy[0], pts[k][1] - anchor_xy[1]],
                "attach": "center",
            }
        )
    return out


def _bracket(it: dict, ctx: _Ctx, name: str) -> tuple[dict, list[dict]]:
    between = it.get("between")
    if not isinstance(between, list) or len(between) != 2:
        raise SceneError("bad_item", name, "between")
    (ax, ay), (bx, by) = (_position(p, ctx, name) for p in between)
    gap = _num_arg(it, "gap_mm", 1.0, name)
    tick = _num_arg(it, "tick_mm", 1.0, name)
    side = it.get("side", "above")
    if side == "above":
        yl = min(ay, by) - gap - tick
        pts = [(ax, yl + tick), (ax, yl), (bx, yl), (bx, yl + tick)]
    elif side == "below":
        yl = max(ay, by) + gap + tick
        pts = [(ax, yl - tick), (ax, yl), (bx, yl), (bx, yl - tick)]
    else:
        raise SceneError("bad_item", name, "side")
    obj = _path_from_points(name, it, pts, lineCap="butt")
    raw = [(p, resolve_anchor(p["anchor"], ctx, name)) for p in between]
    return obj, _node_records(name, pts, [(0, raw[0]), (3, raw[1])])


def _connector(it: dict, ctx: _Ctx, name: str) -> tuple[dict, list[dict]]:
    a = _position(it.get("from"), ctx, name)
    b = _position(it.get("to"), ctx, name)
    route = it.get("route", "straight")
    if route == "straight":
        pts = [a, b]
    elif route == "elbow-h":
        pts = [a, (b[0], a[1]), b]
    elif route == "elbow-v":
        pts = [a, (a[0], b[1]), b]
    else:
        raise SceneError("bad_item", name, "route")
    head = it.get("headEnd", "triangle")
    obj = _path_from_points(name, it, pts, **({"headEnd": head} if head != "none" else {}))
    refs = [
        (0, (it["from"], resolve_anchor(it["from"]["anchor"], ctx, name))),
        (len(pts) - 1, (it["to"], resolve_anchor(it["to"]["anchor"], ctx, name))),
    ]
    return obj, _node_records(name, pts, refs)


#: `box` 的 `fit` 写错时 `bad_item` 的 detail：把期望形态直接给出来（插件工具与桌面助手把它原样念给模型）
FIT_FORM = (
    'fit 应是目标列表，每个目标是单键对象，如 [{"panel": {"id": "P1"}}]；'
    '也可写 {"element": {"panel": "P1", "gid": …}} 或 {"item": {"name": …}}'
)


def _target_box(target: object, ctx: _Ctx, name: str) -> tuple[float, float, float, float]:
    """「框住」的一个目标 → 画布外接框（x0, y0, x1, y1）。"""
    if not isinstance(target, dict) or len(target) != 1:
        raise SceneError("bad_item", name, FIT_FORM)
    kind, spec = next(iter(target.items()))
    if not isinstance(spec, dict):
        raise SceneError("bad_item", name, FIT_FORM)
    if kind == "panel":
        pn = _panel_of(ctx, spec.get("id"), name)["panel"]
        return (pn["x"], pn["y"], pn["x"] + pn["w"], pn["y"] + pn["h"])
    if kind == "element":
        p = _panel_of(ctx, spec.get("panel"), name)
        _require_untransformed(p, name)
        x, y, w, h = panel_rect_to_page(p["panel"], element_ink_box(p, spec.get("gid"), name))
        return (x, y, x + w, y + h)
    if kind == "item":
        box = ctx.items.get(spec.get("name"))  # type: ignore[arg-type]
        if box is None:
            raise SceneError("unknown_ref", name, "item")
        return (box[0], box[1], box[0] + box[2], box[1] + box[3])
    raise SceneError("bad_item", name, FIT_FORM)


def _box(it: dict, ctx: _Ctx, name: str) -> tuple[dict, dict]:
    targets = it.get("fit")
    if not isinstance(targets, list) or not targets:
        raise SceneError("bad_item", name, FIT_FORM)
    boxes = [_target_box(t, ctx, name) for t in targets]
    pad = _num_arg(it, "padding_mm", 1.5, name)
    x0 = min(b[0] for b in boxes) - pad
    y0 = min(b[1] for b in boxes) - pad
    x1 = max(b[2] for b in boxes) + pad
    y1 = max(b[3] for b in boxes) + pad
    obj = {
        "id": name,
        "type": "shape",
        "x": x0,
        "y": y0,
        "w": x1 - x0,
        "h": y1 - y0,
        "shape": "rect",
        "strokePt": float(it.get("strokePt", 0.75)),
        "color": it.get("color", "#000000"),
        "fill": None,
        **({"dash": it["dash"]} if "dash" in it else {}),
        **({"cornerRadius": it["cornerRadius"]} if "cornerRadius" in it else {}),
    }
    return obj, {"item": name, "targets": targets, "boxes": boxes}


_STYLE = {"strokePt", "color"}
#: 每种元素允许的键（闭集）：写错的键（如给箭头写 `dash`）不静默丢掉，整批回 `bad_item`。
ITEM_KEYS = {
    "arrow": {"kind", "name", "from", "to", "head", "headEnd", "over_data"} | _STYLE,
    "text": {
        "kind",
        "name",
        "text",
        "at",
        "attach",
        "sizePt",
        "bold",
        "italic",
        "fontFamily",
        "color",
        "provenance",
        "over_data",
    },
    "shape": {"kind", "name", "shape", "at", "size_mm", "attach", "fill", "over_data"} | _STYLE,
    "bracket": {"kind", "name", "between", "side", "gap_mm", "tick_mm", "over_data"} | _STYLE,
    "connector": {"kind", "name", "from", "to", "route", "headEnd", "over_data"} | _STYLE,
    "box": {"kind", "name", "fit", "padding_mm", "dash", "cornerRadius"} | _STYLE,
}


def solve(
    items: object,
    panels: dict,
    *,
    measure=_measure_text_mm,
    fingerprint: str | None = None,
    page: dict | None = None,
) -> dict:
    """场景描述 → 文档对象（局部 id = 元素名）+ 锚点记录。整批要么全解出、要么全拒（`ok` 为假时 `objects` 为空）。

    `panels`：子图 id → `{"panel": PanelObject, "draw_manifest": …, "requests": [...], "data_points": [...]}`
    （`requests` 与 `data_points` 一一对应，来自同一次 `export(..., data_points=requests)`）。
    `fingerprint`：读场景时拿到的 `build_scene(...)["fingerprint"]`；给了就先核，对不上回 `scene_stale`。"""
    if fingerprint is not None and fingerprint != scene_fingerprint(panels, page):
        return {
            "ok": False,
            "objects": [],
            "anchors": [],
            "fits": [],
            "statistical": [],
            "errors": [SceneError("scene_stale").as_dict()],
        }
    ctx = _Ctx(panels)
    if not isinstance(items, list):
        return {
            "ok": False,
            "objects": [],
            "anchors": [],
            "errors": [SceneError("bad_item").as_dict()],
        }
    objects: list[dict] = []
    anchors: list[dict] = []
    fits: list[dict] = []
    seen: set[str] = set()
    try:
        for it in items:
            name = it.get("name") if isinstance(it, dict) else None
            if not isinstance(name, str) or not name:
                raise SceneError("bad_item", None, "name")
            if name in seen:
                raise SceneError("duplicate_name", name)
            seen.add(name)
            kind = it.get("kind")
            if kind not in ITEM_KEYS:
                raise SceneError("bad_item", name, "kind")
            unknown = sorted(set(it) - ITEM_KEYS[kind])
            if unknown:
                raise SceneError("bad_item", name, "unknown_key")
            records: list[dict] | None = None
            if kind == "arrow":
                obj = _arrow(it, ctx, name)
            elif kind == "text":
                obj = _text(it, ctx, name, measure)
            elif kind == "shape":
                obj = _shape(it, ctx, name)
            elif kind == "bracket":
                obj, records = _bracket(it, ctx, name)
            elif kind == "connector":
                obj, records = _connector(it, ctx, name)
            elif kind == "box":
                obj, fit = _box(it, ctx, name)
                fits.append(fit)
                records = []
            else:
                raise SceneError("bad_item", name, "kind")
            ctx.items[name] = (obj["x"], obj["y"], obj["w"], obj["h"])
            objects.append(obj)
            if records is not None:
                anchors += records
                continue
            for key in ("from", "to", "at"):
                pos = it.get(key)
                if isinstance(pos, dict) and isinstance(pos.get("anchor"), dict):
                    anchors.append(
                        {
                            "item": name,
                            "role": key,
                            "anchor": pos["anchor"],
                            "offset_mm": pos.get("offset_mm", [0.0, 0.0]),
                            "attach": it.get("attach", "w" if kind == "text" else "center"),
                        }
                    )
    except SceneError as exc:
        return {
            "ok": False,
            "objects": [],
            "anchors": [],
            "fits": [],
            "statistical": [],
            "errors": [exc.as_dict()],
        }
    # 锚点的持久身份：元素锚点带上 manifest 的 `identity`（脚本 label= 的摘要，ADR 0208 §六）——脚本重跑、gid
    # 重新编号后据它认回同一个元素；没有 label 的元素没有 identity，只能按 gid（与 override 的口径一致）
    for rec in anchors:
        spec = rec["anchor"].get("element") if isinstance(rec.get("anchor"), dict) else None
        if isinstance(spec, dict):
            p = panels.get(spec.get("panel"))
            el = next(
                (
                    e
                    for e in ((p or {}).get("draw_manifest") or {}).get("elements", [])
                    if e.get("gid") == spec.get("gid")
                ),
                None,
            )
            if el is not None and isinstance(el.get("identity"), str):
                rec["identity"] = el["identity"]
    # 统计标注即使写了来源，也单独列出来交给宿主给用户确认（来源是 Agent 自己填的，不等于用户确认过）
    statistical = [
        {"item": it["name"], "provenance": it.get("provenance")}
        for it in items
        if it.get("kind") == "text" and is_stat_text(it.get("text", ""))
    ]
    return {
        "ok": True,
        "objects": objects,
        "anchors": anchors,
        "fits": fits,
        "statistical": statistical,
        "errors": [],
    }


# ------------------------------------------------------------------ 读：图的几何场景（ADR 0208 §二）

#: 空白区的网格步长与最小矩形（ADR 0208 §十一 暂定数）
FREE_CELL_MM = 0.5
FREE_MIN_MM = 2.0
FREE_MAX_RECTS = 6


def _field(el: dict, prop: str):
    for f in el.get("editable") or []:
        if f.get("prop") == prop:
            return f.get("value")
    return None


def _scale_of(p: dict) -> float:
    size = (p.get("draw_manifest") or {}).get("size_mm") or [0, 0]
    full = panel_full_rect(p["panel"])
    return full[2] / size[0] if size and size[0] else 1.0


def scene_fingerprint(panels: dict, page: dict | None = None) -> str:
    """场景指纹：每张子图的落位与这次导出绘制的 manifest + 页面。落地前再算一次，不同 = 场景过期（`scene_stale`）。"""
    import hashlib
    import json

    blob = {
        "page": page or {},
        "panels": {
            pid: {"panel": p["panel"], "manifest": p.get("draw_manifest")}
            for pid, p in sorted(panels.items())
        },
    }
    return hashlib.sha256(
        json.dumps(blob, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def build_scene(panels: dict, *, page: dict | None = None, objects: list | None = None) -> dict:
    """给 Agent 看的场景：全部是短号 + 画布毫米（不含任何文字内容之外的脚本信息）。

    - `panels[].axes[]`：每根坐标轴（短号、gid、毫米框、范围、刻度类型）；
    - `panels[].elements[]`：带 gid 的数据 / 文字 / 图例元素（短号、角色、标签、墨迹框，没有墨迹几何的标 `ink: false`）；
    - `panels[].free[]`：每根坐标轴里的空白矩形（没有数据墨迹、没有文字），按面积排序，每块至少 2 × 2 mm；
    - `style`：图里的字体族、刻度 / 轴标题字号、轴线线宽（都已乘子图缩放，是画布上的实际值）、系列配色；
    - `annotations[]`：画布上已有的标注对象（id、类型、毫米框）；
    - `fingerprint`：`scene_fingerprint`。翻转 / 旋转过的子图只给外框（`transformed: true`）。"""
    from . import interference

    roles = set(interference.DATA_ROLES) | set(interference.TEXT_ROLES) | {"legend"}
    out_panels = []
    fonts: dict[str, int] = {}
    sizes: dict[str, list[float]] = {"tick_label": [], "axis_label": [], "title": []}
    spines: list[float] = []
    palette: list[str] = []
    for pid, p in panels.items():
        pn = p["panel"]
        entry: dict = {"ref": pid, "box_mm": [pn["x"], pn["y"], pn["w"], pn["h"]]}
        if panel_rotation(pn) or pn.get("flipH") or pn.get("flipV"):
            entry["transformed"] = True
            out_panels.append(entry)
            continue
        scale = _scale_of(p)
        man = p.get("draw_manifest") or {}
        axes_out, elements_out = [], []
        for el in man.get("elements", []):
            role, gid = el.get("role"), el.get("gid")
            if role == "axes" and isinstance(el.get("bbox"), list):
                x, y, w, h = panel_rect_to_page(pn, el["bbox"])
                axes_out.append(
                    {
                        "ref": f"{pid}.A{len(axes_out) + 1}",
                        "gid": gid,
                        "box_mm": [x, y, w, h],
                        "xlim": _field(el, "xlim"),
                        "ylim": _field(el, "ylim"),
                        "xscale": _field(el, "xscale"),
                        "yscale": _field(el, "yscale"),
                    }
                )
                lw = _field(el, "spine_linewidth")
                if isinstance(lw, (int, float)):
                    spines.append(float(lw) * scale)
                tf = _field(el, "tick_fontsize")
                if isinstance(tf, (int, float)):
                    sizes["tick_label"].append(float(tf) * scale)
                continue
            if role == "ticks":  # 刻度集合（`TickSet`）：刻度文字的字号挂在它身上
                fs = _field(el, "fontsize")
                if isinstance(fs, (int, float)) and interference.is_visible(el):
                    sizes["tick_label"].append(float(fs) * scale)
                continue
            if role not in roles or not interference.is_visible(el):
                continue
            try:
                ink = element_ink_box(p, gid, "scene")
                x, y, w, h = panel_rect_to_page(pn, ink)
                rec = {
                    "ref": f"{pid}.E{len(elements_out) + 1}",
                    "gid": gid,
                    "role": role,
                    "label": el.get("label"),
                    "box_mm": [x, y, w, h],
                    "ink": True,
                }
            except SceneError:
                if not isinstance(el.get("bbox"), list):
                    continue
                x, y, w, h = panel_rect_to_page(pn, el["bbox"])
                rec = {
                    "ref": f"{pid}.E{len(elements_out) + 1}",
                    "gid": gid,
                    "role": role,
                    "label": el.get("label"),
                    "box_mm": [x, y, w, h],
                    "ink": False,
                }
            elements_out.append(rec)
            fam = _field(el, "fontfamily")
            if isinstance(fam, str):
                fonts[fam] = fonts.get(fam, 0) + 1
            fs = _field(el, "fontsize")
            if isinstance(fs, (int, float)):
                bucket = {
                    "ticklabel": "tick_label",
                    "axis_label": "axis_label",
                    "title": "title",
                }.get(role)
                if bucket:
                    sizes[bucket].append(float(fs) * scale)
            col = _field(el, "color")
            if role in interference.DATA_ROLES and isinstance(col, str) and col not in palette:
                palette.append(col)
        entry.update(
            axes=axes_out,
            elements=elements_out,
            free=[r for a in axes_out for r in _free_rects(p, a["box_mm"])],
        )
        out_panels.append(entry)

    def typical(vals):
        return round(sorted(vals)[len(vals) // 2], 2) if vals else None

    return {
        "page": page,
        "panels": out_panels,
        "annotations": [
            {
                "id": o.get("id"),
                "type": o.get("type"),
                "box_mm": [o.get("x"), o.get("y"), o.get("w"), o.get("h")],
            }
            for o in (objects or [])
            if isinstance(o, dict) and o.get("type") != "panel" and not o.get("hidden")
        ],
        "style": {
            "font_family": max(fonts, key=fonts.get) if fonts else None,
            "size_pt": {k: typical(v) for k, v in sizes.items()},
            "axis_line_pt": typical(spines),
            "palette": palette[:12],
        },
        "fingerprint": scene_fingerprint(panels, page),
    }


def _free_rects(p: dict, axes_box: list) -> list[dict]:
    """一根坐标轴绘图区里的空白矩形：按 0.5 mm 网格标出数据墨迹与文字占的格子，反复取最大的空矩形。"""
    from . import interference

    pn = p["panel"]
    ax0, ay0, aw, ah = axes_box
    nx, ny = max(1, int(aw / FREE_CELL_MM)), max(1, int(ah / FREE_CELL_MM))
    full = panel_full_rect(pn)
    busy = [[False] * nx for _ in range(ny)]

    def mark_rect(x0, y0, x1, y1, pad=1):
        i0 = max(0, int((x0 - ax0) / FREE_CELL_MM) - pad)
        i1 = min(nx - 1, int((x1 - ax0) / FREE_CELL_MM) + pad)
        j0 = max(0, int((y0 - ay0) / FREE_CELL_MM) - pad)
        j1 = min(ny - 1, int((y1 - ay0) / FREE_CELL_MM) + pad)
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                busy[j][i] = True

    def to_mm(q):
        return (full[0] + q[0] * full[2], full[1] + q[1] * full[3])

    for el in (p.get("draw_manifest") or {}).get("elements", []):
        role = el.get("role")
        if not interference.is_visible(el):
            continue
        if role in interference.DATA_ROLES:
            paths = (el.get("geometry") or {}).get("paths")
            if not paths or role in interference.BBOX_BASIS_ROLES:
                r = interference.visible_rect(el)
                if r:
                    a, b = to_mm((r[0], r[1])), to_mm((r[2], r[3]))
                    mark_rect(a[0], a[1], b[0], b[1], pad=0)
                continue
            for path in paths:
                pts = [
                    to_mm(q)
                    for q in path.get("points") or []
                    if isinstance(q, (list, tuple)) and len(q) == 2
                ]
                for a, b in zip(pts, pts[1:] + (pts[:1] if path.get("closed") else [])):
                    n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / (FREE_CELL_MM / 2)))
                    for k in range(n + 1):
                        x, y = a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n
                        mark_rect(x, y, x, y)
                if path.get("closed") and len(pts) > 2:
                    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
                    mark_rect(min(xs), min(ys), max(xs), max(ys), pad=0)
        elif role in interference.TEXT_ROLES:
            r = interference.visible_rect(el)
            if r:
                a, b = to_mm((r[0], r[1])), to_mm((r[2], r[3]))
                mark_rect(a[0], a[1], b[0], b[1], pad=0)
    out = []
    min_cells = int(FREE_MIN_MM / FREE_CELL_MM)
    for _ in range(FREE_MAX_RECTS):
        best = _largest_empty(busy, nx, ny)
        if best is None:
            break
        i0, j0, i1, j1 = best
        if i1 - i0 + 1 < min_cells or j1 - j0 + 1 < min_cells:
            break
        out.append(
            {
                "box_mm": [
                    ax0 + i0 * FREE_CELL_MM,
                    ay0 + j0 * FREE_CELL_MM,
                    (i1 - i0 + 1) * FREE_CELL_MM,
                    (j1 - j0 + 1) * FREE_CELL_MM,
                ]
            }
        )
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                busy[j][i] = True
    return out


def _largest_empty(busy, nx, ny):
    """最大全空矩形（直方图 + 单调栈，O(nx·ny)）：(i0, j0, i1, j1) 或 None。"""
    heights = [0] * nx
    best, best_area = None, 0
    for j in range(ny):
        for i in range(nx):
            heights[i] = 0 if busy[j][i] else heights[i] + 1
        stack: list[int] = []
        for i in range(nx + 1):
            h = heights[i] if i < nx else 0
            while stack and heights[stack[-1]] >= h:
                top = stack.pop()
                height = heights[top]
                left = stack[-1] + 1 if stack else 0
                area = height * (i - left)
                if height and area > best_area:
                    best_area, best = area, (left, j - height + 1, i - 1, j)
            stack.append(i)
    return best
