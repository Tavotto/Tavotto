"""图内排版（ADR 0199 §十二）的开源几何：认主坐标轴、认跟着走的元素、按确定规则摆它们、量验收指标。

一张图里本来就有好几个子图（`subplots` / `GridSpec` / 手摆的 `add_axes`）时，一键排版排的单位是**主坐标轴**。
本模块只读 manifest（精确几何，ADR 0128 / 0017），不碰文档、不判权益（`license.has` 只在 `quicklayout.py`）：

* `analyze(manifest)`：主轴（去 twin、去色条、去 inset）及各自的文字外沿；色条与宿主；跨几个轴的图级文字；
  脚本画的子图标签；认不出归属的图级元素（障碍物）。
* `solver_input(...)`：给闭源求解器的那一份——只有短号、角色、外框与数字，**不给文字内容**（ADR 0198 §三）。
* `follow(...)`：求解器挪了主轴之后，twin、色条、共享轴标题、子图标签按确定规则落到哪（开源侧摆，不交给求解器）。
* `check(...)`：ADR 0199 §六 的指标在轴这一级照量（从最终几何量，不信求解器自己的网格）。

坐标：manifest 的 `bbox` 是相对图幅的比例 `[x, y, w, h]`（y 向下）；本模块内部一律换成图幅 mm 的
`[x0, y0, x1, y1]`（y 向下）。`axes.position` 是 matplotlib 的 `[左, 下, 宽, 高]`（y 向上），只在落成覆盖时换。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

PT_MM = 25.4 / 72.0
#: 认作「脚本画的子图标签」的文字：`(a)`、`a`、`A)`、`a.`——一个字母，可带括号 / 右括号 / 句点
LABEL_RE = re.compile(r"^\(?([A-Za-z])\)?\.?$")
#: 标签左上角到它那组轴文字外沿左上角之间的竖向空隙（mm）
LABEL_PAD_MM = 0.4
#: 验收阈值（与 `quicklayout` 的多图版同一口径，ADR 0199 §六）
ALIGN_TOL_MM = 0.1 * PT_MM
GAP_TOL_MM = 0.1
EDGE_TOL_MM = 0.05
LABEL_TOL_MM = 0.1
#: 归属判据：文字与一个轴在另一维上重叠超过较短一方的这么多，才算覆盖它
COVER_RATIO = 0.5
#: 共享轴标题沿长边与一个轴的绘图区至少重叠这么多（mm），才算覆盖这个轴
COVER_MIN_MM = 1.0
#: 共享轴标题到它覆盖的那几个轴文字外沿的最小空隙（mm）：脚本原样里压着刻度文字的，排完至少空出这么多
SHARED_PAD_MM = 0.8
#: 计入一个轴文字外沿的角色（gid 以该轴 gid 为前缀的元素）
INK_ROLES = frozenset({"ticks", "ticklabel", "axis_label", "title", "text", "legend"})


Rect = list[float]


def _finite(*vals: object) -> bool:
    return all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vals
    )


def _rect(bbox: list, size: tuple[float, float]) -> Rect | None:
    if not (isinstance(bbox, list) and len(bbox) == 4 and _finite(*bbox)):
        return None
    w, h = size
    x, y, bw, bh = (float(v) for v in bbox)
    return [x * w, y * h, (x + bw) * w, (y + bh) * h]


def _union(rects: list[Rect]) -> Rect:
    return [
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    ]


def _overlaps(a: Rect, b: Rect, eps: float = 1e-6) -> bool:
    return a[0] < b[2] - eps and b[0] < a[2] - eps and a[1] < b[3] - eps and b[1] < a[3] - eps


def _span_overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = min(a1, b1) - max(a0, b0)
    small = min(a1 - a0, b1 - b0)
    return inter / small if small > 0 else 0.0


def _owner(gid: str) -> str | None:
    """`axes_3.xticks` → `axes_3`；图级元素（`fig.texts_0`）→ None。"""
    head, _, rest = gid.partition(".")
    return head if rest and head.startswith("axes_") else None


def _editable(e: dict, prop: str):
    for f in e.get("editable") or []:
        if f.get("prop") == prop:
            return f.get("value")
    return None


@dataclass
class MainAxes:
    gid: str
    plot: Rect  # 绘图区（mm）
    ink: Rect  # 自己的文字外沿：绘图区 + 刻度 / 轴标题 / 标题 / 轴里的文字 / 图例（mm）
    equal: bool  # 等比例轴：matplotlib 会在 position 框里再收缩，只参与排位、不计对齐
    twins: list[str] = field(default_factory=list)


@dataclass
class Colorbar:
    gid: str  # 色条**轴**的 gid（几何写它的 position）
    rect: Rect
    ink: Rect
    hosts: list[str]  # 主轴 gid
    vertical: bool


@dataclass
class SharedText:
    gid: str
    rect: Rect
    anchor: tuple[float, float]  # pos_frac 锚点（比例，y 向下）
    covers: list[str]
    side: str  # left / right / top / bottom


@dataclass
class Label:
    gid: str
    rect: Rect
    anchor: tuple[float, float]
    letter: int  # 0 = a
    group: str  # 这个标签管的那组轴里最先的那个主轴 gid


@dataclass
class Analysis:
    size: tuple[float, float]
    mains: list[MainAxes]
    colorbars: list[Colorbar]
    shared: list[SharedText]
    labels: list[Label]
    #: 认不出归属的图级元素：原地不动（按图幅比例跟着走），排完与谁重叠就整份作废
    obstacles: list[tuple[str, list[float]]]
    #: 「标签：脚本里有 / 没有 / 有但认不全」——认不全时既不挪、也不另加画布标签
    label_mode: str  # move / canvas / none
    #: 各主轴属于哪组（组 = 一个子图标签管的那几个轴；没有标签时每个轴自成一组）
    group_of: dict[str, str] = field(default_factory=dict)
    #: 一张图里有锁定的主轴（子轴的落位归父级 locator 管）：不排
    locked: bool = False


def _main_axes(elements: list[dict], size) -> tuple[list[dict], dict[str, str], bool, set[str]]:
    """主轴：去色条轴、去 inset（`inset_of` 或外框完全落在另一个轴里）、twin 去重（与宿主外框相同、互为随行）。

    返回 `(主轴元素, twin / 子轴 gid → 主轴 gid, 有没有认不出归属的锁定轴, 锁定子轴的 gid)`。"""
    axes = [
        e
        for e in elements
        if e.get("role") == "axes" and not e.get("is_colorbar") and _rect(e.get("bbox"), size)
    ]

    def inside(a: dict, b: dict) -> bool:
        ra, rb = _rect(a["bbox"], size), _rect(b["bbox"], size)
        return (
            a is not b
            and ra[0] >= rb[0] - 1e-6
            and ra[1] >= rb[1] - 1e-6
            and ra[2] <= rb[2] + 1e-6
            and ra[3] <= rb[3] + 1e-6
            and (ra[2] - ra[0]) * (ra[3] - ra[1]) < (rb[2] - rb[0]) * (rb[3] - rb[1]) - 1e-6
        )

    cands = [
        a
        for a in axes
        if not a.get("inset_of")
        and a.get("resizable") is not False
        and not any(inside(a, b) for b in axes)
    ]
    mains: list[dict] = []
    twin_of: dict[str, str] = {}
    for a in cands:
        ra = _rect(a["bbox"], size)
        host = next(
            (
                m
                for m in mains
                if max(abs(p - q) for p, q in zip(_rect(m["bbox"], size), ra)) < 0.05
            ),
            None,
        )
        if host is not None:
            twin_of[a["gid"]] = host["gid"]
            continue
        mains.append(a)
    # 落位不归 Tavotto 管的轴（`secondary_xaxis` 这类子轴，由父轴的 locator 摆）：跟着贴着它的那个主轴走，
    # 文字计进那个主轴的外沿。认不出贴着谁 = 这张图不排
    locked = False
    children: set[str] = set()
    for a in axes:
        if a.get("resizable") is not False or a.get("inset_of") or a in mains:
            continue
        host = _adjacent_host(_rect(a["bbox"], size), mains, size)
        if host is None:
            locked = True
        else:
            twin_of[a["gid"]] = host
            children.add(a["gid"])
    return mains, twin_of, locked, children


def _adjacent_host(r: Rect, mains: list[dict], size) -> str | None:
    """贴着哪个主轴：与它一维的跨度基本重合、另一维紧挨着或落在里面（1 mm 内）的那一个。"""
    for m in mains:
        mr = _rect(m["bbox"], size)
        same_x = abs(r[0] - mr[0]) < 1.0 and abs(r[2] - mr[2]) < 1.0
        same_y = abs(r[1] - mr[1]) < 1.0 and abs(r[3] - mr[3]) < 1.0
        near_y = r[3] >= mr[1] - 1.0 and r[1] <= mr[3] + 1.0
        near_x = r[2] >= mr[0] - 1.0 and r[0] <= mr[2] + 1.0
        if (same_x and near_y) or (same_y and near_x):
            return m["gid"]
    return None


def _equal_aspect(e: dict) -> bool:
    v = _editable(e, "aspect")
    return v not in (None, "auto")


def _label_candidates(elements: list[dict], size) -> list[tuple[dict, int, Rect]]:
    out = []
    for e in elements:
        if e.get("role") != "text" or e.get("drag_prop") != "pos_frac":
            continue
        text = str(_editable(e, "text") or "").strip()
        m = LABEL_RE.match(text)
        r = _rect(e.get("bbox"), size)
        anchor = e.get("anchor")
        if not m or r is None or not (isinstance(anchor, list) and len(anchor) == 2):
            continue
        out.append((e, ord(m.group(1).lower()) - 97, r))
    return out


def analyze(manifest: dict) -> Analysis:
    size_raw = manifest.get("size_mm")
    if not (isinstance(size_raw, list) and len(size_raw) == 2 and _finite(*size_raw)):
        raise ValueError("no size")
    size = (float(size_raw[0]), float(size_raw[1]))
    elements = [e for e in manifest.get("elements") or [] if isinstance(e, dict) and e.get("gid")]
    main_els, twin_of, locked, children = _main_axes(elements, size)
    main_gids = {e["gid"] for e in main_els}
    main_of = {g: g for g in main_gids} | twin_of  # 轴 gid（含 twin）→ 主轴 gid
    cbar_axes = {e["gid"]: e for e in elements if e.get("role") == "axes" and e.get("is_colorbar")}
    inset_gids = {
        e["gid"]
        for e in elements
        if e.get("role") == "axes" and not e.get("is_colorbar") and e["gid"] not in main_of
    }

    # 子图标签：一个字母、可拖（pos_frac）；字母要从 a 起连续、不重复、至少两个，否则认不全（不挪也不加）
    cands = _label_candidates(elements, size)
    letters = sorted(c[1] for c in cands)
    if not cands:
        label_mode = "canvas"
    elif len(cands) >= 2 and letters == list(range(len(cands))):
        label_mode = "move"
    else:
        label_mode = "none"
    label_gids = {c[0]["gid"] for c in cands} if label_mode == "move" else set()

    inks: dict[str, list[Rect]] = {
        g: [_rect(e["bbox"], size)] for g in main_gids for e in main_els if e["gid"] == g
    }
    cbar_inks: dict[str, list[Rect]] = {g: [_rect(e["bbox"], size)] for g, e in cbar_axes.items()}
    figure_level: list[dict] = []
    for e in elements:
        gid = e["gid"]
        role = e.get("role")
        r = _rect(e.get("bbox"), size)
        if r is None or gid in label_gids:
            continue
        own = _owner(gid)
        if own is None:
            if role in ("text", "legend") and gid.startswith("fig"):
                figure_level.append(e)
            continue
        if role not in INK_ROLES:
            continue
        if own in cbar_inks:
            cbar_inks[own].append(r)
        elif own in main_of:
            inks[main_of[own]].append(r)
        # inset 里的文字落在宿主绘图区里，不计外沿

    mains = [
        MainAxes(
            gid=e["gid"],
            plot=_rect(e["bbox"], size),
            ink=_union(inks[e["gid"]]),
            equal=_equal_aspect(e),
            # 子轴的落位归父轴的 locator，不写它的 position
            twins=sorted(t for t, h in twin_of.items() if h == e["gid"] and t not in children),
        )
        for e in main_els
    ]
    by_gid = {m.gid: m for m in mains}

    colorbars: list[Colorbar] = []
    obstacles: list[tuple[str, list[float]]] = []
    for e in elements:
        if e.get("role") != "colorbar":
            continue
        cax = e.get("geom_gid") or e["gid"].rsplit(".", 1)[0]
        if cax not in cbar_axes:
            continue
        raw_hosts = e.get("owner_gids") or ([e["host_gid"]] if e.get("host_gid") else [])
        hosts = sorted({main_of[h] for h in raw_hosts if h in main_of})
        rect = _rect(cbar_axes[cax]["bbox"], size)
        if not hosts or cbar_axes[cax].get("resizable") is False:
            obstacles.append((cax, _union(cbar_inks[cax])))
            continue
        colorbars.append(
            Colorbar(
                gid=cax,
                rect=rect,
                ink=_union(cbar_inks[cax]),
                hosts=hosts,
                vertical=(rect[3] - rect[1]) >= (rect[2] - rect[0]),
            )
        )
    del inset_gids  # inset 跟着宿主的 position 走（落位归父级），不单独处理

    labels: list[Label] = []
    if label_mode == "move":
        for e, letter, r in cands:
            # 归属：标签中心在它左上方的主轴里，离轴左上角最近的那个
            cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
            best = None
            for m in mains:
                mx, my = (m.plot[0] + m.plot[2]) / 2, (m.plot[1] + m.plot[3]) / 2
                if cx <= mx and cy <= my:
                    d = math.hypot(m.plot[0] - cx, m.plot[1] - cy)
                    if best is None or d < best[0]:
                        best = (d, m.gid)
            if best is None:
                # 认不出它管哪个轴：整套标签都不挪（也不另加），免得挪错
                label_mode, labels = "none", []
                break
            labels.append(
                Label(
                    gid=e["gid"],
                    rect=r,
                    anchor=(float(e["anchor"][0]), float(e["anchor"][1])),
                    letter=letter,
                    group=best[1],
                )
            )
        if label_mode == "move" and len({lab.group for lab in labels}) != len(labels):
            label_mode, labels = "none", []  # 两个标签认到同一个轴：认不全

    group_of: dict[str, str] = {m.gid: m.gid for m in mains}
    if labels:
        heads = {lab.group: lab for lab in labels}
        for m in mains:
            if m.gid in heads:
                continue
            # 没有自己标签的轴：归到左上方最近的那个标签（实例里 (a) 管上面三个示意图）
            mx, my = (m.plot[0] + m.plot[2]) / 2, (m.plot[1] + m.plot[3]) / 2
            best = None
            for lab in labels:
                cx, cy = (lab.rect[0] + lab.rect[2]) / 2, (lab.rect[1] + lab.rect[3]) / 2
                if (
                    cx <= mx
                    and cy <= my
                    and _span_overlap(lab.rect[1], m.plot[3], m.plot[1], m.plot[3]) >= 0
                ):
                    head = by_gid[lab.group]
                    # 同一行（与标签管的那个轴纵向重叠）才归过去
                    if (
                        _span_overlap(head.plot[1], head.plot[3], m.plot[1], m.plot[3])
                        < COVER_RATIO
                    ):
                        continue
                    d = math.hypot(cx - m.plot[0], cy - m.plot[1])
                    if best is None or d < best[0]:
                        best = (d, lab.group)
            if best is not None:
                group_of[m.gid] = best[1]

    shared: list[SharedText] = []
    for e in figure_level:
        r = _rect(e["bbox"], size)
        anchor = e.get("anchor")
        side, covers = _side_of(r, mains)
        if (
            e.get("role") != "text"
            or e.get("drag_prop") != "pos_frac"
            or not (isinstance(anchor, list) and len(anchor) == 2)
            or side is None
        ):
            obstacles.append((e["gid"], r))
            continue
        shared.append(
            SharedText(
                gid=e["gid"],
                rect=r,
                anchor=(float(anchor[0]), float(anchor[1])),
                covers=covers,
                side=side,
            )
        )
    return Analysis(
        size=size,
        mains=mains,
        colorbars=colorbars,
        shared=shared,
        labels=labels,
        obstacles=obstacles,
        label_mode=label_mode,
        group_of=group_of,
        locked=locked,
    )


def _side_of(r: Rect, mains: list[MainAxes]) -> tuple[str | None, list[str]]:
    """图级文字在哪几个轴的哪一侧：在轴并集的左 / 右（与之纵向重叠的那几个轴）或上 / 下（横向重叠的）。认不出 = None。"""
    if not mains:
        return None, []
    eps = 0.05
    for side in ("left", "right", "top", "bottom"):
        # 覆盖 = 沿长边与这个轴的绘图区重叠至少 COVER_MIN_MM（跨两个轴的共享标题常各只压住每个轴的一小半）
        lo, hi = (1, 3) if side in ("left", "right") else (0, 2)
        covers = [
            m for m in mains if min(r[hi], m.plot[hi]) - max(r[lo], m.plot[lo]) >= COVER_MIN_MM
        ]
        if not covers:
            continue
        # 按绘图区判在哪一侧（脚本原样里共享轴标题常已压着刻度文字，按文字外沿判会认不出）
        u = _union([m.plot for m in covers])
        ok = {
            "left": r[2] <= u[0] + eps,
            "right": r[0] >= u[2] - eps,
            "top": r[3] <= u[1] + eps,
            "bottom": r[1] >= u[3] - eps,
        }[side]
        if ok:
            return side, sorted(m.gid for m in covers)
    return None, []


# ---------------------------------------------------------------------------
# 给求解器
# ---------------------------------------------------------------------------
def deco_ink(a: Analysis, m: MainAxes) -> Rect:
    """排版时这个轴四周要让出的外沿：自己的文字 + 挂在它侧边的色条与共享轴标题（只计垂直于长边的那一维）。"""
    x0, y0, x1, y1 = m.ink
    for cb in a.colorbars:
        if m.gid not in cb.hosts:
            continue
        if cb.vertical:
            x0, x1 = min(x0, cb.ink[0]), max(x1, cb.ink[2])
        else:
            y0, y1 = min(y0, cb.ink[1]), max(y1, cb.ink[3])
    by = {q.gid: q for q in a.mains}
    for s in a.shared:
        if m.gid not in s.covers:
            continue
        # 按 `follow` 排完的位置计（离覆盖的那几个轴的文字外沿至少 SHARED_PAD_MM），不按脚本原样里压着刻度的位置
        u = _union([by[g].ink for g in s.covers])
        r = s.rect
        if s.side == "left":
            x0 = min(x0, m.ink[0] - max(SHARED_PAD_MM, u[0] - r[2]) - (r[2] - r[0]))
        elif s.side == "right":
            x1 = max(x1, m.ink[2] + max(SHARED_PAD_MM, r[0] - u[2]) + (r[2] - r[0]))
        elif s.side == "top":
            y0 = min(y0, m.ink[1] - max(SHARED_PAD_MM, u[1] - r[3]) - (r[3] - r[1]))
        else:
            y1 = max(y1, m.ink[3] + max(SHARED_PAD_MM, r[1] - u[3]) + (r[3] - r[1]))
    return [x0, y0, x1, y1]


def solver_input(a: Analysis, refs: dict[str, str]) -> list[dict]:
    """主轴组（ADR 0199 §十二）：短号、外框、文字外沿（比例，x0,y0,x1,y1）、组号、是否等比例。不给文字。"""
    w, h = a.size
    groups = {g: i for i, g in enumerate(dict.fromkeys(a.group_of[m.gid] for m in a.mains))}
    out = []
    for m in a.mains:
        d = deco_ink(a, m)
        out.append(
            {
                "ref": refs[m.gid],
                "bbox": [
                    round(v, 6)
                    for v in (
                        m.plot[0] / w,
                        m.plot[1] / h,
                        (m.plot[2] - m.plot[0]) / w,
                        (m.plot[3] - m.plot[1]) / h,
                    )
                ],
                "ink": [
                    round(v, 6) for v in (m.ink[0] / w, m.ink[1] / h, m.ink[2] / w, m.ink[3] / h)
                ],
                "deco_ink": [round(v, 6) for v in (d[0] / w, d[1] / h, d[2] / w, d[3] / h)],
                "group": groups[a.group_of[m.gid]],
                "equal_aspect": m.equal,
            }
        )
    return out


# ---------------------------------------------------------------------------
# 跟着走的元素（开源侧按确定规则摆）
# ---------------------------------------------------------------------------
def _map_span(a0, a1, u0, u1, n0, n1):
    s = (n1 - n0) / (u1 - u0) if u1 > u0 else 1.0
    return n0 + (a0 - u0) * s, n0 + (a1 - u0) * s


def follow(
    a: Analysis, new_plot: dict[str, Rect]
) -> tuple[dict[str, Rect], list[tuple[str, Rect]]]:
    """主轴的新绘图区（mm，新图幅下）已定：算 twin / 色条轴的新外框，以及共享文字的新外框。

    * twin：与宿主同框。
    * 色条：长边方向跟着宿主并集按比例伸缩；短边方向与宿主并集的间距、自身粗细（mm）不变。
    * 共享轴标题：沿长边居中到它覆盖的那几个轴的并集；到这些轴自己文字外沿的 mm 距离不变。
    返回 `({轴 gid: 新外框}, [(文字 gid, 新外框)])`。"""
    axes: dict[str, Rect] = {}
    by = {m.gid: m for m in a.mains}
    for m in a.mains:
        for t in m.twins:
            axes[t] = list(new_plot[m.gid])
    for cb in a.colorbars:
        old_u = _union([by[h].plot for h in cb.hosts])
        new_u = _union([new_plot[h] for h in cb.hosts])
        r = cb.rect
        if cb.vertical:
            y0, y1 = _map_span(r[1], r[3], old_u[1], old_u[3], new_u[1], new_u[3])
            if r[0] >= old_u[2]:
                x0 = new_u[2] + (r[0] - old_u[2])
            else:
                x0 = new_u[0] - (old_u[0] - r[0])
            axes[cb.gid] = [x0, y0, x0 + (r[2] - r[0]), y1]
        else:
            x0, x1 = _map_span(r[0], r[2], old_u[0], old_u[2], new_u[0], new_u[2])
            if r[1] >= old_u[3]:
                y0 = new_u[3] + (r[1] - old_u[3])
            else:
                y0 = new_u[1] - (old_u[1] - r[1])
            axes[cb.gid] = [x0, y0, x1, y0 + (r[3] - r[1])]
    texts: list[tuple[str, Rect]] = []
    for s in a.shared:
        cov = [by[g] for g in s.covers]
        old_ink = _union([m.ink for m in cov])
        # 新的「自己的文字外沿」= 新绘图区按旧的 mm 装饰外扩（下一轮渲染再量真值）
        new_ink = _union(
            [
                [
                    new_plot[m.gid][0] - (m.plot[0] - m.ink[0]),
                    new_plot[m.gid][1] - (m.plot[1] - m.ink[1]),
                    new_plot[m.gid][2] + (m.ink[2] - m.plot[2]),
                    new_plot[m.gid][3] + (m.ink[3] - m.plot[3]),
                ]
                for m in cov
            ]
        )
        new_u = _union([new_plot[g] for g in s.covers])
        r = s.rect
        tw, th = r[2] - r[0], r[3] - r[1]
        if s.side in ("left", "right"):
            cy = (new_u[1] + new_u[3]) / 2
            if s.side == "left":
                x1 = new_ink[0] - max(SHARED_PAD_MM, old_ink[0] - r[2])
                nr = [x1 - tw, cy - th / 2, x1, cy + th / 2]
            else:
                x0 = new_ink[2] + max(SHARED_PAD_MM, r[0] - old_ink[2])
                nr = [x0, cy - th / 2, x0 + tw, cy + th / 2]
        else:
            cx = (new_u[0] + new_u[2]) / 2
            if s.side == "top":
                y1 = new_ink[1] - max(SHARED_PAD_MM, old_ink[1] - r[3])
                nr = [cx - tw / 2, y1 - th, cx + tw / 2, y1]
            else:
                y0 = new_ink[3] + max(SHARED_PAD_MM, r[1] - old_ink[3])
                nr = [cx - tw / 2, y0, cx + tw / 2, y0 + th]
        texts.append((s.gid, nr))
    return axes, texts


def anchor_for(
    old_rect: Rect, old_anchor: tuple[float, float], old_size, new_rect: Rect, new_size
) -> list[float]:
    """`pos_frac` 定的是**锚点**（比例，y 向下），不是外框角：锚点到外框左上角的 mm 偏移在字号不变时不变。"""
    ax_mm = old_anchor[0] * old_size[0] - old_rect[0]
    ay_mm = old_anchor[1] * old_size[1] - old_rect[1]
    return [
        round((new_rect[0] + ax_mm) / new_size[0], 5),
        round((new_rect[1] + ay_mm) / new_size[1], 5),
    ]


def position_value(r: Rect, size) -> list[float]:
    """mm 外框（y 向下）→ matplotlib `position` `[左, 下, 宽, 高]`（比例，y 向上），4 位小数（与手动拖轴同）。"""
    w, h = size
    return [
        round(r[0] / w, 4),
        round(1.0 - r[3] / h, 4),
        round((r[2] - r[0]) / w, 4),
        round((r[3] - r[1]) / h, 4),
    ]


# ---------------------------------------------------------------------------
# 验收（从最终几何量）
# ---------------------------------------------------------------------------
def _cluster(items: list[MainAxes], lo: int, hi: int) -> list[list[MainAxes]]:
    """按 [lo, hi] 区间重叠（超过较短一方的一半）**传递地**归堆，堆按位置排。"""
    groups: list[list[MainAxes]] = []
    for m in sorted(items, key=lambda q: (q.plot[lo] + q.plot[hi]) / 2):
        hit = [
            g
            for g in groups
            if any(
                _span_overlap(m.plot[lo], m.plot[hi], o.plot[lo], o.plot[hi]) >= COVER_RATIO
                for o in g
            )
        ]
        merged = [m]
        for g in hit:
            merged += g
            groups.remove(g)
        groups.append(merged)
    groups.sort(key=lambda g: min(o.plot[lo] for o in g))
    return groups


def layout_of(mains: list[MainAxes]) -> list[list[list[MainAxes]]]:
    """按最终绘图区认版式：行（纵向重叠）→ 列（行内横向重叠）→ 列里自上而下叠着的轴。"""
    return [
        [sorted(c, key=lambda q: q.plot[1]) for c in _cluster(r, 0, 2)]
        for r in _cluster(mains, 1, 3)
    ]


def check(
    a: Analysis,
    *,
    gap: float,
    padding: float,
    canvas_labels: list[Rect] | None = None,
) -> list[dict]:
    """ADR 0199 §十二 的验收（轴这一级），版式从**最终几何**认（`layout_of`），不信求解器的网格：

    * 同一行各列最上面那个轴的上边、最下面那个轴的下边；各行打头那一列的轴左边；列数相同的行逐列左边：
      偏差 ≤ 0.1 pt（等比例轴不计）；
    * 同一行相邻两列（纵向有重叠的轴之间）、列内上下相邻两轴、上下相邻两行之间的文字外沿：≥ 图内间距 − 0.1 mm；
    * 一切文字外沿（含色条、共享轴标题、标签）到图框：≥ 留白 − 0.05 mm（图框 = 页边，图内排版时图铺满页宽）；
    * 主轴文字外沿、色条、共享文字、标签、障碍物两两不重叠；
    * 挪过的子图标签：底边到各自那组轴文字外沿顶的距离一致，管打头那一列各轴的标签左边对齐。"""
    bad: list[dict] = []
    w, h = a.size
    rows = layout_of(a.mains)

    def spread(vals: list[float], metric: str) -> None:
        if len(vals) >= 2 and max(vals) - min(vals) > ALIGN_TOL_MM:
            bad.append({"metric": metric, "delta_mm": round(max(vals) - min(vals), 4)})

    for r in rows:
        spread([c[0].plot[1] for c in r if not c[0].equal], "align_top")
        spread([c[-1].plot[3] for c in r if not c[-1].equal], "align_bottom")
    spread([m.plot[0] for r in rows for m in r[0] if not m.equal], "align_left")
    by_k: dict[int, list[list[list[MainAxes]]]] = {}
    for r in rows:
        by_k.setdefault(len(r), []).append(r)
    for k, rs in by_k.items():
        if k >= 2 and len(rs) >= 2:
            for i in range(1, k):
                spread([m.plot[0] for r in rs for m in r[i] if not m.equal], "align_left")

    def need(d: float, p: MainAxes, q: MainAxes) -> None:
        if d < gap - GAP_TOL_MM:
            bad.append({"metric": "gap", "refs": [p.gid, q.gid], "delta_mm": round(d - gap, 4)})

    for r in rows:
        for left, right in zip(r, r[1:]):
            for p in left:
                for q in right:
                    if _span_overlap(p.ink[1], p.ink[3], q.ink[1], q.ink[3]) > 0:
                        need(q.ink[0] - p.ink[2], p, q)
        for col in r:
            for p, q in zip(col, col[1:]):
                need(q.ink[1] - p.ink[3], p, q)
    for upper, lower in zip(rows, rows[1:]):
        for p in (m for c in upper for m in c):
            for q in (m for c in lower for m in c):
                if _span_overlap(p.ink[0], p.ink[2], q.ink[0], q.ink[2]) > 0:
                    need(q.ink[1] - p.ink[3], p, q)
    things: list[tuple[str, Rect]] = [(m.gid, m.ink) for m in a.mains]
    things += [(c.gid, c.ink) for c in a.colorbars]
    things += [(s.gid, s.rect) for s in a.shared]
    things += [(lab.gid, lab.rect) for lab in a.labels]
    things += [(f"canvas_label_{i}", r) for i, r in enumerate(canvas_labels or [])]
    obstacles = [(g, r) for g, r in a.obstacles]
    for g, r in things + obstacles:
        edge = min(r[0], r[1], w - r[2], h - r[3])
        if edge < padding - EDGE_TOL_MM:
            bad.append({"metric": "edge", "ref": g, "mm": round(edge, 3)})
    allr = things + obstacles
    for i, (ga, ra) in enumerate(allr):
        for gb, rb in allr[i + 1 :]:
            if _overlaps(ra, rb):
                metric = "obstacle" if (ga, ra) in obstacles or (gb, rb) in obstacles else "overlap"
                bad.append({"metric": metric, "refs": [ga, gb]})
    if a.labels:
        # 标签底边到它那组第一个轴文字外沿顶的距离全版一致；管着「打头的轴」的那几个标签左边对齐
        by = {m.gid: m for m in a.mains}
        dy = [lab.rect[3] - by[lab.group].ink[1] for lab in a.labels]
        leaders = {m.gid for r in rows for m in r[0]}
        xs = [lab.rect[0] for lab in a.labels if lab.group in leaders]
        if max(dy) - min(dy) > LABEL_TOL_MM or (xs and max(xs) - min(xs) > LABEL_TOL_MM):
            bad.append({"metric": "label_offset"})
    return bad
