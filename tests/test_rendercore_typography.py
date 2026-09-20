"""Typography 层（统一实施包 U06，ADR 0059 / 0060）：分层没有 fallback 脸、量宽与落笔同一份
shaped plan（RC-030）、换行 / 对齐 / 上下标的用户合同、合成上下标的 ActualText 依据（RC-036）。

用合成脸（`tests/support/fakeface.py`）跑：不需要候选包、不需要字体文件。真字体上的结论在
`test_rendercore_fonts.py` / `test_rendercore_writer.py`。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tavotto import glyphplan, richtext
from tavotto.rendercore import typography as ty

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

import fakeface  # noqa: E402

PROVIDER = fakeface.FakeProvider()
FACES = ty.faces_for(PROVIDER, "serif", False, False)
FACES_NO_CJK = ty.faces_for(fakeface.FakeProvider(cjk=False), "serif", False, False)
PT = fakeface.LATIN_ADV / fakeface.UPEM  # 一个拉丁字符在 1 pt 字号下的宽度（0.6 pt）


def _text(**kw) -> dict:
    base = {
        "id": "t1",
        "type": "text",
        "text": "Hello world",
        "x_mm": 10.0,
        "y_mm": 20.0,
        "w_mm": 40.0,
        "h_mm": 10.0,
        "size_pt": 10.0,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- 族与分层


def test_the_family_closed_set_and_default_match_the_old_facade():
    from tavotto import pdfbackend

    assert ty.CANVAS_TEXT_FAMILIES == ("serif", "sans-serif", "monospace")
    assert ty.CANVAS_TEXT_FAMILIES == pdfbackend.CANVAS_TEXT_FAMILIES  # 三方同源对的第三份宿主
    assert ty.COVERAGE_MAX_CP == pdfbackend.COVERAGE_MAX_CP
    assert ty.CANVAS_TEXT_DEFAULT_FAMILY == "serif"
    for bad in (None, "", "Times New Roman", "Arial", "宋体", 7):
        assert ty.latin_family(bad) == "serif"
    for fam in ty.CANVAS_TEXT_FAMILIES:
        assert ty.latin_family(fam) == fam


def test_layers_are_primary_cjk_missing_and_never_fallback():
    """没有隐式回退脸：正文脸没有、CJK 脸也没有的字就是 missing——不是某张系统脸悄悄画出来。"""
    plan = ty.text_plan("Ab 图 ∇", FACES)
    assert plan == [("Ab ", "primary"), ("图", "cjk"), (" ", "primary"), ("∇", "missing")]
    assert "fallback" not in {layer for _, layer in plan}
    assert ty.missing_glyphs("Ab 图 ∇ ∇", FACES) == ["∇"]
    assert ty.coverage_ranges(FACES, hi=0x100)["fallback"] == []
    # 覆盖表的 primary 区间真的是脸的 cmap 压出来的（ASCII 可见区 + 几个符号）
    prim = ty.coverage_ranges(FACES, hi=0x100)["primary"]
    assert prim[0] == [0x20, 0x7E]


def test_without_a_cjk_face_han_is_missing_not_substituted():
    assert ty.text_plan("图", FACES_NO_CJK) == [("图", "missing")]
    assert ty.missing_glyphs("图", FACES_NO_CJK) == ["图"]


def test_degree_sign_below_the_cjk_break_point_still_reaches_the_cjk_face_last():
    """`℃`（U+2103 < 0x2E80）：正文脸没有、没有 fallback、第 4 步轮到 CJK 脸——四步顺序不可交换。"""
    assert ty.text_plan("℃", FACES) == [("℃", "cjk")]
    assert glyphplan.layer_of(0x2103, ty.coverage(FACES)) == "cjk"


# ---------------------------------------------------------------- 量宽 = 落笔（RC-030）


def test_width_is_the_sum_of_shaped_advances_of_the_same_plan():
    """`Hello` 五个拉丁字符 × 0.6 pt × 10 pt；`图` 走 CJK 脸 1.0 em。量的是 shaping 的 advance，
    不是另一张宽度表。"""
    assert ty.text_width("Hello", 10.0, FACES) == pytest.approx(5 * PT * 10)
    assert ty.text_width("图", 10.0, FACES) == pytest.approx(10.0)
    assert ty.text_width("a 图", 10.0, FACES) == pytest.approx(
        PT * 10 + fakeface.SPACE_ADV / fakeface.UPEM * 10 + 10.0
    )
    pieces = ty.shape_segment("Hello 图", FACES, 10.0)
    assert [(p.layer, p.text) for p in pieces] == [("primary", "Hello "), ("cjk", "图")]
    assert sum(p.width for p in pieces) == pytest.approx(ty.text_width("Hello 图", 10.0, FACES))


def test_a_ligature_is_one_glyph_covering_two_codepoints():
    pieces = ty.shape_segment("fi", FACES, 10.0)
    assert len(pieces[0].glyphs) == 1
    assert pieces[0].glyphs[0].text == "fi"
    assert pieces[0].glyphs[0].gid == fakeface.LIGATURE_GID


# ---------------------------------------------------------------- 换行 / 对齐


def test_words_wrap_greedily_and_spaces_attach_to_the_previous_word():
    """框宽 40 mm = 113.39 pt；10 pt 字号下一个字符 6 pt：`Hello world` 11 字符 = 66 pt 放得下；
    30 个字符放不下 → 按词折。"""
    block = ty.layout_text(_text(text="alpha beta gamma delta epsilon"), FACES)
    assert block is not None
    lines = [ln.logical for ln in block.lines]
    # 前 18 字符 "alpha beta gamma " = 102 pt < 113.39；再加 "delta" 就超 → 第二行
    assert lines == ["alpha beta gamma", "delta epsilon"]
    # rstrip 后 14 个字母 × 6 pt + 2 个空格 × 3 pt
    assert block.lines[0].width == pytest.approx(14 * 6.0 + 2 * 3.0)
    assert block.lines[1].baseline - block.lines[0].baseline == pytest.approx(10.0 * 1.25)


def test_cjk_breaks_per_character_and_an_overlong_word_breaks_inside_itself():
    cjk = ty.layout_text(_text(text="图" * 30, w_mm=40.0), FACES)  # 每字 10 pt，一行 11 个
    assert cjk is not None
    assert [len(ln.logical) for ln in cjk.lines] == [11, 11, 8]
    long_word = ty.layout_text(_text(text="a" * 40), FACES)  # 240 pt 的一个词
    assert long_word is not None
    assert [len(ln.logical) for ln in long_word.lines] == [18, 18, 4]


def test_alignment_moves_the_line_start_not_the_glyphs():
    left = ty.layout_text(_text(align="left"), FACES)
    center = ty.layout_text(_text(align="center"), FACES)
    right = ty.layout_text(_text(align="right"), FACES)
    assert left and center and right
    x0 = 10.0 * 72 / 25.4
    box_w = 40.0 * 72 / 25.4
    w = 10 * 6.0 + 3.0  # 10 个字母 + 1 个空格
    assert left.lines[0].x == pytest.approx(x0)
    assert center.lines[0].x == pytest.approx(x0 + (box_w - w) / 2)
    assert right.lines[0].x == pytest.approx(x0 + box_w - w)


def test_the_first_baseline_is_the_css_line_box_baseline_of_the_primary_face():
    """baseline = y0 + size × ((line_h − (asc − desc)) / 2 + asc)，asc / desc 来自正文脸。"""
    block = ty.layout_text(_text(line_height=1.5, padding_mm=1.0), FACES)
    assert block is not None
    y0 = 20.0 * 72 / 25.4 + 1.0 * 72 / 25.4
    asc, desc = FACES.primary.ascender, FACES.primary.descender
    assert block.lines[0].baseline == pytest.approx(y0 + 10.0 * ((1.5 - (asc - desc)) / 2 + asc))


# ---------------------------------------------------------------- 上下标与 ActualText


def test_markup_scripts_get_the_richtext_size_and_rise():
    block = ty.layout_text(_text(text="H_{2}O E^{2}"), FACES)
    assert block is not None
    pieces = block.lines[0].pieces
    assert [(p.text, p.size, p.rise) for p in pieces] == [
        ("H", 10.0, 0.0),
        ("2", 10.0 * richtext.SCRIPT_SIZE, -10.0 * richtext.SUB_DROP),
        ("O E", 10.0, 0.0),
        ("2", 10.0 * richtext.SCRIPT_SIZE, 10.0 * richtext.SUP_RISE),
    ]
    # 用户自己写的 ^{}：括号里就是他打的字，不需要 ActualText
    assert all(p.actual_text is None for p in pieces)


def test_a_composed_unicode_superscript_keeps_the_user_text_as_actual_text():
    """`m⁻²`：`⁻` 在合成脸里没有 → auto 档把 `⁻²` 折成上标 `-2`。画的是 `-2`，用户写的是 `⁻²`，
    这一段的 `actual_text` 必须是 `⁻²`——写入器据此包 ActualText，文本层才不会变成 `m-2`。"""
    block = ty.layout_text(_text(text="m⁻²"), FACES)
    assert block is not None
    pieces = block.lines[0].pieces
    assert [(p.text, p.logical, p.actual_text) for p in pieces] == [
        ("m", "m", None),
        ("-2", "⁻²", "⁻²"),
    ]
    assert pieces[1].rise == pytest.approx(10.0 * richtext.SUP_RISE)
    assert block.lines[0].logical == "m⁻²"
    assert block.missing == ()  # 合成之后没有方框


def test_a_drawable_unicode_superscript_stays_a_real_glyph_in_auto_mode():
    """`⁵` 合成脸画得出 → auto 档不折：真上标字形、文本层原样，不需要 ActualText。"""
    block = ty.layout_text(_text(text="10⁵"), FACES)
    assert block is not None
    assert [(p.text, p.actual_text, p.rise) for p in block.lines[0].pieces] == [("10⁵", None, 0.0)]


def test_scientific_mode_composes_a_superscript_the_primary_face_lacks():
    """`⁷` 只在 CJK 脸里：auto 档让 CJK 脸画真字形（画得出就不折）；scientific 档因为它不是
    正文脸的字而折成上标 `7`，用户原文 `⁷` 留在 actual_text。"""
    auto = ty.layout_text(_text(text="10⁷"), FACES)
    assert auto is not None
    assert [(p.text, p.layer, p.actual_text) for p in auto.lines[0].pieces] == [
        ("10", "primary", None),
        ("⁷", "cjk", None),
    ]
    sci = ty.layout_text(_text(text="10⁷", interpretation="scientific"), FACES)
    assert sci is not None
    assert [(p.text, p.layer, p.actual_text) for p in sci.lines[0].pieces] == [
        ("10", "primary", None),
        ("7", "primary", "⁷"),
    ]


def test_missing_and_cjk_characters_are_reported_on_the_block():
    block = ty.layout_text(_text(text="∇ 图 ∇"), FACES)
    assert block is not None
    assert block.missing == ("∇",)
    assert block.cjk_chars == ("图",)


def test_blank_text_lays_out_to_nothing():
    assert ty.layout_text(_text(text="   \n "), FACES) is None
