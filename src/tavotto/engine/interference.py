"""真实渲染几何上的干涉检测：文字互相重叠、文字压到别的子图、图例压住数据。

输入是引擎 manifest（`bbox` / `clip_bbox` 都是 figure 分数、y 向下；`geometry`
是真正画出来的路径），输出与 `engine/preflight.py` **同一形状**的问题条目
（`id / severity / text / message / object_ids / gids / detail`），所以它们能和
预检结果放进同一份报告、同一份 proof。这里不是第二个规范求值器：规范讲的是
「该长什么样」，这里量的是「画出来有没有撞上」。

## 不把所有包围盒相交当成错误

同一张图上合法的相交比非法的多得多。三条只报**确定能读出坏结果**的：

* `text-overlap`——两段正文的布局框咬进对方 ≥ `MIN_OVERLAP_MM`（两个方向都要）。
  同一个图例里的条目之间、条目与自己的图例框之间不算（matplotlib 排的）。
* `text-over-axes`——一个子图的装饰文字（标题 / 轴标题 / 刻度 / 图例）落进了
  **另一个**顶层子图的绘图区。自己的插图、被自己包住的子图不算（插图本来就
  在父图里）。
* `legend-over-data`——图例框与**同一子图里**数据的真实几何相交。拿得到
  `geometry`（曲线的折线、散点每颗 marker）时是**确定**的（`detail.certain`），
  只有包围盒（误差棒容器、图像、Collection）时只标**风险**，不当阻断。

曲线相交、注释（`text` 角色）压在数据上、箭头连到目标、父子包含——一律不报：
那些是画图的常态，不是缺陷。看不见的（`visible: false`）一个都不参与。

纯标准库：Flask 与 MCP server 两个进程都 import 得动。
"""

from __future__ import annotations

from . import profiles as profiles_mod

#: 两个文字框要在两个方向上都至少咬进这么多 mm 才算重叠。布局框（`get_window_extent`）
#: 比墨迹宽：descender 留白、坐标取整都会让相邻刻度标签的框轻碰一下。0.25 mm 在
#: 8 pt 字上不到一个字母的宽度，肉眼已经看得出两个字挤在一起。
MIN_OVERLAP_MM = 0.25

#: 装饰文字的角色——它们由子图的排版决定落点，撞上别的东西就是排版出了问题。
DECORATION_ROLES = ("title", "axis_label", "ticklabel", "legend_text")
#: 参与「文字互相重叠」的全部角色：装饰文字 + 独立文字 + 图例框（图例作为一个块）。
TEXT_ROLES = (*DECORATION_ROLES, "text", "legend")
#: 数据 artist 的角色：图例压住它们才叫「压住数据」。
DATA_ROLES = (
    "line",
    "errorbar",
    "scatter",
    "fill",
    "patch",
    "collection",
    "linecoll",
    "image",
    "bar",
    "bar_series",
    "stem_series",
)

CHECK_TEXT_OVERLAP = "text-overlap"
CHECK_TEXT_OVER_AXES = "text-over-axes"
CHECK_LEGEND_OVER_DATA = "legend-over-data"
CHECK_IDS = (CHECK_TEXT_OVERLAP, CHECK_TEXT_OVER_AXES, CHECK_LEGEND_OVER_DATA)


def _field(el: dict, prop: str):
    for f in el.get("editable") or []:
        if f.get("prop") == prop:
            return f.get("value")
    return None


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f and abs(f) != float("inf") else None


def _rect(v) -> tuple[float, float, float, float] | None:
    """`[x, y, w, h]`（figure 分数）→ `(x0, y0, x1, y1)`；不是四个有限数就 None。"""
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        return None
    nums = [_num(x) for x in v]
    if any(n is None for n in nums):
        return None
    x, y, w, h = nums  # type: ignore[misc]
    return (x, y, x + w, y + h)


def _intersect(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _contains(outer, inner) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def is_visible(el: dict) -> bool:
    return _field(el, "visible") is not False


def visible_rect(el: dict) -> tuple[float, float, float, float] | None:
    """元素**画出来**占的框（figure 分数）：`bbox` 先折进 `clip_bbox`（有的话）。

    曲线 / 散点的 `bbox` 是未裁剪的整个数据范围（见 `preflight.py` 对 `clip_bbox`
    的说明），拿它判干涉会把图幅外的离群点也算进来。
    """
    if not is_visible(el):
        return None
    box = _rect(el.get("bbox"))
    if box is None:
        return None
    clip = _rect(el.get("clip_bbox"))
    if clip is not None:
        box = _intersect(box, clip)
    return box


def owner_axes(gid: str) -> str:
    """`axes_3.legend.texts_0` → `axes_3`；figure 级元素回空串。"""
    if not gid.startswith("axes_"):
        return ""
    return gid.split(".", 1)[0]


def legend_of(gid: str) -> str:
    """`axes_0.legend.texts_1` → `axes_0.legend`；不是图例条目回空串。"""
    head, sep, rest = gid.rpartition(".")
    if sep and rest.startswith("texts_") and (head.endswith(".legend") or "legend_" in head):
        return head
    return ""


def is_top_level_axes(el: dict) -> bool:
    """有 `position` 字段的 axes：插图 / 次坐标轴 / 寄生轴的落位被锁（manifest 不发），
    它们在父图里是常态，不算别人的「绘图区」。"""
    return el.get("role") in ("axes", "axes3d") and _field(el, "position") is not None


class _Issue:
    """与 `preflight._Sink.add` 同一形状的一条。"""

    __slots__ = ("id", "gids", "detail", "message", "text", "worse")

    def __init__(self, cid: str, gids: list[str], text: str, message: tuple, detail: dict, worse):
        self.id = cid
        self.gids = gids
        self.text = text
        self.message = message
        self.detail = detail
        self.worse = worse


def _segments_hit_rect(paths: list, rect) -> bool:
    """折线的任一线段是否穿过矩形（figure 分数）。Liang–Barsky 裁剪。"""
    x0, y0, x1, y1 = rect
    for path in paths or []:
        pts = path.get("points") if isinstance(path, dict) else None
        if not isinstance(pts, list):
            continue
        clean = []
        for p in pts:
            if isinstance(p, (list, tuple)) and len(p) == 2:
                px, py = _num(p[0]), _num(p[1])
                if px is not None and py is not None:
                    clean.append((px, py))
        if len(clean) == 1:
            px, py = clean[0]
            if x0 <= px <= x1 and y0 <= py <= y1:
                return True
        for (ax, ay), (bx, by) in zip(clean, clean[1:]):
            if _segment_in_rect(ax, ay, bx, by, x0, y0, x1, y1):
                return True
        if path.get("closed") and len(clean) > 2:
            (ax, ay), (bx, by) = clean[-1], clean[0]
            if _segment_in_rect(ax, ay, bx, by, x0, y0, x1, y1):
                return True
    return False


def _segment_in_rect(ax, ay, bx, by, x0, y0, x1, y1) -> bool:
    dx, dy = bx - ax, by - ay
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if p == 0:
            if q < 0:
                return False
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return False
            t0 = max(t0, r)
        else:
            if r < t0:
                return False
            t1 = min(t1, r)
    return t0 <= t1


def detect(manifest: dict, profile: dict | None = None, *, panel_id: str = "figure") -> list[dict]:
    """跑一遍干涉检测，返回预检形状的问题清单（顺序稳定：按 gid 排序）。"""
    size = manifest.get("size_mm") or [0.0, 0.0]
    W = _num(size[0] if len(size) > 0 else None) or 0.0
    H = _num(size[1] if len(size) > 1 else None) or 0.0
    if W <= 0 or H <= 0:
        return []
    elements = [e for e in manifest.get("elements") or [] if isinstance(e, dict)]
    by_gid = {e.get("gid", ""): e for e in elements}
    found: list[_Issue] = []

    # ---- 文字互相重叠 ----
    texts = []
    for el in elements:
        gid = el.get("gid", "")
        if el.get("role") not in TEXT_ROLES:
            continue
        r = visible_rect(el)
        if r is None:
            continue
        texts.append((gid, el.get("role"), r))
    texts.sort(key=lambda t: t[0])
    for i in range(len(texts)):
        ga, ra, a = texts[i]
        for j in range(i + 1, len(texts)):
            gb, rb, b = texts[j]
            if _same_legend_family(ga, gb):
                continue
            hit = _intersect(a, b)
            if hit is None:
                continue
            dx_mm = (hit[2] - hit[0]) * W
            dy_mm = (hit[3] - hit[1]) * H
            if dx_mm < MIN_OVERLAP_MM or dy_mm < MIN_OVERLAP_MM:
                continue
            area = round(dx_mm * dy_mm, 3)
            found.append(
                _Issue(
                    CHECK_TEXT_OVERLAP,
                    [ga, gb],
                    f"文字互相重叠 {dx_mm:.1f}×{dy_mm:.1f} mm（{_label(by_gid, ga)} 与 {_label(by_gid, gb)}）",
                    ("textOverlap", {"a": ga, "b": gb, "w": f"{dx_mm:.1f}", "h": f"{dy_mm:.1f}"}),
                    {
                        "overlap_mm": [round(dx_mm, 3), round(dy_mm, 3)],
                        "overlap_mm2": area,
                        "certain": True,
                        "basis": "bbox",
                        "roles": [ra, rb],
                    },
                    area,
                )
            )

    # ---- 装饰文字压到别的子图的绘图区 ----
    axes_boxes = []
    for el in elements:
        if not is_top_level_axes(el):
            continue
        r = visible_rect(el)
        if r is not None:
            axes_boxes.append((el.get("gid", ""), r))
    all_axes_rect = {
        e.get("gid", ""): _rect(e.get("bbox"))
        for e in elements
        if e.get("role") in ("axes", "axes3d")
    }
    for gid, role, r in texts:
        if role not in (*DECORATION_ROLES, "legend"):
            continue
        owner = owner_axes(gid)
        owner_rect = all_axes_rect.get(owner)
        for ax_gid, box in axes_boxes:
            if ax_gid == owner:
                continue
            # 自己的插图 / 自己被包在别人里：包含关系不是干涉
            if owner_rect is not None and (
                _contains(box, owner_rect) or _contains(owner_rect, box)
            ):
                continue
            hit = _intersect(r, box)
            if hit is None:
                continue
            dx_mm = (hit[2] - hit[0]) * W
            dy_mm = (hit[3] - hit[1]) * H
            if dx_mm < MIN_OVERLAP_MM or dy_mm < MIN_OVERLAP_MM:
                continue
            area = round(dx_mm * dy_mm, 3)
            found.append(
                _Issue(
                    CHECK_TEXT_OVER_AXES,
                    [gid, ax_gid],
                    f"{_label(by_gid, gid)} 压进了 {_label(by_gid, ax_gid)} 的绘图区 "
                    f"{dx_mm:.1f}×{dy_mm:.1f} mm",
                    (
                        "textOverAxes",
                        {"a": gid, "b": ax_gid, "w": f"{dx_mm:.1f}", "h": f"{dy_mm:.1f}"},
                    ),
                    {
                        "overlap_mm": [round(dx_mm, 3), round(dy_mm, 3)],
                        "overlap_mm2": area,
                        "certain": True,
                        "basis": "bbox",
                        "roles": [role, "axes"],
                    },
                    area,
                )
            )

    # ---- 图例压住同一子图里的数据 ----
    for el in elements:
        if el.get("role") != "legend":
            continue
        leg_gid = el.get("gid", "")
        leg_rect = visible_rect(el)
        if leg_rect is None:
            continue
        owner = owner_axes(leg_gid)
        if not owner:
            continue  # figure 级图例：它压谁是排版决定，这里不猜
        certain_hits: list[str] = []
        risk_hits: list[str] = []
        for d in elements:
            dg = d.get("gid", "")
            if d.get("role") not in DATA_ROLES or owner_axes(dg) != owner:
                continue
            dr = visible_rect(d)
            if dr is None or _intersect(dr, leg_rect) is None:
                continue
            geom = d.get("geometry")
            if isinstance(geom, dict) and isinstance(geom.get("paths"), list):
                if _segments_hit_rect(geom["paths"], leg_rect):
                    certain_hits.append(dg)
            else:
                risk_hits.append(dg)
        if not certain_hits and not risk_hits:
            continue
        hits = certain_hits or risk_hits
        certain = bool(certain_hits)
        found.append(
            _Issue(
                CHECK_LEGEND_OVER_DATA,
                [leg_gid, *hits],
                (
                    f"图例压住了数据（{'、'.join(hits[:4])}）"
                    if certain
                    else f"图例可能压住数据（只有包围盒相交，量不到几何：{'、'.join(hits[:4])}）"
                ),
                (
                    "legendOverData" if certain else "legendOverDataRisk",
                    {"legend": leg_gid, "targets": "、".join(hits[:4]), "count": str(len(hits))},
                ),
                {
                    "certain": certain,
                    "basis": "geometry" if certain else "bbox",
                    "targets": hits,
                    "risk_only": risk_hits if certain else [],
                    "legend_rect_mm": [
                        round(leg_rect[0] * W, 3),
                        round(leg_rect[1] * H, 3),
                        round((leg_rect[2] - leg_rect[0]) * W, 3),
                        round((leg_rect[3] - leg_rect[1]) * H, 3),
                    ],
                },
                len(hits),
            )
        )

    out = []
    for it in found:
        severity = (
            profiles_mod.severity_of(profile, it.id) if profile else profiles_mod.DEFAULT_SEVERITY
        )
        out.append(
            {
                "id": it.id,
                "severity": severity,
                "text": it.text,
                "message": {"key": it.message[0], "params": dict(it.message[1])},
                "object_ids": [panel_id],
                "gids": list(it.gids),
                "detail": dict(it.detail),
            }
        )
    return out


def _same_legend_family(ga: str, gb: str) -> bool:
    """同一个图例里的两个条目、或条目与它自己的框——matplotlib 排的，不算重叠。"""
    la, lb = legend_of(ga), legend_of(gb)
    if la and lb and la == lb:
        return True
    return (la and la == gb) or (lb and lb == ga)


def _label(by_gid: dict, gid: str) -> str:
    el = by_gid.get(gid) or {}
    return str(el.get("label") or gid)


def issue_key(issue: dict) -> tuple:
    """按稳定身份配对两份清单里的同一条问题：检查 id + 涉及的 gid（排序）。"""
    return (str(issue.get("id")), tuple(sorted(str(g) for g in issue.get("gids") or [])))


def measure(issue: dict) -> float | None:
    """一条问题「有多严重」的数值（同一 id 之间才可比）：面积 / 探出毫米数 / 命中数。"""
    d = issue.get("detail") or {}
    for key in ("overlap_mm2", "overflow_mm", "count"):
        v = _num(d.get(key))
        if v is not None:
            return v
    targets = d.get("targets")
    if isinstance(targets, list):
        return float(len(targets))
    return None
