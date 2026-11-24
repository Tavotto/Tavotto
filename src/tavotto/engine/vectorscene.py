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
  （圈出一个点、框一块区域）；位置 = `{"anchor": 锚点, "offset_mm": [dx, dy]}`（偏移可省）。
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
    "needs_confirmation",  # 统计标注（星号 / n.s. / p 值 / n）没写明来自用户或数据：不由 Agent 编（ADR 0208 §四）
)

#: 统计标注的文字（ADR 0208 §四，维护者 2026-11-24 定）：只能来自用户的话或脚本 / 数据里的结果，
#: 描述里要写 `"provenance": "user" | "data"`；没写的整批不落地，回 `needs_confirmation`。
_STAT_TEXT = re.compile(
    r"^\s*(\*{1,4}|n\.?\s?s\.?|ns)\s*$|\bp\s*[<>=≤≥]|\bp\s*-?\s*value|\bn\s*=\s*\d",
    re.IGNORECASE,
)
PROVENANCES = ("user", "data")


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
    for key in ("from", "to", "at"):
        pos = it.get(key)
        if isinstance(pos, dict) and isinstance(pos.get("anchor"), dict):
            found.append(pos["anchor"])
    return found


class _Ctx:
    def __init__(self, panels: dict):
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
        el = next(
            (
                e
                for e in (p.get("draw_manifest") or {}).get("elements", [])
                if e.get("gid") == spec.get("gid")
            ),
            None,
        )
        if el is None or not isinstance(el.get("bbox"), list):
            raise SceneError("unknown_ref", item, "element")
        return _box_point(
            panel_rect_to_page(p["panel"], el["bbox"]), spec.get("point", "center"), item
        )
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
    if _STAT_TEXT.search(text) and it.get("provenance") not in PROVENANCES:
        raise SceneError("needs_confirmation", name, "statistical_text")
    size = float(it.get("sizePt", 8))
    bold, italic = bool(it.get("bold", False)), bool(it.get("italic", False))
    family = it.get("fontFamily", "sans-serif")
    natural_mm = faces(text, size, bold, italic, family) * PT_TO_MM
    em = size * PT_TO_MM
    w = natural_mm * 1.04 + 0.5 * em  # 与前端 `textFit.fitWidthMm` 同一个余量
    h = size * 1.25 * PT_TO_MM  # 行高缺省 1.25（`TextView` / `typography.layout_text`）
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


def solve(items: object, panels: dict, *, measure=_measure_text_mm) -> dict:
    """场景描述 → 文档对象（局部 id = 元素名）+ 锚点记录。整批要么全解出、要么全拒（`ok` 为假时 `objects` 为空）。

    `panels`：子图 id → `{"panel": PanelObject, "draw_manifest": …, "requests": [...], "data_points": [...]}`
    （`requests` 与 `data_points` 一一对应，来自同一次 `export(..., data_points=requests)`）。"""
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
            if kind == "arrow":
                obj = _arrow(it, ctx, name)
            elif kind == "text":
                obj = _text(it, ctx, name, measure)
            elif kind == "shape":
                obj = _shape(it, ctx, name)
            else:
                raise SceneError("bad_item", name, "kind")
            ctx.items[name] = (obj["x"], obj["y"], obj["w"], obj["h"])
            objects.append(obj)
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
        return {"ok": False, "objects": [], "anchors": [], "errors": [exc.as_dict()]}
    return {"ok": True, "objects": objects, "anchors": anchors, "errors": []}
