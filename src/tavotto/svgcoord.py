"""SVG 场景的坐标与变换链（ADR 0181，SG05）——纯函数，零 I/O。

`svgscene.scene_inventory` 给出「有什么节点」；本模块给出「节点在哪里」：

- **变换矩阵累积**：从根到目标元素的 `transform` 属性链 + `<use>` 的 x/y 位移，
  行向量约定（与 `rendercore/ir.Matrix` 同形：``p' = p · M``），点先经内层再经外层。
- **用户单位 → 物理 pt**：viewBox + width/height + preserveAspectRatio 全语义
  （none 非均匀 / meet·slice + 九宫对齐）。这是 SVG 面板接入既有
  面板裁剪/翻转/旋转 → 页面 mm → 屏幕链条的**第一段**；后段复用 RenderCore
  的 placement（面板级）与前端 display 变换（屏幕级），不在本模块重复。

同一份字节（同 scene_digest）永远得到同一组矩阵——几何是派生事实，
身份由 svgscene 的摘要钉住。
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"

#: 行向量单位阵。
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_TRANSFORM_RE = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(\s*([^)]*)\)")


def compose(m1: tuple, m2: tuple) -> tuple:
    """先 m1 后 m2（`p' = p · m1 · m2`）——与 rendercore.ir.compose 同一定义。"""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def apply(m: tuple, x: float, y: float) -> tuple[float, float]:
    return (x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5])


def parse_transform(text: str) -> tuple:
    """SVG transform 属性 → 行向量矩阵。未知函数按不变参报错（宁可拒绝不猜）。"""
    m = IDENTITY
    matches = list(_TRANSFORM_RE.finditer(text or ""))
    residue = _TRANSFORM_RE.sub("", text or "")
    if residue.strip(" ,\t\r\n"):
        raise ValueError(f"unsupported transform syntax: {residue.strip()[:40]!r}")
    for match in matches:
        name, raw = match.groups()
        if re.sub(_NUM, "", raw).strip(" ,\t\r\n"):
            raise ValueError(f"unsupported transform arguments: {name}")
        vals = [float(v) for v in re.findall(_NUM, raw)]
        if not all(math.isfinite(v) for v in vals):
            raise ValueError("transform arguments must be finite")
        if name == "matrix" and len(vals) == 6:
            a, b, c, d, e, f = vals
            local = (a, b, c, d, e, f)
        elif name == "translate" and len(vals) in (1, 2):
            tx = vals[0]
            ty = vals[1] if len(vals) > 1 else 0.0
            local = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif name == "scale" and len(vals) in (1, 2):
            sx = vals[0]
            sy = vals[1] if len(vals) > 1 else sx
            local = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and len(vals) in (1, 3):
            ang = math.radians(vals[0])
            cos, sin = math.cos(ang), math.sin(ang)
            rot = (cos, sin, -sin, cos, 0.0, 0.0)
            if len(vals) == 3:
                # rotate(cx,cy)：先平到原点、旋转、再平回（p' = (p-c)·R + c）
                cx, cy = vals[1], vals[2]
                local = compose(
                    compose((1.0, 0.0, 0.0, 1.0, -cx, -cy), rot), (1.0, 0.0, 0.0, 1.0, cx, cy)
                )
            else:
                local = rot
        elif name == "skewX" and len(vals) == 1:
            local = (1.0, 0.0, math.tan(math.radians(vals[0])), 1.0, 0.0, 0.0)
        elif name == "skewY" and len(vals) == 1:
            local = (1.0, math.tan(math.radians(vals[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            raise ValueError(f"unsupported transform {name} with {len(vals)} args")
        # SVG's list is equivalent to nested groups in written order. Under
        # our row-vector convention the rightmost function acts first.
        m = compose(local, m)
    return m


def _find_by_id(root: ET.Element, node_id: str) -> ET.Element | None:
    if root.attrib.get("id") == node_id:
        return root
    for el in root.iter():
        if el.attrib.get("id") == node_id:
            return el
    return None


def _use_anchor_offset(el: ET.Element) -> tuple:
    try:
        return (float(el.attrib.get("x", "0")), float(el.attrib.get("y", "0")))
    except ValueError as exc:
        raise ValueError("use x/y must be numbers") from exc


def node_matrix(root: ET.Element, node_id: str) -> tuple:
    """根 → 指定节点的累积矩阵（含 use 的 x/y 位移；定义段 defs 内不追踪）。

    行向量约定下内层矩阵先作用于点：``p_页 = p_局部 · M_目标 · … · M_根``，
    所以从根往下走时**每个元素矩阵 prepend**（compose(M_element, m)）；
    `use` 的 x/y 比 transform 更深（更先作用于内容），也 prepend 在前。
    """
    target = _find_by_id(root, node_id)
    if target is None:
        raise KeyError(node_id)
    chain: list[ET.Element] = []
    node: ET.Element | None = target
    while node is not None:
        chain.append(node)
        node = (
            None
            if node is root
            else next((p for p in root.iter() if any(c is node for c in p)), None)
        )
    m = IDENTITY
    for el in reversed(chain):  # 根 → 目标
        if el.attrib.get("transform"):
            m = compose(parse_transform(el.attrib["transform"]), m)
        if el.tag.split("}")[-1] == "use":
            dx, dy = _use_anchor_offset(el)
            if dx or dy:
                # SVG 1.1：use 的 x/y 等价于 transform 列表**末尾**的 translate
                # —— 列表末尾 = 最深 = 最先作用于内容 = 行向量下最前。
                m = compose((1.0, 0.0, 0.0, 1.0, dx, dy), m)
    return m


def invert(m: tuple) -> tuple:
    """仿射矩阵的逆（行列式非零；奇异矩阵报错——调用方按不合法场景处理）。"""
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        raise ValueError("singular matrix cannot be inverted")
    return (
        d / det,
        -b / det,
        -c / det,
        a / det,
        (c * f - d * e) / det,
        (b * e - a * f) / det,
    )


def matrix_attr(m: tuple) -> str:
    return f"matrix({m[0]},{m[1]},{m[2]},{m[3]},{m[4]},{m[5]})"


_PRESERVE_RE = re.compile(r"^(xMin|xMid|xMax)(YMin|YMid|YMax)(?:\s+(meet|slice))?$")


def viewbox_to_pt(view_box: list[float], w_pt: float, h_pt: float, preserve: str | None) -> tuple:
    """viewBox 用户单位 → 物理 pt 的映射矩阵（SVG y-down 保持不变，只做比例与对齐）。"""
    vb_x, vb_y, vb_w, vb_h = view_box
    if not all(math.isfinite(v) for v in (*view_box, w_pt, h_pt)):
        raise ValueError("viewBox and viewport must be finite")
    if min(vb_w, vb_h, w_pt, h_pt) <= 0:
        raise ValueError("viewBox must have positive width and height")
    spec = (preserve or "xMidYMid meet").strip()
    if spec.startswith("defer "):
        spec = spec[6:].strip()
    if re.fullmatch(r"none(?:\s+(?:meet|slice))?", spec):
        sx, sy = w_pt / vb_w, h_pt / vb_h
        return (sx, 0.0, 0.0, sy, -vb_x * sx, -vb_y * sy)
    m = _PRESERVE_RE.match(spec)
    if m is None:
        raise ValueError(f"unsupported preserveAspectRatio {spec!r}")
    align_x, align_y, mode = m.group(1), m.group(2), m.group(3) or "meet"
    scale = min(w_pt / vb_w, h_pt / vb_h) if mode == "meet" else max(w_pt / vb_w, h_pt / vb_h)
    free_x, free_y = w_pt - vb_w * scale, h_pt - vb_h * scale
    tx = {"xMin": 0.0, "xMid": free_x / 2.0, "xMax": free_x}[align_x]
    ty = {"YMin": 0.0, "YMid": free_y / 2.0, "YMax": free_y}[align_y]
    return (scale, 0.0, 0.0, scale, tx - vb_x * scale, ty - vb_y * scale)
