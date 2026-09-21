"""render_spike 的**受限自有 emitter**：pikepdf 做对象模型 / 序列化 / 外来页导入，fontTools 做子集，
HarfBuzz 做 shaping，内容流的操作符由本模块自己写。**不是通用 PDF 写入器，更不是 parser**——
它只会做 ADR 0055 点名的那几件事：导入一页外来 PDF（非对称页盒）并做变换 / clip、透明组
（整体 opacity）、矩形填充、可检索文字（真字体文件 → shaped glyph → 子集 → CID 编码 →
ToUnicode）。

坐标：全程 PDF 原生 pt、原点左下、y 向上；毫米 / 顶原点的画布语义到这里的换算是 U06 的合同，
本模块不替它决定。

字体程序两条路，其余格式**显式拒绝**（`UnsupportedFontFormat`）：
* TrueType（`glyf`）→ `/CIDFontType2` + `/FontFile2`，编码 Identity-H，code = 子集后的 GID，
  `/CIDToGIDMap /Identity`；
* CFF CID-keyed（`CFF ` + ROS）→ `/CIDFontType0` + `/FontFile3 /Subtype /CIDFontType0C`，
  code = 原始 CID（子集后 charset 保留 CID，PDF 阅读器经 charset 找 GID）；
* 变量字体（`fvar`）、彩色字体（`COLR` / `SVG ` / `CBDT` / `sbix`）、CFF2、非 CID 的 CFF、
  位图字体 → 拒绝。「支持所有字体」不是本轮承诺（RC-037）。

ToUnicode 按 HarfBuzz 的 cluster 写：一个 glyph 对应它所覆盖的那一段**原文**（连字 `fi` 一个
glyph → 两个码位），所以文本层抽回来的是用户输入的字，不是 glyph 名。
"""

from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
import uharfbuzz as hb
from fontTools import subset
from fontTools.ttLib import TTFont


class UnsupportedFontFormat(Exception):
    """本轮不支持的字体程序格式（显式边界，不静默换脸）。"""


class MissingGlyph(Exception):
    """字符在任何一张脸里都没有字形（spike 里当场失败；产品里由 glyphplan 报 missing 层）。"""


Matrix = tuple[float, float, float, float, float, float]


def mat_mul(m1: Matrix, m2: Matrix) -> Matrix:
    """行向量约定：先 m1 后 m2（p' = p·m1·m2）。"""
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


def mat_apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def translate(tx: float, ty: float) -> Matrix:
    return (1, 0, 0, 1, tx, ty)


def scale(sx: float, sy: float) -> Matrix:
    return (sx, 0, 0, sy, 0, 0)


def rotate_ccw(deg: float) -> Matrix:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return (c, s, -s, c, 0, 0)


def _num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


# ---------------------------------------------------------------------------
# 字体
# ---------------------------------------------------------------------------
@dataclass
class ShapedGlyph:
    gid: int
    text: str  # 该 glyph 覆盖的原文（cluster）
    x_advance: int  # 字体单位
    x_offset: int
    y_offset: int


class FontFace:
    """一个真实字体文件：探测格式、HarfBuzz shaping、记录用过的 glyph，最后子集嵌入。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.tt = TTFont(str(self.path))
        tables = set(self.tt.keys())
        if "fvar" in tables:
            raise UnsupportedFontFormat(f"{self.path.name}: 变量字体（fvar）本轮不支持")
        if tables & {"COLR", "SVG ", "CBDT", "sbix"}:
            raise UnsupportedFontFormat(f"{self.path.name}: 彩色字体本轮不支持")
        if "CFF2" in tables:
            raise UnsupportedFontFormat(f"{self.path.name}: CFF2 本轮不支持")
        if "glyf" in tables:
            self.kind = "truetype"
        elif "CFF " in tables:
            top = self.tt["CFF "].cff.topDictIndex[0]
            if not hasattr(top, "ROS"):
                raise UnsupportedFontFormat(f"{self.path.name}: 非 CID-keyed 的 CFF 本轮不支持")
            self.kind = "cff-cid"
        else:
            raise UnsupportedFontFormat(f"{self.path.name}: 既无 glyf 也无 CFF（位图字体？）")
        self.upem: int = self.tt["head"].unitsPerEm
        self.cmap: dict[int, str] = self.tt.getBestCmap() or {}
        self.glyph_order: list[str] = list(self.tt.getGlyphOrder())
        self.hmtx = self.tt["hmtx"].metrics
        blob = hb.Blob.from_file_path(str(self.path))
        self.hb_face = hb.Face(blob)
        self.hb_font = hb.Font(self.hb_face)
        self.hb_font.scale = (self.upem, self.upem)
        self.used: dict[int, str] = {}  # gid → 首次见到的 cluster 原文
        self.postscript_name = self.tt["name"].getDebugName(6) or self.path.stem
        self.family_name = self.tt["name"].getDebugName(1) or self.path.stem

    # -- 覆盖 / shaping ----------------------------------------------------
    def covers(self, ch: str) -> bool:
        return ord(ch) in self.cmap

    def shape(self, text: str, features: dict | None = None) -> list[ShapedGlyph]:
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf, features or {})
        infos, positions = buf.glyph_infos, buf.glyph_positions
        # cluster → 原文片段：同一 cluster 的 glyph 共享这一段（连字 = 多个码位）；
        # 反过来一个 cluster 也可能出多个 glyph（组合标记），此时原文只记在第一个 glyph 上，
        # 其余 glyph 的 ToUnicode 为空串——文本层不重复吐字。
        clusters = sorted({i.cluster for i in infos})
        bounds = {
            c: (c, clusters[k + 1] if k + 1 < len(clusters) else len(text))
            for k, c in enumerate(clusters)
        }
        seen: set[int] = set()
        out: list[ShapedGlyph] = []
        for info, pos in zip(infos, positions):
            start, end = bounds[info.cluster]
            piece = text[start:end] if info.cluster not in seen else ""
            seen.add(info.cluster)
            out.append(
                ShapedGlyph(info.codepoint, piece, pos.x_advance, pos.x_offset, pos.y_offset)
            )
            if info.codepoint not in self.used and piece:
                self.used[info.codepoint] = piece
            elif info.codepoint not in self.used:
                self.used[info.codepoint] = ""
        return out

    def advance_units(self, gid: int) -> int:
        return self.hmtx[self.glyph_order[gid]][0]

    def width_1000(self, gid: int) -> float:
        return self.advance_units(gid) * 1000.0 / self.upem

    def cid_of(self, gid: int) -> int:
        """CFF CID-keyed：glyph 名 `cidNNNNN` 就是 CID；.notdef 是 0。"""
        name = self.glyph_order[gid]
        if name == ".notdef":
            return 0
        assert name.startswith("cid"), name
        return int(name[3:])

    # -- 子集与嵌入 ---------------------------------------------------------
    def _subset(self) -> tuple[TTFont, dict[int, int]]:
        """返回 (子集后的字体, 原 GID → 子集后 GID)。子集在一个**新加载**的 TTFont 上做，
        self.tt 保持原样（cmap / hmtx / glyph 名都还按原字体查）。"""
        gids = sorted(set(self.used) | {0})
        opts = subset.Options()
        opts.retain_gids = False
        opts.notdef_outline = True
        opts.notdef_glyph = True
        opts.glyph_names = False
        opts.layout_features = []
        opts.hinting = False
        opts.desubroutinize = True
        opts.recalc_bounds = False
        opts.recalc_timestamp = False
        opts.name_IDs = [0, 1, 2, 3, 4, 5, 6]
        opts.drop_tables += [
            "GSUB",
            "GPOS",
            "GDEF",
            "BASE",
            "JSTF",
            "MATH",
            "DSIG",
            "kern",
            "vhea",
            "vmtx",
            "VORG",
            "hdmx",
            "LTSH",
            "VDMX",
            "gasp",
            "STAT",
            "meta",
        ]
        # recalcTimestamp=False：否则 save() 会把「现在」写进 head.modified，同一输入两次跑出的
        # 字体程序字节不同，evidence 的 hash 就不可复现
        fresh = TTFont(str(self.path), recalcTimestamp=False)
        s = subset.Subsetter(opts)
        s.populate(gids=gids)
        s.subset(fresh)
        new_order = list(fresh.getGlyphOrder())
        index = {name: i for i, name in enumerate(new_order)}
        remap = {gid: index[self.glyph_order[gid]] for gid in gids}
        return fresh, remap

    def code_of(self, gid: int, remap: dict[int, int]) -> int:
        """内容流里写的 2 字节 code：TrueType = 子集后 GID；CFF CID-keyed = 原 CID。"""
        return remap[gid] if self.kind == "truetype" else self.cid_of(gid)

    def build_pdf_font(self, pdf: pikepdf.Pdf) -> tuple[pikepdf.Object, dict[int, int], dict]:
        """子集 → 嵌入 → Type0 字体字典。返回 (字体对象, 原 GID → 子集 GID, 事实)。"""
        sub, remap = self._subset()
        tag = _subset_tag(self.used)
        base = f"{tag}+{self.postscript_name}"
        descriptor = self._descriptor(pdf, sub, base)
        # W 数组按 code 排
        widths: dict[int, float] = {}
        for gid in self.used:
            widths[self.code_of(gid, remap)] = self.width_1000(gid)
        w_array = []
        for code in sorted(widths):
            w_array += [code, [round(widths[code], 3)]]
        cid_dict: dict = {
            "/Type": pikepdf.Name.Font,
            "/BaseFont": pikepdf.Name("/" + base),
            "/CIDSystemInfo": pikepdf.Dictionary(
                Registry=pikepdf.String("Adobe"), Ordering=pikepdf.String("Identity"), Supplement=0
            ),
            "/FontDescriptor": descriptor,
            "/DW": 1000,
            "/W": pikepdf.Array(w_array),
        }
        if self.kind == "truetype":
            cid_dict["/Subtype"] = pikepdf.Name.CIDFontType2
            cid_dict["/CIDToGIDMap"] = pikepdf.Name.Identity
        else:
            cid_dict["/Subtype"] = pikepdf.Name.CIDFontType0
        cid_font = pdf.make_indirect(pikepdf.Dictionary(cid_dict))
        tounicode = pdf.make_indirect(
            pikepdf.Stream(
                pdf, self._tounicode({self.code_of(g, remap): t for g, t in self.used.items()})
            )
        )
        type0 = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type0,
                BaseFont=pikepdf.Name("/" + base),
                Encoding=pikepdf.Name("/Identity-H"),
                DescendantFonts=pikepdf.Array([cid_font]),
                ToUnicode=tounicode,
            )
        )
        facts = {
            "file": self.path.name,
            "kind": self.kind,
            "base_font": base,
            "glyphs_in_source": len(self.glyph_order),
            "glyphs_in_subset": len(sub.getGlyphOrder()),
            "used_glyphs": len(self.used),
            "font_program_bytes": len(descriptor[self._fontfile_key()].read_raw_bytes()),
        }
        return type0, remap, facts

    def _fontfile_key(self) -> str:
        return "/FontFile2" if self.kind == "truetype" else "/FontFile3"

    def _descriptor(self, pdf: pikepdf.Pdf, sub: TTFont, base: str) -> pikepdf.Object:
        head, os2, post = sub["head"], sub["OS/2"], sub["post"]
        k = 1000.0 / self.upem
        flags = 4  # Symbolic（CID 字体一律走 Identity 编码，不声称 Nonsymbolic）
        if self.tt["name"].getDebugName(1) and "Serif" in (self.tt["name"].getDebugName(1) or ""):
            flags |= 2
        if post.italicAngle:
            flags |= 64
        d: dict = {
            "/Type": pikepdf.Name.FontDescriptor,
            "/FontName": pikepdf.Name("/" + base),
            "/Flags": flags,
            "/FontBBox": pikepdf.Array(
                [
                    round(head.xMin * k),
                    round(head.yMin * k),
                    round(head.xMax * k),
                    round(head.yMax * k),
                ]
            ),
            "/ItalicAngle": float(post.italicAngle),
            "/Ascent": round(os2.sTypoAscender * k),
            "/Descent": round(os2.sTypoDescender * k),
            "/CapHeight": round(getattr(os2, "sCapHeight", 0) * k) or round(os2.sTypoAscender * k),
            "/StemV": 80,
        }
        if self.kind == "truetype":
            buf = io.BytesIO()
            sub.save(buf)
            data = buf.getvalue()
            d["/FontFile2"] = pdf.make_indirect(pikepdf.Stream(pdf, data, Length1=len(data)))
        else:
            data = sub["CFF "].compile(sub)
            d["/FontFile3"] = pdf.make_indirect(
                pikepdf.Stream(pdf, data, Subtype=pikepdf.Name.CIDFontType0C)
            )
        return pdf.make_indirect(pikepdf.Dictionary(d))

    @staticmethod
    def _tounicode(mapping: dict[int, str]) -> bytes:
        lines = [
            "/CIDInit /ProcSet findresource begin",
            "12 dict begin",
            "begincmap",
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
            "/CMapName /Adobe-Identity-UCS def",
            "/CMapType 2 def",
            "1 begincodespacerange",
            "<0000> <FFFF>",
            "endcodespacerange",
        ]
        entries = [(c, t) for c, t in sorted(mapping.items()) if t]
        for i in range(0, len(entries), 100):
            chunk = entries[i : i + 100]
            lines.append(f"{len(chunk)} beginbfchar")
            for code, text in chunk:
                lines.append(f"<{code:04X}> <{text.encode('utf-16-be').hex().upper()}>")
            lines.append("endbfchar")
        lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
        return ("\n".join(lines) + "\n").encode("ascii")


def _subset_tag(used: dict[int, str]) -> str:
    """六个大写字母的子集前缀，由用过的 glyph 集合派生（同集合同前缀，方便对拍）。"""
    h = hashlib.sha256(",".join(str(g) for g in sorted(used)).encode()).digest()
    return "".join(chr(ord("A") + b % 26) for b in h[:6])


# ---------------------------------------------------------------------------
# 文档
# ---------------------------------------------------------------------------
@dataclass
class TextRun:
    face: FontFace
    glyphs: list[ShapedGlyph]
    size: float
    rise: float


@dataclass
class TextOp:
    x: float
    y: float
    runs: list[TextRun]
    rgb: tuple[float, float, float]


@dataclass
class _Recorder:
    """一段内容流 + 它自己的资源（页面与每个透明组各一份）。"""

    ops: list[str | TextOp] = field(default_factory=list)
    xobjects: dict[str, pikepdf.Object] = field(default_factory=dict)
    extgstates: dict[str, pikepdf.Object] = field(default_factory=dict)


class Document:
    def __init__(self, width_pt: float, height_pt: float):
        self.width, self.height = float(width_pt), float(height_pt)
        self.pdf = pikepdf.new()
        self.page = _Recorder()
        self._cur = self.page
        self._n = 0
        self.fonts: dict[int, FontFace] = {}
        self.facts: dict = {
            "page": [self.width, self.height],
            "imports": [],
            "groups": [],
            "text": [],
        }

    def _name(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def _gs(self, opacity: float) -> str:
        key = f"GSa{int(round(opacity * 100)):03d}"
        if key not in self._cur.extgstates:
            self._cur.extgstates[key] = self.pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.ExtGState, ca=float(opacity), CA=float(opacity)
                )
            )
        return key

    # -- 图形 -----------------------------------------------------------
    def fill_rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        rgb: tuple[float, float, float],
        opacity: float = 1.0,
    ) -> None:
        ops = ["q"]
        if opacity < 1.0:
            ops.append(f"/{self._gs(opacity)} gs")
        ops.append(
            f"{_num(rgb[0])} {_num(rgb[1])} {_num(rgb[2])} rg {_num(x)} {_num(y)} {_num(w)} {_num(h)} re f"
        )
        ops.append("Q")
        self._cur.ops.append(" ".join(ops))

    def group(self, bbox: tuple[float, float, float, float], opacity: float, draw) -> dict:
        """透明组：`draw(doc)` 里画的东西进一个带 /Group 的 Form XObject，整组以 `opacity` 合成。

        组内的 alpha 从 1.0 起算（PDF 32000 §11.6.6），所以组内重叠不会被二次压暗——这正是
        旧后端在 opacity<1 时只能退到位图的那件事。"""
        outer = self._cur
        rec = _Recorder()
        self._cur = rec
        try:
            draw(self)
        finally:
            self._cur = outer
        form = self._form(rec, bbox, transparency_group=True)
        name = self._name("Grp")
        outer.xobjects[name] = form
        outer.ops.append(f"q /{self._gs(opacity)} gs /{name} Do Q")
        fact = {"name": name, "bbox": list(bbox), "opacity": opacity, "transparency_group": True}
        self.facts["groups"].append(fact)
        return fact

    def import_page(
        self,
        src_path: Path,
        rect: tuple[float, float, float, float],
        *,
        rotate_cw_deg: float = 0.0,
        crop: tuple[float, float, float, float] | None = None,
        opacity: float = 1.0,
    ) -> dict:
        """把外来 PDF 第一页当 Form XObject 画进 `rect`（x, y, w, h，pt，y 向上）。

        * 可见框由 qpdf 定（TrimBox → CropBox → MediaBox），非对称页盒因此天然正确；
        * `crop` 是归一化 (x, y, w, h)、顶原点、相对可见框——与旧后端 `_crop_clip` 同一约定；
          有 crop 时**裁剪后的那块**映射到 `rect`，并在源空间下 `re W n`；
        * `rotate_cw_deg`：绕 `rect` 中心顺时针（CSS 语义，PDF 空间取负），任意角度；
        * `opacity < 1` 时整页包成透明组再画（矢量，不退位图）。
        """
        with pikepdf.open(str(src_path)) as src:
            form = self.pdf.copy_foreign(src.pages[0].as_form_xobject())
        bx0, by0, bx1, by1 = (float(v) for v in form.BBox)
        bw, bh = bx1 - bx0, by1 - by0
        if crop:
            cx, cy, cw, ch = crop
            sx0, sy1 = bx0 + cx * bw, by1 - cy * bh
            sw, sh = cw * bw, ch * bh
            sy0 = sy1 - sh
        else:
            sx0, sy0, sw, sh = bx0, by0, bw, bh
        tx, ty, tw, th = rect
        m = mat_mul(translate(-sx0, -sy0), scale(tw / sw, th / sh))
        m = mat_mul(m, translate(tx, ty))
        if rotate_cw_deg:
            cx_, cy_ = tx + tw / 2, ty + th / 2
            m = mat_mul(
                m,
                mat_mul(
                    translate(-cx_, -cy_), mat_mul(rotate_ccw(-rotate_cw_deg), translate(cx_, cy_))
                ),
            )
        name = self._name("Fm")
        inner = [
            "q",
            " ".join(_num(v) for v in m) + " cm",
            f"{_num(sx0)} {_num(sy0)} {_num(sw)} {_num(sh)} re W n",
            f"/{name} Do",
            "Q",
        ]
        fact = {
            "name": name,
            "source": Path(src_path).name,
            "bbox": [bx0, by0, bx1, by1],
            "source_rect": [sx0, sy0, sw, sh],
            "target_rect": list(rect),
            "rotate_cw_deg": rotate_cw_deg,
            "crop": list(crop) if crop else None,
            "matrix": list(m),
            "opacity": opacity,
        }
        if opacity < 1.0:
            rec = _Recorder()
            rec.xobjects[name] = form
            rec.ops.append(" ".join(inner))
            corners = [mat_apply(m, sx0 + dx, sy0 + dy) for dx in (0, sw) for dy in (0, sh)]
            gb = (
                min(p[0] for p in corners),
                min(p[1] for p in corners),
                max(p[0] for p in corners),
                max(p[1] for p in corners),
            )
            wrap = self._form(rec, gb, transparency_group=True)
            wname = self._name("Grp")
            self._cur.xobjects[wname] = wrap
            self._cur.ops.append(f"q /{self._gs(opacity)} gs /{wname} Do Q")
            fact["group"] = wname
        else:
            self._cur.xobjects[name] = form
            self._cur.ops.append(" ".join(inner))
        self.facts["imports"].append(fact)
        return fact

    # -- 文字 -----------------------------------------------------------
    def text(
        self,
        x: float,
        y: float,
        size: float,
        pieces: list[tuple[str, FontFace | None, FontFace, str]],
        rgb: tuple[float, float, float] = (0, 0, 0),
    ) -> dict:
        """`pieces` = [(文本, 主脸或 None, CJK 脸, script)]，script ∈ {"", "sup", "sub"}。

        每个 piece 先按「主脸覆盖 → 主脸；否则 CJK 脸覆盖 → CJK；否则 MissingGlyph」逐字归属、
        合并成段，再各自 shaping。上下标 = 字号 × SCRIPT_SIZE、基线 ± 正文字号 × 比例
        （与 `tavotto.richtext` 同一组常量，由调用方传进 script 与比例）。"""
        from tavotto.richtext import SCRIPT_SIZE, SUB_DROP, SUP_RISE

        runs: list[TextRun] = []
        pen = 0.0
        for text, primary, cjk, script in pieces:
            if not text:
                continue
            fsize = size * SCRIPT_SIZE if script else size
            rise = (
                size * SUP_RISE
                if script == "sup"
                else (-size * SUB_DROP if script == "sub" else 0.0)
            )
            for seg_text, face in _itemize(text, primary, cjk):
                glyphs = face.shape(seg_text)
                self.fonts[id(face)] = face
                runs.append(TextRun(face, glyphs, fsize, rise))
                pen += sum(g.x_advance for g in glyphs) * fsize / face.upem
        op = TextOp(x, y, runs, rgb)
        self._cur.ops.append(op)
        fact = {
            "x": x,
            "y": y,
            "size": size,
            "advance_pt": pen,
            "text": "".join(p[0] for p in pieces),
            "faces": sorted({r.face.postscript_name for r in runs}),
        }
        self.facts["text"].append(fact)
        return fact

    # -- 序列化 -----------------------------------------------------------
    def _form(
        self, rec: _Recorder, bbox: tuple[float, float, float, float], *, transparency_group: bool
    ) -> pikepdf.Object:
        assert not any(isinstance(o, TextOp) for o in rec.ops), "spike 的透明组里不放文字"
        content = ("\n".join(o for o in rec.ops if isinstance(o, str)) + "\n").encode("latin-1")
        stream = pikepdf.Stream(self.pdf, content)
        stream["/Type"] = pikepdf.Name.XObject
        stream["/Subtype"] = pikepdf.Name.Form
        stream["/BBox"] = pikepdf.Array([float(v) for v in bbox])
        stream["/Resources"] = self._resources(rec, fonts=None)
        if transparency_group:
            stream["/Group"] = pikepdf.Dictionary(
                S=pikepdf.Name.Transparency, CS=pikepdf.Name.DeviceRGB, I=True, K=False
            )
        return self.pdf.make_indirect(stream)

    def _resources(
        self, rec: _Recorder, fonts: dict[str, pikepdf.Object] | None
    ) -> pikepdf.Dictionary:
        res = pikepdf.Dictionary()
        if rec.xobjects:
            res["/XObject"] = pikepdf.Dictionary({"/" + k: v for k, v in rec.xobjects.items()})
        if rec.extgstates:
            res["/ExtGState"] = pikepdf.Dictionary({"/" + k: v for k, v in rec.extgstates.items()})
        if fonts:
            res["/Font"] = pikepdf.Dictionary({"/" + k: v for k, v in fonts.items()})
        return res

    def save(self, path: Path) -> dict:
        font_objs: dict[str, pikepdf.Object] = {}
        remaps: dict[int, dict[int, int]] = {}
        font_names: dict[int, str] = {}
        font_facts = []
        for i, (key, face) in enumerate(
            sorted(self.fonts.items(), key=lambda kv: kv[1].postscript_name)
        ):
            obj, remap, facts = face.build_pdf_font(self.pdf)
            name = f"F{i + 1}"
            font_objs[name] = obj
            remaps[key] = remap
            font_names[key] = name
            facts["resource"] = name
            font_facts.append(facts)
        lines: list[str] = []
        for op in self.page.ops:
            if isinstance(op, str):
                lines.append(op)
                continue
            lines.append(self._text_ops(op, font_names, remaps))
        content = ("\n".join(lines) + "\n").encode("latin-1")
        page = pikepdf.Dictionary(
            Type=pikepdf.Name.Page,
            MediaBox=pikepdf.Array([0, 0, self.width, self.height]),
            Resources=self._resources(self.page, font_objs),
            Contents=self.pdf.make_indirect(pikepdf.Stream(self.pdf, content)),
        )
        self.pdf.pages.append(pikepdf.Page(page))
        self.pdf.save(
            str(path),
            object_stream_mode=pikepdf.ObjectStreamMode.disable,
            compress_streams=True,
            deterministic_id=True,
            min_version="1.5",
        )
        self.facts["fonts"] = font_facts
        return self.facts

    @staticmethod
    def _text_ops(op: TextOp, names: dict[int, str], remaps: dict[int, dict[int, int]]) -> str:
        out = [
            "BT",
            f"{_num(op.rgb[0])} {_num(op.rgb[1])} {_num(op.rgb[2])} rg",
            f"1 0 0 1 {_num(op.x)} {_num(op.y)} Tm",
        ]
        pen = 0.0  # 相对 Tm 原点的笔位置（pt）
        for run in op.runs:
            face, key = run.face, id(run.face)
            out.append(f"/{names[key]} {_num(run.size)} Tf {_num(run.rise)} Ts")
            # 每段 run 用绝对 Td 定位到当前笔位置（rise 由 Ts 管）
            out.append(f"1 0 0 1 {_num(op.x + pen)} {_num(op.y)} Tm")
            tj: list[str] = []
            k = 1000.0 / face.upem
            for g in run.glyphs:
                code = face.code_of(g.gid, remaps[key])
                if g.x_offset:
                    tj.append(_num(-g.x_offset * k))
                tj.append(f"<{code:04X}>")
                # 笔的净位移要等于 HarfBuzz 的 x_advance：glyph 前先挪了 x_offset，
                # PDF 自己按 W（hmtx）走，剩下的差用正数（左移）补回来。
                adj = (face.advance_units(g.gid) + g.x_offset - g.x_advance) * k
                if abs(adj) > 0.01:
                    tj.append(_num(adj))
                pen += g.x_advance * run.size / face.upem
            out.append("[" + " ".join(tj) + "] TJ")
        out.append("ET")
        return "\n".join(out)


def _itemize(text: str, primary: FontFace | None, cjk: FontFace) -> list[tuple[str, FontFace]]:
    """逐字归属再合段：主脸 → CJK 脸 → MissingGlyph。空白跟着前一个字走。"""
    segs: list[tuple[str, FontFace]] = []
    for ch in text:
        if ch.isspace() and segs:
            segs[-1] = (segs[-1][0] + ch, segs[-1][1])
            continue
        if primary is not None and primary.covers(ch):
            face = primary
        elif cjk.covers(ch):
            face = cjk
        elif ch.isspace() and primary is not None:
            face = primary
        else:
            raise MissingGlyph(f"U+{ord(ch):04X} {ch!r} 在主脸与 CJK 脸里都没有字形")
        if segs and segs[-1][1] is face:
            segs[-1] = (segs[-1][0] + ch, face)
        else:
            segs.append((ch, face))
    return segs
