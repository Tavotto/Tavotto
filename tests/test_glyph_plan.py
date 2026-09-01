"""字形归属计划与画布文字的字体回退（Prompt 14）。

这里盯住四件事：

1. **计划与真正落笔的那张脸一致**——判据不是我们自己的表，是导出的 PDF 里
   实际用到了哪几个字体（两把独立的尺子，同源了就等于自己验自己）；
2. **量宽与落笔同一份计划**——分段判据一旦有两份，换行位置就和画出来的字对
   不上；
3. **覆盖表还配得上真字体**——前端读的是那张生成的表，漂了就该在这里红；
4. **raw text 一个字符都不改**——受控解释只生成渲染表示。
"""

import json
import tempfile
from pathlib import Path

import pymupdf
import pytest

from tavotto import glyphplan, pdfbackend, richtext
from tavotto.pdfbackend import pymupdf_backend as backend

GOLDEN = Path(__file__).parent / "golden" / "glyph_plan_vectors.json"
VECTORS = json.loads(GOLDEN.read_text(encoding="utf-8"))["vectors"]


def _place(text: str, **kw) -> pymupdf.Document:
    """把一段文字走**真实导出路径**画出来，回那份 PDF。"""
    obj = {
        "type": "text",
        "text": text,
        "x_mm": 3,
        "y_mm": 3,
        "w_mm": 110,
        "h_mm": 24,
        "size_pt": 12,
        "bold": False,
        "italic": False,
        "color": "#000000",
        "align": "left",
    }
    obj.update(kw)
    path = Path(tempfile.mkdtemp()) / "one.pdf"
    with pdfbackend.compose(120, 30) as canvas:
        canvas.place(obj, dpi=300, resolve_panel=lambda o, d: path)
        canvas.save_pdf(path)
    return pymupdf.open(path)


# --------------------------------------------------------------------------
# 1. 跨语言看护向量（vitest 跑同一份）
# --------------------------------------------------------------------------
@pytest.mark.parametrize("vec", VECTORS, ids=[v["name"] for v in VECTORS])
def test_golden_vectors_match_python_side(vec):
    runs = pdfbackend.text_plan(vec["text"], family=vec["family"])
    assert [{"text": t, "layer": layer} for t, layer in runs] == vec["runs"]
    assert pdfbackend.missing_glyphs(vec["text"], family=vec["family"]) == vec["missing"]


def test_generator_is_up_to_date():
    """向量文件是生成物：改了算法却没重跑生成器时，这里红。"""
    import subprocess
    import sys

    root = Path(__file__).parent.parent
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "gen_glyph_plan_vectors.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",  # 生成器的输出全是中文；Windows 的系统代码页解不了
        cwd=root,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------
# 2. 分层顺序：四步不可交换
# --------------------------------------------------------------------------
def test_subscript_two_stays_on_the_fallback_layer():
    """`₂` 在中日韩脸里有，码位却在 CJK 段之外——第 2 步轮不到它。

    这一条钉的是覆盖表的裁剪条件：多减一个 `cjk` 会让前端把它判成 `cjk`、
    后端仍判 `fallback`，一个**只在下标字符上发作**的两侧分歧。
    """
    assert pdfbackend.text_plan("₂") == [("₂", "fallback")]
    assert glyphplan.plan("₂", glyphplan.canvas_coverage())[0].layer == "fallback"


def test_box_drawing_is_rescued_by_the_fourth_step():
    """`━` 拉丁脸与隐式回退都没有、中日韩脸有：第 4 步就是为它存在的。"""
    assert pdfbackend.text_plan("━") == [("━", "cjk")]


def test_cjk_is_not_reported_as_a_substitution():
    """中日韩落在 `cjk` 层，但**不进「换了脸」那张单子**。

    它只有一张脸（能力限制），不随用户的任何选择变化——为一个恒定的、改不动
    的限制在每一条中文标注上挂一条建议，只会训练用户忽略整个问题面板。
    真正值得说的是 `fallback`：那张脸是渲染器自己挑的，与族和字重都无关。
    """
    cov = glyphplan.canvas_coverage()
    assert glyphplan.plan("样品", cov)[0].layer == "cjk"
    assert glyphplan.substituted_chars("样品 A", cov) == []
    assert glyphplan.substituted_chars("样品 ×10⁵", cov) == ["⁵"]


def test_unrenderable_character_is_missing_not_silently_dropped():
    assert pdfbackend.text_plan("؟") == [("؟", "missing")]
    assert pdfbackend.missing_glyphs("T؟ = 5") == ["؟"]


# --------------------------------------------------------------------------
# 3. 计划 == 真正落笔的脸（独立的第二把尺子：读导出 PDF 的字体表）
# --------------------------------------------------------------------------
@pytest.mark.parametrize("family", pdfbackend.CANVAS_TEXT_FAMILIES)
@pytest.mark.parametrize(
    ("text", "want_layers"),
    [
        ("Sample A", {"primary"}),
        ("×10⁵", {"primary", "fallback"}),
        ("样品", {"cjk"}),
    ],
)
def test_plan_matches_the_faces_the_pdf_actually_uses(family, text, want_layers):
    layers = {layer for _, layer in pdfbackend.text_plan(text, family=family)}
    assert layers == want_layers
    # `interpretation="scientific"` 会把上标合成掉，这里要的是原样落笔
    doc = _place(text, font_family=family, interpretation="auto")
    used = {name for _, _, _, name, _, _ in doc[0].get_fonts()}
    doc.close()
    # 一层一张脸：primary 一张，fallback 会多出一张（PyMuPDF 自己挑的），
    # cjk 也是另一张。层数与脸数必须对得上——对不上说明计划在撒谎。
    assert len(used) == len(want_layers), (used, want_layers)


def test_fallback_face_is_the_same_regardless_of_family_and_weight():
    """回退脸**与请求的族和字重无关**（实测 1.28.2 一律 Noto Serif Regular）。

    这正是 `glyph-substituted` 必须报出来的理由：sans-serif 的粗体标签里
    那个下标会是一个衬线常规的字形，而没有任何人被告知。
    """
    seen = set()
    for family in pdfbackend.CANVAS_TEXT_FAMILIES:
        for bold in (False, True):
            doc = _place("H₂O", font_family=family, bold=bold, interpretation="auto")
            fonts = {name for _, _, _, name, _, _ in doc[0].get_fonts()}
            doc.close()
            seen |= fonts - {
                "Times-Roman",
                "Times-Bold",
                "Helvetica",
                "Helvetica-Bold",
                "Courier",
                "Courier-Bold",
            }
    assert len(seen) == 1, seen


# --------------------------------------------------------------------------
# 4. 量宽与落笔同一份计划
# --------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["×10⁵ A m⁻²", "H₂O", "样品 A", "━┃", "Sample"])
def test_measured_width_equals_the_advance_actually_written(text):
    page = pymupdf.open().new_page(width=600, height=200)
    writer = pymupdf.TextWriter(page.rect)
    latin = backend.latin_font(False, False, "serif")
    cjk = backend.cjk_font()
    x = 10.0
    for seg, layer in pdfbackend.text_plan(text):
        face = cjk if layer == "cjk" else latin
        writer.append((x, 100), seg, font=face, fontsize=20)
        x += face.text_length(seg, 20)
    assert pdfbackend.text_width(text, 20.0) == pytest.approx(x - 10.0, abs=1e-6)


# --------------------------------------------------------------------------
# 5. 覆盖表还配得上真字体
# --------------------------------------------------------------------------
def test_coverage_table_matches_the_live_fonts():
    stored = json.loads(glyphplan.coverage_table_path().read_text(encoding="utf-8"))
    assert stored["backend_version"] == pdfbackend.BACKEND_VERSION
    assert stored["layers"] == pdfbackend.coverage_ranges()


def test_every_base14_face_shares_one_charset():
    """三个族 × 四个字形共用一张 `primary` 表——这条承诺要被量一次。

    不成立的话，加粗的标签与常规的标签会在不同的字符上回退，而覆盖表只有
    一份，前端给出的答案会对其中几张脸是错的。
    """
    faces = [
        backend.latin_font(bold, italic, family)
        for family in pdfbackend.CANVAS_TEXT_FAMILIES
        for bold in (False, True)
        for italic in (False, True)
    ]
    rng = range(0x20, 0x3000)
    reference = {cp for cp in rng if faces[0].has_glyph(cp)}
    # 隐式回退那一层也要比：分层缓存按**码位**记（键里没有字体），而
    # `canvas_coverage.json` 也只有一张与族无关的表——两处都靠这条假设。
    ref_fallback = {cp for cp in rng if faces[0].has_glyph(cp, fallback=True)}
    for face in faces[1:]:
        assert {cp for cp in rng if face.has_glyph(cp)} == reference
        assert {cp for cp in rng if face.has_glyph(cp, fallback=True)} == ref_fallback


# --------------------------------------------------------------------------
# 6. raw text 不被改写；两个解释档各自兑现自己那句话
# --------------------------------------------------------------------------
def test_auto_mode_keeps_the_pdf_text_layer_verbatim():
    """默认档下**复制出来的还是 `×10⁵`**。

    合成上下标会把文本层里的 `⁵` 变成 `5`（`10⁵` 复制出来是 `105`），
    那是语义损坏——所以它只能是用户明确选的那一档。
    """
    doc = _place("×10⁵ H₂O")
    assert doc[0].get_text().strip() == "×10⁵ H₂O"
    doc.close()


def test_scientific_mode_draws_everything_with_one_face():
    doc = _place("×10⁵ H₂O", font_family="sans-serif", interpretation="scientific")
    used = {name for _, _, _, name, _, _ in doc[0].get_fonts()}
    text = doc[0].get_text().strip()
    doc.close()
    assert used == {"Helvetica"}
    # 代价说清楚：文本层降级成基础字符。这条断言是那句话的凭据。
    assert text == "×105 H2O"


def test_designed_superscripts_are_never_synthesized():
    """`m²` 的 `²` 是 base-14 自己画得出的设计字形，两档都不该动它。"""
    for mode in richtext.TEXT_INTERPRETATIONS:
        doc = _place("m²", interpretation=mode)
        assert doc[0].get_text().strip() == "m²"
        doc.close()


def test_interpretation_only_produces_a_render_representation():
    """解释不经过 `parse_runs` ↔ `serialize_runs` 那一对——文档字符串不变。"""
    raw = "×10⁵ A m⁻² H₂O"
    assert richtext.plain_text(raw) == raw
    cov = glyphplan.canvas_coverage()
    folded = richtext.interpret_runs(
        richtext.parse_runs(raw),
        is_primary=cov.primary,
        is_drawable=lambda cp: glyphplan.layer_of(cp, cov) != "missing",
        mode="scientific",
    )
    assert "".join(r.text for r in folded) != raw  # 渲染表示确实变了
    assert richtext.plain_text(raw) == raw  # 而原文没有


def test_a_run_of_unicode_scripts_folds_as_one_piece():
    """`m⁻²`：`⁻` 与 `²` 的处境不同，但**整串一起折**。

    逐字符处理会得到一个 62% 的合成减号紧挨着一个全尺寸的设计上标——比原样
    还难看。这条盯的是分块，不是折不折：把 `interpret_runs` 里那个
    「吃掉同类字符」的循环改成逐字符，这里立刻红。
    """
    folded = richtext.interpret_runs(
        richtext.parse_runs("m⁻²"),
        # `²` 在 Latin-1 里，正文脸画得出；`⁻` 画不出——两个字符两种处境
        is_primary=lambda cp: cp < 0x80 or cp == 0xB2,
        is_drawable=lambda cp: cp < 0x80 or cp == 0xB2,
        mode="auto",
    )
    assert [(r.text, r.script) for r in folded] == [("m", ""), ("-2", "sup")]


def test_superscript_and_subscript_never_merge():
    """相邻的上标段与下标段是两段——合并的话下标会被画到上标的基线上。"""
    folded = richtext.interpret_runs(
        richtext.parse_runs("x⁵₂"),
        is_primary=lambda cp: cp < 0x80,
        is_drawable=lambda cp: cp < 0x80,
        mode="auto",
    )
    assert [(r.text, r.script) for r in folded] == [("x", ""), ("5", "sup"), ("2", "sub")]


def test_missing_glyph_is_folded_even_in_auto_mode():
    """auto 档的那句承诺：**只有「不然就是方框」的才合成**。

    本后端的三张脸盖住了全部上下标字符，所以这条用注入的判据跑——它守的是
    「换一个覆盖更窄的后端时 auto 仍然救得回方框」，而不是当前这一版的表现。
    """
    ascii_only = richtext.interpret_runs(
        richtext.parse_runs("×10⁵"),
        is_primary=lambda cp: cp < 0x80,
        is_drawable=lambda cp: cp < 0x80,
        mode="auto",
    )
    assert [(r.text, r.script) for r in ascii_only] == [("×10", ""), ("5", "sup")]
    # 同一段文字，三张脸都画得出上标时 auto **不动它**（文本层不降级）
    cov = glyphplan.canvas_coverage()
    kept = richtext.interpret_runs(
        richtext.parse_runs("×10⁵"),
        is_primary=cov.primary,
        is_drawable=lambda cp: glyphplan.layer_of(cp, cov) != "missing",
        mode="auto",
    )
    assert "".join(r.text for r in kept) == "×10⁵"
