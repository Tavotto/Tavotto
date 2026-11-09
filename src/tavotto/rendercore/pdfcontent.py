"""Frozen PDF page content -> existing IR, without an execution companion.

This native adapter interprets admitted original drawing operations.  Character
codes select actual embedded glyphs, never glyphs guessed from ToUnicode.  Text
uses the approved text pipeline only after an exact embedded-program/GID/width
proof; otherwise original glyph outlines are retained. Unrepresented paint/font
operations fail instead of disappearing.
Use the existing bounded render-child lifecycle for untrusted PDF decoding.
Importing this module loads no native package and performs no IO.
"""

from __future__ import annotations

import copy
import hashlib
import io
import math
from contextlib import ExitStack
from dataclasses import dataclass

from . import ir, placement, raster
from .geometry import rect_segments
from .hbshaper import require

PROFILE = "pdf-content/1"
ERROR_CODES = ("pdfcontent_invalid", "pdfcontent_unsupported", "pdfcontent_budget")


class PDFContentError(ValueError):
    def __init__(self, code: str, detail: str, operation: str = "") -> None:
        if code not in ERROR_CODES:
            raise AssertionError(code)
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.operation = code, detail, operation


@dataclass(frozen=True)
class PDFContentBudget:
    max_source_bytes: int = 64 * 1024 * 1024
    max_decoded_bytes: int = 128 * 1024 * 1024
    max_font_bytes: int = 16 * 1024 * 1024
    max_ops: int = 500_000
    max_segments: int = 500_000
    max_glyphs: int = 100_000
    max_forms: int = 256
    max_depth: int = 8
    max_clips: int = 8
    max_image_pixels: int = raster.SOURCE_MAX_PIXELS
    max_total_image_pixels: int = raster.DOCUMENT_MAX_PIXELS


@dataclass(frozen=True)
class PDFContentResult:
    page: ir.Page
    files: dict[str, bytes]
    source_sha256: str
    facts: dict


def _fail(detail: str, op: str = "", *, unsupported: bool = False) -> None:
    raise PDFContentError(
        "pdfcontent_unsupported" if unsupported else "pdfcontent_invalid", detail, op
    )


def _number(value) -> float:
    if isinstance(value, (bool, str, bytes)):
        _fail("not a PDF number")
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PDFContentError("pdfcontent_invalid", "invalid number") from exc
    if not math.isfinite(value):
        _fail("non-finite number")
    return value


def _integer(value, *, minimum=0, maximum=None) -> int:
    number = _number(value)
    if int(number) != number or number < minimum or (maximum is not None and number > maximum):
        _fail("integer outside range")
    return int(number)


def _inverse(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    if not math.isfinite(det) or det == 0:
        _fail("singular transform", unsupported=True)
    return d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det


def _transformed(segments, matrix):
    return tuple(
        (s[0], *(v for i in range(1, len(s), 2) for v in ir.apply(matrix, *s[i : i + 2])))
        for s in segments
    )


@dataclass
class _State:
    ctm: tuple = ir.IDENTITY
    clips: tuple = ()
    empty_clip: bool = False
    fill: tuple = (0.0, 0.0, 0.0)
    stroke: tuple = (0.0, 0.0, 0.0)
    ca: float = 1.0
    CA: float = 1.0
    width: float = 1.0
    cap: int = 0
    join: int = 0
    miter: float = 10.0
    dash: tuple = ()
    phase: float = 0.0
    font: str = ""
    size: float = 0.0
    tc: float = 0.0
    tw: float = 0.0
    hz: float = 1.0
    rise: float = 0.0
    leading: float = 0.0
    tr: int = 0


@dataclass
class _TextPosition:
    # Text matrices are NOT part of the q/Q graphics-state stack.
    matrix: tuple = ir.IDENTITY
    line: tuple = ir.IDENTITY
    active: bool = False


class _Interpreter:
    def __init__(self, pdf, q, budget: PDFContentBudget, stack: ExitStack, provider=None):
        self.pdf, self.q, self.budget, self.stack = pdf, q, budget, stack
        self.provider = provider
        self.resources, self.files, self.fonts, self.images = {}, {}, {}, {}
        self.used = dict.fromkeys(
            ("decoded_bytes", "ops", "segments", "glyphs", "forms", "image_pixels"), 0
        )
        self.active_forms = set()
        self.counts = dict.fromkeys(("paths", "text_runs", "images", "forms"), 0)
        self.font_hashes = set()
        self.text_glyphs = 0
        self.outlined_glyphs = 0

    def approved_subset(self, data, tt):
        """A name only narrows candidates; exact original subset bytes prove them.

        Reuse the approved face's existing subset implementation. No uploaded
        font is registered or granted permission by its family/name/cmap.
        """
        if self.provider is None or not hasattr(self.provider, "registry"):
            return None
        postscript = tt["name"].getDebugName(6) if "name" in tt else None
        cmap = tt.getBestCmap() or {}
        for record in self.provider.registry.faces.values():
            if record.info.postscript_name != postscript:
                continue
            if any(cp not in record.info.cmap for cp in cmap):
                continue
            face = self.provider.face_by_resource(record.resource)
            wanted = {0} | {record.info.cmap[cp] for cp in cmap}
            generated, remap, _ = face.subset(wanted)
            if generated == data:
                return face, {subset_gid: gid for gid, subset_gid in remap.items()}
        return None

    def charge(self, key, amount, limit):
        self.used[key] += amount
        if self.used[key] > limit:
            raise PDFContentError("pdfcontent_budget", key)

    def decoded(self, obj, *, font=False):
        if not isinstance(obj, self.q.Stream):
            _fail("expected stream")
        data = obj.read_bytes()
        self.charge("decoded_bytes", len(data), self.budget.max_decoded_bytes)
        if font and len(data) > self.budget.max_font_bytes:
            raise PDFContentError("pdfcontent_budget", "font_bytes")
        return data

    def instructions(self, container):
        if isinstance(container, self.q.Page):
            content = container.obj.get("/Contents")
            streams = list(content) if isinstance(content, self.q.Array) else [content]
            data = b"\n".join(self.decoded(s) for s in streams if s is not None)
        else:
            data = self.decoded(container)
        # Parse the bounded decoded copy, not a second native decode of the source.
        return self.q.parse_content_stream(self.q.Stream(self.pdf, data))

    def add_segments(self, segments):
        segments = tuple(segments)
        self.charge("segments", len(segments), self.budget.max_segments)
        return segments

    def wrap(self, node, state, matrix=ir.IDENTITY):
        if matrix != ir.IDENTITY:
            node = ir.Group((node,), transform=matrix)
        for clip in reversed(state.clips):
            node = ir.Group((node,), clip=clip)
        return node

    def stroke(self, state):
        if state.width == 0:
            _fail("PDF hairline has no exact SVG equivalent", unsupported=True)
        return ir.Stroke(
            ir.Paint(state.stroke, state.CA),
            state.width,
            ir.LINE_CAPS[state.cap],
            ir.LINE_JOINS[state.join],
            state.dash,
            state.phase,
            state.miter,
        )

    def font(self, obj):
        key = obj.objgen if obj.is_indirect else ("direct", id(obj))
        if key in self.fonts:
            return self.fonts[key]
        if str(obj.get("/Subtype")) != "/Type0" or str(obj.get("/Encoding")) != "/Identity-H":
            _fail("font encoding is not embedded Identity-H", unsupported=True)
        descendants = obj.get("/DescendantFonts")
        if descendants is None or len(descendants) != 1:
            _fail("invalid descendant font")
        cid = descendants[0]
        if str(cid.get("/Subtype")) != "/CIDFontType2":
            _fail("CID font format", unsupported=True)
        desc = cid.get("/FontDescriptor")
        if desc is None or "/FontFile2" not in desc:
            _fail("font program unavailable", unsupported=True)
        data = self.decoded(desc.FontFile2, font=True)
        require("fontTools")
        from fontTools.ttLib import TTFont

        tt = TTFont(io.BytesIO(data), lazy=False, recalcTimestamp=False)
        self.stack.callback(tt.close)
        if not {"head", "glyf", "hmtx"}.issubset(tt.keys()):
            _fail("font program is not TrueType glyf", unsupported=True)
        self.font_hashes.add(hashlib.sha256(data).hexdigest())
        gidmap = cid.get("/CIDToGIDMap", self.q.Name.Identity)
        if isinstance(gidmap, self.q.Stream):
            gidmap = self.decoded(gidmap)
            if len(gidmap) % 2:
                _fail("odd CIDToGIDMap size")
        elif str(gidmap) == "/Identity":
            gidmap = None
        else:
            _fail("invalid CIDToGIDMap")
        widths, ws = {}, list(cid.get("/W", []))
        i = 0
        while i < len(ws):
            start = _integer(ws[i], maximum=65535)
            if isinstance(ws[i + 1], self.q.Array):
                values = [_number(v) for v in ws[i + 1]]
                i += 2
            else:
                end = _integer(ws[i + 1], minimum=start, maximum=65535)
                values = [_number(ws[i + 2])] * (end - start + 1)
                i += 3
            if start + len(values) > 65536:
                _fail("font width code outside range")
            for code, value in enumerate(values, start):
                if code in widths:
                    _fail("overlapping font widths")
                widths[code] = value
        approved = self.approved_subset(data, tt)
        mapping = {}
        if approved is not None and obj.get("/ToUnicode") is not None:
            from .inspector import parse_tounicode

            mapping, code_bytes = parse_tounicode(self.decoded(obj.ToUnicode))
            if code_bytes != 2:
                mapping = {}
        result = tt, gidmap, widths, _number(cid.get("/DW", 1000)), approved, mapping
        self.fonts[key] = result
        return result

    def glyph_segments(self, tt, gid):
        from fontTools.pens.basePen import BasePen

        owner, glyphs = self, tt.getGlyphSet()

        class Pen(BasePen):
            def __init__(self):
                super().__init__(glyphs)
                self.segments = []

            def add(self, *segment):
                owner.charge("segments", 1, owner.budget.max_segments)
                self.segments.append(segment)

            def _moveTo(self, p):
                self.add("M", *p)

            def _lineTo(self, p):
                self.add("L", *p)

            def _curveToOne(self, p1, p2, p3):
                self.add("C", *p1, *p2, *p3)

            def _closePath(self):
                self.add("Z")

            def _endPath(self):
                pass

        order = tt.getGlyphOrder()
        if gid >= len(order):
            _fail("GID outside embedded font")
        pen = Pen()
        glyphs[order[gid]].draw(pen)
        return tuple(pen.segments)

    def show(self, raw, state, pos, resources):
        if not pos.active or not state.font or state.size <= 0:
            _fail("text showing outside a valid text object")
        if state.tr != 0:
            _fail("text rendering mode requires additional geometry", unsupported=True)
        if len(raw) % 2:
            _fail("partial Identity-H character code")
        tt, gidmap, widths, dw, approved, mapping = self.font(resources.Font[state.font])
        upem = _integer(tt["head"].unitsPerEm, minimum=1)
        out = []
        self.counts["text_runs"] += 1
        native_glyphs = []
        native_originals = []
        native_origin = None

        def flush_native():
            nonlocal native_origin
            if not native_glyphs:
                return
            text = "".join(g.text for g in native_glyphs)
            # SVG text performs shaping again. Admit a whole run only when it
            # recreates these exact original GIDs/advances, including kerning.
            if face.shape(text) != tuple(native_glyphs):
                for original_gid, origin in native_originals:
                    segments = self.glyph_segments(tt, original_gid)
                    matrix = (state.size / upem * state.hz, 0, 0, state.size / upem, 0, state.rise)
                    matrix = ir.compose(ir.compose(matrix, origin), state.ctm)
                    if segments and not state.empty_clip and state.ca:
                        out.append(
                            self.wrap(
                                ir.Path(segments, fill=ir.Paint(state.fill, state.ca)),
                                state,
                                matrix,
                            )
                        )
                        self.outlined_glyphs += 1
                native_glyphs.clear()
                native_originals.clear()
                native_origin = None
                return
            node = ir.ShapedText(
                0,
                0,
                (ir.GlyphRun(font_key, state.size, tuple(native_glyphs)),),
                ir.Paint(state.fill, state.ca),
            )
            matrix = ir.compose(
                ir.compose((state.hz, 0, 0, 1, 0, state.rise), native_origin), state.ctm
            )
            if not state.empty_clip and state.ca:
                out.append(self.wrap(node, state, matrix))
                self.text_glyphs += len(native_glyphs)
            native_glyphs.clear()
            native_originals.clear()
            native_origin = None

        for i in range(0, len(raw), 2):
            self.charge("glyphs", 1, self.budget.max_glyphs)
            code = int.from_bytes(raw[i : i + 2], "big")
            gid = code if gidmap is None else int.from_bytes(gidmap[2 * code : 2 * code + 2], "big")
            native = None
            if approved is not None:
                face, approved_gids = approved
                text = mapping.get(code)
                approved_gid = approved_gids.get(gid)
                if text and approved_gid is not None:
                    from .pdfwriter import glyph_width_1000

                    shaped = face.shape(text)
                    if (
                        len(shaped) == 1
                        and shaped[0].gid == approved_gid
                        and not shaped[0].x_offset
                        and not shaped[0].y_offset
                        and widths.get(code, dw) == glyph_width_1000(face, approved_gid)
                    ):
                        font_key = "pdf-font-" + face.resource.sha256
                        self.resources[font_key] = face.resource
                        native = ir.Glyph(approved_gid, text, face.advance(approved_gid))
            if native is not None and state.tc == 0:
                if not native_glyphs:
                    native_origin = pos.matrix
                native_glyphs.append(native)
                native_originals.append((gid, pos.matrix))
                segments = ()
            else:
                flush_native()
                segments = self.glyph_segments(tt, gid)
            matrix = (state.size / upem * state.hz, 0, 0, state.size / upem, 0, state.rise)
            matrix = ir.compose(ir.compose(matrix, pos.matrix), state.ctm)
            if segments and not state.empty_clip and state.ca:
                out.append(
                    self.wrap(ir.Path(segments, fill=ir.Paint(state.fill, state.ca)), state, matrix)
                )
                self.outlined_glyphs += 1
            # Word spacing applies only to a single-byte 0x20 character, not two-byte Identity-H.
            advance = (widths.get(code, dw) * state.size / 1000 + state.tc) * state.hz
            pos.matrix = ir.compose(ir.translate(advance, 0), pos.matrix)
        flush_native()
        return out

    def image(self, obj, state):
        key = obj.objgen if obj.is_indirect else ("direct", id(obj))
        if key not in self.images:
            if obj.get("/ImageMask", False) or any(
                obj.get(k) is not None
                for k in ("/Mask", "/Decode", "/Alternates", "/OPI", "/Intent")
            ):
                _fail("image stencil/decode/alternate policy", unsupported=True)
            if obj.get("/Interpolate", False):
                _fail("explicit image interpolation", unsupported=True)
            if (
                str(obj.get("/ColorSpace")) not in ("/DeviceRGB", "/DeviceGray")
                or _integer(obj.get("/BitsPerComponent", 0)) != 8
            ):
                _fail("image color space/bit depth", unsupported=True)
            width, height = _integer(obj.Width, minimum=1), _integer(obj.Height, minimum=1)
            pixels = width * height
            if pixels > self.budget.max_image_pixels:
                raise PDFContentError("pdfcontent_budget", "image_pixels")
            self.charge("image_pixels", pixels, self.budget.max_total_image_pixels)
            # Charge the exact decoded grid before a native JPEG/Flate decoder
            # runs; qpdf.read_bytes is not the JPEG image decoder.
            channels = 3 if str(obj.ColorSpace) == "/DeviceRGB" else 1
            self.charge("decoded_bytes", pixels * channels, self.budget.max_decoded_bytes)
            mask = obj.get("/SMask")
            if mask is not None:
                if (
                    str(mask.get("/ColorSpace")) != "/DeviceGray"
                    or _integer(mask.get("/BitsPerComponent", 0)) != 8
                    or any(
                        mask.get(k) is not None for k in ("/Decode", "/Matte", "/SMask", "/Mask")
                    )
                    or mask.get("/ImageMask", False)
                    or mask.get("/Interpolate", False)
                ):
                    _fail("image soft mask representation", unsupported=True)
                if (_integer(mask.Width, minimum=1), _integer(mask.Height, minimum=1)) != (
                    width,
                    height,
                ):
                    _fail("image soft mask grid differs", unsupported=True)
                self.charge("decoded_bytes", pixels, self.budget.max_decoded_bytes)
            with self.q.PdfImage(obj).as_pil_image() as original:
                with original.convert("RGB") as image:
                    if mask is not None:
                        with self.q.PdfImage(mask).as_pil_image() as alpha:
                            with alpha.convert("L") as channel:
                                image.putalpha(channel)
                    stream = io.BytesIO()
                    image.save(stream, format="PNG")
                    data = stream.getvalue()
            digest = hashlib.sha256(data).hexdigest()
            resource_key = "pdf-image-" + digest
            self.resources[resource_key] = ir.FileResource(
                resource_key, "png", digest, "static", digest
            )
            self.files[resource_key] = data
            self.images[key] = resource_key
        self.counts["images"] += 1
        node = ir.Image(self.images[key], (0, 0, 1, 1), opacity=state.ca)
        return self.wrap(node, state, state.ctm)

    def form(self, obj, state, resources, depth):
        key = obj.objgen if obj.is_indirect else ("direct", id(obj))
        if key in self.active_forms:
            _fail("recursive Form XObject")
        self.charge("forms", 1, self.budget.max_forms)
        self.counts["forms"] += 1
        child = copy.copy(state)
        fm = tuple(_number(v) for v in obj.get("/Matrix", ir.IDENTITY))
        if len(fm) != 6:
            _fail("invalid Form matrix")
        child.ctm = ir.compose(fm, state.ctm)
        _inverse(child.ctm)
        bbox = tuple(_number(v) for v in obj.BBox)
        if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            _fail("invalid Form BBox")
        # Parent clipping applies to the painted group; do not multiply it into
        # every isolated child before group alpha is applied.
        child.clips = ()
        child.empty_clip = False
        opacity = 1.0
        group = obj.get("/Group")
        if group is not None:
            if (
                str(group.get("/S")) != "/Transparency"
                or not group.get("/I", False)
                or group.get("/K", False)
                or str(group.get("/CS", "/DeviceRGB")) != "/DeviceRGB"
            ):
                _fail("non-isolated/knockout/group color space", unsupported=True)
            opacity, child.ca, child.CA = state.ca, 1.0, 1.0
        self.active_forms.add(key)
        try:
            children = self.walk(obj, obj.get("/Resources", resources), child, depth + 1)
        finally:
            self.active_forms.remove(key)
        clip = ir.Path(
            _transformed(
                rect_segments(bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1]), child.ctm
            )
        )
        return self.wrap(ir.Group(tuple(children), clip=clip, opacity=opacity), state)

    def graphics_state(self, gs, state):
        if any(k not in {"/Type", "/ca", "/CA", "/BM", "/SMask", "/AIS"} for k in gs.keys()):
            _fail("additional ExtGState key", unsupported=True)
        if (
            str(gs.get("/BM", "/Normal")) not in ("/Normal", "/Compatible")
            or str(gs.get("/SMask", "/None")) != "/None"
            or gs.get("/AIS", False)
        ):
            _fail("blend/graphics-state soft mask/alpha-is-shape", unsupported=True)
        state.ca, state.CA = _number(gs.get("/ca", state.ca)), _number(gs.get("/CA", state.CA))
        if not 0 <= state.ca <= 1 or not 0 <= state.CA <= 1:
            _fail("alpha outside range")

    def walk(self, container, resources, initial=None, depth=0):
        if depth > self.budget.max_depth:
            raise PDFContentError("pdfcontent_budget", "depth")
        state = copy.copy(initial) if initial else _State()
        pos = _TextPosition()
        stack, out, path = [], [], []
        current, start, pending_clip = None, None, None
        arities = {
            "q": 0,
            "Q": 0,
            "cm": 6,
            "m": 2,
            "l": 2,
            "c": 6,
            "v": 4,
            "y": 4,
            "re": 4,
            "h": 0,
            "W": 0,
            "W*": 0,
            "n": 0,
            "f": 0,
            "F": 0,
            "f*": 0,
            "S": 0,
            "s": 0,
            "B": 0,
            "B*": 0,
            "b": 0,
            "b*": 0,
            "rg": 3,
            "RG": 3,
            "g": 1,
            "G": 1,
            "w": 1,
            "J": 1,
            "j": 1,
            "M": 1,
            "d": 2,
            "gs": 1,
            "Do": 1,
            "BT": 0,
            "ET": 0,
            "Tf": 2,
            "Tm": 6,
            "Td": 2,
            "TD": 2,
            "T*": 0,
            "Tc": 1,
            "Tw": 1,
            "Tz": 1,
            "TL": 1,
            "Ts": 1,
            "Tr": 1,
            "Tj": 1,
            "TJ": 1,
            "'": 1,
            '"': 3,
        }
        for instruction in self.instructions(container):
            self.charge("ops", 1, self.budget.max_ops)
            op, a = str(instruction.operator), list(instruction.operands)
            if op not in arities:
                _fail("unrepresented content operation", op, unsupported=True)
            if len(a) != arities[op]:
                _fail("operator operand count", op)
            if op == "q":
                if len(stack) >= self.budget.max_depth:
                    raise PDFContentError("pdfcontent_budget", "graphics_state_depth", op)
                stack.append(copy.copy(state))
            elif op == "Q":
                if not stack:
                    _fail("unbalanced graphics state", op)
                state = stack.pop()
            elif op == "cm":
                state.ctm = ir.compose(tuple(_number(v) for v in a), state.ctm)
                _inverse(state.ctm)
            elif op in ("m", "l", "c", "v", "y", "re", "h"):
                if op == "h":
                    if start is None:
                        _fail("closepath without subpath")
                    segments, current = (("Z",),), start
                elif op == "re":
                    segments = _transformed(rect_segments(*[_number(v) for v in a]), state.ctm)
                    current = start = segments[0][1:]
                else:
                    coords = tuple(
                        v
                        for i in range(0, len(a), 2)
                        for v in ir.apply(state.ctm, _number(a[i]), _number(a[i + 1]))
                    )
                    if op != "m" and current is None:
                        _fail("path segment without moveto")
                    if op == "v":
                        segments = (("C", *current, *coords),)
                    elif op == "y":
                        segments = (("C", *coords, *coords[-2:]),)
                    else:
                        segments = (({"m": "M", "l": "L", "c": "C"}[op], *coords),)
                    current = coords[-2:]
                    if op == "m":
                        start = current
                path.extend(self.add_segments(segments))
            elif op in ("W", "W*"):
                pending_clip = "evenodd" if op == "W*" else "nonzero"
            elif op in ("f", "F", "f*", "S", "s", "B", "B*", "b", "b*", "n"):
                if op in ("s", "b", "b*") and path:
                    path.extend(self.add_segments((("Z",),)))
                if op != "n" and path and not state.empty_clip:
                    fill = (
                        ir.Paint(state.fill, state.ca)
                        if op in ("f", "F", "f*", "B", "B*", "b", "b*")
                        else None
                    )
                    stroke = self.stroke(state) if op in ("S", "s", "B", "B*", "b", "b*") else None
                    local = _transformed(path, _inverse(state.ctm))
                    out.append(
                        self.wrap(
                            ir.Path(local, fill, stroke, "evenodd" if "*" in op else "nonzero"),
                            state,
                            state.ctm,
                        )
                    )
                    self.counts["paths"] += 1
                if pending_clip is not None:
                    if not path:
                        state.empty_clip = True
                    else:
                        if len(state.clips) >= self.budget.max_clips:
                            raise PDFContentError("pdfcontent_budget", "clips")
                        state.clips = (*state.clips, ir.Path(tuple(path), fill_rule=pending_clip))
                path, current, start, pending_clip = [], None, None, None
            elif op in ("rg", "g", "RG", "G"):
                values = tuple(_number(v) for v in a)
                color = values if len(values) == 3 else values * 3
                if any(v < 0 or v > 1 for v in color):
                    _fail("color component outside range")
                setattr(state, "fill" if op in ("rg", "g") else "stroke", color)
            elif op in ("w", "J", "j", "M"):
                value = _number(a[0])
                if op in ("J", "j"):
                    value = _integer(a[0], maximum=2)
                if (op == "w" and value < 0) or (op == "M" and value < 1):
                    _fail("invalid stroke parameter")
                setattr(state, {"w": "width", "J": "cap", "j": "join", "M": "miter"}[op], value)
            elif op == "d":
                state.dash, state.phase = tuple(_number(v) for v in a[0]), _number(a[1])
                if state.dash and (any(v < 0 for v in state.dash) or not any(state.dash)):
                    _fail("invalid dash pattern")
            elif op == "gs":
                self.graphics_state(resources.ExtGState[a[0]], state)
            elif op == "Do":
                obj = resources.XObject[a[0]]
                if str(obj.get("/Subtype")) == "/Image":
                    node = self.image(obj, state)
                elif str(obj.get("/Subtype")) == "/Form":
                    node = self.form(obj, state, resources, depth)
                else:
                    _fail("XObject subtype", unsupported=True)
                if not state.empty_clip:
                    out.append(node)
            elif op == "BT":
                if pos.active:
                    _fail("nested text object")
                pos = _TextPosition(active=True)
            elif op == "ET":
                if not pos.active:
                    _fail("unbalanced text object")
                pos.active = False
            elif op == "Tf":
                state.font, state.size = str(a[0]), _number(a[1])
            elif op == "Tm":
                pos.matrix = pos.line = tuple(_number(v) for v in a)
            elif op in ("Td", "TD", "T*", "'", '"'):
                if op in ("'", '"'):
                    if op == '"':
                        state.tw, state.tc = _number(a[0]), _number(a[1])
                    tx, ty = 0, -state.leading
                elif op == "T*":
                    tx, ty = 0, -state.leading
                else:
                    tx, ty = _number(a[0]), _number(a[1])
                    if op == "TD":
                        state.leading = -ty
                pos.line = ir.compose(ir.translate(tx, ty), pos.line)
                pos.matrix = pos.line
                if op in ("'", '"'):
                    if not isinstance(a[-1], self.q.String):
                        _fail("text showing requires a string", op)
                    out.extend(self.show(bytes(a[-1]), state, pos, resources))
            elif op in ("Tc", "Tw", "TL", "Ts", "Tr", "Tz"):
                value = _number(a[0])
                setattr(
                    state,
                    {"Tc": "tc", "Tw": "tw", "TL": "leading", "Ts": "rise", "Tr": "tr", "Tz": "hz"}[
                        op
                    ],
                    value / 100 if op == "Tz" else value,
                )
            elif op in ("Tj", "TJ"):
                if op == "Tj" and not isinstance(a[0], self.q.String):
                    _fail("text showing requires a string", op)
                if op == "TJ" and not isinstance(a[0], self.q.Array):
                    _fail("TJ requires an array", op)
                values = [a[0]] if op == "Tj" else list(a[0])
                text_bytes = bytearray()
                for value in values:
                    if isinstance(value, self.q.String):
                        text_bytes.extend(bytes(value))
                    else:
                        shift = _number(value)
                        if shift == 0:
                            continue
                        if text_bytes:
                            out.extend(self.show(bytes(text_bytes), state, pos, resources))
                            text_bytes.clear()
                        advance = -shift * state.size * state.hz / 1000
                        pos.matrix = ir.compose(ir.translate(advance, 0), pos.matrix)
                if text_bytes:
                    out.extend(self.show(bytes(text_bytes), state, pos, resources))
        if stack or pending_clip is not None or pos.active:
            _fail("unbalanced content state")
        return out


def pdf_to_ir(
    data: bytes, *, page_index: int = 0, budget: PDFContentBudget | None = None, provider=None
) -> PDFContentResult:
    """Interpret a frozen PDF page into existing IR and frozen raster resources.

    No path access, execution, receipt, companion, or geometry authority is
    created. Native decoding must run under the caller's bounded child process.
    Unsupported operators fail with ``PDFContentError`` before any output write.
    Original glyphs are outlined unless an approved face recreates the exact
    embedded font-program bytes and its shaped Unicode selects the same GID.
    Native text positions remain explicit original PDF positions, not reshaped
    whole strings. Pass the same approved provider to the IR writers.
    """
    budget = PDFContentBudget() if budget is None else budget
    if not isinstance(budget, PDFContentBudget) or any(
        type(v) is not int or v <= 0 for v in vars(budget).values()
    ):
        raise ValueError("invalid PDF content budget")
    if not isinstance(data, bytes) or not data:
        raise PDFContentError("pdfcontent_invalid", "nonempty frozen PDF bytes required")
    if len(data) > budget.max_source_bytes:
        raise PDFContentError("pdfcontent_budget", "source_bytes")
    if type(page_index) is not int or page_index < 0:
        raise PDFContentError("pdfcontent_invalid", "invalid page index")
    require("pikepdf")
    import pikepdf as q

    try:
        with ExitStack() as stack:
            pdf = stack.enter_context(q.open(io.BytesIO(data)))
            if page_index >= len(pdf.pages):
                _fail("page index outside document")
            page = pdf.pages[page_index]
            if page.obj.get("/Group") is not None:
                _fail("page transparency group", unsupported=True)
            parser = _Interpreter(pdf, q, budget, stack, provider)
            nodes = parser.walk(page, page.obj.get("/Resources", q.Dictionary()))
            form = page.as_form_xobject(handle_transformations=True)
            bbox = tuple(_number(v) for v in form.BBox)
            fm = tuple(_number(v) for v in form.get("/Matrix", ir.IDENTITY))
            if len(bbox) != 4 or len(fm) != 6:
                _fail("invalid page geometry")
            x, y, width, height = placement.visible_box(bbox, fm)
            clip = ir.Path(rect_segments(bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1]))
            root = ir.Group(tuple(nodes), transform=ir.compose(fm, ir.translate(-x, -y)), clip=clip)
            model = ir.Page(width, height, (root,), resources=parser.resources, background=None)
            ir.validate(model)
            digest = hashlib.sha256(data).hexdigest()
            facts = {
                "profile": PROFILE,
                "source_sha256": digest,
                "page_index": page_index,
                "page_count": len(pdf.pages),
                "size_pt": [width, height],
                "text_policy": "text" if parser.text_glyphs else "outlines",
                "counts": {
                    **parser.counts,
                    "glyphs": parser.used["glyphs"],
                    "text_glyphs": parser.text_glyphs,
                    "outlined_glyphs": parser.outlined_glyphs,
                },
                "font_program_sha256": sorted(parser.font_hashes),
            }
            return PDFContentResult(model, dict(parser.files), digest, facts)
    except PDFContentError:
        raise
    except Exception as exc:  # noqa: BLE001 -- malformed native input must not leak parser-specific failures
        raise PDFContentError("pdfcontent_invalid", "malformed or unrepresented PDF data") from exc


__all__ = ["PROFILE", "PDFContentBudget", "PDFContentError", "PDFContentResult", "pdf_to_ir"]
