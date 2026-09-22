"""ArtifactInspector（统一实施包 U08，ADR 0068）：重新打开封口产物量事实、四值判据、D08 两档政策。

主语是**文件字节**：每条负例都是把一个合法产物改坏（改页盒 / 改 IHDR 尺寸 / 截断 / 坏 CRC / 丢 FontFile / 错 ToUnicode /
低 ppi 位图 / 自引用 Form）再重新检查；正例是候选与旧后端写出的真产物。PNG / TIFF 那一半纯标准库，任何机器都跑；
PDF 那一半要 pikepdf（rc-venv），缺则 skip 并写明。
"""

from __future__ import annotations

import importlib.util
import struct
import zlib
from pathlib import Path

import pytest

from tavotto.rendercore import inspector as I

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "pdf_png_assets"
HAS_PIKEPDF = importlib.util.find_spec("pikepdf") is not None
needs_pikepdf = pytest.mark.skipif(not HAS_PIKEPDF, reason="pikepdf 未装（not_run，不是绿）")
HAS_CANDIDATE = all(
    importlib.util.find_spec(m) is not None for m in ("pypdfium2", "pikepdf", "uharfbuzz", "PIL")
)
needs_candidate = pytest.mark.skipif(not HAS_CANDIDATE, reason="候选包未装（not_run，不是绿）")


# ---------------------------------------------------------------------------
# PNG / TIFF（纯标准库）
# ---------------------------------------------------------------------------
def _png(w: int, h: int, *, dpi: float | None = None, color_type: int = 2) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    channels = {2: 3, 6: 4}[color_type]
    raw = b"".join(b"\x00" + bytes([200, 30, 30, 255][:channels]) * w for _ in range(h))
    out = b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0)
    )
    if dpi:
        ppm = int(round(dpi / 0.0254))
        out += chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1))
    out += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return out


def test_png_observation_reads_size_alpha_and_density_from_the_bytes(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(_png(30, 20, dpi=300, color_type=6))
    o = I.observe_png(p)
    assert o["integrity"] == "verified" and o["px"] == [30, 20]
    assert o["alpha"] is True and abs(o["dpi"] - 300) < 0.5
    p.write_bytes(_png(30, 20))
    o = I.observe_png(p)
    assert o["alpha"] is False and o["dpi"] is None  # 没写 pHYs 就是 None，不编一个数


@pytest.mark.parametrize("damage", ["signature", "truncate", "crc", "no_iend"])
def test_a_damaged_png_is_integrity_failed_not_unknown(tmp_path, damage):
    data = bytearray(_png(30, 20, dpi=300))
    if damage == "signature":
        data[0] = 0x00
    elif damage == "truncate":
        data = data[: len(data) - 20]
    elif damage == "crc":
        data[-1] ^= 0xFF  # IEND 的 CRC
    elif damage == "no_iend":
        data = data[: len(data) - 12]
    p = tmp_path / "bad.png"
    p.write_bytes(bytes(data))
    o = I.observe_png(p)
    assert o["integrity"] == "failed" and o["problems"], damage


def test_tiff_observation_reads_size_alpha_and_resolution_and_catches_out_of_file_strips(tmp_path):
    from tavotto import tiffwrite

    p = tmp_path / "a.tiff"
    tiffwrite.write_tiff(p, 4, 3, bytes([10, 20, 30, 255]) * 12, 4, dpi=600)
    o = I.observe_tiff(p)
    assert o["integrity"] == "verified" and o["px"] == [4, 3]
    assert o["alpha"] is True and o["alpha_premultiplied"] is False and abs(o["dpi"] - 600) < 0.5
    tiffwrite.write_tiff(p, 4, 3, bytes([10, 20, 30]) * 12, 3, dpi=None)
    o = I.observe_tiff(p)
    assert o["alpha"] is False and o["dpi"] is None and o["dpi_unit"] == "none"
    data = p.read_bytes()
    (tmp_path / "cut.tiff").write_bytes(data[: len(data) - 8])
    o = I.observe_tiff(tmp_path / "cut.tiff")
    assert o["integrity"] == "failed"
    (tmp_path / "junk.tiff").write_bytes(b"not a tiff at all")
    assert I.observe_tiff(tmp_path / "junk.tiff")["integrity"] == "failed"


def test_raster_policy_size_is_required_and_density_only_under_strict(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(_png(30, 20, dpi=150))
    ok = I.inspect(p, "png", plan={"px": [30, 20], "ppi": 150}, policy="standard")
    assert ok["policy"]["verdict"] == "accepted" and ok["checks"]["size"] == "verified"
    assert ok["checks"]["dpi_tag"] == "verified" and ok["checks"]["raster_density"] == "unknown"
    assert ok["artifact"]["sha256"] and ok["artifact"]["bytes"] == p.stat().st_size
    off = I.inspect(p, "png", plan={"px": [31, 20], "ppi": 150}, policy="standard")
    assert off["checks"]["size"] == "verified"  # 1 px 容差（旧后端 round vs ceil）
    bad = I.inspect(p, "png", plan={"px": [40, 20], "ppi": 150}, policy="standard")
    assert bad["policy"]["verdict"] == "rejected" and bad["policy"]["failed"] == ["size"]
    # strict：密度低于规范 → 拒；没有规范（unknown）→ 也拒（必需 unknown 不许当通过）
    strict = I.inspect(
        p,
        "png",
        plan={"px": [30, 20], "ppi": 150},
        policy="strict",
        profile={"min_raster_dpi": 300},
    )
    assert strict["policy"]["verdict"] == "rejected" and strict["policy"]["failed"] == [
        "raster_density"
    ]
    strict_ok = I.inspect(
        p,
        "png",
        plan={"px": [30, 20], "ppi": 150},
        policy="strict",
        profile={"min_raster_dpi": 150},
    )
    assert strict_ok["policy"]["verdict"] == "accepted"
    no_profile = I.inspect(p, "png", plan={"px": [30, 20], "ppi": 150}, policy="strict")
    assert no_profile["policy"]["verdict"] == "rejected" and no_profile["policy"]["unknown"] == [
        "raster_density"
    ]
    # 文件说的密度与请求不符：standard 只记不拒；strict 也只是可选项（raster_density 才是必需）
    p.write_bytes(_png(30, 20, dpi=96))
    m = I.inspect(p, "png", plan={"px": [30, 20], "ppi": 600}, policy="standard")
    assert m["checks"]["dpi_tag"] == "failed" and m["policy"]["verdict"] == "accepted"
    assert "dpi_tag" in m["policy"]["optional_failed"]


def test_unknown_is_never_reported_as_verified_and_strict_blocks_on_it(tmp_path):
    """RC-070：None / unknown 不许按 truthy 当通过。"""
    p = tmp_path / "a.png"
    p.write_bytes(_png(30, 20))  # 没有 pHYs
    m = I.inspect(
        p,
        "png",
        plan={"px": [30, 20], "ppi": 300},
        policy="strict",
        profile={"min_raster_dpi": 300},
    )
    assert m["checks"]["dpi_tag"] == "unknown" and m["checks"]["raster_density"] == "verified"
    assert m["policy"]["verdict"] == "accepted"  # dpi_tag 是可选项：unknown 说明、不阻断
    assert "dpi_tag" in m["policy"]["optional_unknown"]
    m = I.inspect(
        p, "png", plan={"px": None, "ppi": 300}, policy="strict", profile={"min_raster_dpi": 300}
    )
    assert m["checks"]["size"] == "unknown" and m["policy"]["verdict"] == "rejected"
    m = I.inspect(p, "png", plan={"px": None, "ppi": 300}, policy="standard")
    assert m["checks"]["size"] == "unknown" and m["policy"]["verdict"] == "accepted"
    for v in m["checks"].values():
        assert v in I.VERDICTS
    with pytest.raises(ValueError):
        I.inspect(p, "png", plan={}, policy="lenient")


# ---------------------------------------------------------------------------
# ToUnicode 解析
# ---------------------------------------------------------------------------
def test_tounicode_parser_handles_bfchar_bfrange_and_array_ranges():
    cmap = b"""
1 begincodespacerange <0000> <FFFF> endcodespacerange
2 beginbfchar <0001> <0048> <0002> <00E9> endbfchar
2 beginbfrange <0010> <0012> <0061> <0020> <0021> [<0041> <0042>] endbfrange
"""
    mapping, n = I.parse_tounicode(cmap)
    assert n == 2
    assert mapping[1] == "H" and mapping[2] == "é"
    assert mapping[0x10] == "a" and mapping[0x11] == "b" and mapping[0x12] == "c"
    assert mapping[0x20] == "A" and mapping[0x21] == "B"
    one_byte, n = I.parse_tounicode(b"1 begincodespacerange <00> <FF> endcodespacerange")
    assert n == 1 and one_byte == {}


# ---------------------------------------------------------------------------
# PDF（pikepdf）
# ---------------------------------------------------------------------------
@needs_pikepdf
def test_pdf_observation_reads_the_visible_size_with_cropbox_rotate_and_userunit(tmp_path):
    import pikepdf

    o = I.observe_pdf(FIXTURE / "page.pdf")
    assert o["integrity"] == "verified" and o["size_pt"] == [
        270.0,
        160.0,
    ]  # CropBox 而不是 MediaBox
    assert o["carrier"] == "vector" and o["census"]["paths"] == 3 and o["census"]["text"] == 1
    assert o["fonts_used"][0]["name"] == "Helvetica" and o["fonts_used"][0]["embedded"] == "failed"
    assert o["text_lines"] == [
        "U00 fixture: y = 3x + 1"
    ]  # 简单字体的 ASCII 段按 StandardEncoding 读
    with pikepdf.open(str(FIXTURE / "page.pdf")) as pdf:
        pdf.pages[0].obj["/Rotate"] = 90
        pdf.pages[0].obj["/UserUnit"] = 2
        pdf.save(str(tmp_path / "ru.pdf"))
    o = I.observe_pdf(tmp_path / "ru.pdf")
    assert o["size_pt"] == [320.0, 540.0] and o["rotate"] == 90 and o["user_unit"] == 2.0


@needs_pikepdf
def test_a_pdf_whose_page_box_was_altered_after_writing_fails_the_size_check(tmp_path):
    """RC-063 / RC-064：改文件不改计划——量的是文件里的页盒，不是计划里的宽高。"""
    import pikepdf

    plan = {"page_pt": [270.0, 160.0], "vector": True}
    assert I.inspect(FIXTURE / "page.pdf", "pdf", plan=plan)["checks"]["size"] == "verified"
    with pikepdf.open(str(FIXTURE / "page.pdf")) as pdf:
        pdf.pages[0].obj["/CropBox"] = [15, 10, 200, 170]
        pdf.save(str(tmp_path / "shrunk.pdf"))
    m = I.inspect(tmp_path / "shrunk.pdf", "pdf", plan=plan)
    assert m["checks"]["size"] == "failed" and m["policy"]["verdict"] == "rejected"
    assert m["plan"] == plan  # 计划原样，不被观测改写


@needs_pikepdf
def test_a_truncated_or_encrypted_pdf_is_integrity_failed(tmp_path):
    import pikepdf

    data = (FIXTURE / "page.pdf").read_bytes()
    (tmp_path / "cut.pdf").write_bytes(data[: len(data) // 2])
    m = I.inspect(tmp_path / "cut.pdf", "pdf", plan={"page_pt": [270, 160]})
    assert m["checks"]["integrity"] == "failed" and m["policy"]["verdict"] == "rejected"
    assert all(v == "unknown" for k, v in m["checks"].items() if k != "integrity")
    with pikepdf.open(str(FIXTURE / "page.pdf")) as pdf:
        pdf.save(str(tmp_path / "enc.pdf"), encryption=pikepdf.Encryption(owner="o", user="u"))
    o = I.observe_pdf(tmp_path / "enc.pdf")
    assert o["integrity"] == "failed" and "encrypted" in o["problems"]


@needs_pikepdf
def test_fonts_are_reported_as_used_not_merely_declared_and_forms_are_walked(tmp_path):
    """RC-065：顶层声明了 F1 却没用，Form 里用了 F2——用到的是 F2。"""
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))
    f1 = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont="/Unused")
    )
    f2 = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont="/Inner")
    )
    form = pikepdf.Stream(pdf, b"BT /F2 10 Tf 5 5 Td (hi) Tj ET")
    form["/Type"], form["/Subtype"], form["/BBox"] = (
        pikepdf.Name.XObject,
        pikepdf.Name.Form,
        [0, 0, 100, 100],
    )
    form["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F2=f2))
    form = pdf.make_indirect(form)
    page.obj["/Resources"] = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=f1), XObject=pikepdf.Dictionary(X0=form)
    )
    page.obj["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, b"q /X0 Do Q"))
    pdf.save(str(tmp_path / "forms.pdf"))
    o = I.observe_pdf(tmp_path / "forms.pdf")
    assert o["fonts_declared"] == ["Unused"]
    assert [f["name"] for f in o["fonts_used"]] == ["Inner"]
    assert o["text_lines"] == ["hi"] and o["census"]["forms"] == 1


@needs_pikepdf
def test_a_self_referencing_form_exhausts_the_budget_and_everything_downstream_is_unknown(tmp_path):
    """RC-072：无限递归被预算挡住，之后的普查 / 字体 / 文字 / 位图全 unknown——不是 pass。"""
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))
    form = pikepdf.Stream(pdf, b"q /X0 Do Q")
    form["/Type"], form["/Subtype"], form["/BBox"] = (
        pikepdf.Name.XObject,
        pikepdf.Name.Form,
        [0, 0, 100, 100],
    )
    form = pdf.make_indirect(form)
    form["/Resources"] = pikepdf.Dictionary(XObject=pikepdf.Dictionary(X0=form))
    page.obj["/Resources"] = pikepdf.Dictionary(XObject=pikepdf.Dictionary(X0=form))
    page.obj["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, b"q /X0 Do Q"))
    pdf.save(str(tmp_path / "loop.pdf"))
    m = I.inspect(
        tmp_path / "loop.pdf",
        "pdf",
        plan={"page_pt": [100, 100], "vector": True, "text": ["x"]},
        policy="strict",
        profile={"min_raster_dpi": 300},
    )
    assert m["observed"]["budget_exceeded"] == "depth"
    assert m["checks"]["integrity"] == "verified" and m["checks"]["size"] == "verified"
    for k in ("carrier", "fonts_embedded", "text_layer", "image_ppi"):
        assert m["checks"][k] == "unknown", k
    assert m["policy"]["verdict"] == "rejected" and set(m["policy"]["unknown"]) >= {
        "carrier",
        "text_layer",
    }
    # standard：同一份文件可以交付，unknown 只在说明里
    m2 = I.inspect(tmp_path / "loop.pdf", "pdf", plan={"page_pt": [100, 100], "vector": True})
    assert m2["policy"]["verdict"] == "accepted" and "carrier" in m2["policy"]["optional_unknown"]


@needs_pikepdf
def test_image_effective_ppi_comes_from_pixels_over_the_accumulated_ctm(tmp_path):
    """RC-068：600 dpi 的请求不等于文件里那张位图的有效 ppi——按像素 ÷ 累计 CTM 算。"""
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    img = pikepdf.Stream(pdf, b"\x00" * (10 * 10 * 3))
    img["/Type"], img["/Subtype"] = pikepdf.Name.XObject, pikepdf.Name.Image
    img["/Width"], img["/Height"], img["/ColorSpace"], img["/BitsPerComponent"] = (
        10,
        10,
        pikepdf.Name.DeviceRGB,
        8,
    )
    img = pdf.make_indirect(img)
    page.obj["/Resources"] = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img))
    # 外层 cm 缩 0.5，内层 cm 把 10 px 铺到 72 pt：有效宽 36 pt → 20 ppi
    page.obj["/Contents"] = pdf.make_indirect(
        pikepdf.Stream(pdf, b"q 0.5 0 0 0.5 0 0 cm q 72 0 0 72 10 10 cm /Im0 Do Q Q")
    )
    pdf.save(str(tmp_path / "img.pdf"))
    o = I.observe_pdf(tmp_path / "img.pdf")
    assert o["carrier"] == "raster" and o["images"][0]["px"] == [10, 10]
    assert o["images"][0]["ppi"] == [20.0, 20.0]
    m = I.inspect(
        tmp_path / "img.pdf",
        "pdf",
        plan={"page_pt": [200, 200], "vector": False},
        policy="strict",
        profile={"min_raster_dpi": 300},
    )
    assert m["checks"]["image_ppi"] == "failed" and m["policy"]["verdict"] == "rejected"
    m = I.inspect(
        tmp_path / "img.pdf",
        "pdf",
        plan={"page_pt": [200, 200], "vector": False},
        policy="strict",
        profile={"min_raster_dpi": 20},
    )
    assert m["checks"]["image_ppi"] == "verified" and m["checks"]["carrier"] == "verified"
    # 计划说矢量、文件全是位图 → carrier 失败（RC-067 must_fail：所有 PDF 都 vector=True）
    m = I.inspect(tmp_path / "img.pdf", "pdf", plan={"page_pt": [200, 200], "vector": True})
    assert m["checks"]["carrier"] == "failed"


@needs_candidate
def test_the_candidate_writer_output_verifies_fonts_text_and_carrier_under_strict(tmp_path):
    """正例：候选写入器的产物在严格政策下全部必需项 verified；文字层按抽回来的字符串比（不是 ToUnicode 存在）。"""
    from tavotto.rendercore import fonts

    if fonts.FontRegistry.discover().missing:
        pytest.skip("批准字体不全（not_run）")
    import os

    from tavotto import pdfbackend

    os.environ[pdfbackend.BACKEND_ENV] = pdfbackend.BACKEND_RENDERCORE
    try:
        with pdfbackend.compose(80, 40) as cv:
            cv.place(
                {
                    "type": "text",
                    "id": "t",
                    "text": "Hello ×10⁵ 图",
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 70,
                    "h_mm": 10,
                    "size_pt": 10,
                },
                300,
                lambda o, d: None,
            )
            cv.save_pdf(tmp_path / "t.pdf")
    finally:
        os.environ.pop(pdfbackend.BACKEND_ENV, None)
    plan = {"page_pt": [80 * 72 / 25.4, 40 * 72 / 25.4], "vector": True, "text": ["Hello ×10⁵ 图"]}
    m = I.inspect(
        tmp_path / "t.pdf", "pdf", plan=plan, policy="strict", profile={"min_raster_dpi": 300}
    )
    assert m["policy"]["verdict"] == "accepted", m["checks"]
    assert m["checks"]["fonts_embedded"] == "verified" and m["checks"]["text_layer"] == "verified"
    assert m["checks"]["carrier"] == "verified" and m["checks"]["image_ppi"] == "not_applicable"
    # 负例①：期望的文字换一个字 → 文字层 failed（ToUnicode 还在，映射却不是那句话）
    m = I.inspect(
        tmp_path / "t.pdf",
        "pdf",
        plan={**plan, "text": ["Hello ×10⁶ 图"]},
        policy="strict",
        profile={"min_raster_dpi": 300},
    )
    assert m["checks"]["text_layer"] == "failed" and m["policy"]["verdict"] == "rejected"
    # 负例②：把字体程序摘掉（有名无 FontFile）→ fonts_embedded failed（RC-066）
    import pikepdf

    with pikepdf.open(str(tmp_path / "t.pdf")) as pdf:
        for _name, font in pdf.pages[0].obj["/Resources"]["/Font"].items():
            desc = font["/DescendantFonts"][0]["/FontDescriptor"]
            for k in ("/FontFile", "/FontFile2", "/FontFile3"):
                if k in desc:
                    del desc[k]
        pdf.save(str(tmp_path / "stripped.pdf"))
    m = I.inspect(
        tmp_path / "stripped.pdf",
        "pdf",
        plan=plan,
        policy="strict",
        profile={"min_raster_dpi": 300},
    )
    assert m["checks"]["fonts_embedded"] == "failed" and m["policy"]["verdict"] == "rejected"
    # 负例③：ToUnicode 改成错的映射（存在 ≠ 正确）→ 文字层 failed
    with pikepdf.open(str(tmp_path / "t.pdf")) as pdf:
        for _name, font in pdf.pages[0].obj["/Resources"]["/Font"].items():
            tu = font["/ToUnicode"].read_bytes()
            font["/ToUnicode"] = pikepdf.Stream(pdf, tu.replace(b"<0048>", b"<0058>"))  # H → X
        pdf.save(str(tmp_path / "wrongmap.pdf"))
    m = I.inspect(
        tmp_path / "wrongmap.pdf",
        "pdf",
        plan=plan,
        policy="strict",
        profile={"min_raster_dpi": 300},
    )
    assert m["checks"]["text_layer"] == "failed"
    # standard 下同一份坏映射文件仍可交付（可选项失败只说明）
    m = I.inspect(tmp_path / "wrongmap.pdf", "pdf", plan=plan)
    assert m["policy"]["verdict"] == "accepted" and "text_layer" in m["policy"]["optional_failed"]


def test_summary_is_a_projection_without_the_bulky_observations(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(_png(30, 20, dpi=300))
    m = I.inspect(
        p,
        "png",
        plan={"px": [30, 20], "ppi": 300, "plan_identity": "sha256:abc", "backend": "rendercore"},
    )
    s = I.summary(m)
    assert (
        s["verdict"] == "accepted"
        and s["checks"] == m["checks"]
        and s["sha256"] == m["artifact"]["sha256"]
    )
    assert (
        s["plan_identity"] == "sha256:abc" and s["backend"] == "rendercore" and "observed" not in s
    )
