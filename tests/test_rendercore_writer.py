"""RenderCore 写入器：真字体、真 PDF、独立读取器（统一实施包 U06，ADR 0059 / 0060）。

判据的主语是**写出来的那个文件此刻的字节**，尺子与写入侧不同源（RC-095）：

* 纯标准库读取器 `tests/support/pdfread.py`（页面 / 字体结构 / ToUnicode / 内容流 / 子集 cmap）；
* pypdfium2（文字层含 ActualText、对象普查、栅格像素）；
* poppler `pdftotext`（系统里有就用：另一家实现的 ActualText 读法）；
* pdfminer.six（装了就用：**不认 ActualText**，抽回的是 ToUnicode 那一层——两把尺子刻意不同）。

几何期望在本文件手算（毫米 → pt、y 翻转、像素坐标），不调 `plan` 的换算。

跑的前提：`tavotto[rendercore]` 的候选包 + 批准字体目录（`scripts/fetch_fonts.py`）。缺任一样
**skip 并说明理由**（skip 不是绿：`docs/implementation/tavotto-foundation/handoffs/U06_ir_text.md` 记
这批用例在哪个环境真跑过）。
"""

from __future__ import annotations

import hashlib
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.rendercore import fonts, ir, plan, sources

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

import pdfread  # noqa: E402

MM = 72.0 / 25.4
PAGE_MM = (150.0, 80.0)
W_PT, H_PT = PAGE_MM[0] * MM, PAGE_MM[1] * MM


def _candidates() -> None:
    from tavotto.rendercore.hbshaper import CandidatePackagesMissing, require

    try:
        require("pikepdf", "fontTools", "uharfbuzz", "pypdfium2")
    except CandidatePackagesMissing as exc:
        pytest.skip(f"候选包未装（not_run）：{exc}")


@pytest.fixture(scope="module")
def provider():
    _candidates()
    from tavotto.rendercore.hbshaper import HbFaceProvider

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(
            f"批准字体不全（not_run）：缺 {reg.missing}；先跑 scripts/fetch_fonts.py（目录 {reg.root}）"
        )
    return HbFaceProvider(reg)


@pytest.fixture(scope="module")
def pdfium():
    _candidates()
    import pypdfium2

    return pypdfium2


def _text(text: str, y_mm: float, **kw) -> dict:
    return {
        "type": "text",
        "id": kw.pop("id", f"t{y_mm:g}"),
        "text": text,
        "x_mm": 10.0,
        "y_mm": y_mm,
        "w_mm": 120.0,
        "h_mm": 10.0,
        "size_pt": 10.0,
        **kw,
    }


LINES = {
    "ascii": "Tavotto U06: efficient flow, E = mc^{2}, H_{2}O",
    "sci": "10⁵ m⁻² — Δλ = 532 nm, μ ≈ 1.5",
    "cjk": "图一：温度 T (K) 随时间变化 ℃",
    "combining": "cafe\u0301 x\u0303y \u00e9",
}
#: 各把尺子对同一行该抽出什么。`logical` = ActualText 优先（PDFium / poppler / 本文件的读取器），
#: `tounicode_only` = 只看 ToUnicode（pdfminer）。合成上下标那一行两者不同——这是刻意的、可解释的差异。
EXPECT = {
    "ascii": {"logical": "Tavotto U06: efficient flow, E = mc2, H2O"},
    "sci": {
        "logical": "10⁵ m⁻² — Δλ = 532 nm, μ ≈ 1.5",
        "tounicode_only": "10⁵ m-2 — Δλ = 532 nm, μ ≈ 1.5",
    },
    "cjk": {"logical": "图一：温度 T (K) 随时间变化 ℃"},
    # 用户打的是分解形式（e + U+0301）：ToUnicode 回的就是它，不归一化
    "combining": {"logical": "cafe\u0301 x\u0303y \u00e9"},
}


def _objects() -> list[dict]:
    return [
        _text(LINES["ascii"], 8, id="ascii"),
        _text(LINES["sci"], 18, id="sci"),
        _text(LINES["cjk"], 28, id="cjk", font_family="sans-serif", bold=True),
        _text(LINES["combining"], 38, id="combining", font_family="monospace", italic=True),
        # z 序：先红后蓝，重叠区必须是蓝（列表顺序，不是 id 顺序——id 故意逆序）
        {
            "type": "shape",
            "id": "z-red",
            "shape": "rect",
            "x_mm": 90,
            "y_mm": 50,
            "w_mm": 30,
            "h_mm": 20,
            "fill": "#ff0000",
            "stroke_pt": 0.05,
            "color": "#ff0000",
        },
        {
            "type": "shape",
            "id": "a-blue",
            "shape": "rect",
            "x_mm": 100,
            "y_mm": 55,
            "w_mm": 30,
            "h_mm": 20,
            "fill": "#0000ff",
            "stroke_pt": 0.05,
            "color": "#0000ff",
        },
        # 半透明椭圆：中心像素 = 0.5 红 + 0.5 白
        {
            "type": "shape",
            "id": "ell",
            "shape": "ellipse",
            "x_mm": 10,
            "y_mm": 52,
            "w_mm": 30,
            "h_mm": 16,
            "fill": "#ff0000",
            "fill_opacity": 0.5,
            "stroke_pt": 0.05,
            "color": "#ff0000",
        },
        {
            "type": "arrow",
            "id": "arr",
            "x_mm": 45,
            "y_mm": 55,
            "w_mm": 40,
            "h_mm": 10,
            "start": {"rx": 0, "ry": 0.5},
            "end": {"rx": 1, "ry": 0.5},
            "head_end": "triangle",
            "color": "#008000",
            "stroke_pt": 1.5,
        },
        _text("rotated", 66, id="rot", w_mm=40.0, rotation_deg=20, bg="#ffeecc", underline=True),
    ]


@pytest.fixture(scope="module")
def written(provider, tmp_path_factory) -> dict:
    from tavotto.rendercore import pdfwriter

    compiled = plan.compile_page(
        *PAGE_MM, _objects(), sources=sources.StaticSourceResolver(Path(".")), faces=provider
    )
    out = tmp_path_factory.mktemp("u06") / "page.pdf"
    facts = pdfwriter.write_pdf(compiled.page, out, provider)
    data = out.read_bytes()
    objs = pdfread.objects(data)
    page_head, content = pdfread.page(objs)
    fnt = pdfread.fonts(objs, page_head)
    maps = {name: pdfread.decode_tounicode(f["tounicode"]) for name, f in fnt.items()}
    return {
        "compiled": compiled,
        "path": out,
        "bytes": data,
        "facts": facts,
        "objs": objs,
        "page_head": page_head,
        "content": content,
        "fonts": fnt,
        "maps": maps,
        "runs": pdfread.text_runs(content, maps),
    }


# ---------------------------------------------------------------- 结构


def test_page_size_is_the_canvas_in_pt(written):
    assert pdfread.media_box(written["page_head"]) == pytest.approx([0, 0, W_PT, H_PT], abs=1e-3)
    assert written["facts"].page == pytest.approx((W_PT, H_PT))


def test_fonts_are_embedded_subsets_of_both_program_kinds_with_tounicode(written):
    """三张 Liberation（TrueType）+ 一张 Noto（CFF CID）：Type0 / Identity-H / 六字母子集前缀 /
    字体程序是真子集 / ToUnicode 非空 / W 有条目。"""
    fnt = written["fonts"]
    kinds = {f["kind"] for f in fnt.values()}
    assert kinds == {"truetype", "cff-cid"}, kinds
    bases = {f["base"] for f in fnt.values()}
    assert {
        "LiberationSerif",
        "LiberationSans-Bold",
        "LiberationMono-Italic",
        "NotoSansSC-Regular",
    } <= bases
    for name, f in fnt.items():
        assert f["type0"] and f["identity_h"], name
        assert f["tag"] and len(f["tag"]) == 6, name
        assert f["program"] and 0 < len(f["program"]) < 40_000, (name, len(f["program"] or b""))
        assert f["w_entries"] >= 1, name
        assert pdfread.decode_tounicode(f["tounicode"]), f"{name} 的 ToUnicode 为空"
        if f["kind"] == "truetype":
            assert f["cid_to_gid_identity"] and f["length1_ok"], name
        else:
            assert f["fontfile3_subtype_ok"], name
    for ff in written["facts"].fonts:
        assert ff["glyphs_in_subset"] < ff["glyphs_in_source"] / 10, ff


def _content_codes(written) -> list[tuple[str, int]]:
    return [(font, code) for r in written["runs"] for font, code in r["codes"]]


def test_every_code_in_the_content_stream_has_a_tounicode_entry_and_the_subset_cmap_agrees(written):
    """RC-034：主语是**内容流里真的写了的 code**。每个 code 在它那张脸的 ToUnicode 里有条目；TrueType
    脸上单码位的条目，嵌入子集自己的 cmap 把那个码位映到同一个 GID（不走写入器的 remap）。"""
    codes = _content_codes(written)
    assert len(codes) >= 80, len(codes)
    cmaps = {
        name: pdfread.sfnt_cmap(f["program"])
        for name, f in written["fonts"].items()
        if f["kind"] == "truetype"
    }
    checked = 0
    for font, code in codes:
        assert code in written["maps"][font], (
            font,
            code,
            "内容流里的 code 在 ToUnicode 里没有条目",
        )
        text = written["maps"][font][code]
        if font in cmaps and len(text) == 1 and code != 0:
            assert cmaps[font].get(ord(text)) == code, (
                f"{font}: code {code} 的 ToUnicode 是 U+{ord(text):04X}，"
                f"子集 cmap 映到 GID {cmaps[font].get(ord(text))}"
            )
            checked += 1
    assert checked >= 40, checked


def test_widths_in_w_array_are_the_hmtx_advances_of_the_glyphs_used(written, provider):
    """/W 里每个 code 的宽度 = 这张脸 hmtx 的 advance（千分之一 em）；量宽与写入同一份度量（RC-030）。"""
    fnt = written["fonts"]
    n = 0
    for name, f in fnt.items():
        face = next(
            provider.face_by_resource(r)
            for r in written["compiled"].page.resources.values()
            if isinstance(r, ir.FontResource) and r.postscript_name == f["base"]
        )
        for code, width in f["widths"].items():
            # code → 原 GID：TrueType 经 ToUnicode 反查 cmap；CFF 的 code 就是 CID
            text = written["maps"][name].get(code, "")
            if f["kind"] == "truetype":
                if len(text) != 1:
                    continue
                gid = face.record.info.cmap.get(ord(text))
            else:
                gid = next((g for g in range(len(face._order)) if face.cid_of(g) == code), None)
            if gid is None:
                continue
            assert width == pytest.approx(face.advance(gid) * 1000.0 / face.upem, abs=0.002), (
                name,
                code,
            )
            n += 1
    assert n >= 40, n


# ---------------------------------------------------------------- 文字层：四把尺子


def test_pure_reader_decodes_every_line_through_tounicode_and_actualtext(written):
    runs = written["runs"]
    logical = [r["logical"] for r in runs]
    for key, exp in EXPECT.items():
        assert exp["logical"] in logical, (key, logical)
    # ToUnicode 那一层：合成上下标是 `m-2`；其它行两层相同
    tounicode = [r["text"] for r in runs]
    assert EXPECT["sci"]["tounicode_only"] in tounicode, tounicode
    assert "�" not in "".join(tounicode), "有 code 在 ToUnicode 里查不到"


def test_pdfium_text_layer_returns_the_user_text_with_actualtext_honoured(written, pdfium):
    doc = pdfium.PdfDocument(str(written["path"]))
    text = doc[0].get_textpage().get_text_range()
    flat = "".join(ch for ch in text if not ch.isspace())
    for key, exp in EXPECT.items():
        want = "".join(ch for ch in exp["logical"] if not ch.isspace())
        assert want in flat, (key, text)


def test_pdfminer_sees_the_tounicode_layer_only(written):
    pdfminer = pytest.importorskip("pdfminer.high_level", reason="pdfminer.six 未装（not_run）")
    text = pdfminer.extract_text(str(written["path"]))
    flat = "".join(ch for ch in text if not ch.isspace())
    assert "".join(ch for ch in EXPECT["sci"]["tounicode_only"] if not ch.isspace()) in flat, text
    assert "".join(ch for ch in EXPECT["cjk"]["logical"] if not ch.isspace()) in flat


def _pdftotext(path: Path) -> tuple[str, str]:
    """poppler `pdftotext` 抽出的 (stdout, stderr)。**显式 `-enc UTF-8`**：输出编码是 poppler
    的构建期默认（Git for Windows 自带的那份是 Latin1），不钉的话 `²` 会以 0xb2 一个字节出来、
    U+1D538 这种 Latin1 放不下的直接被丢——u06-rendercore.yml 的 Windows 腿就是这样红的两条
    （父进程按 UTF-8 解、读线程炸、stdout 成 None；notdef 用例抽回 `ab`）。本机反证：换成
    `-enc Latin1` 同样两条红。"""
    exe = shutil.which("pdftotext")
    if not exe:
        pytest.skip("系统里没有 poppler pdftotext（not_run）")
    proc = subprocess.run([exe, "-enc", "UTF-8", str(path), "-"], capture_output=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8", errors="replace")


def test_poppler_pdftotext_honours_actualtext_and_reports_no_syntax_error(written):
    stdout, stderr = _pdftotext(written["path"])
    assert "Syntax Error" not in stderr, stderr
    flat = "".join(ch for ch in stdout if not ch.isspace())
    for key, exp in EXPECT.items():
        assert "".join(ch for ch in exp["logical"] if not ch.isspace()) in flat, (key, stdout)


def test_composed_superscript_and_multi_glyph_cluster_get_actualtext_spans(written):
    """`⁻²`（合成上下标，run 级）与 `x̃`（一个 cluster 两个字形）各一个 Span，且**每个 Span 里恰好一个
    TJ**（PDFium 对跨多个文字对象的 Span 会吐两遍）；`x̃` 的标记字形在 ToUnicode 里映到空串；
    合成上标那一段用 Ts 抬了 0.42 em。"""
    content = written["content"]
    spans = re.findall(rb"/ActualText <([0-9A-Fa-f]+)>>> BDC(.*?)EMC", content, re.S)
    texts = [bytes.fromhex(h.decode()).decode("utf-16") for h, _ in spans]
    assert "\u207b\u00b2" in texts and "x\u0303" in texts, texts
    assert all(body.count(b"] TJ") == 1 for _, body in spans), [b for _, b in spans]
    assert written["facts"].actualtext_spans == len(spans)
    mono = next(n for n, f in written["fonts"].items() if f["base"] == "LiberationMono-Italic")
    assert "" in written["maps"][mono].values(), "标记字形的 code 应映到空串"
    sci = next(r for r in written["runs"] if r["logical"] == EXPECT["sci"]["logical"])
    assert 10.0 * 0.42 in {round(v, 3) for v in sci["rises"]}, sci["rises"]


def test_pen_position_between_runs_equals_the_shaped_advance(written):
    """RC-030：第二个 run 的 Tm.x − 第一个 run 的 Tm.x == 第一个 run 的 Σx_advance × size ÷ upem。
    读的是内容流里的数（本文件的读取器），算的是 IR 里的数——两边不同源。"""
    node = next(
        n
        for n, _ in ir.walk(written["compiled"].page)
        if isinstance(n, ir.ShapedText) and n.object_id == "ascii"
    )
    res = written["compiled"].page.resources
    run = next(
        r
        for r in written["runs"]
        if r["tms"] and r["tms"][0] == pytest.approx((node.x, node.y), abs=1e-3)
    )
    xs = [x for x, _ in run["tms"]]
    assert len(xs) == len(node.runs) >= 4
    for i, grun in enumerate(node.runs[:-1]):
        upem = res[grun.font].units_per_em
        adv = sum(g.x_advance for g in grun.glyphs) * grun.size / upem
        assert xs[i + 1] - xs[i] == pytest.approx(adv, abs=0.002), i


# ---------------------------------------------------------------- 像素（PDFium 栅格）


def _pixel(img, x_pt: float, y_pt: float, scale: int) -> tuple[int, int, int]:
    px, py = int(x_pt * scale), int((H_PT - y_pt) * scale)
    r, g, b, *_ = img.getpixel((px, py))
    return (r, g, b)


@pytest.fixture(scope="module")
def raster(written, pdfium):
    doc = pdfium.PdfDocument(str(written["path"]))
    return doc[0].render(scale=2).to_pil().convert("RGBA")


def test_raster_size_and_background(raster):
    assert raster.size == (math.ceil(W_PT * 2), math.ceil(H_PT * 2))  # PDFium 向上取整
    assert _pixel(raster, 2.0, 2.0, 2) == (255, 255, 255)


def test_z_order_is_the_list_order_not_the_id_order(raster):
    """红 (90,50,30×20) 在前、蓝 (100,55,30×20) 在后：重叠区 (110,60) mm 必须是蓝；只有红的
    (92,52) 是红。id 逆序（z-red / a-blue）是陷阱。"""
    overlap = _pixel(raster, 110 * MM, H_PT - 60 * MM, 2)
    only_red = _pixel(raster, 92 * MM, H_PT - 52 * MM, 2)
    assert overlap == (0, 0, 255), overlap
    assert only_red == (255, 0, 0), only_red


def test_half_transparent_fill_composites_over_white(raster):
    cx, cy = (10 + 15) * MM, H_PT - (52 + 8) * MM
    got = _pixel(raster, cx, cy, 2)
    assert all(abs(a - b) <= 6 for a, b in zip(got, (255, 128, 128))), got


def test_text_lines_have_ink_where_the_layout_put_them(raster, written):
    """每个（没旋转的）ShapedText 的基线上方 0.7 em 里有墨：y 向上的坐标直接来自 IR，本文件按同一约定
    取像素。旋转过的文字块坐标在组的局部空间里，不在这条判据的主语里。"""
    checked = 0
    for grp in written["compiled"].page.children:
        if not isinstance(grp, ir.Group) or grp.transform != ir.IDENTITY:
            continue
        for node in grp.children:
            if not isinstance(node, ir.ShapedText):
                continue
            size = node.runs[0].size
            dark = 0
            for xp in range(int(node.x * 2), int((node.x + 40) * 2)):
                for yp in range(int((H_PT - node.y - size * 0.7) * 2), int((H_PT - node.y) * 2)):
                    r, g, b, *_ = raster.getpixel((xp, yp))
                    if r < 100 and g < 100 and b < 100:
                        dark += 1
            assert dark > 20, (node.object_id, dark)
            checked += 1
    assert checked >= 4


def test_half_transparent_text_renders_lighter_than_opaque_text(provider, tmp_path, pdfium):
    """同一行字两份：alpha 1 与 alpha 0.5。0.5 那份最深的像素必须明显比 1 那份浅（黑字 0.5 叠白 ≈ 128），
    否则 gs 没起作用。主语是 PDFium 栅格里的像素，不是内容流里有没有 gs。"""
    from tavotto.rendercore import pdfwriter

    face = provider.face_for("serif", False, False)
    glyphs = face.shape("MMMM")
    font = {"font": face.resource}
    page = ir.Page(
        60,
        30,
        (
            ir.ShapedText(2, 18, (ir.GlyphRun("font", 12.0, glyphs),), ir.Paint((0, 0, 0), 1.0)),
            ir.ShapedText(2, 4, (ir.GlyphRun("font", 12.0, glyphs),), ir.Paint((0, 0, 0), 0.5)),
        ),
        font,
        (1.0, 1.0, 1.0),
    )
    out = tmp_path / "alpha.pdf"
    pdfwriter.write_pdf(page, out, provider)
    img = pdfium.PdfDocument(str(out))[0].render(scale=4).to_pil().convert("RGB")

    def darkest(y_top: float, y_bottom: float) -> int:
        vals = [
            img.getpixel((x, y))[0]
            for x in range(8, 200)
            for y in range(int((30 - y_bottom) * 4), int((30 - y_top) * 4))
        ]
        return min(vals)

    opaque = darkest(18, 18 + 12 * 0.7)
    half = darkest(4, 4 + 12 * 0.7)
    assert opaque < 40, opaque
    assert 100 < half < 170, half


def test_pdfium_object_census_counts_real_text_objects_not_outlines(written, pdfium):
    """RC-035：文字是文字对象（FPDF_PAGEOBJ_TEXT = 1），不是路径。"""
    doc = pdfium.PdfDocument(str(written["path"]))
    kinds = [o.type for o in doc[0].get_objects()]
    assert kinds.count(1) >= 9, kinds  # 4 行 + rotated + 拆成多 run 的段
    assert kinds.count(2) >= 5, kinds  # 矩形 ×2、椭圆、箭头杆 + 帽、文字底色、下划线


# ---------------------------------------------------------------- 可复现


def test_writing_the_same_page_twice_gives_identical_bytes(written, provider, tmp_path):
    from tavotto.rendercore import pdfwriter

    out = tmp_path / "again.pdf"
    pdfwriter.write_pdf(written["compiled"].page, out, provider)
    assert hashlib.sha256(out.read_bytes()).hexdigest() == written["facts"].sha256


# ---------------------------------------------------------------- 能力表交叉核对（RC-011）


def _page_with(op: str, provider) -> ir.Page:
    """用到恰好这一种操作的最小页面。"""
    sha = "ab" * 32
    black = ir.Paint((0.0, 0.0, 0.0))
    rect = ir.Path((("M", 1, 1), ("L", 10, 1), ("L", 10, 10), ("Z",)), fill=black)
    face = provider.face_for("serif", False, False)
    font = {"font": face.resource}
    if op == "page_background":
        return ir.Page(20, 20, (), {}, (1.0, 1.0, 1.0))
    if op == "path_fill":
        return ir.Page(20, 20, (rect,), {}, None)
    if op == "path_stroke":
        return ir.Page(20, 20, (ir.Path(rect.segments, stroke=ir.Stroke(black, 1.0)),), {}, None)
    if op == "clip":
        return ir.Page(20, 20, (ir.Group((rect,), clip=rect),), {}, None)
    if op == "group_opacity":
        return ir.Page(20, 20, (ir.Group((rect,), opacity=0.5),), {}, None)
    if op == "object_alpha":
        # 路径与文字各一份：文字的 alpha 走另一条发射路径（BT 之前的 gs），只用路径测会漏掉它（Codex #460 P2）
        glyphs = face.shape("Ab")
        return ir.Page(
            20,
            20,
            (
                ir.Path(rect.segments, fill=ir.Paint((0, 0, 0), 0.5)),
                ir.ShapedText(2, 12, (ir.GlyphRun("font", 9.0, glyphs),), ir.Paint((0, 0, 0), 0.5)),
            ),
            font,
            None,
        )
    if op == "text":
        glyphs = face.shape("Ab")
        return ir.Page(
            20, 20, (ir.ShapedText(2, 2, (ir.GlyphRun("font", 9.0, glyphs),), black),), font, None
        )
    if op == "image":
        res = ir.FileResource("x.png", "png", sha, "static", "sha256:" + "0" * 64)
        return ir.Page(20, 20, (ir.Image("img", (1, 1, 5, 5)),), {"img": res}, None)
    if op == "imported_page":
        res = ir.FileResource("x.pdf", "pdf", sha, "static", "sha256:" + "0" * 64)
        return ir.Page(20, 20, (ir.ImportedPage("pg", (1, 1, 5, 5)),), {"pg": res}, None)
    if op == "flip":
        res = ir.FileResource("x.png", "png", sha, "static", "sha256:" + "0" * 64)
        return ir.Page(20, 20, (ir.Image("img", (1, 1, 5, 5), flip_h=True),), {"img": res}, None)
    raise AssertionError(f"没有为操作 {op} 准备最小页面——能力表加了操作，这里要跟上")


MARKERS = {
    "page_background": b" re f",
    "path_fill": b" f",
    "path_stroke": b" S",
    "clip": b" W n",
    "group_opacity": b"/Group",
    "object_alpha": b" gs",
    "text": b"BT",
}


@pytest.mark.parametrize("op", ir.OPERATIONS)
def test_each_declared_capability_matches_what_the_writer_really_does(op, provider, tmp_path):
    """声明 native 的：写得出，且内容流 / 对象里有对应操作符；声明 unsupported 的：以同一条理由拒绝。"""
    from tavotto.rendercore import pdfwriter

    cap = ir.capability("pdf", op)
    page = _page_with(op, provider)
    out = tmp_path / f"{op}.pdf"
    if cap.level == ir.CAP_NATIVE:
        pdfwriter.write_pdf(page, out, provider)
        objs = pdfread.objects(out.read_bytes())
        blob = b"".join((h + (d or b"")) for h, d in objs.values())
        assert MARKERS[op] in blob, op
        if op == "object_alpha":
            # 文字对象自己前面也要有 gs（不是只有路径那一处）
            _, content = pdfread.page(objs)
            assert re.search(rb"q\n/GS\d+ gs\nBT", content), content
    else:
        with pytest.raises(pdfwriter.UnsupportedCapability) as ei:
            pdfwriter.write_pdf(page, out, provider)
        used = {o for n, _ in ir.walk(page) for o in ir.operations_of(n)}
        assert op in used and ei.value.operation in used
        assert ir.capability("pdf", ei.value.operation).level == ir.CAP_UNSUPPORTED
        assert ei.value.params["reason"] == ir.capability("pdf", ei.value.operation).reason
        assert not out.exists()


# ---------------------------------------------------------------- 负例


def test_a_missing_glyph_is_written_as_notdef_and_reported_not_substituted(
    provider, tmp_path, pdfium
):
    """`𝔸`（U+1D538）不在任何批准脸里：编译期报 glyph_missing、写入器记 notdef、ToUnicode 仍回原字
    （本文件的读取器 / poppler 抽回 `a𝔸b`）、画面上是方框——没有任何一步换成别的脸。
    PDFium 把 CID 0 当空白（抽回 `a b`）：那是读取器的规则，这里只钉它没有多字、没有少字。"""
    from tavotto.rendercore import pdfwriter

    compiled = plan.compile_page(
        60,
        20,
        [_text("a\U0001d538b", 2, id="m", w_mm=50.0)],
        sources=sources.StaticSourceResolver(Path(".")),
        faces=provider,
    )
    assert compiled.problems == (
        {"code": "glyph_missing", "object_id": "m", "chars": ["\U0001d538"]},
    )
    out = tmp_path / "missing.pdf"
    facts = pdfwriter.write_pdf(compiled.page, out, provider)
    assert facts.notdef_codes == 1
    assert len(facts.fonts) == 1 and facts.fonts[0]["base_font"].endswith("LiberationSerif")
    objs = pdfread.objects(out.read_bytes())
    page_head, content = pdfread.page(objs)
    fnt = pdfread.fonts(objs, page_head)
    maps = {name: pdfread.decode_tounicode(f["tounicode"]) for name, f in fnt.items()}
    assert [r["logical"] for r in pdfread.text_runs(content, maps)] == ["a\U0001d538b"]
    assert maps["F1"][0] == "\U0001d538"
    text = pdfium.PdfDocument(str(out))[0].get_textpage().get_text_range()
    assert re.fullmatch(r"a.b", text.strip()), text
    if shutil.which("pdftotext"):
        assert "a\U0001d538b" in _pdftotext(out)[0]


def test_a_same_named_face_with_different_bytes_is_refused(provider, tmp_path):
    """RC-023：资源说的是「LiberationSerif-Regular，但 sha256 是别的」→ 写入器拒绝，不按名字找一张脸凑。"""
    from tavotto.rendercore import pdfwriter

    face = provider.face_for("serif", False, False)
    forged = ir.FontResource(
        face_id=face.resource.face_id,
        sha256="cd" * 32,
        family="serif",
        bold=False,
        italic=False,
        kind="truetype",
        postscript_name=face.resource.postscript_name,
        units_per_em=face.resource.units_per_em,
    )
    page = ir.Page(
        20,
        20,
        (ir.ShapedText(2, 2, (ir.GlyphRun("f", 9.0, face.shape("A")),), ir.Paint((0, 0, 0))),),
        {"f": forged},
        None,
    )
    with pytest.raises(pdfwriter.WriterError) as ei:
        pdfwriter.write_pdf(page, tmp_path / "forged.pdf", provider)
    assert ei.value.code == "font_identity"


def _mutated(written, tmp_path, how: str) -> Path:
    """用 pikepdf 造坏文件：丢 FontFile / 丢 ToUnicode / 内容流里的 code 全 +1（错 GID）。"""
    import pikepdf

    pdf = pikepdf.open(str(written["path"]))
    fonts_ = pdf.pages[0].Resources.Font
    if how == "no_fontfile":
        for f in fonts_.values():
            fd = f.DescendantFonts[0].FontDescriptor
            for key in ("/FontFile2", "/FontFile3"):
                if key in fd:
                    del fd[key]
    elif how == "no_tounicode":
        for f in fonts_.values():
            del f["/ToUnicode"]
    elif how == "wrong_gid":
        raw = pdf.pages[0].Contents.read_bytes()
        raw = re.sub(
            rb"<([0-9A-F]{4})>", lambda m: b"<%04X>" % ((int(m.group(1), 16) + 1) % 0x10000), raw
        )
        pdf.pages[0].Contents = pikepdf.Stream(pdf, raw)
    out = tmp_path / f"{how}.pdf"
    pdf.save(str(out), object_stream_mode=pikepdf.ObjectStreamMode.disable)
    return out


@pytest.mark.parametrize("how", ["no_fontfile", "no_tounicode", "wrong_gid"])
def test_the_readers_catch_each_broken_file(written, tmp_path, how, pdfium):
    """负例的反证：三种坏文件在本文件的判据下**必须红**——否则上面的绿说明不了「字体结构对」。"""
    bad = _mutated(written, tmp_path, how)
    objs = pdfread.objects(bad.read_bytes())
    page_head, content = pdfread.page(objs)
    fnt = pdfread.fonts(objs, page_head)
    if how == "no_fontfile":
        assert all(f["program"] is None for f in fnt.values())
    elif how == "no_tounicode":
        assert all(not pdfread.decode_tounicode(f["tounicode"]) for f in fnt.values())
        text = pdfium.PdfDocument(str(bad))[0].get_textpage().get_text_range()
        assert "efficient" not in text
    else:
        maps = {name: pdfread.decode_tounicode(f["tounicode"]) for name, f in fnt.items()}
        runs = pdfread.text_runs(content, maps)
        assert EXPECT["ascii"]["logical"] not in [r["logical"] for r in runs]
        # RC-034 那条判据（内容流里每个 code 都有 ToUnicode 条目）在错 GID 的文件上必须红
        orphans = [(f, c) for r in runs for f, c in r["codes"] if c not in maps[f]]
        assert orphans, "错 GID 的文件竟然每个 code 都有 ToUnicode 条目——判据是空的"
