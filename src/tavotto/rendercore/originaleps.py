"""完整冻结 SVG → 既有 SVGIR/字形轮廓 → 有界 PS 场景。

只准备原图；不是画布 EPS 写入器，不替换 Python live Figure/Type42。
Matplotlib 容器在既有 worker 内调用 ``epscontainer.pack_prepared_eps``。
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, replace
from typing import Mapping

from . import epswriter, ir, svgir
from .epscontainer import MAX_BYTES, MAX_EXPANDED_COST, SCHEMA, SceneEPSError, validate_payload
from .hbshaper import HbFaceProvider


@dataclass(frozen=True)
class PreparedScenePS:
    ps_ascii: str
    width_pt: float
    height_pt: float
    source_svg_sha256: str
    ps_sha256: str
    expanded_cost: int
    facts: dict
    notes: tuple[str, ...]

    def to_payload(self) -> dict:
        return validate_payload(
            {
                "schema": SCHEMA,
                "ps_ascii": self.ps_ascii,
                "width_pt": self.width_pt,
                "height_pt": self.height_pt,
                "source_svg_sha256": self.source_svg_sha256,
                "ps_sha256": self.ps_sha256,
                "expanded_cost": self.expanded_cost,
            }
        )


def _unsupported(operation, node):
    raise epswriter.UnsupportedCapability(
        operation, epswriter.UNSUPPORTED[operation], getattr(node, "object_id", "")
    )


def _opaque_node(node):
    """终态 IR 的精确零贡献消去；部分 alpha 不乘到子节点冒充组 alpha。"""
    if isinstance(node, ir.Group):
        if node.opacity == 0:
            return None
        children = tuple(n for c in node.children if (n := _opaque_node(c)) is not None)
        if not children:
            return None
        if node.opacity != 1:
            _unsupported("group_opacity", node)
        return replace(node, children=children)
    if isinstance(node, ir.Path):
        fill = node.fill if node.fill is not None and node.fill.alpha != 0 else None
        stroke = node.stroke if node.stroke is not None and node.stroke.paint.alpha != 0 else None
        if fill is None and stroke is None:
            return None
        if (fill is not None and fill.alpha != 1) or (
            stroke is not None and stroke.paint.alpha != 1
        ):
            _unsupported("object_alpha", node)
        return replace(node, fill=fill, stroke=stroke)
    if isinstance(node, ir.ShapedText):
        if node.paint.alpha == 0 or all(r.invisible or not r.glyphs for r in node.runs):
            return None
        if node.paint.alpha != 1:
            _unsupported("object_alpha", node)
        return node
    if isinstance(node, (ir.Image, ir.ImportedPage)):
        if node.opacity == 0:
            return None
        _unsupported("image" if isinstance(node, ir.Image) else "imported_page", node)
    raise SceneEPSError("native_eps_invalid_payload", "Unknown scene IR node")


def _cost(node):
    """先计算实际重复执行量，后分配；嵌套 union 不能造指数级输出。"""
    if isinstance(node, ir.Group):
        children = sum(_cost(c) for c in node.children)
        clips = node.clip.paths if isinstance(node.clip, ir.ClipUnion) else (node.clip,)
        result = sum(1 + (len(c.segments) if c else 0) + children for c in clips)
    elif isinstance(node, ir.Path):
        result = 1 + len(node.segments)
    else:
        result = 1 + sum(len(r.glyphs) for r in node.runs)
    if result > MAX_EXPANDED_COST:
        raise SceneEPSError("native_eps_budget", "Expanded scene exceeds vector budget")
    return result


def _lower(node):
    if not isinstance(node, ir.Group):
        return (node,)
    children = tuple(n for c in node.children for n in _lower(c))
    # Only reached after every visible paint has been proved opaque. Replaying
    # the SAME ordered subtree per union member is idempotent in overlaps.
    # Concatenating clip segments would break opposite winding and member holes.
    if isinstance(node.clip, ir.ClipUnion):
        return tuple(replace(node, children=children, clip=c) for c in node.clip.paths)
    return (replace(node, children=children),)


class _PSParts(list):
    """共享 defs/body 字节账；沿既有 writer 排字/描边，不复制 glyph authority。"""

    def __init__(self, used):
        super().__init__()
        self.used = used

    def append(self, value):
        amount = len(value.encode("ascii")) + 1
        if self.used[0] + amount > MAX_BYTES:
            raise SceneEPSError("native_eps_budget", "Prepared PS exceeds vector byte budget")
        self.used[0] += amount
        super().append(value)

    def extend(self, values):
        for value in values:
            self.append(value)


def prepare_scene_ps(
    svg: bytes,
    w_pt: float,
    h_pt: float,
    *,
    faces: HbFaceProvider,
    files: Mapping[str, bytes] | None = None,
) -> PreparedScenePS:
    """完整最终 SVG（含未绑定装饰）→ 冻结 worker payload 和真实准备回执。

    图片仍由 SVGIR 解析并交付同身份资源；本版本拒绝可见 Image，不假装整图
    栅格化就是 EPS 矢量。``files`` 不被修改，字体只认现批准 provider。
    """
    source_hash = hashlib.sha256(svg).hexdigest()
    resources = dict(files or {})
    page, notes = svgir.svg_to_ir(svg, w_pt, h_pt, faces, files=resources)
    children = tuple(n for c in page.children if (n := _opaque_node(c)) is not None)
    if page.background is not None:
        children = (
            ir.Path(
                (("M", 0, 0), ("L", w_pt, 0), ("L", w_pt, h_pt), ("L", 0, h_pt), ("Z",)),
                fill=ir.Paint(page.background),
            ),
            *children,
        )
    cost = sum(_cost(c) for c in children)
    if cost > MAX_EXPANDED_COST:
        raise SceneEPSError("native_eps_budget", "Expanded scene exceeds vector budget")
    lowered = ir.validate(replace(page, children=tuple(n for c in children for n in _lower(c))))
    writer = epswriter.OverlayWriter(lowered, faces)
    # Deliberately reuse the complete existing path/glyph writer. These bounded
    # sinks protect BOTH caches before render() joins them into one PS string.
    used = [0]
    writer._defs = _PSParts(used)
    writer._body = _PSParts(used)
    ps = writer.render()
    prepared = PreparedScenePS(
        ps,
        page.width_pt,
        page.height_pt,
        source_hash,
        hashlib.sha256(ps.encode("ascii")).hexdigest(),
        cost,
        asdict(writer.facts),
        tuple(notes),
    )
    prepared.to_payload()
    return prepared
