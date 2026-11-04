"""画布文字竖排（ADR 0163「横排 / 竖排」）：RenderCore 的导出侧。

预览是 CSS（`writing-mode: vertical-rl; text-orientation: mixed`），导出是 `rendercore/vertical.py`；
两侧共有的是**语义**（直立 / 侧躺的判据、折行算法、列序、对齐、中线基线），这里逐条钉住。

分两层：
* 纯模型（合成脸，不要字体 / 候选包）：直立判据、折行与列序、对齐、横排不被连累；
* 真排（批准字体 + 候选包，缺则 skip 并说明）：字形朝向 / 位置（pymupdf 与 PDFium 两把独立尺子读写出来的
  PDF）、竖排字形（`，。「」（）` 的 gid 不同于横排）、中英混排、粗斜体、旋转叠加、用户字体缺 vmtx 的回退、
  文本层顺序。
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from tavotto.rendercore import fonts, ir, pdfwriter, plan, sources, typography, vertical
from tests.support import pdftext
from tests.support.fakeface import _CJK, _LATIN, UPEM, FakeFace, _resource

MM = 72 / 25.4


# ---------------------------------------------------------------- 纯模型
class _VFace(FakeFace):
    """带竖排塑形的合成脸：每字一格 = 1 em，字形在字宽中线、上升处落笔（像有 vmtx 的 CJK 字体）。"""

    def shape_vertical(self, text):
        return tuple(
            ir.VGlyph(g.gid, g.text, UPEM, -(self.advance(g.gid) // 2), -880)
            for g in self.shape(text)
        )


def _fake_faces(vertical_metrics: bool = True) -> typography.FaceSet:
    cls = _VFace if vertical_metrics else FakeFace
    primary = cls(_resource("p", "Fake", False, False), dict(_LATIN))
    cjk = cls(_resource("c", "FakeCJK", False, False), dict(_CJK), default_adv=1000)
    return typography.FaceSet(primary, cjk)


def _obj(text: str, **kw) -> dict:
    return {
        "type": "text",
        "id": "t",
        "text": text,
        "x_mm": 10.0,
        "y_mm": 10.0,
        "w_mm": 40.0,
        "h_mm": 30.0,
        "size_pt": 10.0,
        "writing_mode": "vertical",
        **kw,
    }


HAN = "".join(chr(0x4E00 + i) for i in range(8))  # 假脸 CJK 区里的 8 个字


def test_the_orientation_table_matches_the_css_mixed_rule():
    """中日韩、假名、全角标点直立；拉丁、数字、空格、半角标点侧躺（CSS `text-orientation: mixed`）。"""
    for ch in "弹性段あアー，。「」（）：；！？、〈〉《》【】Ａ１　×±①":
        assert vertical.is_upright(ch), ch
    for ch in "AaZ09 ,.()[]-+=/%°µΩα":
        assert not vertical.is_upright(ch), ch
    assert vertical.split_orientation("E = 210 GPa，单位") == [
        ("E = 210 GPa", "E = 210 GPa", False),
        ("，单位", "，单位", True),
    ]


def test_columns_fold_by_box_height_and_run_right_to_left():
    faces = _fake_faces()
    # 字号 10 pt：一个直立字 10 pt 长；框高 30 mm = 85.04 pt、内边距 0 → 每列放 8 个字（80 pt），第 9 个折行
    block = vertical.layout_text(_obj(HAN + HAN + HAN, h_mm=30.0), faces)
    assert block is not None and block.lines == ()
    assert [len(c.logical) for c in block.columns] == [8, 8, 8]
    bx, bw = 10.0 * MM, 40.0 * MM
    pitch = 10.0 * 1.25
    for i, col in enumerate(block.columns):
        assert col.cx == pytest.approx(bx + bw - (i + 0.5) * pitch)  # 第一列贴右边，往左排
    assert [c.cx for c in block.columns] == sorted((c.cx for c in block.columns), reverse=True)
    # 列内每个字一格：沿列方向首尾相接，每格 = 1 em
    for col in block.columns:
        assert col.length == pytest.approx(len(col.logical) * 10.0)


def test_padding_shrinks_the_column_length_and_insets_the_first_column():
    faces = _fake_faces()
    block = vertical.layout_text(_obj(HAN, h_mm=30.0, padding_mm=10.0), faces)
    pad = 10.0 * MM
    length = 30.0 * MM - 2 * pad  # 25.5 pt → 2 个字一列
    assert max(c.length for c in block.columns) <= length + 1e-6
    assert block.columns[0].cx == pytest.approx(50.0 * MM - pad - 0.5 * 12.5)
    assert block.columns[0].top == pytest.approx(10.0 * MM + pad)


@pytest.mark.parametrize(("align", "frac"), [("left", 0.0), ("center", 0.5), ("right", 1.0)])
def test_align_means_top_middle_bottom_inside_the_column(align, frac):
    """`align` 在竖排里解释为列内的 上 / 中 / 下（CSS vertical-rl 的 line-left 就是上边）。"""
    faces = _fake_faces()
    block = vertical.layout_text(_obj(HAN[:3], align=align), faces)
    col = block.columns[0]
    length = 30.0 * MM
    assert col.top == pytest.approx(10.0 * MM + (length - col.length) * frac)


def test_a_blank_line_keeps_its_column_slot():
    """与横排一致：空行占一个行位（这里是一列），后面的列不往前挤。"""
    faces = _fake_faces()
    block = vertical.layout_text(_obj("AB\n\nCD"), faces)
    pitch = 10.0 * 1.25
    xs = [c.cx for c in block.columns]
    assert len(xs) == 2
    assert xs[0] - xs[1] == pytest.approx(2 * pitch)


def test_the_fold_algorithm_is_the_horizontal_one_with_a_different_ruler():
    """折行只有一份算法：传入与横排同一把尺，结果与横排逐行相同（保证没有第二套折行）。"""
    faces = _fake_faces()
    text = "Hello world 弹性段落 mixed 测试文字 and more words"
    horizontal = typography.break_lines(text, faces, 10.0, 60.0, "auto")
    same_ruler = typography.break_lines(
        text,
        faces,
        10.0,
        60.0,
        "auto",
        measure=lambda u: typography._unit_w(u, faces, 10.0),
    )
    assert horizontal == same_ruler


def test_horizontal_layout_has_no_columns_and_is_untouched():
    faces = _fake_faces()
    plain = {k: v for k, v in _obj("Hello 弹性").items() if k != "writing_mode"}
    block = typography.layout_text(plain, faces)
    assert block.columns == () and block.lines
    # 缺席 / 显式的横排取值：都是横排
    assert typography.layout_text({**plain, "writing_mode": "horizontal"}, faces).columns == ()


def test_sideways_runs_are_one_item_and_upright_chars_are_separate_cells():
    """没有 `shape_vertical` 的脸（测试替身 / 将来的别种脸）按 HarfBuzz 合成度量的形状兜底：进退 = 上升 − 下降。"""
    faces = _fake_faces(vertical_metrics=False)
    block = vertical.layout_text(_obj("ab弹c"), faces)
    kinds = [(i.kind, round(i.pos, 3)) for i in block.columns[0].items]
    assert [k for k, _ in kinds] == ["side", "upright", "side"]
    # 侧躺段长度 = 横排字宽（假脸拉丁 600/1000 em）；直立字 = 竖排进退（假脸：上升 − 下降 = 1.107 em）
    items = block.columns[0].items
    assert items[0].length == pytest.approx(2 * 0.6 * 10.0)
    assert items[1].pos == pytest.approx(items[0].length)
    assert items[1].length == pytest.approx((0.891 + 0.216) * 10.0)


# ---------------------------------------------------------------- 真排
pytest.importorskip("fontTools")
pytest.importorskip("pikepdf")
pytest.importorskip("uharfbuzz")
pytest.importorskip("pypdfium2")
pymupdf = pytest.importorskip("pymupdf")


@pytest.fixture(scope="module")
def registry():
    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}；先跑 scripts/fetch_fonts.py")
    return reg


@pytest.fixture()
def provider(registry):
    from tavotto.rendercore.hbshaper import HbFaceProvider

    return HbFaceProvider(registry)


PAGE = (100.0, 100.0)


def _t(text: str, **kw) -> dict:
    return {
        "type": "text",
        "id": "t1",
        "text": text,
        "x_mm": 20.0,
        "y_mm": 10.0,
        "w_mm": 40.0,
        "h_mm": 60.0,
        "size_pt": 14.0,
        "writing_mode": "vertical",
        **kw,
    }


def _compile(provider, objs, math=None):
    return plan.compile_page(
        *PAGE, objs, sources=sources.StaticSourceResolver(Path(".")), faces=provider, math=math
    )


def _export(provider, tmp_path, objs, name="v.pdf", math=None):
    compiled = _compile(provider, objs, math)
    out = tmp_path / name
    pdfwriter.write_pdf(compiled.page, out, provider)
    return compiled, out


def _chars(doc_path):
    """pymupdf 读回的字符：[(字, 原点 x, 原点 y（顶原点）, 该行阅读方向 dir)]，按内容流顺序。"""
    doc = pymupdf.open(str(doc_path))
    out = []
    for b in doc[0].get_text("rawdict")["blocks"]:
        for ln in b["lines"]:
            for s in ln["spans"]:
                for c in s["chars"]:
                    out.append((c["c"], c["origin"][0], c["origin"][1], ln["dir"]))
    return out


def _shaped(page: ir.Page):
    """页面树里所有 ShapedText（穿过 Group），带上它所在 Group 的累计变换是否含旋转。"""
    out = []

    def walk(n, rotated):
        if isinstance(n, ir.Group):
            r = rotated or n.transform != ir.IDENTITY
            for c in n.children:
                walk(c, r)
        elif isinstance(n, ir.ShapedText):
            out.append((n, rotated))

    for n in page.children:
        walk(n, False)
    return out


def test_pure_chinese_is_upright_stacked_in_one_column(provider, tmp_path):
    _, pdf = _export(provider, tmp_path, [_t("弹性段刚度")])
    cs = _chars(pdf)
    assert [c[0] for c in cs] == list("弹性段刚度")
    for _c, _x, _y, d in cs:
        assert d == pytest.approx((1.0, 0.0)), d  # 字头朝上：行方向仍是 (1, 0)，没有整体转过
    xs = [c[1] for c in cs]
    ys = [c[2] for c in cs]
    assert max(xs) - min(xs) < 0.01  # 同一列：原点 x 相同（Noto 竖排：字形在字宽中线）
    pitch = [b - a for a, b in zip(ys, ys[1:])]
    assert all(p == pytest.approx(14.0, abs=0.05) for p in pitch), pitch  # 1 em 一格，自上而下
    # 列贴框右边：列中线 = 框右边 − 半个行距（14 pt × 1.25 / 2）
    cx = (60.0 * MM) - 14.0 * 1.25 / 2
    assert xs[0] + 14.0 / 2 == pytest.approx(cx, abs=0.2)  # 原点在字格左缘，加半个 em 到中线


def test_mixed_script_latin_lies_on_its_side_and_cjk_stays_upright(provider, tmp_path):
    _, pdf = _export(provider, tmp_path, [_t("弹性段 E = 210 GPa", h_mm=80.0)])
    cs = _chars(pdf)
    assert "".join(c[0] for c in cs).replace(" ", "") == "弹性段E=210GPa"
    cjk = [c for c in cs if c[0] in "弹性段"]
    latin = [c for c in cs if c[0] in "E=210GPa"]
    assert all(d == pytest.approx((1.0, 0.0)) for *_, d in cjk)
    # 侧躺：阅读方向朝下（0, 1）——顺时针转了 90°
    assert all(d == pytest.approx((0.0, 1.0), abs=1e-3) for *_, d in latin), [d for *_, d in latin]
    # 侧躺字沿列往下读：y 单调增、x（基线）不变；且都在直立字的下面
    ly = [c[2] for c in latin]
    assert ly == sorted(ly) and min(ly) > max(c[2] for c in cjk)
    assert max(c[1] for c in latin) - min(c[1] for c in latin) < 0.01
    # 中线基线：侧躺段的 em 方框中点与直立字的 em 方框中点落在同一条列中线上
    # （侧躺基线 x = 中线 − (升 + 降) / 2 × 字号；Liberation Serif 的 typo 度量取自批准字体）
    face = provider.face_for("serif", False, False)
    mid = (face.ascender + face.descender) / 2 * 14.0
    cx = 60.0 * MM - 14.0 * 1.25 / 2
    assert latin[0][1] == pytest.approx(cx - mid, abs=0.2)


def test_fullwidth_punctuation_uses_the_vertical_glyphs(provider, tmp_path):
    """`，。「」（）` 在竖排里换 `vert` 字形：gid 与横排塑形的不同（写进 PDF 之前的 IR 就可以量）。"""
    text = "，。「」（）"
    compiled, _ = _export(provider, tmp_path, [_t(text)])
    cjk = provider.cjk_face("serif")  # _t 的默认族是 serif
    horizontal = [g.gid for g in cjk.shape(text)]
    placed = [st.runs[0].glyphs[0].gid for st, _ in _shaped(compiled.page)]
    assert len(placed) == len(horizontal)
    assert all(p != h for p, h in zip(placed, horizontal)), (placed, horizontal)
    # 同一批字形由 HarfBuzz 的竖排塑形直接给出（不是本模块猜的）
    assert placed == [g.gid for g in cjk.shape_vertical(text)]


def test_vertical_metrics_come_from_vmtx_for_noto_and_are_synthesised_for_liberation(provider):
    lib = provider.face_for("serif", False, False)
    for family in ("serif", "sans-serif"):  # Noto Serif SC / Noto Sans SC：vmtx 都给 1 em
        noto = provider.cjk_face(family)
        (g,) = noto.shape_vertical("弹")
        assert g.y_advance == noto.resource.units_per_em, family
    (a,) = lib.shape_vertical("×")
    # Liberation 没有 vmtx / vhea：HarfBuzz 合成（进退 = 上升 − 下降），字形落在字宽中线
    assert a.y_advance == lib._tt["hhea"].ascent - lib._tt["hhea"].descent
    assert a.x_offset < 0 and a.y_offset < 0


def test_long_text_folds_into_columns_whose_text_layer_reads_in_order(provider, tmp_path):
    text = "弹性段刚度与塑性段屈服强度的比较：测试结果显示 E = 210 GPa，「合格」（见图）。"
    compiled, pdf = _export(provider, tmp_path, [_t(text, h_mm=40.0)])
    xs = sorted({round(st.x, 1) for st, rot in _shaped(compiled.page) if not rot}, reverse=True)
    assert len(xs) >= 2
    # 文本层按内容流顺序读回 == 用户原文（列从右往左、列内从上到下）；PDFium 与 pymupdf 两把尺子
    expect = "".join(text.split())
    assert "".join(pdftext.text(pdf).split()) == expect
    assert "".join(c[0] for c in _chars(pdf)).replace(" ", "") == expect
    # 列序：内容流里每个字的 x 先不增（换列后 x 变小），同一列内 y 增
    cs = [c for c in _chars(pdf) if c[3] == pytest.approx((1.0, 0.0))]
    col_x = [round(c[1], 1) for c in cs]
    assert col_x == sorted(col_x, reverse=True)


def test_sideways_text_wraps_between_words_and_never_past_the_box_bottom(provider, tmp_path):
    text = "Elastic modulus of the steel specimen was measured at room temperature"
    compiled, pdf = _export(provider, tmp_path, [_t(text, h_mm=30.0, w_mm=60.0)])
    bottom = (10.0 + 30.0) * MM
    cs = _chars(pdf)
    assert max(c[2] for c in cs) <= bottom + 14.0 * 0.25  # 末字原点不越过框底（容一点字高）
    assert len({round(c[1], 1) for c in cs}) >= 3  # 折成了多列
    # 内容流顺序（pymupdf）= 用户原文。PDFium 对「整体转了 90° 的多行文字」自己按几何重排各行（横排文字转 90°
    # 也一样：三行读回来是反的），那是读取器的启发式、不是写入的顺序，所以顺序只让按内容流读的尺子作证；
    # PDFium 只验「字一个不少」
    assert "".join(c[0] for c in cs).replace(" ", "") == "".join(text.split())
    assert sorted(pdftext.text(pdf).replace(" ", "")) == sorted(text.replace(" ", ""))


def test_bold_and_italic_pick_the_variant_for_sideways_text_and_leave_cjk_alone(provider, tmp_path):
    _, pdf = _export(provider, tmp_path, [_t("弹性 Stress", bold=True, italic=True)])
    cs = pdftext.chars(pdf)
    latin_fonts = {c.font for c in cs if c.c in "Stress"}
    cjk_fonts = {c.font for c in cs if c.c in "弹性"}
    assert any("BoldItalic" in f for f in latin_fonts), latin_fonts
    assert all("Noto" in f for f in cjk_fonts), cjk_fonts


@pytest.mark.parametrize("deg", [30, 90])
def test_rotation_composes_after_vertical_layout(provider, tmp_path, deg):
    """先竖排排版，再整体绕未旋转框的中心顺时针转 θ——与横排同一条公式（ADR 0163 第 6 条）。"""
    _, base = _export(provider, tmp_path, [_t("弹性 EA")], "base.pdf")
    _, rot = _export(provider, tmp_path, [_t("弹性 EA", rotation_deg=deg)], "rot.pdf")
    b0, r0 = _chars(base), _chars(rot)
    cx, cy = (20.0 + 20.0) * MM, (10.0 + 30.0) * MM
    a = math.radians(deg)
    for (c1, x1, y1, d1), (c2, x2, y2, d2) in zip(b0, r0):
        assert c1 == c2
        want = (
            cx + (x1 - cx) * math.cos(a) - (y1 - cy) * math.sin(a),
            cy + (x1 - cx) * math.sin(a) + (y1 - cy) * math.cos(a),
        )
        assert (x2, y2) == pytest.approx(want, abs=0.6), (c1, deg)
        # 阅读方向也跟着转 θ
        want_d = (
            d1[0] * math.cos(a) - d1[1] * math.sin(a),
            d1[0] * math.sin(a) + d1[1] * math.cos(a),
        )
        assert d2 == pytest.approx(want_d, abs=1e-3)


def test_underline_is_a_vertical_rule_on_the_left_of_each_column(provider, tmp_path):
    compiled, _ = _export(provider, tmp_path, [_t("弹性段刚度", underline=True)])
    rules = [n for n in compiled.page.children[0].children if isinstance(n, ir.Path) and n.stroke]
    assert len(rules) == 1
    (m, l_) = rules[0].segments
    assert m[1] == l_[1]  # 竖线
    chars = _chars(tmp_path / "v.pdf")
    assert m[1] < min(c[1] for c in chars) + 14.0 / 2  # 在字的左边
    # 长度 = 这一列的长度（5 字 × 1 em）
    assert abs(m[2] - l_[2]) == pytest.approx(5 * 14.0, abs=0.1)


def test_background_and_border_follow_the_box_not_the_columns(provider, tmp_path):
    compiled, _ = _export(
        provider, tmp_path, [_t("弹性", bg="#ffee00", border_color="#000000", border_pt=1.0)]
    )
    group = compiled.page.children[0]
    path = group.children[0]
    xs = [s[1] for s in path.segments if len(s) > 1]
    assert min(xs) == pytest.approx(20.0 * MM) and max(xs) == pytest.approx(60.0 * MM)


def test_formula_is_a_sideways_block_in_the_column(provider, tmp_path):
    box = {
        "ok": True,
        "width": 2.0,
        "ascent": 0.8,
        "descent": 0.2,
        "segments": [["M", 0.0, -0.2], ["L", 2.0, -0.2], ["L", 2.0, 0.8], ["Z"]],
    }
    key = typography.math_key("\\frac{a}{b}", "serif")
    compiled, pdf = _export(
        provider,
        tmp_path,
        [_t("弹性 $\\frac{a}{b}$ 段", text_format="latex", h_mm=80.0)],
        math={key: box},
    )
    rotated = [n for n in compiled.page.children[0].children if isinstance(n, ir.Group)]
    paths = [p for g in rotated for p in g.children if isinstance(p, ir.Path)]
    assert len(paths) == 1  # 公式轮廓在转 90° 的 Group 里
    # 文本层仍给出源码、顺序在「弹性」与「段」之间
    assert "".join(pdftext.text(pdf).split()) == "弹性$\\frac{a}{b}$段"


def test_a_user_font_without_vmtx_falls_back_to_synthesised_vertical_metrics(
    provider, tmp_path, monkeypatch
):
    """用户本机字体（ADR 0164）没有竖排表时，HarfBuzz 合成竖排度量，导出照常、不报错、不换脸。"""
    mpl = pytest.importorskip("matplotlib.font_manager")
    from tavotto.rendercore import sourcefont, userfonts

    path = Path(str(mpl.findfont(mpl.FontProperties(family="DejaVu Sans"))))
    loc = userfonts.FontLocator(tmp_path / "loc.json", system_dirs=lambda: [path.parent])
    monkeypatch.setattr(userfonts, "_LOCATOR", loc)
    ref = {"family": "DejaVu Sans", "sha256": fonts.sha256_of(path), "face": 0}
    assert sourcefont.parse_source_font(ref)[0] is not None
    compiled, pdf = _export(provider, tmp_path, [_t("A×B±C", source_font=ref, h_mm=60.0)], "uf.pdf")
    assert not [p for p in compiled.problems if p["code"] == "source_font_unavailable"]
    face = provider.user_face(sourcefont.parse_source_font(ref)[0].primary)
    assert "vmtx" not in face._tt
    up = [g for g in face.shape_vertical("×±")]
    assert all(g.y_advance > 0 and g.x_offset < 0 for g in up)
    # 直立的 × ± 用这张脸、各占一格（合成进退）；侧躺的 A B C 同一张脸
    names = {c.font for c in pdftext.chars(pdf)}
    assert any("DejaVuSans" in n for n in names), names
    assert "".join(pdftext.text(pdf).split()) == "A×B±C"


def test_the_wire_value_matches_the_frontend_constant():
    """`writing_mode` 的取值两侧同源：前端 `WRITING_MODE_VERTICAL`（也是文档里 `writingMode` 的落盘值）↔ 后端 `WRITING_VERTICAL`。"""
    import re

    src = (Path(__file__).resolve().parent.parent / "web/src/lib/typography.ts").read_text(
        encoding="utf-8"
    )
    m = re.search(r"export const WRITING_MODE_VERTICAL = '([^']+)'", src)
    assert m and m.group(1) == typography.WRITING_VERTICAL
