"""Typography —— 画布文字从字符串到**已排字形**只走这一条路（统一实施包 U06，ADR 0059 / 0060）。

旧 facade 在 `pymupdf_backend._draw_text` 里把「分层 → 量宽 → 换行 → 落笔」写在一起，
量宽问 `Font.text_length`、落笔交给 `TextWriter.append`——两者恰好同源只是 PyMuPDF 的
实现事实。这里把它拆成两层，**同一份 shaped plan 既用来量也用来画**（RC-030）：

* `Face`（协议）：一张脸能回答三件事——覆盖（`covers`）、排版（`shape`：HarfBuzz 的
  glyph / cluster / advance / offset）、度量（`advance` / `ascender` / `descender`）。
  真实实现在 `hbshaper`（uharfbuzz + fontTools，native 适配层）；测试用一张合成的
  假脸就能把本模块跑完（纯模型不需要候选包）。
* `FaceSet`：正文脸 + 唯一一张 CJK 脸。**没有 fallback 脸**——旧后端的第 3 层
  （PyMuPDF 自己挑 Noto Serif）在这条路上不存在，`coverage()` 的 fallback oracle 恒 False，
  于是 `glyphplan.layer_of` 只会给出 primary / cjk / missing 三档：画不出的字就是
  `missing`，进问题系统，**不暗中回退系统脸**（RC-037、任务书「本轮集合外明示限制」）。

换行 / 对齐 / 行高 / 上下标 / 下划线的**用户合同**逐条从 `_draw_text` 搬过来（贪心按
单元换行、CJK 逐字、拉丁按词、单词超宽逐字兜底、CSS 行盒基线、下划线画在正文基线、
上下标比例走 `richtext`），前端 TextView 与它是同一套语义。会变的只有 D07 批准的那几项：
脸换了（Liberation / Noto Sans SC），asc / desc 换成新脸的 OS/2 typo 度量。

坐标：本模块在**页面 y 向下的 pt 空间**里算（与画布语义同向，公式照抄不用翻），
`plan.compile_page()` 把每个基线点翻到 y 向上时只翻一次。

纯标准库。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .. import glyphplan, richtext
from .ir import FontResource, Glyph, mm2pt

#: 画布文字能选的字体族。**闭集，顺序即默认序**——与 `web/src/lib/typography.ts` 的
#: `CANVAS_TEXT_FAMILIES` 和旧 facade 同名常量逐字相同（`test_typography_families.py` 钉着
#: 闭集 + 顺序）。它是一句能力承诺：三个族 × 四态由批准字体集合（ADR 0060）兑现。
CANVAS_TEXT_FAMILIES = ("serif", "sans-serif", "monospace")
CANVAS_TEXT_DEFAULT_FAMILY = CANVAS_TEXT_FAMILIES[0]

#: 覆盖表枚举到哪（与旧 facade 同一个数：BMP + SMP）。
COVERAGE_MAX_CP = 0x30000

#: 换行单元的判据（历史判据，只管「逐字还是按词」，不是覆盖判据；见 glyphplan）。
_CJK_BREAK = 0x2E80


@runtime_checkable
class Face(Protocol):
    """一张可排版的脸。`resource` 是它进 IR 的身份。"""

    resource: FontResource
    #: em 单位的上升 / 下降（下降为负），OS/2 sTypoAscender / sTypoDescender ÷ unitsPerEm。
    ascender: float
    descender: float

    def covers(self, cp: int) -> bool: ...

    def shape(self, text: str) -> tuple[Glyph, ...]: ...

    def advance(self, gid: int) -> int: ...


@dataclass(frozen=True)
class FaceSet:
    primary: Face
    cjk: Face | None

    def face_of(self, layer: str) -> Face:
        """哪一层交给哪张脸落笔。`missing` 仍交给正文脸：它画出的是 .notdef（方框），
        而 ToUnicode 里那个 code 仍回原字——画面诚实、文本层也诚实。"""
        if layer == "cjk" and self.cjk is not None:
            return self.cjk
        return self.primary


def latin_family(name: object) -> str:
    """任意取值 → 闭集里的一个族。认不出来的一律回默认（衬线）——不把不认识的名字
    当「用户指定的字体」去解析（与旧 facade `latin_family` 同一条合同）。"""
    return name if name in CANVAS_TEXT_FAMILIES else CANVAS_TEXT_DEFAULT_FAMILY


class FaceProvider(Protocol):
    """族 / 粗 / 斜 → 脸。真实实现是 `hbshaper.HbFaceProvider`（读批准字体目录）。"""

    def face_for(self, family: str, bold: bool, italic: bool) -> Face: ...

    def cjk_face(self) -> Face | None: ...


def faces_for(provider: FaceProvider, family: object, bold: bool, italic: bool) -> FaceSet:
    return FaceSet(
        provider.face_for(latin_family(family), bool(bold), bool(italic)), provider.cjk_face()
    )


# ---------------------------------------------------------------------------
# 覆盖 / 分层 / 量宽
# ---------------------------------------------------------------------------
def coverage(faces: FaceSet) -> glyphplan.Coverage:
    """三层 oracle。**fallback 恒 False**（本轮没有隐式回退脸）。"""
    cjk = faces.cjk
    return glyphplan.Coverage(
        primary=faces.primary.covers,
        cjk=(cjk.covers if cjk is not None else (lambda cp: False)),
        fallback=lambda cp: False,
    )


def text_plan(s: str, faces: FaceSet) -> list[tuple[str, str]]:
    return [(r.text, r.layer) for r in glyphplan.plan(s, coverage(faces))]


def missing_glyphs(s: str, faces: FaceSet) -> list[str]:
    return glyphplan.missing_chars(s, coverage(faces))


def coverage_ranges(faces: FaceSet, hi: int = COVERAGE_MAX_CP) -> dict:
    """与旧 facade `coverage_ranges()` 同形的三层区间表（生成 `canvas_coverage.json` 用）。
    `fallback` 恒为空表——这不是漏写，是本轮的能力边界。"""
    cov = coverage(faces)
    lo = 0x20
    return {
        "primary": glyphplan.ranges_of(cov.primary, lo, hi),
        "cjk": glyphplan.ranges_of(cov.cjk, lo, hi),
        "fallback": [],
    }


@dataclass(frozen=True)
class ShapedPiece:
    """一段同脸、同字号、同基线的已排字形。`logical` 是这段的**用户原文**：合成上下标时
    画的是 `5`、用户写的是 `⁵`，两者不同就要靠 ActualText 把文本层还给用户（RC-036）。"""

    face: Face
    layer: str
    text: str
    glyphs: tuple[Glyph, ...]
    size: float
    rise: float
    logical: str = ""

    @property
    def actual_text(self) -> str | None:
        return self.logical if self.logical and self.logical != self.text else None

    @property
    def width(self) -> float:
        upem = self.face.resource.units_per_em
        return sum(g.x_advance for g in self.glyphs) * self.size / upem


def shape_segment(
    text: str, faces: FaceSet, size: float, rise: float = 0.0, logical: str | None = None
) -> list[ShapedPiece]:
    """一段文字（同一 script）→ 按分层切开、各自 shaping。**量宽与落笔都读这个结果。**
    `logical`（与 `text` 逐字符对齐的用户原文）随分段一起切。"""
    out: list[ShapedPiece] = []
    logical = text if logical is None else logical
    pos = 0
    for seg, layer in text_plan(text, faces):
        face = faces.face_of(layer)
        out.append(
            ShapedPiece(
                face, layer, seg, face.shape(seg), size, rise, logical[pos : pos + len(seg)]
            )
        )
        pos += len(seg)
    return out


def mixed_width(text: str, faces: FaceSet, size: float) -> float:
    return sum(p.width for p in shape_segment(text, faces, size))


def text_width(s: str, size_pt: float, faces: FaceSet) -> float:
    """中英混排字符串宽度（pt）——与旧 facade `text_width` 同一签名语义
    （族由 `faces` 决定；量宽用的族 == 落笔用的族）。"""
    return mixed_width(s, faces, float(size_pt))


# ---------------------------------------------------------------------------
# 换行与排版（y 向下的页面 pt 空间）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LaidOutLine:
    x: float  # 行起点（pt，页面 y 向下空间）
    baseline: float  # 基线 y（pt，y 向下）
    width: float
    pieces: tuple[ShapedPiece, ...]
    #: 这一行**用户写的**原文（含 Unicode 上下标字符，不含 `^{}` 标记）——ActualText 的依据。
    logical: str


@dataclass(frozen=True)
class TextBlock:
    lines: tuple[LaidOutLine, ...]
    #: 对象框（pt，y 向下）：x, y, w, h
    box: tuple[float, float, float, float]
    size: float
    color: str
    underline: bool
    #: 这段文字里画不出来的字符（去重、原文顺序）——按**渲染表示**量（合成过上下标之后）。
    missing: tuple[str, ...]
    #: 落在 cjk 层的字符（换族不换它；不算换脸）。
    cjk_chars: tuple[str, ...]


#: 换行单元里的一片：(渲染文字, script, 用户原文)。渲染文字与用户原文**逐字符对齐**
#: （`richtext.interpret_runs` 的合成是单字符 → 单字符的表查，长度不变）。
Unit = list[tuple[str, str, str]]


def _unit_w(u: Unit, faces: FaceSet, size: float) -> float:
    return sum(mixed_width(seg, faces, richtext.run_metrics(sc, size)[0]) for seg, sc, _ in u)


def _rstrip(u: Unit) -> Unit:
    out = list(u)
    while out and not out[-1][0].rstrip():
        out.pop()
    if out:
        seg, sc, logical = out[-1]
        stripped = seg.rstrip()
        cut = len(seg) - len(stripped)
        out[-1] = (stripped, sc, logical[: len(logical) - cut] if cut else logical)
    return out


def _chars_of(u: Unit) -> list[Unit]:
    """把一个换行单元拆成逐字符的单元；script 标记与原文跟着每个字符走。"""
    return [[(ch, sc, lch)] for seg, sc, logical in u for ch, lch in zip(seg, logical)]


def interpreted_pieces(
    raw: str, cov: glyphplan.Coverage, interpretation: str
) -> list[tuple[str, str, str]]:
    """一行原文 → [(渲染文字, script, 用户原文)]。合成上下标（`⁵` → 上标 `5`）的那一段
    `用户原文 != 渲染文字`；用户自己写的 `^{5}` 两者相同（括号里就是他打的字）。"""
    out: list[tuple[str, str, str]] = []
    for run in richtext.parse_runs(raw):
        pos = 0
        for r in richtext.interpret_runs(
            [run],
            is_primary=cov.primary,
            is_drawable=lambda cp: glyphplan.layer_of(cp, cov) != "missing",
            mode=interpretation,
        ):
            logical = run.text[pos : pos + len(r.text)] if not run.script else r.text
            assert len(logical) == len(r.text), (run, r)
            out.append((r.text, r.script, logical))
            pos += len(r.text)
    return out


def break_lines(
    text: str, faces: FaceSet, size: float, box_w: float, interpretation: str
) -> list[Unit]:
    """贪心换行——与旧 facade `_draw_text` 逐句相同：行内标记先解析成片段，Unicode 上下标
    按 `richtext.interpret_runs` 受控解释；CJK 逐字、拉丁按词（空格附着前词）；单个单元
    自己就超宽时先独占一行再逐字断。返回每行的片段序列（已 rstrip）。"""
    cov = coverage(faces)
    lines: list[Unit] = []
    for raw in text.split("\n"):
        units: list[Unit] = []
        cur: Unit = []

        def _push_char(ch: str, sc: str, lch: str) -> None:
            if cur and cur[-1][1] == sc:
                cur[-1] = (cur[-1][0] + ch, sc, cur[-1][2] + lch)
            else:
                cur.append((ch, sc, lch))

        for text_, script, logical in interpreted_pieces(raw, cov, interpretation):
            for ch, lch in zip(text_, logical):
                if ord(ch) > _CJK_BREAK:
                    if cur:
                        units.append(cur)
                        cur = []
                    units.append([(ch, script, lch)])
                else:
                    _push_char(ch, script, lch)
                    if ch == " ":
                        units.append(cur)
                        cur = []
        if cur:
            units.append(cur)

        line: Unit = []
        for u in units:
            if len(_chars_of(u)) > 1 and _unit_w(_rstrip(u), faces, size) > box_w:
                if line:
                    lines.append(_rstrip(line))
                    line = []
                for cu in _chars_of(u):
                    cand = line + cu
                    if line and _unit_w(_rstrip(cand), faces, size) > box_w:
                        lines.append(_rstrip(line))
                        line = list(cu)
                    else:
                        line = cand
                continue
            cand = line + u
            if line and _unit_w(_rstrip(cand), faces, size) > box_w:
                lines.append(_rstrip(line))
                line = list(u)
            else:
                line = cand
        lines.append(_rstrip(line))
    return lines


def _same_run(a: ShapedPiece, b: ShapedPiece) -> bool:
    return a.face is b.face and a.size == b.size and a.rise == b.rise and a.layer == b.layer


def layout_text(t: dict, faces: FaceSet) -> TextBlock | None:
    """画布文字对象（`text` / `size_pt` / `x_mm` … 与旧 facade 同一份字段）→ 已排版的块。
    空白文字回 None（什么都不画）。"""
    text = t.get("text", "")
    if not text.strip():
        return None
    size = float(t.get("size_pt", 9))
    line_h = float(t.get("line_height") or 1.25)
    pad = mm2pt(float(t.get("padding_mm") or 0))
    interp = str(t.get("interpretation") or richtext.DEFAULT_INTERPRETATION)
    x0, y0 = mm2pt(t["x_mm"]) + pad, mm2pt(t["y_mm"]) + pad
    box_w = mm2pt(t["w_mm"]) - 2 * pad
    align = t.get("align", "left")

    units_lines = break_lines(text, faces, size, box_w, interp)
    asc, desc = faces.primary.ascender, faces.primary.descender
    baseline0 = y0 + size * ((line_h - (asc - desc)) / 2 + asc)
    cov = coverage(faces)
    out: list[LaidOutLine] = []
    rendered = ""
    for i, line in enumerate(units_lines):
        if not line:
            continue
        w = _unit_w(line, faces, size)
        x = (
            x0 + (box_w - w) / 2
            if align == "center"
            else x0 + box_w - w
            if align == "right"
            else x0
        )
        pieces: list[ShapedPiece] = []
        for seg_text, seg_script, seg_logical in line:
            seg_size, rise = richtext.run_metrics(seg_script, size)
            for piece in shape_segment(seg_text, faces, seg_size, rise, seg_logical):
                # 相邻、同脸同字号同基线的片段合成一段：换行单元按空格切开只是为了换行，
                # 落笔时它们是同一段文字（advance 已各自排好，合并不改几何）
                if pieces and _same_run(pieces[-1], piece):
                    prev = pieces[-1]
                    pieces[-1] = ShapedPiece(
                        prev.face,
                        prev.layer,
                        prev.text + piece.text,
                        prev.glyphs + piece.glyphs,
                        prev.size,
                        prev.rise,
                        prev.logical + piece.logical,
                    )
                else:
                    pieces.append(piece)
        logical = "".join(seg_logical for _, _, seg_logical in line)
        rendered += logical
        out.append(LaidOutLine(x, baseline0 + i * size * line_h, w, tuple(pieces), logical))
    missing, _subst = glyphplan.text_diagnostics(text, cov, interp)
    cjk_chars = tuple(
        dict.fromkeys(ch for ch in rendered if glyphplan.layer_of(ord(ch), cov) == "cjk")
    )
    return TextBlock(
        lines=tuple(out),
        box=(mm2pt(t["x_mm"]), mm2pt(t["y_mm"]), mm2pt(t["w_mm"]), mm2pt(t["h_mm"])),
        size=size,
        color=str(t.get("color", "#000000")),
        underline=bool(t.get("underline")),
        missing=tuple(missing),
        cjk_chars=cjk_chars,
    )
