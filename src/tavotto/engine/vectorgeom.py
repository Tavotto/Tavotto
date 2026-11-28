"""路径几何的「语义层」：画布矢量对象 ↔ 内核编码、结果怎么变回路径对象、谁的样式 / id 被沿用（ADR 0212 第 3 期）。

逐条照前端 `web/src/lib/pathfinder.ts`（路径查找器 / 偏移 / 轮廓化描边 / 简化）与 `lib/pathEdit.ts`（`pathToMm` / `pathFromMm`）、
`lib/pathGeom.fitPathBox`（曲线实际范围的包围盒）、`lib/gradient.remapGradients`；内核调用在 `engine/vecops.py`（同一份 wasm）。
**纯函数**：输入对象字典（文档里的 camelCase 形态），输出要换上去的对象；落位（插进数组哪一位、删谁）在 `engine/vectoredit.py`。

坐标：对象各自的比例坐标 → 页面 mm（旋转已算进去，结果都是不旋转的对象）→ pt 交给内核（精度 0.01 是文档单位，用 pt 就是 0.01 pt），
结果再换回来。内核几何见 `vecops/src/lib.rs`；这里只做坐标换算与规则。

**与前端不同的地方**（如实）：
* 「转为路径」（`to_path_object`）是前端 `lib/convertToPath.toPathObject` 的 Python 版，共享向量
  `tests/golden/convert_to_path_vectors.json`（前端生成，`tests/test_vector_convert.py` 逐数比）；
* 轮廓化描边的拆件（虚线、端型、内外对齐）是前端 `lib/outlineStroke` 的 Python 版（`stroke_parts` / `dash_subpaths`），
  几何拆件复用导出那一份 `geometry.path_segments`；对拍样本 `tests/golden/outline_stroke_vectors.json` 的 `doc` 部分；
* 路径锚点编辑（ADR 0212 第 4 期）也经这里的 `path_to_mm` / `path_from_mm` / `plan_replace` 落位，纯函数在 `engine/pathedit.py`；
* 结果的比例坐标与包围盒保留 6 位小数（与 `rendercore/svgimport.path_object` 同一口径），前端不取整。
"""

from __future__ import annotations

import math
from typing import Callable

from ..rendercore import geometry
from ..rendercore.svgimport import fit_box

MM_PER_PT = 25.4 / 72
#: 内核的 mm → pt（与前端 `PT_PER_MM = 1 / MM_PER_PT` 逐位同一个算式）
PT_PER_MM = 1 / MM_PER_PT

#: 与 `vecops/src/lib.rs` 的 `pathfinder_op`、前端 `pathfinder.ts` 的 `OP_CODE` 同一张表（名字是前端驼峰的蛇形）
PATHFINDER_MODES: dict[str, int] = {
    "unite": 0,
    "minus_front": 1,
    "intersect": 2,
    "exclude": 3,
    "divide": 4,
    "trim": 5,
    "merge": 6,
    "crop": 7,
    "minus_back": 8,
}
JOIN_CODE = {"miter": 0, "round": 1, "bevel": 2}
CAP_CODE = {"butt": 0, "round": 1, "square": 2}

#: 偏移路径缺省拐角（`o.lineJoin ?? 'round'`）、斜接上限缺省 10
DEFAULT_JOIN = "round"

XY = tuple[float, float]


def _r(v: float) -> float:
    return round(v, 6) + 0.0


def _pt(v: object) -> XY | None:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        a, b = v
        if all(
            isinstance(q, (int, float)) and not isinstance(q, bool) and math.isfinite(q)
            for q in (a, b)
        ):
            return (float(a), float(b))
    return None


def rotation_of(o: dict) -> float:
    r = o.get("rotationDeg")
    return float(r) if isinstance(r, (int, float)) and not isinstance(r, bool) else 0.0


def rotate_about(p: XY, c: XY, deg: float) -> XY:
    """`pathGeom.rotateAbout`"""
    if not deg:
        return p
    rad = deg * math.pi / 180
    cos, sin = math.cos(rad), math.sin(rad)
    dx, dy = p[0] - c[0], p[1] - c[1]
    return (c[0] + dx * cos - dy * sin, c[1] + dx * sin + dy * cos)


def _center(o: dict) -> XY:
    return (float(o["x"]) + float(o["w"]) / 2, float(o["y"]) + float(o["h"]) / 2)


# ------------------------------------------------------------------ 子路径（页面 mm）


def path_to_mm(o: dict) -> list[dict]:
    """`pathEdit.pathToMm`：页面 mm、未旋转；手柄缺省 = 锚点；`smooth` 只认 True。读不成数对的锚点丢掉。"""
    x, y, w, h = (float(o[k]) for k in ("x", "y", "w", "h"))

    def at(q: XY) -> XY:
        return (x + q[0] * w, y + q[1] * h)

    out = []
    for sp in o.get("subpaths") or ():
        if not isinstance(sp, dict):
            continue
        nodes = []
        for n in sp.get("nodes") or ():
            p = _pt(n.get("p")) if isinstance(n, dict) else None
            if p is None:
                continue
            nodes.append(
                {
                    "p": at(p),
                    "in": at(_pt(n.get("in")) or p),
                    "out": at(_pt(n.get("out")) or p),
                    "smooth": n.get("smooth") is True,
                }
            )
        out.append({"closed": sp.get("closed") is True, "nodes": nodes})
    return out


def world_subpaths(o: dict) -> list[dict]:
    """`pathfinder.worldSubpaths`：旋转已算进去"""
    c, deg = _center(o), rotation_of(o)

    def rot(q: XY) -> XY:
        return rotate_about(q, c, deg)

    return [
        {
            "closed": sp["closed"],
            "nodes": [
                {"p": rot(n["p"]), "in": rot(n["in"]), "out": rot(n["out"]), "smooth": n["smooth"]}
                for n in sp["nodes"]
            ],
        }
        for sp in path_to_mm(o)
    ]


# ------------------------------------------------------------------ 内核编码（pt）


def encode_subpaths(subs: list[dict]) -> list[float]:
    """`pathfinder.encodeSubpaths`：`[子路径数, (闭合 0/1, 锚点数, (px, py, in_x, in_y, out_x, out_y, 平滑 0/1)*)*]`"""
    o: list[float] = [len(subs)]
    for sp in subs:
        o += [1 if sp["closed"] else 0, len(sp["nodes"])]
        for n in sp["nodes"]:
            for k in ("p", "in", "out"):
                o += [n[k][0] * PT_PER_MM, n[k][1] * PT_PER_MM]
            o.append(1 if n["smooth"] else 0)
    return o


def decode_subpaths(b: list[float], at: int = 0) -> tuple[list[dict], int] | None:
    """`pathfinder.decodeSubpaths`：从 `at` 起读一条编码过的路径（pt → mm）；格式不对 = None"""
    i = at

    def get() -> float:
        nonlocal i
        v = b[i] if i < len(b) else math.nan
        i += 1
        return v

    def mm() -> XY:
        return (get() * MM_PER_PT, get() * MM_PER_PT)

    def count() -> int | None:
        v = get()
        return int(v) if math.isfinite(v) and v == int(v) and v >= 0 else None

    ns = count()
    if ns is None:
        return None
    subs = []
    for _ in range(ns):
        closed = get() == 1
        k = count()
        if k is None:
            return None
        nodes = []
        for _j in range(k):
            p = mm()
            inh = mm()
            outh = mm()
            nodes.append({"p": p, "in": inh, "out": outh, "smooth": get() == 1})
        subs.append({"closed": closed, "nodes": nodes})
    if i > len(b) or any(
        not math.isfinite(v)
        for sp in subs
        for n in sp["nodes"]
        for k in ("p", "in", "out")
        for v in n[k]
    ):
        return None
    return subs, i


def decode_results(out: list[float]) -> list[dict] | None:
    """`pathfinder.decodeResults`：`[结果数, (key, 路径…)*]`"""
    if not out or not math.isfinite(out[0]):
        return None
    results = []
    i = 1
    for _ in range(int(out[0])):
        if i >= len(out):
            return None
        key = int(out[i])
        i += 1
        d = decode_subpaths(out, i)
        if d is None:
            return None
        i = d[1]
        results.append({"key": key, "subs": d[0]})
    return results


def encode_stack(items: list[tuple[int, list[dict]]]) -> list[float]:
    """形状栈（从后往前）：`[形状数, (填充规则 0 非零 / 1 奇偶, key = 下标, 路径…)*]`"""
    out: list[float] = [len(items)]
    for i, (rule, subs) in enumerate(items):
        out += [rule, i, *encode_subpaths(subs)]
    return out


# ------------------------------------------------------------------ 渐变 / 样式


def _remap_gradient(g: dict, old: tuple[float, float], new: tuple[float, float], delta: XY) -> dict:
    """`gradient.remapGradient`"""
    ow, oh = old[0] or 1e-9, old[1] or 1e-9
    nw, nh = new[0] or 1e-9, new[1] or 1e-9

    def mp(p: list[float]) -> list[float]:
        return [
            ((p[0] - 0.5) * ow - delta[0]) / nw + 0.5,
            ((p[1] - 0.5) * oh - delta[1]) / nh + 0.5,
        ]

    if g.get("kind") == "linear":
        return {**g, "from": mp(g["from"]), "to": mp(g["to"])}
    return {
        **g,
        "center": mp(g["center"]),
        "radius": (g["radius"] * max(ow, oh)) / max(nw, nh),
    }


def _remap_gradients(target: dict, old: dict, new: dict, deg: float) -> dict:
    """`gradient.remapGradients`：包围盒换了（同一个旋转角），渐变在页面上逐点不动"""
    if not target.get("fillGradient") and not target.get("strokeGradient"):
        return target
    dx = new["x"] + new["w"] / 2 - (old["x"] + old["w"] / 2)
    dy = new["y"] + new["h"] / 2 - (old["y"] + old["h"] / 2)
    t = -deg * math.pi / 180
    delta = (dx * math.cos(t) - dy * math.sin(t), dx * math.sin(t) + dy * math.cos(t))
    out = dict(target)
    for k in ("fillGradient", "strokeGradient"):
        if target.get(k):
            out[k] = _remap_gradient(target[k], (old["w"], old["h"]), (new["w"], new["h"]), delta)
    return out


def path_from_mm(base: dict, subs: list[dict]) -> dict | None:
    """`pathEdit.pathFromMm`：编辑后的子路径 → 同一个对象的新版本（包围盒按曲线实际范围重算、比例坐标换算回去；
    旋转中心取 `base` 的框中心，框换了中心就整体补一次平移，画面上逐点不动）。少于两个锚点的子路径丢掉；一条不剩 = None。"""
    kept = [sp for sp in subs if len(sp["nodes"]) >= 2]
    if not kept:
        return None
    box = fit_box(kept)
    if box is None:
        return None
    x, y, w, h = box
    deg = rotation_of(base)
    if deg:
        c = (x + w / 2, y + h / 2)
        moved = rotate_about(c, _center(base), deg)
        x += moved[0] - c[0]
        y += moved[1] - c[1]

    def frac(q: XY) -> list[float]:
        return [_r((q[0] - (box[0])) / w), _r((q[1] - box[1]) / h)]

    out_subs = []
    for sp in kept:
        nodes = []
        for n in sp["nodes"]:
            node: dict = {"p": frac(n["p"])}
            if n["in"] != n["p"]:
                node["in"] = frac(n["in"])
            if n["out"] != n["p"]:
                node["out"] = frac(n["out"])
            if n.get("smooth"):
                node["smooth"] = True
            nodes.append(node)
        out_subs.append({"closed": sp["closed"], "nodes": nodes})
    nxt = {**base, "x": _r(x), "y": _r(y), "w": _r(w), "h": _r(h), "subpaths": out_subs}
    return _remap_gradients(nxt, base, nxt, deg)


#: `styleBase` 去掉的键：旋转、填充规则、名字、组、剪切蒙版、版式钉、两个渐变（算完再放回）
_STYLE_DROP = (
    "rotationDeg",
    "fillRule",
    "name",
    "groupId",
    "groupPath",
    "groupNames",
    "clipMask",
    "layoutPinned",
    "fillGradient",
    "strokeGradient",
)


def style_base(src: dict, group_id: str | None) -> dict:
    """结果对象的样式底子：沿用 `src` 的填充 / 描边 / 线型，去掉旋转、填充规则与几何。留在原对象那个组里时整条链跟着走；
    剪切蒙版的身份不传给结果（结果是可见的新形状）。"""
    rest = {k: v for k, v in src.items() if k not in _STYLE_DROP}
    out = dict(rest)
    if group_id:
        out["groupId"] = group_id
        if group_id == src.get("groupId") and src.get("groupPath"):
            out["groupPath"] = list(src["groupPath"])
        # 组名跟着留下的那条链走（只留链上的键，同前端 `setGroupPath`）
        names = src.get("groupNames")
        chain = out.get("groupPath") or [group_id]
        kept = {g: n for g, n in names.items() if g in chain} if isinstance(names, dict) else {}
        if kept:
            out["groupNames"] = kept
    return out


def with_gradients(made: dict, src: dict) -> dict:
    out = dict(made)
    if src.get("fillGradient") and made.get("fill"):
        out["fillGradient"] = src["fillGradient"]
    if src.get("strokeGradient"):
        out["strokeGradient"] = src["strokeGradient"]
    return out


def _unit_frame(base: dict) -> dict:
    return {**base, "x": 0, "y": 0, "w": 1, "h": 1}


def common_group(objs: list[dict]) -> str | None:
    g = objs[0].get("groupId") if objs else None
    return g if g and all(o.get("groupId") == g for o in objs) else None


# ------------------------------------------------------------------ 转为路径

#: 路径的缺省拐角（`pathGeom.PATH_DEFAULT_JOIN`）
PATH_DEFAULT_JOIN = "round"


def can_convert_to_path(o: dict) -> bool:
    """`convertToPath.canConvertToPath`：形状（全部七种）与箭头"""
    return o.get("type") in ("shape", "arrow")


def _num_or(v: object, default: float) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _shape_stroke_on(o: dict) -> bool:
    """`types/document.shapeStrokeOn`"""
    return o.get("shape") in geometry.STROKE_ONLY_SHAPES or o.get("stroke") is not False


def _shape_inset(o: dict, sw: float) -> float:
    """`shapeGeometry.shapeInset`（文档形态：`strokeAlign`）"""
    if not _shape_stroke_on(o):
        return 0.0
    if o.get("shape") in geometry.STROKE_ONLY_SHAPES:
        return sw / 2
    align = o.get("strokeAlign")
    return 0.0 if align == "center" else -sw / 2 if align == "outside" else sw / 2


def _end(v: object, default: XY) -> XY:
    if isinstance(v, dict):
        return (_num_or(v.get("rx"), default[0]), _num_or(v.get("ry"), default[1]))
    return default


def arrow_heads(o: dict) -> tuple[str, str]:
    """`types/document.arrowHeads`：新端型字段优先，没有就按旧 `head` 推三角头"""
    if o.get("headStart") is not None or o.get("headEnd") is not None:
        return (o.get("headStart") or "none", o.get("headEnd") or "none")
    head = o.get("head")
    return (
        "triangle" if head == "both" else "none",
        "triangle" if head in ("end", "both") else "none",
    )


def _node(p: XY, i: XY | None = None, out: XY | None = None, smooth: bool = False) -> dict:
    n: dict = {"p": p}
    if i is not None:
        n["in"] = i
    if out is not None:
        n["out"] = out
    if smooth:
        n["smooth"] = True
    return n


def _local_subpaths(o: dict, sw: float) -> list[dict]:
    """`convertToPath.localSubpaths`：对象自己的 mm（原点在框左上、未旋转）里的子路径；手柄缺省 = 不写。"""
    w = max(float(o["w"]), 0.001)
    h = max(float(o["h"]), 0.001)
    if o.get("type") == "arrow":
        s, e = _end(o.get("start"), (0.0, 0.0)), _end(o.get("end"), (0.0, 0.0))
        return [
            {"closed": False, "nodes": [_node((s[0] * w, s[1] * h)), _node((e[0] * w, e[1] * h))]}
        ]
    inset = _shape_inset(o, sw)
    kind = o.get("shape")
    if kind == "line":
        s, e = _end(o.get("start"), (0.0, 0.5)), _end(o.get("end"), (1.0, 0.5))
        return [
            {"closed": False, "nodes": [_node((s[0] * w, s[1] * h)), _node((e[0] * w, e[1] * h))]}
        ]
    if kind == "rect":
        x0 = y0 = inset
        rw = max(w - 2 * inset, 0.001)
        rh = max(h - 2 * inset, 0.001)
        x1, y1 = x0 + rw, y0 + rh
        cr = _num_or(o.get("cornerRadius"), 0.0)
        r = min(cr, min(rw, rh) / 2) if cr else 0.0
        if not r > 0:
            return [
                {
                    "closed": True,
                    "nodes": [_node((x0, y0)), _node((x1, y0)), _node((x1, y1)), _node((x0, y1))],
                }
            ]
        k = geometry.KAPPA * r
        return [
            {
                "closed": True,
                "nodes": [
                    _node((x0 + r, y0), (x0 + r - k, y0), None, True),
                    _node((x1 - r, y0), None, (x1 - r + k, y0), True),
                    _node((x1, y0 + r), (x1, y0 + r - k), None, True),
                    _node((x1, y1 - r), None, (x1, y1 - r + k), True),
                    _node((x1 - r, y1), (x1 - r + k, y1), None, True),
                    _node((x0 + r, y1), None, (x0 + r - k, y1), True),
                    _node((x0, y1 - r), (x0, y1 - r + k), None, True),
                    _node((x0, y0 + r), None, (x0, y0 + r - k), True),
                ],
            }
        ]
    if kind == "ellipse":
        cx, cy = w / 2, h / 2
        rx, ry = max(w / 2 - inset, 0.001), max(h / 2 - inset, 0.001)
        kx, ky = geometry.KAPPA * rx, geometry.KAPPA * ry
        # 与后端 `ellipse_segments` 同一个起点与方向（最右点起、y 向下的空间里顺时针）
        return [
            {
                "closed": True,
                "nodes": [
                    _node((cx + rx, cy), (cx + rx, cy - ky), (cx + rx, cy + ky), True),
                    _node((cx, cy + ry), (cx + kx, cy + ry), (cx - kx, cy + ry), True),
                    _node((cx - rx, cy), (cx - rx, cy + ky), (cx - rx, cy - ky), True),
                    _node((cx, cy - ry), (cx - kx, cy - ry), (cx + kx, cy - ry), True),
                ],
            }
        ]
    if kind in ("triangle", "diamond", "polygon", "star"):
        if kind == "triangle":
            pts = [(w / 2, inset), (w - inset, h - inset), (inset, h - inset)]
        elif kind == "diamond":
            pts = [(w / 2, inset), (w - inset, h / 2), (w / 2, h - inset), (inset, h / 2)]
        elif kind == "polygon":
            pts = geometry.polygon_points(_num_or(o.get("sides"), 6), w, h, inset)  # type: ignore[arg-type]
        else:
            pts = geometry.star_points(
                _num_or(o.get("sides"), geometry.DEFAULT_STAR_POINTS),  # type: ignore[arg-type]
                w,
                h,
                inset,
                _num_or(o.get("starRatio"), geometry.DEFAULT_STAR_RATIO),
            )
        return [{"closed": True, "nodes": [_node(p) for p in pts]}]
    if kind == "brace":
        # `shapeGeometry.braceParts`；二次贝塞尔 Q(p0, c, p1) 精确等于三次 C(p0 + ⅔(c − p0), p1 + ⅔(c − p1), p1)
        cx = w / 2
        tip = min(h * 0.06, h / 2 - inset)
        parts: list[tuple[XY | None, XY]] = [
            ((cx, inset), (cx, h * 0.25)),
            (None, (cx, h / 2 - tip)),
            ((cx, h / 2), (inset, h / 2)),
            ((cx, h / 2), (cx, h / 2 + tip)),
            (None, (cx, h * 0.75)),
            ((cx, h - inset), (w - inset, h - inset)),
        ]

        def lift(a: XY, c: XY) -> XY:
            return (a[0] + (2 / 3) * (c[0] - a[0]), a[1] + (2 / 3) * (c[1] - a[1]))

        nodes = [_node((w - inset, inset))]
        for c, p in parts:
            if c is not None:
                nodes[-1]["out"] = lift(nodes[-1]["p"], c)
                nodes.append(_node(p, lift(p, c)))
            else:
                nodes.append(_node(p))
        return [{"closed": False, "nodes": nodes}]
    if kind == "arc":
        cx, cy = w - inset, h - inset
        rx, ry = max(w - 2 * inset, 0.001), max(h - 2 * inset, 0.001)
        p0, c1 = (cx - rx, cy), (cx - rx, cy - geometry.KAPPA * ry)
        c2, p3 = (cx - geometry.KAPPA * rx, cy - ry), (cx, cy - ry)
        return [{"closed": False, "nodes": [_node(p0, None, c1), _node(p3, c2)]}]
    return []


def fit_path_box(subs: list[dict], deg: float, pivot: XY) -> dict | None:
    """`pathGeom.fitPathBox`：页面 mm 的子路径（手柄可缺）→ 包围盒（曲线实际范围、短边钳到 0.01 mm）+ 比例坐标；
    手柄写没写照原样（不因为与锚点重合就省掉）。旋转时框中心绕 `pivot` 转过去、整体补回。"""
    full = [
        {
            "closed": sp["closed"],
            "nodes": [
                {"p": n["p"], "in": n.get("in", n["p"]), "out": n.get("out", n["p"])}
                for n in sp["nodes"]
            ],
        }
        for sp in subs
        if len(sp["nodes"]) >= 2
    ]
    box = fit_box(full)
    if box is None:
        return None
    x, y, w, h = box

    def frac(q: XY) -> list[float]:
        return [_r((q[0] - box[0]) / w), _r((q[1] - box[1]) / h)]

    out_subs = []
    for sp in subs:
        nodes = []
        for n in sp["nodes"]:
            node: dict = {"p": frac(n["p"])}
            for k in ("in", "out"):
                if n.get(k) is not None:
                    node[k] = frac(n[k])
            if n.get("smooth"):
                node["smooth"] = True
            nodes.append(node)
        out_subs.append({"closed": sp["closed"], "nodes": nodes})
    if deg:
        c = (x + w / 2, y + h / 2)
        moved = rotate_about(c, pivot, deg)
        x += moved[0] - c[0]
        y += moved[1] - c[1]
    return {"x": _r(x), "y": _r(y), "w": _r(w), "h": _r(h), "subpaths": out_subs}


def to_path_object(o: dict) -> dict | None:
    """`convertToPath.toPathObject`：一个形状 / 箭头 → 同 id 的路径对象（画面逐点不动）。几何退化（不到两个锚点）= None。
    文档层的通用字段原样带过去；填充 / 描边 / 线型 / 端型字段照搬，缺省仍不写；直线与大括号原来的缺省拐角是尖角，
    写明 `lineJoin: miter`。共享向量 `tests/golden/convert_to_path_vectors.json`（前端生成）。"""
    if not can_convert_to_path(o):
        return None
    sw = max(_num_or(o.get("strokePt"), 1.0), 0.05) * MM_PER_PT
    ox, oy = float(o["x"]), float(o["y"])

    def page(q: XY) -> XY:
        return (q[0] + ox, q[1] + oy)

    subs = [
        {
            "closed": sp["closed"],
            "nodes": [
                {k: (page(v) if k in ("p", "in", "out") else v) for k, v in n.items()}
                for n in sp["nodes"]
            ],
        }
        for sp in _local_subpaths(o, sw)
    ]
    deg = rotation_of(o)
    box = fit_path_box(subs, deg, _center(o))
    if box is None:
        return None
    is_arrow = o.get("type") == "arrow"
    closed_shape = not is_arrow and o.get("shape") not in geometry.STROKE_ONLY_SHAPES
    join = o.get("lineJoin") or (
        "round" if is_arrow else geometry.default_line_join(o.get("shape"))
    )
    fill = o.get("fill") if closed_shape else None
    out: dict = {"id": o["id"], "type": "path", **{k: box[k] for k in ("x", "y", "w", "h")}}
    for k in ("locked", "hidden"):
        if o.get(k):
            out[k] = o[k]
    if o.get("name") is not None:
        out["name"] = o["name"]
    if o.get("groupId") is not None:
        out["groupId"] = o["groupId"]
    # 嵌套组的整条链与剪切蒙版的身份一起带过去（ADR 0191）
    if o.get("groupPath"):
        out["groupPath"] = list(o["groupPath"])
    if o.get("groupNames"):
        out["groupNames"] = dict(o["groupNames"])
    for k in ("clipMask", "clipOff"):
        if o.get(k):
            out[k] = True
    for k in ("layoutPinned", "rotationDeg"):
        if o.get(k):
            out[k] = o[k]
    out["subpaths"] = box["subpaths"]
    out["strokePt"] = o.get("strokePt")
    out["color"] = o.get("color")
    out["fill"] = fill
    if fill and not is_arrow and o.get("fillOpacity") is not None:
        out["fillOpacity"] = o["fillOpacity"]
    if closed_shape and not _shape_stroke_on(o):
        out["stroke"] = False
    if o.get("strokeOpacity") is not None:
        out["strokeOpacity"] = o["strokeOpacity"]
    for k in ("dash", "dashPattern"):
        if o.get(k):
            out[k] = o[k]
    if o.get("miterLimit") is not None:
        out["miterLimit"] = o["miterLimit"]
    if o.get("lineCap"):
        out["lineCap"] = o["lineCap"]
    if join != PATH_DEFAULT_JOIN:
        out["lineJoin"] = join
    if is_arrow:
        hs, he = arrow_heads(o)
        if hs != "none":
            out["headStart"] = hs
        if he != "none":
            out["headEnd"] = he
        for k in ("headSizeStart", "headSizeEnd"):
            if o.get(k):
                out[k] = o[k]
    else:
        if fill and o.get("fillGradient"):
            out["fillGradient"] = o["fillGradient"]
        if o.get("strokeGradient"):
            out["strokeGradient"] = o["strokeGradient"]
    return _remap_gradients(out, o, out, deg)


def is_pathfinder_operand(o: dict) -> bool:
    """`pathfinder.isPathfinderOperand`：未锁定、未隐藏的路径，或封闭形状（直线 / 大括号 / 弧线、箭头不收）"""
    if o.get("locked") or o.get("hidden"):
        return False
    if o.get("type") == "path":
        return True
    return o.get("type") == "shape" and o.get("shape") not in geometry.STROKE_ONLY_SHAPES


def keep_identity(made: dict, src: dict) -> dict:
    """同 id 换成路径时，0208 的标注记录（`anchor`：谁加的、锚在哪）跟着走——前端 `toPathObject` 的白名单不带它，
    但 Agent 这边换完还要能按 `by_agent` / `anchored_to` 选中同一个对象（Codex #2268）。"""
    if src.get("anchor") is not None:
        made["anchor"] = src["anchor"]
    return made


def as_path(o: dict) -> dict | None:
    """`pathfinder.asPath`：参与几何运算的对象 → 路径（形状 / 箭头按「转为路径」那份几何换；
    「转为路径」不带的斜接上限与自定义虚线，结果对象接着用；标注记录照 `keep_identity` 带过去，与路径操作数一样）"""
    if o.get("type") == "path":
        return o
    p = to_path_object(o)
    if p is None:
        return None
    keep_identity(p, o)
    if o.get("miterLimit") is not None:
        p["miterLimit"] = o["miterLimit"]
    if o.get("dashPattern"):
        p["dashPattern"] = o["dashPattern"]
    return p


# ------------------------------------------------------------------ 路径查找器


def plan_replace(
    objects: list[dict],
    sources: list[dict],
    results: list[dict],
    new_id: Callable[[int], str],
) -> dict | None:
    """`pathfinder.planReplace`：`results` 是 (样式来源的下标 key, 子路径) 从后往前。没有结果 = None（不删任何东西）。
    每个原对象的样式第一次被沿用时，那块结果继承它的 id 与名字；其余用 `new_id(序号)`。
    返回 `{"remove": [id], "add": [对象]（从后往前）, "at": 删掉原对象之后的数组下标}`。"""
    if not sources:
        return None
    group = common_group(sources)
    used: set[int] = set()
    add: list[dict] = []
    for r in results:
        if not 0 <= r["key"] < len(sources):
            continue
        src = sources[r["key"]]
        made = path_from_mm(_unit_frame(style_base(src, group)), r["subs"])
        if made is None:
            continue
        inherit = r["key"] not in used
        used.add(r["key"])
        obj = with_gradients(made, src)
        obj["id"] = src["id"] if inherit else new_id(len(add))
        if inherit and src.get("name") is not None:
            obj["name"] = src["name"]
        add.append(obj)
    if not add:
        return None
    ids = {s["id"] for s in sources}
    idx = [i for i, o in enumerate(objects) if o.get("id") in ids]
    return {"remove": sorted(ids), "add": add, "at": max(idx) - (len(idx) - 1)}


def run_pathfinder(
    kernel, mode: str, objects: list[dict], sources: list[dict], new_id: Callable[[int], str]
) -> dict | None:
    """`pathfinder.runPathfinder`：`sources` 是按数组顺序（从后往前）排好的操作数。内核算不出或结果是空 = None。"""
    if len(sources) < 2:
        return None
    flat = encode_stack(
        [(1 if o.get("fillRule") == "evenodd" else 0, world_subpaths(o)) for o in sources]
    )
    out = kernel.pathfinder(PATHFINDER_MODES[mode], flat)
    if not out:
        return None
    results = decode_results(out)
    if results is None:
        return None
    return plan_replace(objects, sources, results, new_id)


# ------------------------------------------------------------------ 偏移


def _nonzero_input(kernel, o: dict) -> list[float]:
    """`pathfinder.nonzeroInput`：内核的偏移只认非零环绕；奇偶填充且多子路径的对象先过一遍内核的「合并」。"""
    enc = encode_subpaths(world_subpaths(o))
    if o.get("fillRule") != "evenodd" or len(o.get("subpaths") or []) < 2:
        return enc
    out = kernel.pathfinder(0, [1, 1, 0, *enc])
    d = decode_subpaths(out, 2) if out and out[0] == 1 else None
    return encode_subpaths(d[0]) if d else enc


def offset_path(kernel, o: dict, delta_mm: float, new_id: str) -> dict | None:
    """`pathfinder.offsetPaths` 的一个对象：偏移后的新对象（原对象不动；样式沿用原对象，新 id、没有名字）。拐角跟原对象的拐角。"""
    join = JOIN_CODE.get(o.get("lineJoin") or DEFAULT_JOIN, JOIN_CODE[DEFAULT_JOIN])
    miter = o.get("miterLimit")
    miter = (
        float(miter) if isinstance(miter, (int, float)) and not isinstance(miter, bool) else 10.0
    )
    r = kernel.offset(_nonzero_input(kernel, o), delta_mm * PT_PER_MM, join, miter)
    d = decode_subpaths(r) if r else None
    if d is None:
        return None
    made = path_from_mm(_unit_frame(style_base(o, o.get("groupId"))), d[0])
    if made is None:
        return None
    return {**with_gradients(made, o), "id": new_id}


# ------------------------------------------------------------------ 轮廓化描边


# 轮廓化描边的完整外观：照前端 `lib/outlineStroke.ts`（拆件）+ `pathfinder.strokeArea`（拓宽、合并、裁剪）。
# 虚线、端型（箭头头）、内外对齐先在这里摆好几何（页面 mm），再交给内核拓宽 / 合并 / 裁剪。几何拆件复用导出那一份
# `rendercore.geometry.path_segments`（与前端 `pathGeometry` 严格同源，共享向量 `tests/golden/path_vectors.json`）。


def _corner(p: XY) -> dict:
    return {"p": p, "in": p, "out": p, "smooth": False}


def _page_of(o: dict) -> Callable[[float, float], XY]:
    """框空间 mm → 页面 mm（旋转已算进去）：`rotatePoint(o, [o.x + x, o.y + y])`"""
    ox, oy, c, deg = float(o["x"]), float(o["y"]), _center(o), rotation_of(o)
    return lambda x, y: rotate_about((ox + x, oy + y), c, deg)


def segs_to_subpaths(segs: list, o: dict) -> list[dict]:
    """`outlineStroke.segsToSubpaths`：`path_segments` 的段（框空间 mm）→ 页面 mm 的子路径"""
    page = _page_of(o)
    out: list[dict] = []
    cur: dict | None = None
    for s in segs:
        if s[0] == "M":
            cur = {"closed": False, "nodes": [_corner(page(s[1], s[2]))]}
            out.append(cur)
        elif s[0] == "L" and cur is not None:
            cur["nodes"].append(_corner(page(s[1], s[2])))
        elif s[0] == "C" and cur is not None:
            cur["nodes"][-1]["out"] = page(s[1], s[2])
            end = page(s[5], s[6])
            cur["nodes"].append({"p": end, "in": page(s[3], s[4]), "out": end, "smooth": False})
        elif s[0] == "Z" and cur is not None:
            # 闭合：`path_segments` 已补了「末 → 首」那一段，末点与首点重合——并成首点（入手柄挪过去）
            first, last = cur["nodes"][0], cur["nodes"][-1]
            if (
                len(cur["nodes"]) > 1
                and math.hypot(last["p"][0] - first["p"][0], last["p"][1] - first["p"][1]) < 1e-9
            ):
                first["in"] = last["in"]
                cur["nodes"].pop()
            cur["closed"] = True
    return [sp for sp in out if len(sp["nodes"]) >= 2]


def _head_subpaths(h: tuple, o: dict) -> list[dict]:
    """`outlineStroke.headSubpaths`：一个端型（`geometry.head_geometry` 的形状）→ 页面 mm 的子路径"""
    page = _page_of(o)
    if h[0] == "dot":
        _, cx, cy, r = h
        k = 0.5522847498 * r

        def P(x: float, y: float) -> XY:
            return page(cx + x, cy + y)

        return [
            {
                "closed": True,
                "nodes": [
                    {"p": P(r, 0), "in": P(r, -k), "out": P(r, k), "smooth": True},
                    {"p": P(0, r), "in": P(k, r), "out": P(-k, r), "smooth": True},
                    {"p": P(-r, 0), "in": P(-r, k), "out": P(-r, -k), "smooth": True},
                    {"p": P(0, -r), "in": P(-k, -r), "out": P(k, -r), "smooth": True},
                ],
            }
        ]
    pts = [_corner(page(x, y)) for x, y in h[1]]
    if h[0] == "poly":
        return [{"closed": True, "nodes": pts}]
    return [{"closed": False, "nodes": pts}] if len(pts) >= 2 else []


def _filled_head(h: tuple) -> bool:
    """`shapeGeometry.isFilledHead`：开口 V / 短横是一笔描边，其余（三角 / 隐形 / 菱形 / 圆点）是面积"""
    return h[0] in ("poly", "dot")


def _dash_array(o: dict, sw_mm: float) -> list[float] | None:
    """`shapeGeometry.dashArray(o.dash, sw, custom, MM_PER_PT)`：自定义虚线（pt）优先，点线 = 0.01×线宽 + 2×线宽，
    预设线型按线宽比例。不取整（与 `geometry.dash_pattern` 给导出的那份不同：那份保留 3 位）。"""
    custom = geometry.valid_dash_pattern(o.get("dashPattern"))
    if custom:
        return [v * MM_PER_PT for v in custom]
    dash = o.get("dash")
    if dash == "dotted":
        return [sw_mm * 0.01, sw_mm * 2]
    ratios = geometry.DASH_RATIOS.get(dash) if isinstance(dash, str) else None
    return [sw_mm * r for r in ratios] if ratios else None


_ARC_SAMPLES = 64


def _lerp(p: XY, q: XY, t: float) -> XY:
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def _seg_at(s: tuple, t: float) -> XY:
    a, b, c, d = s
    ab, bc, cd = _lerp(a, b, t), _lerp(b, c, t), _lerp(c, d, t)
    return _lerp(_lerp(ab, bc, t), _lerp(bc, cd, t), t)


def _split(q: tuple, t: float) -> tuple[tuple, tuple]:
    a, b, c, d = q
    ab, bc, cd = _lerp(a, b, t), _lerp(b, c, t), _lerp(c, d, t)
    abc, bcd = _lerp(ab, bc, t), _lerp(bc, cd, t)
    m = _lerp(abc, bcd, t)
    return (a, ab, abc, m), (m, bcd, cd, d)


def _sub_seg(s: tuple, t0: float, t1: float) -> tuple:
    """三次段的 [t0, t1] 那一截（de Casteljau 两刀）"""
    right = _split(s, t0)[1] if t0 > 0 else s
    u = 1 if t0 >= 1 else (t1 - t0) / (1 - t0)
    return _split(right, u)[0] if u < 1 else right


def _arc_table(s: tuple) -> tuple[list[float], float]:
    """一段的弧长表（t 等分 64 份，与前端 `SAMPLES` 同一个数）"""
    ln = [0.0]
    prev = s[0]
    for k in range(1, _ARC_SAMPLES + 1):
        q = _seg_at(s, k / _ARC_SAMPLES)
        ln.append(ln[k - 1] + math.hypot(q[0] - prev[0], q[1] - prev[1]))
        prev = q
    return ln, ln[_ARC_SAMPLES]


def _t_at(tab: tuple[list[float], float], d: float) -> float:
    ln, total = tab
    if d <= 0:
        return 0.0
    if d >= total:
        return 1.0
    i = 1
    while i < _ARC_SAMPLES and ln[i] < d:
        i += 1
    a, b = ln[i - 1], ln[i]
    return (i - 1 + ((d - a) / (b - a) if b > a else 0)) / _ARC_SAMPLES


def dash_subpaths(subs: list[dict], dash: list[float]) -> list[dict]:
    """`outlineStroke.dashSubpaths`：按虚线（画 / 空交替，mm）把子路径切成一段段画出来的部分（开放子路径）。
    每条子路径从头量起；闭合子路径横跨起点的画段接回一段，一段盖满整圈 = 保持闭合。"""
    total = sum(dash)
    if not total > 0:
        return [dict(sp) for sp in subs]
    out: list[dict] = []
    for sp in subs:
        nodes = sp["nodes"]
        n = len(nodes)
        segs = []
        i = 0
        while i + 1 < n or (sp["closed"] and i < n):
            p, q = nodes[i], nodes[(i + 1) % n]
            segs.append((p["p"], p["out"], q["in"], q["p"]))
            if i + 1 >= n:
                break
            i += 1
        k = 0
        left = dash[0]
        on = True
        cur: dict | None = None
        first: dict | None = None
        for si, s in enumerate(segs):
            tab = _arc_table(s)
            pos = 0.0
            while pos < tab[1] - 1e-12:
                step = min(left, tab[1] - pos)
                if on:
                    a, b, c, d = _sub_seg(s, _t_at(tab, pos), _t_at(tab, pos + step))
                    if cur is None:
                        cur = {
                            "closed": False,
                            "nodes": [{"p": a, "in": a, "out": b, "smooth": False}],
                        }
                        out.append(cur)
                        if first is None and k == 0 and pos == 0 and si == 0:
                            first = cur
                    else:
                        cur["nodes"][-1]["out"] = b
                    cur["nodes"].append({"p": d, "in": c, "out": d, "smooth": False})
                pos += step
                left -= step
                if left <= 1e-12:
                    k = (k + 1) % len(dash)
                    left = dash[k]
                    on = not on
                    if not on:
                        cur = None
                    while left <= 1e-12 and on:  # 长度为 0 的「画」段：出一个点
                        q = _seg_at(s, _t_at(tab, pos))
                        out.append({"closed": False, "nodes": [_corner(q), _corner(q)]})
                        k = (k + 1) % len(dash)
                        left = dash[k]
                        on = not on
        if sp["closed"] and first is not None and cur is not None and on:
            if cur is first:
                fn = first["nodes"]
                if len(fn) >= 3:
                    fn[0]["in"] = fn[-1]["in"]
                    fn.pop()
                    first["closed"] = True
            else:
                cur["nodes"][-1]["out"] = first["nodes"][0]["out"]
                cur["nodes"].extend(first["nodes"][1:])
                out.remove(first)
    return out


def _geom_input(o: dict) -> dict:
    """文档形态 → `geometry.path_segments` 读的键（只这几个：子路径、描边开关、端型与大小、线宽）"""
    out = {"subpaths": o.get("subpaths"), "stroke": o.get("stroke"), "stroke_pt": o.get("strokePt")}
    for doc, exp in (
        ("headStart", "head_start"),
        ("headEnd", "head_end"),
        ("headSizeStart", "head_size_start"),
        ("headSizeEnd", "head_size_end"),
        ("strokeAlign", "stroke_align"),
    ):
        if o.get(doc) is not None:
            out[exp] = o[doc]
    return out


def stroke_parts(o: dict) -> dict | None:
    """`outlineStroke.strokeParts`：一条路径的描边拆成要拓宽 / 要合并 / 要裁剪的几何（页面 mm）。没有描边 = None。"""
    sw_pt = o.get("strokePt")
    if o.get("stroke") is False or not (
        isinstance(sw_pt, (int, float)) and not isinstance(sw_pt, bool) and sw_pt > 0
    ):
        return None
    sw = max(float(sw_pt), 0.05) * MM_PER_PT
    gi = _geom_input(o)
    g = geometry.path_segments(gi, float(o["w"]), float(o["h"]), sw)
    if not g["fill"]:
        return None
    custom = geometry.valid_dash_pattern(o.get("dashPattern"))
    dash = _dash_array(o, sw)
    strokes = segs_to_subpaths(g["stroke"] if g["heads"] else g["fill"], o)
    if dash:
        strokes = dash_subpaths(strokes, dash)
    align = geometry.path_stroke_align(gi)
    head_fills: list[dict] = []
    head_strokes: list[tuple[list[dict], str]] = []
    for h in g["heads"]:
        subs = _head_subpaths(h, o)
        if _filled_head(h):
            head_fills += subs
        else:
            head_strokes.append(
                (subs, o.get("lineJoin") or ("miter" if h[0] == "bar" else "round"))
            )
    return {
        "strokes": strokes,
        "cap": geometry.stroke_cap(o.get("lineCap"), None if custom else o.get("dash")),
        "join": o.get("lineJoin") or PATH_DEFAULT_JOIN,
        "miter": geometry.miter_limit(o.get("miterLimit")),
        "head_fills": head_fills,
        "head_strokes": head_strokes,
        "head_cap": geometry.stroke_cap(o.get("lineCap"), None),
        "align": align,
        "region": segs_to_subpaths(g["fill"], o) if align else [],
    }


def can_outline_stroke(o: dict) -> bool:
    """`pathfinder.canOutlineStroke`（锁定 / 隐藏另判）：有描边（`stroke !== false` 且线宽 > 0）"""
    sw = o.get("strokePt")
    return (
        o.get("stroke") is not False
        and isinstance(sw, (int, float))
        and not isinstance(sw, bool)
        and sw > 0
    )


def stroke_area(kernel, p: dict) -> list[dict] | None:
    """`pathfinder.strokeArea`：线杆（已切好虚线）按线宽 / 线端 / 拐角拓宽，并上端型；内外对齐时拓两倍宽再裁到
    填充区域里面 / 外面（页面 mm）。算不出 = None。"""
    parts = stroke_parts(p)
    if parts is None:
        return None
    width = float(p["strokePt"]) * (2 if parts["align"] else 1)
    pieces: list[list[dict]] = []

    def widen(subs: list[dict], w: float, cap: str, join: str) -> bool:
        if not subs:
            return True
        r = kernel.outline(
            encode_subpaths(subs),
            w,
            CAP_CODE[cap],
            JOIN_CODE.get(join, JOIN_CODE[DEFAULT_JOIN]),
            parts["miter"],
        )
        d = decode_subpaths(r) if r else None
        if d is None:
            return False
        pieces.append(d[0])
        return True

    if not widen(parts["strokes"], width, parts["cap"], parts["join"]):
        return None
    for subs, join in parts["head_strokes"]:
        if not widen(subs, float(p["strokePt"]), parts["head_cap"], join):
            return None
    for f in parts["head_fills"]:
        pieces.append([f])
    if len(pieces) == 1:
        area = pieces[0]
    else:
        # 线杆、各个端型叠在一起：并成一块（重叠处不叠两层）
        out = kernel.pathfinder(PATHFINDER_MODES["unite"], encode_stack([(0, s) for s in pieces]))
        res = decode_results(out) if out else None
        if not res:
            return None
        area = [sp for r in res for sp in r["subs"]]
    if parts["align"]:
        rule = 1 if p.get("fillRule") == "evenodd" else 0
        mode = "intersect" if parts["align"] == "inside" else "minus_front"
        out = kernel.pathfinder(
            PATHFINDER_MODES[mode], encode_stack([(0, area), (rule, parts["region"])])
        )
        res = decode_results(out) if out else None
        if not res:
            return None
        area = [sp for r in res for sp in r["subs"]]
    return area or None


def outline_stroke(kernel, p: dict, new_id: str) -> tuple[dict, dict | None] | None:
    """`pathfinder.outlineStrokes` 的一个对象：描边画出来的那块面积变成一个填充对象（填充色 = 原描边色与不透明度，无描边）。
    原对象没有填充：轮廓直接换掉它（同 id、同名）→ `(轮廓, None)`；有填充：原对象关掉描边留下、轮廓作为新对象 → `(原对象, 轮廓)`。"""
    area = stroke_area(kernel, p)
    if area is None:
        return None
    base = style_base(p, p.get("groupId"))
    for k in (
        "dash",
        "dashPattern",
        "lineCap",
        "lineJoin",
        "miterLimit",
        "strokeOpacity",
        "fillOpacity",
    ):
        base.pop(k, None)
    plain = {**base, "fill": p.get("color"), "stroke": False}
    if p.get("strokeOpacity") is not None:
        plain["fillOpacity"] = p["strokeOpacity"]
    made = path_from_mm(_unit_frame(plain), area)
    if made is None:
        return None
    outlined = {**made, "fillGradient": p["strokeGradient"]} if p.get("strokeGradient") else made
    if p.get("fill"):
        return {**p, "stroke": False}, {**outlined, "id": new_id}
    out = {**outlined, "id": p["id"]}
    if p.get("name") is not None:
        out["name"] = p["name"]
    return out, None


# ------------------------------------------------------------------ 简化

#: 简化的缺省容差（pt）：曲线偏离原来不超过它（前端 `SIMPLIFY_TOLERANCE_PT`）
SIMPLIFY_TOLERANCE_PT = 0.5


def simplify_path(kernel, o: dict, tolerance_pt: float = SIMPLIFY_TOLERANCE_PT) -> dict | None:
    """`pathfinder.simplifyPaths` 的一个对象：在对象自己的未旋转空间里算，id、旋转、样式都不变；锚点一个都没少 = None（不算改动）。"""
    subs = path_to_mm(o)
    r = kernel.simplify(encode_subpaths(subs), tolerance_pt)
    d = decode_subpaths(r) if r else None
    if d is None:
        return None
    before = sum(len(sp["nodes"]) for sp in subs)
    after = sum(len(sp["nodes"]) for sp in d[0])
    if after >= before:
        return None
    return path_from_mm(o, d[0])
