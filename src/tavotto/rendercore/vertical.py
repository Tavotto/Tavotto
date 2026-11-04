"""竖排（ADR 0163「横排 / 竖排」）：画布文字从上到下、列从右往左（CSS `writing-mode: vertical-rl`）。

**预览是 CSS，导出是这里**——所以语义逐条对着 CSS 的 `writing-mode: vertical-rl; text-orientation: mixed`：

* 中日韩表意文字、假名、谚文、全角标点**直立**（字头朝上），其余（拉丁、数字、半角符号 …）**侧躺**
  （整段顺时针转 90°，字头朝右）。判据是 UAX #50 `Vertical_Orientation` 的核心区段（`UPRIGHT`）；
  不在表里的一律侧躺，与 CSS 对未登记字符的缺省一致。
* 直立字用 HarfBuzz **TTB** 塑形：套 `vert` / `vrt2`（`，。「」（）` 换竖排字形），读 `vmtx` / `vhea` / `VORG`；
  字体没有这些表（Liberation、多数用户字体）时 HarfBuzz 合成竖排度量。侧躺段仍是横排塑形
  （`typography.shape_segment`），落笔时整体转 90°——与浏览器一样，转的是**整段**，不是逐字。
* 折行与横排是**同一个算法**（`typography.break_lines`：CJK 逐字、拉丁按词、超宽单词逐字兜底），只换了
  量「行长」的尺：行长是框高 `h`（扣内边距），直立字取竖排进退，侧躺字取横排字宽。
* 列从右往左排：第 i 列的中线在 `框右边 − 内边距 − (i + 0.5) × 字号 × 行距`；列内对齐 `align`
  左 / 中 / 右解释为列内的 **上 / 中 / 下**（CSS 里 vertical-rl 的 line-left 就是上边）。
* 侧躺段的基线取**中线基线**（central baseline）：em 方框的竖直中点落在列中线上，与直立字的 em 方框中点对齐
  ——CSS 在竖排里用的就是这条基线，所以中英混排不会一高一低。
* 公式（`$…$`）是侧躺的整块：与侧躺文字同一条基线、同一个转向。

落笔不动写入器：直立字一字一个单字形 `ShapedText`（笔位由本模块逐字算好），侧躺段与公式放进一个
`Group(transform=旋转 −90°)`（y 向上空间里 −90° = 顺时针）。几何全是 IR 里现成的东西，PDF / SVG / EPS / 位图
四个写入器不需要认识「竖排」。

纯标准库；塑形走 `typography.Face`（真实实现 `hbshaper.HbFace.shape_vertical`）。
"""

from __future__ import annotations

from collections.abc import Callable

from .. import glyphplan, richtext
from .ir import VGlyph, mm2pt
from .typography import (
    MATH,
    Face,
    FaceSet,
    MathSlots,
    ShapedPiece,
    TextBlock,
    Unit,
    VColumn,
    VItem,
    _math_text_piece,
    _same_run,
    break_lines,
    cjk_probe,
    coverage,
    shape_segment,
    text_params,
    text_plan,
    user_text,
)

#: 直立（`Vertical_Orientation` = U / Tu / Tr）的码位区间，闭区间。UAX #50 的核心区段；
#: 区间外的字符一律侧躺。改这张表要同时想一想浏览器（Chrome / WebKit / Gecko 都读 UAX #50 的完整表）
#: 会不会不同——差的只有生僻符号，常见的中日韩文字与标点都在下面。
UPRIGHT: tuple[tuple[int, int], ...] = (
    (0x00A7, 0x00A7),  # §
    (0x00A9, 0x00A9),  # ©
    (0x00AE, 0x00AE),  # ®
    (0x00B1, 0x00B1),  # ±
    (0x00BC, 0x00BE),  # ¼ ½ ¾
    (0x00D7, 0x00D7),  # ×
    (0x00F7, 0x00F7),  # ÷
    (0x1100, 0x11FF),  # 谚文字母
    (0x2030, 0x2031),  # ‰ ‱
    (0x203B, 0x203C),  # ※ ‼
    (0x2047, 0x2049),  # ⁇ ⁈ ⁉
    (0x2460, 0x24FF),  # 带圈 / 括号数字字母
    (0x2E80, 0x2FDF),  # 部首
    (0x2FF0, 0x303F),  # 表意文字描述符、CJK 符号与标点（、。〈〉《》「」『』【】〔〕 …）
    (0x3040, 0x30FF),  # 平假名、片假名
    (0x3100, 0x312F),  # 注音
    (0x3130, 0x318F),  # 谚文兼容字母
    (0x3190, 0x31FF),  # 汉文标注、笔画、片假名扩展
    (0x3200, 0x33FF),  # 带圈 CJK、CJK 兼容
    (0x3400, 0x4DBF),  # 扩展 A
    (0x4E00, 0x9FFF),  # 基本区
    (0xA000, 0xA4CF),  # 彝文
    (0xA960, 0xA97F),  # 谚文字母扩展 A
    (0xAC00, 0xD7FF),  # 谚文音节与扩展 B
    (0xF900, 0xFAFF),  # 兼容表意文字
    (0xFE10, 0xFE1F),  # 竖排形式
    (0xFE30, 0xFE4F),  # CJK 兼容形式
    (0xFE50, 0xFE6F),  # 小写变体
    (0xFF00, 0xFF60),  # 全角形式
    (0xFFE0, 0xFFE6),  # 全角符号
    (0x1B000, 0x1B16F),  # 假名补充 / 扩展
    (0x1F200, 0x1F2FF),  # 带圈表意补充
    (0x20000, 0x3FFFF),  # 扩展 B 起的表意文字平面
)


def is_upright(ch: str) -> bool:
    """这个字符在竖排里直立吗（否则侧躺）。"""
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in UPRIGHT)


def split_orientation(text: str, logical: str | None = None) -> list[tuple[str, str, bool]]:
    """一段文字 → [(文字, 用户原文, 是否直立)]，相邻同向的字符并成一段。"""
    logical = text if logical is None else logical
    out: list[tuple[str, str, bool]] = []
    for ch, lch in zip(text, logical):
        up = is_upright(ch)
        if out and out[-1][2] == up:
            t, lg, _ = out[-1]
            out[-1] = (t + ch, lg + lch, up)
        else:
            out.append((ch, lch, up))
    return out


def vglyphs(face: Face, text: str) -> tuple[VGlyph, ...]:
    """直立字的竖排字形。真实脸走 HarfBuzz TTB；没有 `shape_vertical` 的脸（测试替身）按一字一格兜底：
    进退 = 上升 − 下降，字形在字宽中线、上升处落笔——与 HarfBuzz 对没有竖排表的字体的合成度量同一个形状。"""
    shaper = getattr(face, "shape_vertical", None)
    if shaper is not None:
        return shaper(text)
    upem = face.resource.units_per_em
    adv = round((face.ascender - face.descender) * upem)
    return tuple(
        VGlyph(g.gid, g.text, adv, -(g.x_advance // 2), -round(face.ascender * upem))
        for g in face.shape(text)
    )


def _upright_len(text: str, styled: FaceSet, size: float) -> float:
    total = 0.0
    for seg, layer in text_plan(text, styled):
        face = styled.face_of(layer)
        total += sum(g.y_advance for g in vglyphs(face, seg)) * size / face.resource.units_per_em
    return total


def column_measure(faces: FaceSet, size: float, slots: MathSlots | None) -> Callable[[Unit], float]:
    """沿列方向量一个换行单元的长度（pt）。`break_lines` 的 `measure`。"""

    def measure(u: Unit) -> float:
        total = 0.0
        for seg, sc, _logical, st in u:
            if sc == MATH and slots is not None:
                total += slots.width(seg, size)
                continue
            seg_size = richtext.run_metrics(sc, size)[0]
            styled = faces.styled(*st)
            if sc:
                total += _side_len(seg, styled, seg_size)
                continue
            for part, _lg, up in split_orientation(seg):
                total += (
                    _upright_len(part, styled, seg_size)
                    if up
                    else _side_len(part, styled, seg_size)
                )
        return total

    return measure


def _side_len(text: str, styled: FaceSet, size: float) -> float:
    return sum(p.width for p in shape_segment(text, styled, size))


def layout_text(t: dict, faces: FaceSet, math: dict | None = None) -> TextBlock | None:
    """竖排入口（ADR 0163），与 `typography.layout_text`（只排横排）对称：字段解析共用
    `typography.text_params`，由 `plan` 按 `writing_mode` 二选一。空白文字回 None。"""
    if not t.get("text", "").strip():
        return None
    slots, size, line_h, pad, interp, fmt = text_params(t, math)
    return layout(t, faces, slots, size, line_h, pad, interp, fmt)


def layout(
    t: dict,
    faces: FaceSet,
    slots: MathSlots | None,
    size: float,
    line_h: float,
    pad: float,
    interp: str,
    fmt: str | None,
) -> TextBlock | None:
    """竖排版面。参数是 `typography.text_params` 解析好的那一套（横排 / 竖排共用同一份字段解析）。"""
    text = t.get("text", "")
    bx, by = mm2pt(t["x_mm"]), mm2pt(t["y_mm"])
    bw, bh = mm2pt(t["w_mm"]), mm2pt(t["h_mm"])
    length = bh - 2 * pad
    col_w = size * line_h
    align = t.get("align", "left")
    units_cols = break_lines(
        text, faces, size, length, interp, fmt, slots, measure=column_measure(faces, size, slots)
    )
    cov = coverage(faces)
    columns: list[VColumn] = []
    rendered = ""
    for i, col in enumerate(units_cols):
        if not col:
            continue
        items: list[VItem] = []
        pos = 0.0
        for seg_text, seg_script, seg_logical, seg_style in col:
            if seg_script == MATH and slots is not None:
                for ch in seg_text:
                    span = slots.width(ch, size)
                    mp = _math_text_piece(faces.primary, slots.sources[ch], span, size)
                    items.append(
                        VItem(
                            "math",
                            pos,
                            span,
                            size=size,
                            piece=mp,
                            math=(slots.boxes[ch], slots.sources[ch]),
                        )
                    )
                    pos += span
                continue
            seg_size, rise = richtext.run_metrics(seg_script, size)
            styled = faces.styled(*seg_style)
            parts = (
                [(seg_text, seg_logical, False)]
                if seg_script
                else split_orientation(seg_text, seg_logical)
            )
            for part_text, part_logical, up in parts:
                if up:
                    for sub, layer in text_plan(part_text, styled):
                        face = styled.face_of(layer)
                        vg = vglyphs(face, sub)
                        span = sum(g.y_advance for g in vg) * seg_size / face.resource.units_per_em
                        items.append(VItem("upright", pos, span, face, seg_size, vg))
                        pos += span
                    continue
                for piece in shape_segment(part_text, styled, seg_size, rise, part_logical):
                    prev = items[-1] if items else None
                    if prev and prev.kind == "side" and prev.piece and _same_run(prev.piece, piece):
                        p0 = prev.piece
                        merged = ShapedPiece(
                            p0.face,
                            p0.layer,
                            p0.text + piece.text,
                            p0.glyphs + piece.glyphs,
                            p0.size,
                            p0.rise,
                            p0.logical + piece.logical,
                        )
                        items[-1] = VItem("side", prev.pos, prev.length + piece.width, piece=merged)
                    else:
                        items.append(VItem("side", pos, piece.width, piece=piece))
                    pos += piece.width
        body = [seg for seg in col if not (seg[1] == MATH and slots is not None)]
        logical = user_text("".join(seg[2] for seg in body))
        rendered += cjk_probe(body)
        offset = (
            (length - pos) / 2 if align == "center" else length - pos if align == "right" else 0.0
        )
        cx = bx + bw - pad - (i + 0.5) * col_w
        columns.append(
            VColumn(
                cx=cx,
                # 侧躺段基线（转前的横排基线）：em 方框中点落在列中线上
                side_x=cx - size * (faces.primary.ascender + faces.primary.descender) / 2,
                top=by + pad + offset,
                length=pos,
                items=tuple(items),
                logical=logical,
            )
        )
    missing, _subst = glyphplan.text_diagnostics(
        text, cov, interp, text_format=fmt, styled_cov=lambda st: coverage(faces.styled(*st))
    )
    cjk_chars = tuple(
        dict.fromkeys(ch for ch in rendered if glyphplan.layer_of(ord(ch), cov) == "cjk")
    )
    return TextBlock(
        lines=(),
        box=(bx, by, bw, bh),
        size=size,
        color=str(t.get("color", "#000000")),
        underline=bool(t.get("underline")),
        missing=tuple(missing),
        cjk_chars=cjk_chars,
        math_failures=tuple(slots.failed) if slots is not None else (),
        columns=tuple(columns),
    )


__all__ = ["UPRIGHT", "column_measure", "is_upright", "layout", "split_orientation", "vglyphs"]
