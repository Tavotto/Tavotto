"""Render IR → PostScript **叠加层**（只给「原图 EPS + 画布标注」用，QA-WIN-20，ADR 0046 补记）。

EPS 只有 worker 的 matplotlib 写得出原图（ADR 0046），RenderCore 没有完整的 PostScript 写入器——能力表里
`eps` 对整版画布仍是 `unsupported`。带标注的原图导出需要把**同一份 IR**（`plan.compile_page` 为标注编出的路径 /
已排好的字形）也写进 EPS：这里只做「IR → 一段可叠在已有页面上的 PostScript 运算符」，外层 EPS 的壳与原图 EPS 的嵌入在
`engine/epsoverlay.py`。几何一律取 IR（pt、原点左下、y 向上，与 PostScript 用户空间同一约定），不另写标注布局；
文字用批准字体的字形轮廓（fontTools），与 SVG 写入器的 `outlines` 策略同一条路。

PostScript 没有透明：常量 alpha < 1、透明组、位图、外来页一律以 `UnsupportedCapability` 拒绝（调用方逐项报错，
**不静默丢、不静默变不透明**）。纯标准库 + 惰性 fontTools（经 `hbshaper.require`）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

from . import ir
from .hbshaper import HbFaceProvider, require

#: PostScript 画不出的操作（IR `operations_of` 的名字）→ 一句原因。
UNSUPPORTED = {
    "object_alpha": "PostScript has no transparency (constant alpha < 1)",
    "group_opacity": "PostScript has no transparency (group opacity < 1)",
    "image": "embedded bitmaps are not written into the EPS overlay",
    "imported_page": "imported pages are not written into the EPS overlay",
}


class OverlayError(RuntimeError):
    def __init__(self, code: str, message: str, params: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.params = params or {}


class UnsupportedCapability(OverlayError):
    def __init__(self, operation: str, reason: str, object_id: str = "") -> None:
        super().__init__(
            "unsupported_capability",
            f"EPS overlay cannot represent {operation}: {reason}",
            {"operation": operation, "reason": reason, "object_id": object_id},
        )
        self.operation = operation


@dataclass
class OverlayFacts:
    paths: int = 0
    groups: int = 0
    text_objects: int = 0
    outlined_glyphs: int = 0
    fonts: list[str] = field(default_factory=list)


def _num(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError(f"Non-finite PostScript number: {value!r}")
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _rgb(c: ir.RGB) -> str:
    return " ".join(_num(v) for v in c) + " setrgbcolor"


def _path_ops(segments: tuple) -> list[str]:
    out = ["newpath"]
    for seg in segments:
        op, args = seg[0], seg[1:]
        coords = " ".join(_num(v) for v in args)
        if op == "M":
            out.append(f"{coords} moveto")
        elif op == "L":
            out.append(f"{coords} lineto")
        elif op == "C":
            out.append(f"{coords} curveto")
        elif op == "Z":
            out.append("closepath")
    return out


_CAPS = {"butt": 0, "round": 1, "square": 2}
_JOINS = {"miter": 0, "round": 1, "bevel": 2}


class OverlayWriter:
    def __init__(self, page: ir.Page, provider: HbFaceProvider, prefix: str = "tvg") -> None:
        self.prefix = prefix
        self.page = ir.validate(page)
        self.provider = provider
        self.facts = OverlayFacts()
        self._faces: dict = {}
        self._glyph_sets: dict = {}
        self._glyphs: dict[tuple[str, int], str] = {}
        self._defs: list[str] = []
        self._body: list[str] = []

    # ---- 能力核对 ---------------------------------------------------------
    def _check(self, node: ir.Node) -> None:
        for op in ir.operations_of(node):
            if op in UNSUPPORTED:
                raise UnsupportedCapability(op, UNSUPPORTED[op], getattr(node, "object_id", ""))

    # ---- 节点 -------------------------------------------------------------
    def _emit(self, node: ir.Node) -> None:
        self._check(node)
        if isinstance(node, ir.Path):
            self._paint_path(node)
        elif isinstance(node, ir.Group):
            self.facts.groups += 1
            self._body.append(
                "gsave " + "[" + " ".join(_num(v) for v in node.transform) + "] concat"
            )
            if node.clip is not None:
                self._body.extend(_path_ops(node.clip.segments))
                self._body.append("eoclip" if node.clip.fill_rule == "evenodd" else "clip")
                self._body.append("newpath")
            for child in node.children:
                self._emit(child)
            self._body.append("grestore")
        elif isinstance(node, ir.ShapedText):
            self._text(node)

    def _paint_path(self, node: ir.Path) -> None:
        self.facts.paths += 1
        b = self._body
        b.extend(_path_ops(node.segments))
        if node.fill is not None and node.stroke is not None:
            b.append(
                "gsave "
                + _rgb(node.fill.rgb)
                + (" eofill" if node.fill_rule == "evenodd" else " fill")
                + " grestore"
            )
        elif node.fill is not None:
            b.append(_rgb(node.fill.rgb) + (" eofill" if node.fill_rule == "evenodd" else " fill"))
        if node.stroke is not None:
            s = node.stroke
            dash = "[" + " ".join(_num(v) for v in s.dash) + "] " + _num(s.dash_phase) + " setdash"
            b.append(
                f"{_num(s.width)} setlinewidth {_CAPS[s.cap]} setlinecap {_JOINS[s.join]} setlinejoin "
                f"{_num(s.miter_limit)} setmiterlimit {dash} {_rgb(s.paint.rgb)} stroke"
            )

    # ---- 文字（字形轮廓）----------------------------------------------------
    def _face(self, key: str):
        if key not in self._faces:
            resource = self.page.resources[key]
            try:
                face = self.provider.face_by_resource(resource)
            except Exception as exc:  # noqa: BLE001
                raise OverlayError("font_identity", str(exc), {"font": key}) from exc
            if face.resource != resource or face.kind not in ir.FONT_KINDS:
                raise OverlayError(
                    "font_identity", "Font resource identity does not match", {"font": key}
                )
            self._faces[key] = face
            self.facts.fonts.append(resource.face_id)
        return self._faces[key]

    def _glyph(self, key: str, gid: int) -> str:
        cached = self._glyphs.get((key, gid))
        if cached is not None:
            return cached
        require("fontTools")
        from fontTools.pens.basePen import BasePen

        face = self._face(key)
        ops: list[str] = []

        class Pen(BasePen):
            def _moveTo(self, pt):
                ops.append(f"{_num(pt[0])} {_num(pt[1])} moveto")

            def _lineTo(self, pt):
                ops.append(f"{_num(pt[0])} {_num(pt[1])} lineto")

            def _curveToOne(self, p1, p2, p3):
                ops.append(" ".join(_num(v) for v in (*p1, *p2, *p3)) + " curveto")

            def _closePath(self):
                ops.append("closepath")

            def _endPath(self):
                pass

        try:
            if key not in self._glyph_sets:
                self._glyph_sets[key] = face._tt.getGlyphSet()
            glyphs = self._glyph_sets[key]
            glyphs[face.glyph_name(gid)].draw(Pen(glyphs))
        except (IndexError, KeyError, ValueError) as exc:
            raise OverlayError(
                "font_format", "Glyph outline is unavailable", {"font": key, "gid": gid}
            ) from exc
        name = f"{self.prefix}{len(self._glyphs)}"
        self._defs.append(f"/{name} {{ newpath " + " ".join(ops) + " } bind def")
        self._glyphs[(key, gid)] = name
        return name

    def _text(self, node: ir.ShapedText) -> None:
        self.facts.text_objects += 1
        pen_x = node.x
        for run in node.runs:
            face = self._face(run.font)
            size = run.size / face.upem
            for glyph in run.glyphs:
                if not run.invisible:
                    name = self._glyph(run.font, glyph.gid)
                    tx = pen_x + glyph.x_offset * size
                    ty = node.y + run.rise + glyph.y_offset * size
                    self._body.append(
                        f"gsave [{_num(size)} 0 0 {_num(size)} {_num(tx)} {_num(ty)}] concat "
                        f"{name} {_rgb(node.paint.rgb)} fill grestore"
                    )
                    self.facts.outlined_glyphs += 1
                pen_x += glyph.x_advance * size

    def render(self) -> str:
        """叠加层 PostScript（假定调用时用户空间 = IR 页面空间：原点左下、单位 pt）。"""
        for node in self.page.children:
            self._emit(node)
        return "\n".join([*self._defs, *self._body]) + "\n"


def overlay_ps(
    page: ir.Page,
    provider: HbFaceProvider,
    files: Mapping[str, bytes] | None = None,
    *,
    prefix: str = "tvg",
) -> tuple[str, OverlayFacts]:
    """`page`（只含标注的 IR）→ (PostScript 叠加层, 事实)。含 PS 表达不了的东西时抛 `UnsupportedCapability`。"""
    writer = OverlayWriter(page, provider, prefix)
    text = writer.render()
    return text, writer.facts
