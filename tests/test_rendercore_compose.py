"""RenderCore 合成：外来页 Form XObject 导入、页盒 / Rotate / UserUnit、crop / 翻转 / 旋转组合、透明组、
同名资源、位图放置（统一实施包 U07，ADR 0065）。

尺子与写入侧不同源（RC-095）：合成源 PDF 由**本文件用 pikepdf 直接拼内容流**（不经写入器），落点期望
由本文件的 `_expect()` 按「顶原点归一化坐标 → 翻转 → 顺时针旋转 → 填满目标框」**独立**算出（不调
`placement`），像素由 pypdfium2 栅格化后采样，结构由纯标准库 `pdfread` 读。

跑的前提：`tavotto[rendercore]` 候选包 + 批准字体。缺则 skip 并说明理由（skip 不是绿）。
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import threading
from pathlib import Path

import pytest

from tavotto.rendercore import fonts, ir, pdfwriter

SUPPORT = Path(__file__).resolve().parent / "support"
import sys  # noqa: E402

if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import pdfread  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "foundation" / "pdf_png_assets"
PAGE_W, PAGE_H = 400.0, 300.0
SCALE = 2


def _candidates() -> None:
    from tavotto.rendercore.hbshaper import CandidatePackagesMissing, require

    try:
        require("pikepdf", "fontTools", "uharfbuzz", "pypdfium2", "PIL")
    except CandidatePackagesMissing as exc:
        pytest.skip(f"候选包未装（not_run）：{exc}")


@pytest.fixture(scope="module")
def provider():
    _candidates()
    from tavotto.rendercore.hbshaper import HbFaceProvider

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}；先跑 scripts/fetch_fonts.py")
    return HbFaceProvider(reg)


@pytest.fixture(scope="module")
def pdfium():
    _candidates()
    import pypdfium2

    return pypdfium2


# ---------------------------------------------------------------- 合成源（测试侧用 pikepdf 直接拼）


def _pdf(
    content: bytes,
    *,
    media=(0, 0, 200, 100),
    crop=None,
    rotate=None,
    userunit=None,
    resources=None,
    inherit=False,
    extra_page=None,
    catalog_extra=None,
) -> bytes:
    import pikepdf

    pdf = pikepdf.new()
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page, MediaBox=list(media), Contents=pdf.make_stream(content)
    )
    if crop is not None:
        page["/CropBox"] = list(crop)
    if rotate is not None:
        page["/Rotate"] = rotate
    if userunit is not None:
        page["/UserUnit"] = userunit
    res = resources(pdf) if resources else pikepdf.Dictionary()
    if inherit:
        pdf.Root.Pages.Resources = res
    else:
        page["/Resources"] = res
    if extra_page:
        extra_page(pdf, page)
    pdf.pages.append(pikepdf.Page(page))
    if catalog_extra:
        catalog_extra(pdf)
    out = io.BytesIO()
    pdf.save(out, deterministic_id=True)
    return out.getvalue()


#: 四色象限（相对可见框）：左上红、右上绿、左下蓝、右下黄。可见框 = CropBox [10 5 190 95]（非零原点）。
QUAD_CROP = (10, 5, 190, 95)
QUAD_COLORS = {"TL": (255, 0, 0), "TR": (0, 255, 0), "BL": (0, 0, 255), "BR": (255, 255, 0)}


def _quadrant_pdf(**kw) -> bytes:
    x0, y0, x1, y1 = QUAD_CROP
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    content = (
        f"1 0 0 rg {x0} {my} {mx - x0} {y1 - my} re f "  # TL
        f"0 1 0 rg {mx} {my} {x1 - mx} {y1 - my} re f "  # TR
        f"0 0 1 rg {x0} {y0} {mx - x0} {my - y0} re f "  # BL
        f"1 1 0 rg {mx} {y0} {x1 - mx} {my - y0} re f "  # BR
        f"0 g {0} {0} {x0} {y0} re f "  # CropBox 之外（MediaBox 内）的一块黑：不该出现
    ).encode()
    return _pdf(content, crop=QUAD_CROP, **kw)


def _overlap_pdf() -> bytes:
    """两块**不透明**矩形重叠：蓝 (20,20,100×60) 在下、红 (60,40,100×40) 在上。"""
    return _pdf(b"0 0 1 rg 20 20 100 60 re f 1 0 0 rg 60 40 100 40 re f")


def _same_name_pdf(text: str, font: str, rgb: tuple[float, float, float], alpha: float) -> bytes:
    """/F1、/X1、/GS0 三个同名资源，各自内容不同。"""

    def resources(pdf):
        import pikepdf

        img = pikepdf.Stream(pdf, bytes(int(c * 255) for c in rgb))
        img["/Type"] = pikepdf.Name.XObject
        img["/Subtype"] = pikepdf.Name.Image
        img["/Width"] = 1
        img["/Height"] = 1
        img["/ColorSpace"] = pikepdf.Name.DeviceRGB
        img["/BitsPerComponent"] = 8
        return pikepdf.Dictionary(
            Font=pikepdf.Dictionary(
                F1=pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type1,
                    BaseFont=pikepdf.Name("/" + font),
                )
            ),
            XObject=pikepdf.Dictionary(X1=pdf.make_indirect(img)),
            ExtGState=pikepdf.Dictionary(
                GS0=pikepdf.Dictionary(Type=pikepdf.Name.ExtGState, ca=alpha, CA=alpha)
            ),
        )

    content = (
        f"q 100 0 0 50 50 25 cm /X1 Do Q "
        f"q /GS0 gs 0 g 0 0 40 100 re f Q "
        f"BT /F1 14 Tf 60 80 Td ({text}) Tj ET"
    ).encode()
    return _pdf(content, resources=resources)


def _untrusted_pdf() -> bytes:
    """打开就想跑 JavaScript、带链接注释与页面动作的源页。"""

    def extra_page(pdf, page):
        import pikepdf

        link = pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.Link,
            Rect=[0, 0, 50, 50],
            A=pikepdf.Dictionary(S=pikepdf.Name.URI, URI=pikepdf.String("http://example.invalid")),
        )
        page["/Annots"] = pikepdf.Array([pdf.make_indirect(link)])
        page["/AA"] = pikepdf.Dictionary(
            O=pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String("app.alert(1)"))
        )

    def catalog_extra(pdf):
        import pikepdf

        pdf.Root["/OpenAction"] = pikepdf.Dictionary(
            S=pikepdf.Name.JavaScript, JS=pikepdf.String("app.alert(2)")
        )
        pdf.Root["/Names"] = pikepdf.Dictionary(
            JavaScript=pikepdf.Dictionary(
                Names=pikepdf.Array(
                    [
                        pikepdf.String("boot"),
                        pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String("x")),
                    ]
                )
            )
        )

    return _pdf(b"0 0 1 rg 20 20 100 60 re f", extra_page=extra_page, catalog_extra=catalog_extra)


def _encrypted_pdf() -> bytes:
    import pikepdf

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 100))
    out = io.BytesIO()
    pdf.save(out, encryption=pikepdf.Encryption(owner="o", user="u", R=6))
    return out.getvalue()


# ---------------------------------------------------------------- 写 / 读 / 采样


def _res(source_id: str, kind: str, data: bytes) -> ir.FileResource:
    return ir.FileResource(
        source_id, kind, hashlib.sha256(data).hexdigest(), "static", "sha256:" + "0" * 64
    )


def _write(
    tmp_path: Path, provider, children, resources, files, name="out.pdf", bg=(1.0, 1.0, 1.0)
):
    page = ir.Page(PAGE_W, PAGE_H, tuple(children), dict(resources), bg)
    out = tmp_path / name
    facts = pdfwriter.write_pdf(page, out, provider, files)
    return out, facts


def _raster(pdfium, path: Path, scale: int = SCALE):
    doc = pdfium.PdfDocument(str(path))
    return doc[0].render(scale=scale).to_pil().convert("RGBA")


def _px(img, x: float, y_up: float, scale: int = SCALE) -> tuple[int, int, int]:
    """页面坐标（pt，y 向上）→ 像素。"""
    r, g, b, _a = img.getpixel((int(x * scale), int((PAGE_H - y_up) * scale)))
    return (r, g, b)


def _close(a, b, tol=6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _expect(
    u: float,
    v: float,
    rect: tuple[float, float, float, float],
    *,
    rot: int = 0,
    flip_h: bool = False,
    flip_v: bool = False,
) -> tuple[float, float]:
    """源可见框（或 crop 块）里**顶原点归一化**的点 (u, v) 落在页面的哪里（pt，y 向上）——本文件自己的
    合同实现：先翻转、再顺时针转 rot、再填满目标框。与 `placement.place()` 不共享一行代码。"""
    if flip_h:
        u = 1.0 - u
    if flip_v:
        v = 1.0 - v
    for _ in range((rot // 90) % 4):
        u, v = 1.0 - v, u  # 顶原点坐标系里顺时针 90°：左上 → 右上
    tx, ty, tw, th = rect
    return (tx + u * tw, ty + th - v * th)


def _census(pdfium, path: Path) -> dict[int, int]:
    doc = pdfium.PdfDocument(str(path))
    kinds = [o.type for o in doc[0].get_objects()]
    return {k: kinds.count(k) for k in set(kinds)}


PDFIUM_TEXT, PDFIUM_PATH, PDFIUM_IMAGE, PDFIUM_FORM = 1, 2, 3, 5


# ================================================================ 页盒（RC-038）


def test_the_visible_box_is_the_crop_box_not_the_media_box(provider, pdfium, tmp_path):
    """U00 夹具 page.pdf：MediaBox 300×200、CropBox [15 10 285 170]。目标 (20,30,135×80)（缩放 0.5）：
    源蓝矩形 (40..140, 40..100) 的左下角落在 (32.5, 45)、一个蓝色内点落在蓝上；CropBox 之外
    MediaBox 之内的区域**不画**；目标框之外是白。"""
    data = (FIXTURE / "page.pdf").read_bytes()
    out, facts = _write(
        tmp_path,
        provider,
        [ir.ImportedPage("pg", (20, 30, 135, 80), object_id="p")],
        {"pg": _res("figs/page.pdf", "pdf", data)},
        {"pg": data},
    )
    assert facts.imported_pages[0]["visible"] == [15.0, 10.0, 270.0, 160.0]
    img = _raster(pdfium, out)
    # 源 (60, 45) 在蓝矩形里、不在红矩形 (90..190, 60..120) 里、不在折线上
    x, y = 20 + (60 - 15) * 0.5, 30 + (45 - 10) * 0.5
    assert _close(_px(img, x, y), (0, 0, 255)), _px(img, x, y)
    # 源 (100, 80)：红 α=0.5 盖在蓝上 → 紫
    x, y = 20 + (100 - 15) * 0.5, 30 + (80 - 10) * 0.5
    assert _close(_px(img, x, y), (128, 0, 128)), _px(img, x, y)
    # 目标框左边缘之外 1pt：白（CropBox 左边那 15pt 的 MediaBox 区域没有被画出来）
    assert _px(img, 18, 70) == (255, 255, 255)
    assert _px(img, 20 + 135 + 2, 70) == (255, 255, 255)
    assert _census(pdfium, out)[PDFIUM_FORM] == 1


# ================================================================ Rotate / UserUnit（RC-039）


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_source_rotate_is_applied_exactly_once(provider, pdfium, tmp_path, rotate):
    """源页 /Rotate 是「显示时顺时针转」：TL 象限（红）在 /Rotate 90 的页上显示在 TR。导入之后它必须
    落在目标的 TR——而不是 TL（忽略）或 BR（应用两次）。四档各一次。"""
    data = _quadrant_pdf(rotate=rotate)
    rect = (40.0, 40.0, 180.0, 90.0) if rotate in (0, 180) else (40.0, 40.0, 90.0, 180.0)
    out, facts = _write(
        tmp_path,
        provider,
        [ir.ImportedPage("pg", rect)],
        {"pg": _res("q.pdf", "pdf", data)},
        {"pg": data},
        name=f"rot{rotate}.pdf",
    )
    img = _raster(pdfium, out)
    # 源可见框里顶原点归一化的象限中心 → 经 /Rotate 显示后的位置（本文件自己转）
    for name, (u, v) in {
        "TL": (0.25, 0.25),
        "TR": (0.75, 0.25),
        "BL": (0.25, 0.75),
        "BR": (0.75, 0.75),
    }.items():
        x, y = _expect(u, v, rect, rot=rotate)
        assert _close(_px(img, x, y), QUAD_COLORS[name]), (rotate, name, _px(img, x, y))
    if rotate:
        assert facts.imported_pages[0]["form_matrix"] is not None, "qpdf 该把 /Rotate 折进 /Matrix"


def test_userunit_scales_the_visible_box_but_the_fit_still_fills_the_rect(
    provider, pdfium, tmp_path
):
    """/UserUnit 2：可见框 360×180（qpdf 折进 /Matrix），映到目标框时缩放随之减半——象限仍各在各位，
    目标框仍被填满（RC-039 must_fail：忽略 UserUnit 时可见框算成 180×90，写进 facts 的 visible 就不对）。"""
    data = _quadrant_pdf(userunit=2)
    rect = (40.0, 40.0, 180.0, 90.0)
    out, facts = _write(
        tmp_path,
        provider,
        [ir.ImportedPage("pg", rect)],
        {"pg": _res("u.pdf", "pdf", data)},
        {"pg": data},
    )
    assert facts.imported_pages[0]["visible"][2:] == [360.0, 180.0]
    img = _raster(pdfium, out)
    for name, (u, v) in {"TL": (0.25, 0.25), "BR": (0.75, 0.75)}.items():
        x, y = _expect(u, v, rect)
        assert _close(_px(img, x, y), QUAD_COLORS[name]), (name, _px(img, x, y))
    assert _px(img, 41, 41) != (255, 255, 255) and _px(img, 219, 129) != (255, 255, 255)


# ================================================================ crop / 翻转 / 旋转组合（RC-044）


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
@pytest.mark.parametrize("flip", [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize("crop", [None, (0.5, 0.0, 0.5, 0.5)])
def test_crop_flip_rotate_combinations_land_each_quadrant_where_the_contract_says(
    provider, pdfium, tmp_path, rot, flip, crop
):
    """组合矩阵而不是单开关：4 转 × 4 翻 × 2 crop = 32 组，每组把象限中心（crop 时是 crop 块里的四个
    子象限）映过去采样。RC-044 must_fail：镜像发生在错误坐标空间。"""
    flip_h, flip_v = flip
    data = _quadrant_pdf()
    rect = (60.0, 60.0, 160.0, 80.0) if rot in (0, 180) else (60.0, 60.0, 80.0, 160.0)
    node = ir.ImportedPage(
        "pg", rect, crop=crop, rotate_cw_deg=float(rot), flip_h=flip_h, flip_v=flip_v
    )
    out, _ = _write(
        tmp_path,
        provider,
        [node],
        {"pg": _res("q.pdf", "pdf", data)},
        {"pg": data},
        name=f"c{rot}{int(flip_h)}{int(flip_v)}{int(crop is not None)}.pdf",
    )
    img = _raster(pdfium, out)
    if crop is None:
        samples = {"TL": (0.25, 0.25), "TR": (0.75, 0.25), "BL": (0.25, 0.75), "BR": (0.75, 0.75)}
    else:
        # crop = 右上象限（全绿）：crop 块里的四个点都该是绿；crop 之外的红不该出现在目标框里
        samples = {
            "TR": (0.25, 0.25),
            "TR2": (0.75, 0.75),
            "TR3": (0.25, 0.75),
            "TR4": (0.75, 0.25),
        }
    for name, (u, v) in samples.items():
        x, y = _expect(u, v, rect, rot=rot, flip_h=flip_h, flip_v=flip_v)
        want = QUAD_COLORS[name[:2]]
        assert _close(_px(img, x, y), want), (rot, flip, crop, name, _px(img, x, y), want)
    # 目标框四边之外 4pt 一圈必须是白：crop 掉的另外三个象限不许溢出到框外（没有 `re W n` 就会）
    tx, ty, tw, th = rect
    for x, y in (
        (tx - 4, ty + th / 2),
        (tx + tw + 4, ty + th / 2),
        (tx + tw / 2, ty - 4),
        (tx + tw / 2, ty + th + 4),
    ):
        assert _px(img, x, y) == (255, 255, 255), (rot, flip, crop, (x, y), _px(img, x, y))


# ================================================================ 透明组（RC-041 / RC-042）


def test_panel_opacity_is_a_transparency_group_not_per_object_alpha(provider, pdfium, tmp_path):
    """源里蓝下红上**不透明**重叠；面板 opacity 0.5：重叠区 = 红以 0.5 合成到白 = (255,128,128)。
    逐对象 alpha 会给 (191,64,128)（红 0.5 盖在「蓝 0.5 盖白」上）——两个数必须分得开。"""
    data = _overlap_pdf()
    rect = (50.0, 50.0, 200.0, 100.0)  # 源 200×100 → 缩放 1
    out, facts = _write(
        tmp_path,
        provider,
        [ir.ImportedPage("pg", rect, opacity=0.5)],
        {"pg": _res("o.pdf", "pdf", data)},
        {"pg": data},
    )
    assert facts.transparency_groups == 1 and facts.imported_pages[0]["transparency_group"]
    img = _raster(pdfium, out)
    overlap = _px(img, 50 + 100, 50 + 70)  # 源 (100, 70)：红盖蓝
    assert _close(overlap, (255, 128, 128)), overlap
    assert not _close(overlap, (191, 64, 128), tol=20)
    blue_only = _px(img, 50 + 30, 50 + 30)
    assert _close(blue_only, (128, 128, 255)), blue_only
    # 仍是矢量：form 对象在、没有 image 对象
    census = _census(pdfium, out)
    assert census.get(PDFIUM_FORM, 0) >= 1 and PDFIUM_IMAGE not in census
    objs = pdfread.objects(out.read_bytes())
    assert any(b"/Group" in h and b"/Transparency" in h for h, _ in objs.values())


def test_the_u00_fixture_with_internal_alpha_composites_as_a_group(provider, pdfium, tmp_path):
    """page.pdf 内部红 α=0.5 盖蓝（紫 (128,0,128)）；整页 0.5 → 紫以 0.5 合成到白 = (191,128,191)；
    逐对象会给 (160,96,191)（ADR 0055 §2.1 的那一对数字）。"""
    data = (FIXTURE / "page.pdf").read_bytes()
    out, _ = _write(
        tmp_path,
        provider,
        [ir.ImportedPage("pg", (20, 30, 270, 160), opacity=0.5)],
        {"pg": _res("figs/page.pdf", "pdf", data)},
        {"pg": data},
    )
    img = _raster(pdfium, out)
    got = _px(img, 20 + (100 - 15), 30 + (80 - 10))
    assert _close(got, (191, 128, 191)), got
    assert not _close(got, (160, 96, 191), tol=12)


def test_opacity_zero_paints_nothing_but_the_vector_object_is_still_there(
    provider, pdfium, tmp_path
):
    """RC-042 must_fail：`or 1.0` 把 0 变 1。opacity=0 → 像素全白；对象仍在（form + 文字层），不是被丢掉。"""
    data = (FIXTURE / "page.pdf").read_bytes()
    out, facts = _write(
        tmp_path,
        provider,
        [ir.ImportedPage("pg", (20, 30, 270, 160), opacity=0.0)],
        {"pg": _res("figs/page.pdf", "pdf", data)},
        {"pg": data},
    )
    assert facts.imported_pages[0]["opacity"] == 0.0
    img = _raster(pdfium, out)
    assert _px(img, 20 + 45, 30 + 35) == (255, 255, 255)
    assert _px(img, 20 + 85, 30 + 70) == (255, 255, 255)
    doc = pdfium.PdfDocument(str(out))
    assert "U00 fixture" in doc[0].get_textpage().get_text_range()
    assert PDFIUM_FORM in _census(pdfium, out)


# ================================================================ 镜像保矢量（RC-040）


def test_a_mirrored_import_keeps_its_vector_and_text_layer(provider, pdfium, tmp_path):
    """flip_h：墨从左半移到右半（旧 test_export_panel_flip_h 的合同），且对象普查里没有 image、文字层
    仍抽得到——旧后端这一档退位图，新写入器不退。"""
    data = (FIXTURE / "page.pdf").read_bytes()
    rect = (20.0, 30.0, 270.0, 160.0)
    plain, _ = _write(
        tmp_path,
        provider,
        [ir.ImportedPage("pg", rect)],
        {"pg": _res("figs/page.pdf", "pdf", data)},
        {"pg": data},
        name="plain.pdf",
    )
    flipped, _ = _write(
        tmp_path,
        provider,
        [ir.ImportedPage("pg", rect, flip_h=True)],
        {"pg": _res("figs/page.pdf", "pdf", data)},
        {"pg": data},
        name="flip.pdf",
    )

    def ink_halves(path):
        img = _raster(pdfium, path)
        left = right = 0
        x0, x1 = int(rect[0] * SCALE), int((rect[0] + rect[2]) * SCALE)
        y0, y1 = int((PAGE_H - rect[1] - rect[3]) * SCALE), int((PAGE_H - rect[1]) * SCALE)
        mid = (x0 + x1) // 2
        for y in range(y0, y1, 2):
            for x in range(x0, x1, 2):
                r, g, b, _ = img.getpixel((x, y))
                v = 765 - r - g - b
                if x < mid:
                    left += v
                else:
                    right += v
        return left, right

    l0, r0 = ink_halves(plain)
    l1, r1 = ink_halves(flipped)
    assert l0 > r0 and r1 > l1, (l0, r0, l1, r1)
    assert abs(l0 - r1) < 0.05 * l0 and abs(r0 - l1) < 0.05 * max(r0, 1), (l0, r0, l1, r1)
    census = _census(pdfium, flipped)
    assert PDFIUM_IMAGE not in census and census.get(PDFIUM_FORM, 0) == 1
    doc = pdfium.PdfDocument(str(flipped))
    assert "U00 fixture: y = 3x + 1" in doc[0].get_textpage().get_text_range()


# ================================================================ 同名资源（RC-043）


def test_same_named_resources_in_two_sources_do_not_collide(provider, pdfium, tmp_path):
    """两个源各有 /F1（Helvetica / Courier）、/X1（红 / 绿 1×1 图）、/GS0（α 0.25 / 0.75）。并排放置：
    两段文字都抽得到，两块图各自的颜色，两块半透明黑各自的灰度。按名字去重的写入器会把第二个源画成
    第一个。"""
    a = _same_name_pdf("alpha-text", "Helvetica", (1, 0, 0), 0.25)
    b = _same_name_pdf("bravo-text", "Courier", (0, 1, 0), 0.75)
    out, facts = _write(
        tmp_path,
        provider,
        [ir.ImportedPage("a", (0, 100, 200, 100)), ir.ImportedPage("b", (200, 100, 200, 100))],
        {"a": _res("a.pdf", "pdf", a), "b": _res("b.pdf", "pdf", b)},
        {"a": a, "b": b},
    )
    img = _raster(pdfium, out)
    assert _close(_px(img, 100, 150), (255, 0, 0)) and _close(_px(img, 300, 150), (0, 255, 0))
    assert _close(_px(img, 20, 150), (191, 191, 191)) and _close(_px(img, 220, 150), (64, 64, 64))
    doc = pdfium.PdfDocument(str(out))
    text = doc[0].get_textpage().get_text_range()
    assert "alpha-text" in text and "bravo-text" in text
    objs = pdfread.objects(out.read_bytes())
    fonts_seen = {m for h, _ in objs.values() for m in re.findall(rb"/BaseFont /(\w+)", h)}
    assert {b"Helvetica", b"Courier"} <= fonts_seen


def test_two_instances_of_one_source_share_one_form_and_two_sources_get_two(provider, tmp_path):
    data = (FIXTURE / "page.pdf").read_bytes()
    other = _overlap_pdf()
    out, facts = _write(
        tmp_path,
        provider,
        [
            ir.ImportedPage("pg", (0, 0, 100, 60)),
            ir.ImportedPage("pg", (100, 0, 100, 60), rotate_cw_deg=180),
            ir.ImportedPage("o", (200, 0, 100, 50)),
        ],
        {"pg": _res("figs/page.pdf", "pdf", data), "o": _res("o.pdf", "pdf", other)},
        {"pg": data, "o": other},
    )
    objs = pdfread.objects(out.read_bytes())
    forms = [h for h, _ in objs.values() if b"/Subtype /Form" in h and b"/Group" not in h]
    assert len(forms) == 2, len(forms)  # 同一源两次 = 一份 form；另一个源另一份
    assert len(facts.imported_pages) == 3


def test_inherited_page_resources_come_along_with_the_form(provider, pdfium, tmp_path):
    """源页的 /Resources 在 /Pages 上（继承）：qpdf 推给页面后随 form 走，文字照样抽得到。"""

    def resources(pdf):
        import pikepdf

        return pikepdf.Dictionary(
            Font=pikepdf.Dictionary(
                F1=pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type1,
                    BaseFont=pikepdf.Name.Helvetica,
                )
            )
        )

    data = _pdf(b"BT /F1 20 Tf 20 40 Td (inherited) Tj ET", resources=resources, inherit=True)
    out, _ = _write(
        tmp_path,
        provider,
        [ir.ImportedPage("pg", (0, 0, 200, 100))],
        {"pg": _res("i.pdf", "pdf", data)},
        {"pg": data},
    )
    doc = pdfium.PdfDocument(str(out))
    assert "inherited" in doc[0].get_textpage().get_text_range()


# ================================================================ 不可信源（RC-046）


def test_actions_annotations_and_javascript_of_the_source_are_not_imported(
    provider, pdfium, tmp_path
):
    data = _untrusted_pdf()
    assert b"/JavaScript" in data and b"/Annots" in data and b"/OpenAction" in data
    out, _ = _write(
        tmp_path,
        provider,
        [ir.ImportedPage("pg", (0, 0, 200, 100))],
        {"pg": _res("bad.pdf", "pdf", data)},
        {"pg": data},
    )
    raw = out.read_bytes()
    for needle in (b"/JavaScript", b"/OpenAction", b"/Annots", b"/AA", b"/URI", b"app.alert"):
        assert needle not in raw, needle
    img = _raster(pdfium, out)
    assert _close(_px(img, 70, 50), (0, 0, 255))


def test_an_encrypted_source_is_refused_structurally(provider, tmp_path):
    data = _encrypted_pdf()
    with pytest.raises(pdfwriter.WriterError) as ei:
        _write(
            tmp_path,
            provider,
            [ir.ImportedPage("pg", (0, 0, 100, 100))],
            {"pg": _res("enc.pdf", "pdf", data)},
            {"pg": data},
        )
    assert ei.value.code == "source_unreadable" and ei.value.params["why"] == "encrypted"


def test_a_broken_source_and_a_missing_page_are_refused_structurally(provider, tmp_path):
    junk = b"%PDF-1.4 not really a pdf"
    with pytest.raises(pdfwriter.WriterError) as ei:
        _write(
            tmp_path,
            provider,
            [ir.ImportedPage("pg", (0, 0, 100, 100))],
            {"pg": _res("junk.pdf", "pdf", junk)},
            {"pg": junk},
        )
    assert ei.value.code == "source_unreadable" and ei.value.params["why"] == "broken"
    data = (FIXTURE / "page.pdf").read_bytes()
    with pytest.raises(pdfwriter.WriterError) as ei:
        _write(
            tmp_path,
            provider,
            [ir.ImportedPage("pg", (0, 0, 100, 100), page_index=3)],
            {"pg": _res("figs/page.pdf", "pdf", data)},
            {"pg": data},
        )
    assert ei.value.code == "source_unreadable" and ei.value.params["why"] == "page_index"


def test_source_bytes_must_match_the_resource_identity(provider, tmp_path):
    """RC-014 的第二道核：交进来的字节 hash 与资源身份不符 → `source_identity`；没交 → `source_bytes_missing`。"""
    data = (FIXTURE / "page.pdf").read_bytes()
    res = {"pg": _res("figs/page.pdf", "pdf", data)}
    with pytest.raises(pdfwriter.WriterError) as ei:
        _write(
            tmp_path, provider, [ir.ImportedPage("pg", (0, 0, 100, 100))], res, {"pg": data + b"\n"}
        )
    assert ei.value.code == "source_identity"
    with pytest.raises(pdfwriter.WriterError) as ei:
        _write(tmp_path, provider, [ir.ImportedPage("pg", (0, 0, 100, 100))], res, {})
    assert ei.value.code == "source_bytes_missing"
    assert not (tmp_path / "out.pdf").exists()


# ================================================================ 位图（Image）


def test_png_with_alpha_is_placed_with_an_smask_and_its_pixels_survive(provider, pdfium, tmp_path):
    """U00 original.png（64×48 RGBA：左半不透明蓝 (30,60,200)，右半红 (200,40,40) alpha 从 255 渐到 0）
    放大到 128×96：左半是蓝；右半第 x 列的 alpha = 255·(63−x)/31 合成到白。"""
    data = (FIXTURE / "original.png").read_bytes()
    rect = (20.0, 100.0, 128.0, 96.0)
    out, facts = _write(
        tmp_path,
        provider,
        [ir.Image("im", rect, object_id="i")],
        {"im": _res("figs/original.png", "png", data)},
        {"im": data},
    )
    f = facts.images[0]
    assert (f["width"], f["height"], f["channels"], f["encoding"], f["smask"]) == (
        64,
        48,
        4,
        "flate",
        True,
    )
    assert f["dpi"] == pytest.approx(300, abs=0.01)
    img = _raster(pdfium, out)
    assert _close(_px(img, 20 + 20, 100 + 48), (30, 60, 200))
    # 源第 40 列：alpha = 255·23/31 ≈ 189 → 红 (200,40,40) 以 0.742 合成到白 = (241, 96, 96)
    col = 40
    a = (63 - col) / 31
    want = tuple(round(c * a + 255 * (1 - a)) for c in (200, 40, 40))
    got = _px(img, 20 + (col + 0.5) * 2, 100 + 48)
    assert _close(got, want, tol=8), (got, want)
    objs = pdfread.objects(out.read_bytes())
    assert any(b"/SMask" in h for h, _ in objs.values())
    assert _census(pdfium, out).get(PDFIUM_IMAGE, 0) == 1


def test_an_rgb_jpeg_passes_through_as_dct_and_a_cmyk_one_is_decoded(provider, pdfium, tmp_path):
    from PIL import Image

    rgb = Image.new("RGB", (8, 4), (10, 200, 30))
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=95)
    jpg = buf.getvalue()
    cmyk = Image.new("CMYK", (8, 4), (0, 0, 0, 0))
    buf2 = io.BytesIO()
    cmyk.save(buf2, "JPEG")
    jpg_cmyk = buf2.getvalue()
    out, facts = _write(
        tmp_path,
        provider,
        [ir.Image("a", (0, 0, 80, 40)), ir.Image("b", (100, 0, 80, 40))],
        {"a": _res("a.jpg", "jpg", jpg), "b": _res("b.jpeg", "jpeg", jpg_cmyk)},
        {"a": jpg, "b": jpg_cmyk},
    )
    assert [f["encoding"] for f in facts.images] == ["dct", "flate"]
    raw = out.read_bytes()
    assert b"/DCTDecode" in raw and jpg[2:64] in raw  # 原 JPEG 字节原样在文件里
    img = _raster(pdfium, out)
    assert _close(_px(img, 40, 20), (10, 200, 30), tol=12)
    assert _close(_px(img, 140, 20), (255, 255, 255), tol=12)


def test_image_crop_flip_and_rotation_use_the_same_contract_as_pages(provider, pdfium, tmp_path):
    """original.png 左半蓝、右半红：crop 右半 + flip_h + 90°：目标框里应只有红（无蓝），且 alpha 渐变
    的方向随翻转与旋转一起走——顶原点归一化 (0, ·) 是不透明那一列（源第 32 列），flip_h 后到右边，
    转 90° 后到底边：目标框**底部**深红、顶部近白。"""
    data = (FIXTURE / "original.png").read_bytes()
    rect = (200.0, 50.0, 48.0, 128.0)
    out, _ = _write(
        tmp_path,
        provider,
        [ir.Image("im", rect, crop=(0.5, 0.0, 0.5, 1.0), flip_h=True, rotate_cw_deg=90)],
        {"im": _res("figs/original.png", "png", data)},
        {"im": data},
    )
    img = _raster(pdfium, out)
    bottom = _px(img, 200 + 24, 50 + 3)
    top = _px(img, 200 + 24, 50 + 125)
    assert _close(bottom, (200, 40, 40), tol=10), bottom
    assert top[0] > 240 and top[1] > 200, top  # 近白
    for x in range(202, 246, 4):
        for y in range(52, 176, 8):
            r, g, b = _px(img, x, y)
            assert not (b > 150 and r < 100), (x, y, (r, g, b))  # 没有蓝：crop 生效


def test_a_raster_whose_bytes_are_not_the_declared_kind_is_refused(provider, tmp_path):
    """扩展名说 jpg、字节是 PNG → `raster_kind_mismatch`；字节根本不是图（PDF）→ `raster_unreadable`。
    两种都不画一张空图。"""
    png = (FIXTURE / "original.png").read_bytes()
    with pytest.raises(pdfwriter.WriterError) as ei:
        _write(
            tmp_path,
            provider,
            [ir.Image("im", (0, 0, 10, 10))],
            {"im": _res("x.jpg", "jpg", png)},
            {"im": png},
        )
    assert ei.value.code == "source_unreadable" and ei.value.params["why"] == "raster_kind_mismatch"
    data = (FIXTURE / "page.pdf").read_bytes()
    with pytest.raises(pdfwriter.WriterError) as ei:
        _write(
            tmp_path,
            provider,
            [ir.Image("im", (0, 0, 10, 10))],
            {"im": _res("x.png", "png", data)},
            {"im": data},
        )
    assert ei.value.code == "source_unreadable" and ei.value.params["why"] == "raster_unreadable"
    assert not (tmp_path / "out.pdf").exists()


def test_a_huge_raster_is_refused_before_it_is_decoded(provider, tmp_path, monkeypatch):
    """Codex #463 P2：压缩得很小的高分辨率位图（IHDR 说 9000×9000 = 81M 像素，IDAT 几乎为空）在解码**之前**按
    头里的尺寸拒（`raster_too_large`，预算 64M），不分配整幅；JPEG 直通路同样按 SOF 尺寸拒。9000² 刻意落在
    Pillow 自己的炸弹闸（89M 警告 / 178M 报错）之下：响的必须是**我们的**预算。变异「不传预算」时 Pillow 会
    真去解——本用例把 Pillow 的 `load` 换成必爆的探针，证明拒绝发生在解码之前。"""
    import struct
    import zlib

    from PIL import ImageFile

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + tag
            + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
        )

    huge_png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 9000, 9000, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00" * 8))
        + chunk(b"IEND", b"")
    )

    def boom(self, *a, **k):  # 解码一旦开始就炸：证明预算判在它之前
        raise AssertionError("解码不该开始")

    monkeypatch.setattr(ImageFile.ImageFile, "load", boom)
    with pytest.raises(pdfwriter.WriterError) as ei:
        _write(
            tmp_path,
            provider,
            [ir.Image("im", (0, 0, 10, 10))],
            {"im": _res("huge.png", "png", huge_png)},
            {"im": huge_png},
        )
    assert ei.value.code == "source_unreadable" and ei.value.params["why"] == "raster_too_large"
    # JPEG：SOF 说 9000×9000（直通要真解一遍——但预算判在解码之前，`load` 探针同样不该被碰）
    huge_jpg = b"\xff\xd8\xff\xc0" + struct.pack(">HBHHB", 8, 8, 9000, 9000, 3) + b"\xff\xd9"
    with pytest.raises(pdfwriter.WriterError) as ei:
        _write(
            tmp_path,
            provider,
            [ir.Image("im", (0, 0, 10, 10))],
            {"im": _res("huge.jpg", "jpg", huge_jpg)},
            {"im": huge_jpg},
        )
    assert ei.value.params["why"] == "raster_too_large"


def test_a_jpeg_that_readers_cannot_decode_is_not_passed_through(provider, tmp_path):
    """Codex #463 P2：只有 SOI + 一个像样的 SOF 的 12 字节假 JPEG：不直通、`decode()` 解不开 → `raster_unreadable`；
    把一张好 JPEG 的熵编码段截掉一半也一样。"""
    import struct

    fake = b"\xff\xd8\xff\xc0" + struct.pack(">HBHHB", 8, 8, 1, 1, 3)
    with pytest.raises(pdfwriter.WriterError) as ei:
        _write(
            tmp_path,
            provider,
            [ir.Image("im", (0, 0, 10, 10))],
            {"im": _res("fake.jpg", "jpg", fake)},
            {"im": fake},
        )
    assert ei.value.code == "source_unreadable" and ei.value.params["why"] == "raster_unreadable"
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 200, 30)).save(buf, "JPEG", quality=95)
    good = buf.getvalue()
    from tavotto.rendercore import rasterio

    assert rasterio.jpeg_passthrough(good, "jpg") is not None
    truncated = good[: len(good) // 2]
    assert rasterio.jpeg_passthrough(truncated, "jpg") is None
    assert not (tmp_path / "out.pdf").exists()


def test_a_source_page_with_a_degenerate_box_is_a_source_error_not_a_crash(provider, tmp_path):
    """Codex #463 P2：语法上读得出、MediaBox 却零宽的源页 → `source_unreadable(why=degenerate_box)`，
    不是 placement 里的 ZeroDivisionError（`job` 要能把它变成该格式的 format_failed）。"""
    data = _pdf(b"0 0 1 rg 0 0 10 10 re f", media=(0, 0, 0, 100))
    with pytest.raises(pdfwriter.WriterError) as ei:
        _write(
            tmp_path,
            provider,
            [ir.ImportedPage("pg", (0, 0, 100, 100))],
            {"pg": _res("flat.pdf", "pdf", data)},
            {"pg": data},
        )
    assert ei.value.code == "source_unreadable" and ei.value.params["why"] == "degenerate_box"
    assert not (tmp_path / "out.pdf").exists()


# ================================================================ 实例隔离（RC-015 / RC-016）


def test_concurrent_writers_do_not_share_any_state(provider, tmp_path):
    """8 个线程各写一份只含自己那个源的文档：每份文档只有自己的文字、自己的 form 数。"""
    errors: list[BaseException] = []
    outs: dict[int, Path] = {}

    def work(i: int) -> None:
        try:
            data = _same_name_pdf(f"thread-{i}-text", "Helvetica", (1, 0, 0), 0.5)
            out, facts = _write(
                tmp_path,
                provider,
                [ir.ImportedPage("pg", (0, 0, 200, 100))],
                {"pg": _res(f"t{i}.pdf", "pdf", data)},
                {"pg": data},
                name=f"t{i}.pdf",
            )
            assert len(facts.imported_pages) == 1
            outs[i] = out
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    import pypdfium2 as pdfium

    for i, out in outs.items():
        text = pdfium.PdfDocument(str(out))[0].get_textpage().get_text_range()
        assert f"thread-{i}-text" in text
        assert not any(f"thread-{j}-text" in text for j in range(8) if j != i)


def test_writing_the_same_composition_twice_gives_identical_bytes(provider, tmp_path):
    data = (FIXTURE / "page.pdf").read_bytes()
    png = (FIXTURE / "original.png").read_bytes()
    children = [
        ir.ImportedPage("pg", (20, 30, 135, 80), opacity=0.5),
        ir.Image("im", (200, 30, 64, 48)),
    ]
    res = {"pg": _res("figs/page.pdf", "pdf", data), "im": _res("figs/original.png", "png", png)}
    a, fa = _write(tmp_path, provider, children, res, {"pg": data, "im": png}, name="a.pdf")
    b, fb = _write(tmp_path, provider, children, res, {"pg": data, "im": png}, name="b.pdf")
    assert a.read_bytes() == b.read_bytes() and fa.sha256 == fb.sha256


def test_page_size_and_matrix_facts_are_finite_and_invertible(provider, tmp_path):
    data = (FIXTURE / "page.pdf").read_bytes()
    out, facts = _write(
        tmp_path,
        provider,
        [ir.ImportedPage("pg", (20, 30, 135, 80), rotate_cw_deg=270, flip_v=True)],
        {"pg": _res("figs/page.pdf", "pdf", data)},
        {"pg": data},
    )
    m = facts.imported_pages[0]["matrix"]
    assert all(math.isfinite(v) for v in m) and abs(m[0] * m[3] - m[1] * m[2]) > 1e-9
    assert facts.page == (PAGE_W, PAGE_H)
