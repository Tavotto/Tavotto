"""路径几何的「语义层」：画布矢量对象 ↔ 内核编码、结果怎么变回路径对象、谁的样式 / id 被沿用（ADR 0212 第 3 期）。

逐条照前端 `web/src/lib/pathfinder.ts`（路径查找器 / 偏移 / 轮廓化描边 / 简化）与 `lib/pathEdit.ts`（`pathToMm` / `pathFromMm`）、
`lib/pathGeom.fitPathBox`（曲线实际范围的包围盒）、`lib/gradient.remapGradients`；内核调用在 `engine/vecops.py`（同一份 wasm）。
**纯函数**：输入对象字典（文档里的 camelCase 形态），输出要换上去的对象；落位（插进数组哪一位、删谁）在 `engine/vectoredit.py`。

坐标：对象各自的比例坐标 → 页面 mm（旋转已算进去，结果都是不旋转的对象）→ pt 交给内核（精度 0.01 是文档单位，用 pt 就是 0.01 pt），
结果再换回来。内核几何见 `vecops/src/lib.rs`；这里只做坐标换算与规则。

**与前端不同的地方**（如实）：
* 形状（rect / ellipse …）不会自动「转为路径」——前端的 `convertToPath` 没有 Python 版，本期只收 `path`；
* 轮廓化描边只做「居中的实线、没有端型」的路径（虚线 / 端型 / 内外对齐的拆件在前端 `lib/outlineStroke`，没有 Python 版）；
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


def stroke_unsupported(o: dict) -> str | None:
    """本期（Python）没有的描边外观：虚线 / 端型 / 内外对齐的拆件只在前端 `lib/outlineStroke`。回字段名；能做 = None。"""
    if o.get("dash") not in (None, "solid") or geometry.valid_dash_pattern(o.get("dashPattern")):
        return "dash"
    if o.get("headStart", "none") != "none" or o.get("headEnd", "none") != "none":
        return "head"
    if o.get("strokeAlign") in ("inside", "outside"):
        drawn = [sp for sp in path_to_mm(o) if len(sp["nodes"]) >= 2]
        if drawn and all(sp["closed"] for sp in drawn):
            return "strokeAlign"
    return None


def can_outline_stroke(o: dict) -> bool:
    """`pathfinder.canOutlineStroke`（路径部分）：有描边（`stroke !== false` 且线宽 > 0）"""
    sw = o.get("strokePt")
    return (
        o.get("stroke") is not False
        and isinstance(sw, (int, float))
        and not isinstance(sw, bool)
        and sw > 0
    )


def stroke_area(kernel, p: dict) -> list[dict] | None:
    """`pathfinder.strokeArea`（居中实线、无端型）：线杆按线宽 / 线端 / 拐角拓宽成的面积（页面 mm）。算不出 = None。"""
    strokes = [
        {"closed": sp["closed"], "nodes": [{**n, "smooth": False} for n in sp["nodes"]]}
        for sp in world_subpaths(p)
        if len(sp["nodes"]) >= 2
    ]
    if not strokes:
        return None
    cap = geometry.stroke_cap(p.get("lineCap"), p.get("dash"))
    join = p.get("lineJoin") or DEFAULT_JOIN
    r = kernel.outline(
        encode_subpaths(strokes),
        float(p["strokePt"]),
        CAP_CODE[cap],
        JOIN_CODE.get(join, JOIN_CODE[DEFAULT_JOIN]),
        geometry.miter_limit(p.get("miterLimit")),
    )
    d = decode_subpaths(r) if r else None
    return d[0] if d and d[0] else None


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
