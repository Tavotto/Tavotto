"""字形归属计划与画布文字的字体回退（Prompt 14）。

这里盯住四件事：

1. **计划与真正落笔的那张脸一致**——判据不是我们自己的表，是导出的 PDF 里
   实际用到了哪几个字体（两把独立的尺子，同源了就等于自己验自己）；
2. **量宽与落笔同一份计划**——分段判据一旦有两份，换行位置就和画出来的字对
   不上（U10 起由 `tests/test_rendercore_typography.py` / `test_rendercore_writer.py` 在写入器
   侧看护：量宽 == 写进去的 advance）；
3. **覆盖表还配得上真字体**——前端读的是那张生成的表，漂了就该在这里红；
4. **raw text 一个字符都不改**——受控解释只生成渲染表示。

U10（ADR 0072）起脸来自批准字体集合（ADR 0060）：没有隐式回退层（`fallback` 恒空），`₂` / `⁵` 是
Liberation 自带的 primary——D07 批准的迁移；独立读取器换成 PDFium 的文字层与 `tests/support/pdfread.py`
直接读字体对象。
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path

import pytest

from tavotto import glyphplan, pdfbackend, richtext

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import pdfread  # noqa: E402
import pdftext  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / "golden" / "glyph_plan_vectors.json"
VECTORS = json.loads(GOLDEN.read_text(encoding="utf-8"))["vectors"]
#: `_place` 的产物都落在这里；`TemporaryDirectory` 的 finalizer 在进程退出时删整棵树
_TMP = tempfile.TemporaryDirectory(prefix="tavotto-glyphplan-")
HAS = all(
    importlib.util.find_spec(m) is not None for m in ("pypdfium2", "pikepdf", "uharfbuzz", "PIL")
)
pytestmark = pytest.mark.skipif(not HAS, reason="RenderCore 依赖未装（not_run，不是绿）")


@pytest.fixture(scope="module", autouse=True)
def _fonts_ready():
    if not HAS:
        pytest.skip("RenderCore 依赖未装（not_run）")
    from tavotto.rendercore import fonts, renderhost

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}；先跑 scripts/fetch_fonts.py")
    yield
    renderhost.shutdown_shared()


def _fonts_used(pdf: Path) -> set[str]:
    """产物首页字体资源表里的脸（去子集前缀；独立读取器直接读对象，不问被测实现）。"""
    objs = pdfread.objects(pdf.read_bytes())
    head, _content = pdfread.page(objs)
    return {f["base"] for f in pdfread.fonts(objs, head).values()}


def _text_of(pdf: Path) -> str:
    """PDFium 按内容顺序抽回的文字层（认 ActualText）。"""
    return pdftext.text(pdf).strip()


def _place(text: str, **kw) -> Path:
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
    obj.setdefault("id", "t")
    # 放进模块级临时目录、进程退出时一并删：之前 mkdtemp() 一去不回，每跑一次全量就往 $TMPDIR
    # 漏几十个目录（2026-09-22 数到 1050 个）。调用方要按路径读（独立读取器 / PDFium），不能随 with 收掉
    path = Path(tempfile.mkdtemp(dir=_TMP.name)) / "one.pdf"
    with pdfbackend.compose(120, 30) as canvas:
        canvas.place(obj, dpi=300, resolve_panel=lambda o, d: path)
        canvas.save_pdf(path)
    return path


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
def test_subscript_two_is_primary_and_the_layers_have_no_fallback():
    """`₂` 在中日韩脸里也有，码位却在 CJK 段之外——第 2 步轮不到它，落在第 1 步（Liberation 自带，
    D07 批准的迁移：U10 之前它靠 PyMuPDF 的隐式回退脸）。

    这一条钉的是覆盖表的裁剪条件：多减一个 `cjk` 会让前端把它判成 `cjk`、后端仍判 `primary`，
    一个**只在下标字符上发作**的两侧分歧。`fallback` 层恒空（ADR 0060 §1）。
    """
    assert pdfbackend.text_plan("₂") == [("₂", "primary")]
    assert glyphplan.plan("₂", glyphplan.canvas_coverage())[0].layer == "primary"
    assert pdfbackend.coverage_ranges()["fallback"] == []


def test_box_drawing_is_rescued_by_the_fourth_step():
    """`━` 拉丁脸没有、中日韩脸有：第 4 步就是为它存在的。"""
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
    # 没有回退层就没有「换了脸」的字符：`⁵` 是 primary 自己画的（U10 之前它是回退脸画的、在这张单子上）
    assert glyphplan.substituted_chars("样品 ×10⁵", cov) == []


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
        ("×10⁵", {"primary"}),  # U10 之前是 {primary, fallback}：⁵ 靠回退脸；Liberation 自带（D07）
        ("样品 ×", {"cjk", "primary"}),
        ("样品", {"cjk"}),
    ],
)
def test_plan_matches_the_faces_the_pdf_actually_uses(family, text, want_layers):
    layers = {layer for _, layer in pdfbackend.text_plan(text, family=family)}
    assert layers == want_layers
    # `interpretation="scientific"` 会把上标合成掉，这里要的是原样落笔
    used = _fonts_used(_place(text, font_family=family, interpretation="auto"))
    # 一层一张脸：primary 一张、cjk 另一张；没有第三张（没有回退脸）。层数与脸数必须对得上
    # ——对不上说明计划在撒谎。
    assert len(used) == len(want_layers), (used, want_layers)


def test_no_fallback_face_is_ever_used_and_missing_stays_on_the_primary_face():
    """没有隐式回退脸（ADR 0060 §1）：任何族 × 字重的 `H₂O` 只用请求的那一张脸；画不出的 `𝔸`
    也不换脸——写成 .notdef 留在主脸上、文本层里字还在（U10 之前这里量的是 PyMuPDF 自己挑的
    Noto Serif 回退脸「与族和字重无关」）。"""
    for family in pdfbackend.CANVAS_TEXT_FAMILIES:
        for bold in (False, True):
            used = _fonts_used(_place("H₂O", font_family=family, bold=bold, interpretation="auto"))
            assert len(used) == 1 and all(u.startswith("Liberation") for u in used), used
    pdf = _place("A𝔸", interpretation="auto")
    assert _fonts_used(pdf) == {"LiberationSerif"}
    assert pdfbackend.missing_glyphs("A𝔸") == ["𝔸"]
    objs = pdfread.objects(pdf.read_bytes())
    head, _ = pdfread.page(objs)
    [face] = pdfread.fonts(objs, head).values()
    assert "𝔸" in pdfread.decode_tounicode(face["tounicode"]).values()


# --------------------------------------------------------------------------
# 4. 量宽与落笔同一份计划 —— U10 起在写入器侧看护（tests/test_rendercore_writer.py::
#    test_pen_position_between_runs_equals_the_shaped_advance、tests/test_rendercore_typography.py::
#    test_width_is_the_sum_of_shaped_advances_of_the_same_plan）：旧用例直接用 PyMuPDF TextWriter 落笔，
#    随旧后端退役（ADR 0072）。
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 5. 覆盖表还配得上真字体
# --------------------------------------------------------------------------
def test_coverage_table_matches_the_live_fonts():
    stored = json.loads(glyphplan.coverage_table_path().read_text(encoding="utf-8"))
    assert stored["backend_version"] == pdfbackend.BACKEND_VERSION
    assert stored["layers"] == pdfbackend.coverage_ranges()


def test_the_single_coverage_table_is_the_intersection_of_all_faces():
    """三个族 × 四个字形共用一张 `primary` 表——这条承诺要被量一次。

    旧后端的 base-14 各脸字符集相同，所以「一张表」是天然的；Liberation 的 12 张脸 cmap 差 16 个
    码位，一张表只能是**交集**（ADR 0060 §1）——不成立的话加粗的标签会在常规标签画得出的字符上出方框，
    而覆盖表只有一份，前端给出的答案会对其中几张脸是错的。完整判据在
    `tests/test_rendercore_glyph_vectors.py::test_the_primary_layer_is_the_intersection_of_all_twelve_faces`；
    这里只钉「表是一张、且它对每一张脸都不说谎」。
    """
    from tavotto.rendercore import facade

    prov = facade.provider()
    primary = {c for lo, hi in pdfbackend.coverage_ranges()["primary"] for c in range(lo, hi + 1)}
    for family in pdfbackend.CANVAS_TEXT_FAMILIES:
        for bold in (False, True):
            for italic in (False, True):
                face = prov.face_for(family, bold, italic)
                assert all(face.covers(cp) for cp in primary if cp < 0x3000), (family, bold, italic)


# --------------------------------------------------------------------------
# 5b. 两侧的闭集常量逐字相同（表是手写的，vectors 覆盖不到它们）
# --------------------------------------------------------------------------
def _ts(name: str) -> str:
    return (ROOT_DIR / "web" / "src" / "lib" / name).read_text(encoding="utf-8")


def _ts_table(src: str, const: str) -> dict[str, str]:
    body = re.search(
        rf"export const {const}: Readonly<Record<string, string>> = \{{(.*?)\n\}}", src, re.S
    )
    assert body, f"richText.ts 里找不到 {const}"
    return {m.group(1): m.group(2) for m in re.finditer(r"'(.+?)':\s*'(.*?)',", body.group(1))}


def test_the_script_character_tables_are_identical_on_both_sides():
    """上下标字符 → 基础字符的两张表，**键与值都逐字相同**。

    往一侧加一个 `⁴` 而另一侧忘了加，预览与导出就在那一个字符上分叉——
    而 golden vectors 覆盖的是分层计划，不是这两张手写的表，所有既有用例
    照绿。
    """
    src = _ts("richText.ts")
    assert _ts_table(src, "SUPERSCRIPT_BASE") == richtext.SUPERSCRIPT_BASE
    assert _ts_table(src, "SUBSCRIPT_BASE") == richtext.SUBSCRIPT_BASE


def test_the_interpretation_modes_are_the_same_closed_set():
    src = _ts("richText.ts")
    m = re.search(r"export const TEXT_INTERPRETATIONS = \[(.*?)\] as const", src, re.S)
    assert m, "找不到 TEXT_INTERPRETATIONS"
    front = tuple(v.strip().strip("'\"") for v in m.group(1).split(",") if v.strip())
    assert front == richtext.TEXT_INTERPRETATIONS
    d = re.search(r"export const DEFAULT_INTERPRETATION: TextInterpretation = '(\w+)'", src)
    assert d and d.group(1) == richtext.DEFAULT_INTERPRETATION


def test_the_layer_names_and_cjk_boundary_are_the_same_on_both_sides():
    """分层名与 CJK 段下界。**顺序也比**——它就是优先级。"""
    src = _ts("glyphPlan.ts")
    m = re.search(r"export const GLYPH_LAYERS = \[(.*?)\] as const", src, re.S)
    assert m, "找不到 GLYPH_LAYERS"
    front = tuple(v.strip().strip("'\"") for v in m.group(1).split(",") if v.strip())
    assert front == glyphplan.GLYPH_LAYERS
    b = re.search(r"export const CJK_START = (0x[0-9a-fA-F]+)", src)
    assert b and int(b.group(1), 16) == glyphplan.CJK_START


# --------------------------------------------------------------------------
# 6. raw text 不被改写；两个解释档各自兑现自己那句话
# --------------------------------------------------------------------------
def test_auto_mode_keeps_the_pdf_text_layer_verbatim():
    """默认档下**复制出来的还是 `×10⁵`**。

    合成上下标会把文本层里的 `⁵` 变成 `5`（`10⁵` 复制出来是 `105`），
    那是语义损坏——所以它只能是用户明确选的那一档。
    """
    assert _text_of(_place("×10⁵ H₂O")) == "×10⁵ H₂O"


def test_scientific_mode_draws_everything_with_one_face():
    """`scientific` 档：一张脸画完整段文字。合成只对**主脸画不出**的上下标发生（`richtext.interpret_runs`
    的 `worth` 判据）：Liberation 自带 `⁵` / `₂`，所以 `×10⁵ H₂O` 在这一档也原样落笔、文本层不降级
    ——U10 之前 Helvetica 没有它们，同一档会折成 `×105 H2O`（ADR 0072 的用户可见变化表）；哪张脸都没有的
    `⁻` 仍被合成：ToUnicode 里是基础字符 `-`（不认 ActualText 的读取器复制出来是 `m-2`），认 ActualText
    的 PDFium 还原用户原文 `m⁻²`（RC-036，ADR 0060 §3）。两句都是凭据。"""
    pdf = _place("×10⁵ H₂O", font_family="sans-serif", interpretation="scientific")
    assert _fonts_used(pdf) == {"LiberationSans"}
    assert _text_of(pdf) == "×10⁵ H₂O"

    pdf = _place("m⁻²", font_family="sans-serif", interpretation="scientific")
    assert _fonts_used(pdf) == {"LiberationSans"}
    objs = pdfread.objects(pdf.read_bytes())
    head, _ = pdfread.page(objs)
    [face] = pdfread.fonts(objs, head).values()
    tounicode = set(pdfread.decode_tounicode(face["tounicode"]).values())
    assert "-" in tounicode and "⁻" not in tounicode
    assert _text_of(pdf) == "m⁻²"


def test_designed_superscripts_are_never_synthesized():
    """`m²` 的 `²` 是主脸自己画得出的设计字形，两档都不该动它。"""
    for mode in richtext.TEXT_INTERPRETATIONS:
        pdf = _place("m²", interpretation=mode)
        assert _text_of(pdf) == "m²"
        objs = pdfread.objects(pdf.read_bytes())
        head, _ = pdfread.page(objs)
        [face] = pdfread.fonts(objs, head).values()
        assert "²" in pdfread.decode_tounicode(face["tounicode"]).values()


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

    这条用注入的判据跑——它守的是「覆盖更窄时 auto 仍然救得回方框」，而不是当前这一版的表现
    （当前集合里 `⁻` 正是这样被合成的，见 golden 的 unit-negative-exponent）。
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
