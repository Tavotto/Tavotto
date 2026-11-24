"""Agent 加的矢量元素：量过才交（ADR 0208 §四）。

在**导出载荷的几何**上量（`agentobjects.to_export_objects` + `rendercore.geometry`，与真正导出同一条路），
不拿求解器的输出验求解器。每一项给 `pass` / `fail` / `unknown`；量不了就是 `unknown`，**不冒充通过**。

- `anchor`：每个锚点，导出几何上的实际位置与目标（锚点在 `draw_manifest` / `data_points` 上重新解出 + 偏移）之差
  ≤ `RESIDUAL_MM`（0.1 pt）；
- `data`：不遮数据。数据元素的角色闭集用 `interference.DATA_ROLES`；有真实轮廓（`geometry.paths`）的按轮廓判
  （线段穿过 + 落在闭合填充里面）；只有外框的（`interference.BBOX_BASIS_ROLES`、没 geometry 的）与新对象相交 → `unknown`。
  箭头锚在数据 / 元素上的那一端允许伸进 `TIP_ALLOWANCE_MM`；描述里写了 `over_data: true` 的元素记 `waived`；
- `text`：不压字。与图里的文字（`interference.TEXT_ROLES` 的可见框）、本批的其它文字 / 形状比；彼此锚定的一对不算；
- `page`：不出页面；
- `journal`：期刊字号 / 线宽下限，直接跑 `preflight.run`（`font-too-small` / `font-below-absolute-floor` /
  `line-width-below-min`，两侧同源的判据，不另写）。

结果：`{"ok": 没有 fail, "verified": 没有 fail 也没有 unknown, "facts": [...]}`。
"""

from __future__ import annotations

import math
from typing import Any

from ..rendercore import geometry
from . import interference, preflight, vectorscene

#: 锚点残差上限：0.1 pt（0199 §六 的口径）
RESIDUAL_MM = 0.1 * 25.4 / 72.0
#: 箭头锚在数据上的那一端可以伸进数据区多少（ADR 0208 §十一 暂定数）
TIP_ALLOWANCE_MM = 1.5
#: 不压字：两个框在 x、y 上都重叠到这么多才算（与 `interference.MIN_OVERLAP_MM` 同一个数）
MIN_OVERLAP_MM = interference.MIN_OVERLAP_MM
#: 沿箭头杆取样的步长
SAMPLE_STEP_MM = 0.2
JOURNAL_CHECKS = ("font-too-small", "font-below-absolute-floor", "line-width-below-min")


class _Facts:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, check: str, status: str, item: str | None = None, **detail: Any) -> None:
        f: dict[str, Any] = {"check": check, "status": status}
        if item is not None:
            f["item"] = item
        if detail:
            f["detail"] = detail
        self.items.append(f)


def _box(e: dict) -> tuple[float, float, float, float]:
    return (e["x_mm"], e["y_mm"], e["x_mm"] + e["w_mm"], e["y_mm"] + e["h_mm"])


def _arrow_ends(e: dict) -> tuple[tuple[float, float], tuple[float, float]]:
    g = geometry.arrow_geometry(e, e["w_mm"], e["h_mm"])
    return (
        (e["x_mm"] + g["a"][0], e["y_mm"] + g["a"][1]),
        (e["x_mm"] + g["b"][0], e["y_mm"] + g["b"][1]),
    )


def _attach_point(e: dict, attach: str) -> tuple[float, float]:
    px, py = vectorscene.POINTS[attach]
    return (e["x_mm"] + px * e["w_mm"], e["y_mm"] + py * e["h_mm"])


def _path_points(e: dict) -> list[tuple[float, float]]:
    """导出载荷里路径的节点（画布毫米；Agent 的路径只有直线段，手柄不参与）。"""
    return [
        (e["x_mm"] + n["p"][0] * e["w_mm"], e["y_mm"] + n["p"][1] * e["h_mm"])
        for sp in e["subpaths"]
        for n in sp["nodes"]
    ]


def _actual(e: dict, role: str, attach: str, node: int | None = None) -> tuple[float, float]:
    if role == "node":
        return _path_points(e)[node]  # type: ignore[index]
    if e["type"] == "arrow":
        a, b = _arrow_ends(e)
        return a if role == "from" else b
    return _attach_point(e, attach)


def _samples(a, b, skip_start: float, skip_end: float) -> list[tuple[float, float]]:
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length <= 1e-9:
        return [a]
    n = max(1, int(length / SAMPLE_STEP_MM))
    out = []
    for i in range(n + 1):
        d = length * i / n
        if d < skip_start or d > length - skip_end:
            continue
        t = d / length
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def _point_in_closed(paths: list, x: float, y: float) -> bool:
    """点在任一闭合路径里（奇偶规则）：柱子、填充区这类面，整个落在里面时线段穿越判不出来。"""
    for path in paths or []:
        if not isinstance(path, dict) or not path.get("closed"):
            continue
        pts = [p for p in path.get("points") or [] if isinstance(p, (list, tuple)) and len(p) == 2]
        inside = False
        for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
            if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / ((by - ay) or 1e-300) + ax:
                inside = not inside
        if inside:
            return True
    return False


class _PanelFrac:
    """画布毫米 → 这个子图的图幅分数（只对没旋转、没翻转的子图；变换过的回 None）。"""

    def __init__(self, panel: dict):
        self.ok = not (
            vectorscene.panel_rotation(panel) or panel.get("flipH") or panel.get("flipV")
        )
        self.full = vectorscene.panel_full_rect(panel)
        self.box = (panel["x"], panel["y"], panel["x"] + panel["w"], panel["y"] + panel["h"])

    def rect(self, r):
        fx, fy, fw, fh = self.full
        return ((r[0] - fx) / fw, (r[1] - fy) / fh, (r[2] - fx) / fw, (r[3] - fy) / fh)

    def point(self, p):
        fx, fy, fw, fh = self.full
        return ((p[0] - fx) / fw, (p[1] - fy) / fh)


def _overlaps(a, b, margin: float) -> bool:
    return (
        min(a[2], b[2]) - max(a[0], b[0]) >= margin and min(a[3], b[3]) - max(a[1], b[1]) >= margin
    )


def _touch(a, b) -> bool:
    """闭区间相交：竖直 / 水平的箭头外接框宽或高为 0，严格相交会把它整根漏掉。"""
    return min(a[2], b[2]) >= max(a[0], b[0]) and min(a[3], b[3]) >= max(a[1], b[1])


def check(
    solved: dict,
    objects: list[dict],
    panels: dict,
    items: list,
    *,
    page: dict | None = None,
    profile: dict | None = None,
) -> dict:
    """`solved` 是 `vectorscene.solve` 的结果，`objects` 是同一批对象**校验过**的样子（`agentobjects` 换过 id，
    顺序不变）。`items` 是原描述（取 `over_data` 与锚点种类）。"""
    from . import agentobjects

    facts = _Facts()
    exports = agentobjects.to_export_objects(objects)
    names = [o["id"] for o in solved["objects"]]
    by_name = dict(zip(names, exports))
    spec = {it.get("name"): it for it in items if isinstance(it, dict)}

    # ---- anchor：在导出几何上重新解锚点
    ctx = vectorscene._Ctx(panels)
    ctx.items = {n: (e["x_mm"], e["y_mm"], e["w_mm"], e["h_mm"]) for n, e in by_name.items()}
    for rec in solved["anchors"]:
        e = by_name[rec["item"]]
        try:
            tx, ty = vectorscene.resolve_anchor(rec["anchor"], ctx, rec["item"])
        except vectorscene.SceneError as exc:
            facts.add("anchor", "unknown", rec["item"], role=rec["role"], reason=exc.code)
            continue
        tx, ty = tx + rec["offset_mm"][0], ty + rec["offset_mm"][1]
        ax, ay = _actual(e, rec["role"], rec["attach"], rec.get("node"))
        res = math.hypot(ax - tx, ay - ty)
        facts.add(
            "anchor",
            "pass" if res <= RESIDUAL_MM else "fail",
            rec["item"],
            role=rec["role"],
            residual_mm=round(res, 6),
        )

    # ---- 每个对象在画布上占的地方：文字 / 形状 = 框；箭头 = 沿杆的取样点（锚在数据 / 元素上的端头让出余量）
    def footprint(name: str, e: dict):
        if e["type"] == "shape" and not e.get("fill"):
            return ("points", _outline_samples(e))  # 只有描边：占的是那一圈线，不是整个框
        it = spec.get(name, {})
        soft = lambda key: (  # noqa: E731
            TIP_ALLOWANCE_MM
            if isinstance((it.get(key) or {}).get("anchor"), dict)
            and next(iter(it[key]["anchor"]), None) in ("data", "element")
            else 0.0
        )
        if e["type"] == "path":
            pts = _path_points(e)
            out: list[tuple[float, float]] = []
            last = len(pts) - 2
            for i, (a, b) in enumerate(zip(pts, pts[1:])):
                out += _samples(
                    a, b, soft("from") if i == 0 else 0.0, soft("to") if i == last else 0.0
                )
            return ("points", out or pts)
        if e["type"] != "arrow":
            return ("rect", _box(e))
        a, b = _arrow_ends(e)
        return ("points", _samples(a, b, soft("from"), soft("to")))

    feet = {n: footprint(n, e) for n, e in by_name.items()}

    # ---- data：不遮数据
    for pid, p in panels.items():
        pf = _PanelFrac(p["panel"])
        elements = (p.get("draw_manifest") or {}).get("elements", [])
        for name, (kind, foot) in feet.items():
            if spec.get(name, {}).get("over_data") is True:
                facts.add("data", "waived", name, panel=pid)
                continue
            fbox = foot if kind == "rect" else _bounds(foot)
            if not _touch(fbox, pf.box):
                continue  # 不在这个子图上
            if not pf.ok:
                facts.add("data", "unknown", name, panel=pid, reason="panel_transformed")
                continue
            hit, unknown = [], []
            for el in elements:
                if el.get("role") not in interference.DATA_ROLES or not interference.is_visible(el):
                    continue
                vis = interference.visible_rect(el)
                paths = (el.get("geometry") or {}).get("paths")
                if el.get("role") in interference.BBOX_BASIS_ROLES or not paths:
                    if vis is not None and _foot_touches_rect(kind, foot, pf, vis):
                        unknown.append(el["gid"])
                    continue
                if _foot_hits(kind, foot, pf, paths):
                    hit.append(el["gid"])
            if hit:
                facts.add("data", "fail", name, panel=pid, gids=hit)
            elif unknown:
                facts.add("data", "unknown", name, panel=pid, gids=unknown, reason="no_geometry")
            else:
                facts.add("data", "pass", name, panel=pid)

    # ---- text：不压字
    linked = {
        (a["item"], a["anchor"]["item"].get("name"))
        for a in solved["anchors"]
        if "item" in a["anchor"]
    }
    linked |= {(b, a) for a, b in linked}
    texts: list[tuple[str, tuple]] = []
    for pid, p in panels.items():
        pf = _PanelFrac(p["panel"])
        if not pf.ok:
            continue
        for el in (p.get("draw_manifest") or {}).get("elements", []):
            if el.get("role") in interference.TEXT_ROLES:
                r = interference.visible_rect(el)
                if r is not None:
                    x0, y0, x1, y1 = r
                    fx, fy, fw, fh = pf.full
                    texts.append(
                        (
                            f"{pid}:{el['gid']}",
                            (fx + x0 * fw, fy + y0 * fh, fx + x1 * fw, fy + y1 * fh),
                        )
                    )
    for name, e in by_name.items():
        if e["type"] == "text":
            texts.append((name, _box(e)))
    for name, (kind, foot) in feet.items():
        clash = []
        for other, r in texts:
            if other == name or (name, other) in linked:
                continue
            if kind == "rect":
                if _overlaps(foot, r, MIN_OVERLAP_MM):
                    clash.append(other)
            elif any(r[0] < x < r[2] and r[1] < y < r[3] for x, y in foot):
                clash.append(other)
        facts.add("text", "fail" if clash else "pass", name, **({"with": clash} if clash else {}))

    # ---- page：不出页面
    if page:
        for name, (kind, foot) in feet.items():
            b = foot if kind == "rect" else _bounds(foot)
            inside = (
                b[0] >= -1e-9
                and b[1] >= -1e-9
                and b[2] <= page["w"] + 1e-9
                and b[3] <= page["h"] + 1e-9
            )
            facts.add("page", "pass" if inside else "fail", name)

    # ---- journal：期刊字号 / 线宽（预检同一套判据）
    if profile is not None:
        from .agentobjects import table  # noqa: F401 — 保证字段表可读（与校验同一份）

        spec_pf = {
            "page": {
                "w_mm": (page or {}).get("w", 1e4),
                "h_mm": (page or {}).get("h", 1e4),
                "margin_mm": 0,
            },
            "panels": [],
            "texts": [
                {
                    "id": n,
                    "text": e["text"],
                    "size_pt": e["size_pt"],
                    "bold": e["bold"],
                    "font_family": e.get("font_family") or "serif",
                    "rect_mm": [e["x_mm"], e["y_mm"], e["w_mm"], e["h_mm"]],
                    "hidden": False,
                }
                for n, e in by_name.items()
                if e["type"] == "text"
            ],
            "objects": [
                {
                    "id": n,
                    "type": e["type"],
                    "rect_mm": [e["x_mm"], e["y_mm"], e["w_mm"], e["h_mm"]],
                    "hidden": False,
                    **(
                        {"stroke_pt": e["stroke_pt"]}
                        if e["type"] in ("arrow", "shape", "path") and e.get("stroke") is not False
                        else {}
                    ),
                }
                for n, e in by_name.items()
            ],
        }
        issues = {i["id"]: i for i in preflight.run(spec_pf, profile) if i["id"] in JOURNAL_CHECKS}
        for name in by_name:
            bad = [cid for cid, i in issues.items() if name in i.get("object_ids", [])]
            facts.add(
                "journal", "fail" if bad else "pass", name, **({"checks": bad} if bad else {})
            )

    # ---- fit：框住的目标都在框里
    for fit in solved.get("fits") or []:
        e = by_name[fit["item"]]
        b = _box(e)
        outside = [
            i
            for i, t in enumerate(fit["boxes"])
            if not (
                t[0] >= b[0] - 1e-9
                and t[1] >= b[1] - 1e-9
                and t[2] <= b[2] + 1e-9
                and t[3] <= b[3] + 1e-9
            )
        ]
        facts.add(
            "fit",
            "fail" if outside else "pass",
            fit["item"],
            **({"outside": outside} if outside else {}),
        )

    # ---- style：与原图一致（字体族同一类；ADR 0208 §四 第 8 条）
    figure_family = vectorscene._figure_family(panels)  # noqa: SLF001
    has_text = any(
        el.get("role") in interference.TEXT_ROLES
        for p in panels.values()
        for el in (p.get("draw_manifest") or {}).get("elements", [])
    )
    for name, e in by_name.items():
        if e["type"] != "text":
            continue
        if not has_text:
            facts.add("style", "unknown", name, reason="no_figure_text")
            continue
        fam = vectorscene.family_class(e.get("font_family") or "serif")
        facts.add(
            "style",
            "pass" if fam == figure_family else "fail",
            name,
            **({} if fam == figure_family else {"family": fam, "figure": figure_family}),
        )

    statuses = {f["status"] for f in facts.items}
    return {
        "ok": "fail" not in statuses,
        "verified": not ({"fail", "unknown"} & statuses),
        "facts": facts.items,
    }


def _outline_samples(e: dict) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = _box(e)
    if e.get("shape") == "ellipse":
        cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
        n = max(16, int(2 * math.pi * max(rx, ry) / SAMPLE_STEP_MM))
        return [
            (cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n))
            for i in range(n)
        ]
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    out: list[tuple[float, float]] = []
    for a, b in zip(corners, corners[1:] + corners[:1]):
        out += _samples(a, b, 0.0, 0.0)
    return out


def _bounds(points) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _foot_touches_rect(kind: str, foot, pf: _PanelFrac, vis) -> bool:
    """只有外框的数据元素：占位本身（框，或沿线的取样点）碰到它的可见框才算「量不了」——
    用整体外接范围会把「框住一整张子图」的描边框判成碰到里面所有的柱子。"""
    if kind == "rect":
        return _touch(pf.rect(foot), vis)
    half = 0.05
    return any(_touch(pf.rect((x - half, y - half, x + half, y + half)), vis) for x, y in foot)


def _foot_hits(kind: str, foot, pf: _PanelFrac, paths: list) -> bool:
    if kind == "rect":
        r = pf.rect(foot)
        cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
        return interference._segments_hit_rect(paths, r) or _point_in_closed(paths, cx, cy)  # noqa: SLF001
    half = 0.05  # mm：取样点按细线的半宽判
    for x, y in foot:
        r = pf.rect((x - half, y - half, x + half, y + half))
        if interference._segments_hit_rect(paths, r) or _point_in_closed(
            paths, (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
        ):  # noqa: SLF001
            return True
    return False
