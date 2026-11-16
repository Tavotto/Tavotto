"""An uncropped image owns its boundary; a second clip changes edge coverage.

Reference PDFs are built directly with qpdf, without either writer/placement.
PDFium runs only in the existing RenderHost child.  Every image pixel and alpha
is compared, including sheared edges; no tolerance is introduced for this fix.
"""

from __future__ import annotations

import base64
import hashlib
from xml.etree import ElementTree as ET

import pytest

from tavotto.rendercore import ir, pdfwriter, raster, svgir, svgwriter
from tavotto.rendercore.renderhost import RenderHost


@pytest.fixture(scope="module")
def host():
    instance = RenderHost()
    try:
        yield instance
    finally:
        instance.close()


def _pixels():
    return bytes(v for y in range(8) for x in range(8) for v in (255, x * 32, y * 32, x * 36))


def _source(tmp_path, matrix, *, crop=None, outer=False, opacity=1, viewport=None):
    import pikepdf as q

    pdf = q.Pdf.new()
    page = pdf.add_blank_page(page_size=(240, 180))
    pixels = _pixels()
    mask = q.Stream(pdf, pixels[3::4])
    mask.Type, mask.Subtype = q.Name.XObject, q.Name.Image
    mask.Width = mask.Height = 8
    mask.BitsPerComponent, mask.ColorSpace = 8, q.Name.DeviceGray
    rgb = bytes(v for i, v in enumerate(pixels) if i % 4 != 3)
    image = q.Stream(pdf, rgb)
    image.Type, image.Subtype = q.Name.XObject, q.Name.Image
    image.Width = image.Height = 8
    image.BitsPerComponent, image.ColorSpace = 8, q.Name.DeviceRGB
    image.SMask = mask
    page.Resources = q.Dictionary(XObject=q.Dictionary(Im=image))
    commands = ["q"]
    if outer:
        commands.append("150 30 30 50 re W n")
    if viewport is not None:
        commands.append(" ".join(str(v) for v in viewport) + " re W n")
    if opacity < 1:
        page.Resources.ExtGState = q.Dictionary(A=q.Dictionary(ca=opacity, CA=opacity))
        commands.append("/A gs")
    commands.append(" ".join(str(v) for v in matrix) + " cm")
    if crop is not None and crop != (0, 0, 1, 1):
        # Crop fixture is centered: top-origin and bottom-origin are both .25.
        commands.append("0.25 0.25 0.5 0.5 re W n")
    commands.extend(["/Im Do", "Q"])
    page.Contents = q.Stream(pdf, "\n".join(commands).encode("ascii"))
    target = tmp_path / "reference.pdf"
    pdf.save(target)
    pdf.close()
    return target


CASES = [
    pytest.param("shear", None, False, False, 0, False, 1, id="shear"),
    pytest.param("shear", (0, 0, 1, 1), False, False, 0, False, 1, id="explicit-full-crop"),
    pytest.param("rect", None, True, False, 0, False, 1, id="flip-h"),
    pytest.param("rect", None, False, True, 0, False, 1, id="flip-v"),
    pytest.param("rect", None, False, False, 90, False, 1, id="rotate90"),
    pytest.param("rect", None, True, False, 90, False, 1, id="rotate90-flip"),
    pytest.param("rect", (0.25, 0.25, 0.5, 0.5), False, False, 0, False, 1, id="real-crop"),
    pytest.param("shear", None, False, False, 0, True, 1, id="outer-clip"),
    pytest.param("shear", None, False, False, 0, False, 0.5, id="object-alpha"),
]


@pytest.mark.parametrize("kind,crop,flip_h,flip_v,angle,outer,opacity", CASES)
def test_image_edges_match_original_pdf_without_a_redundant_clip(
    tmp_path, host, kind, crop, flip_h, flip_v, angle, outer, opacity
):
    if kind == "shear":
        matrix = (60, 10, -5, 60, 140, 20)
        node = ir.Group(
            (ir.Image("image", (0, 0, 1, 1), crop=crop, opacity=opacity),), transform=matrix
        )
    else:
        if crop:
            matrix = (140, 0, 0, 100, -5, 0)
        elif angle and flip_h:
            matrix = (0, 50, 70, 0, 30, 25)
        elif angle:
            matrix = (0, -50, 70, 0, 30, 75)
        elif flip_h:
            matrix = (-70, 0, 0, 50, 100, 25)
        else:
            matrix = (70, 0, 0, -50, 30, 75)
        node = ir.Image(
            "image", (30, 25, 70, 50), crop=crop, rotate_cw_deg=angle, flip_h=flip_h, flip_v=flip_v
        )
    if outer:
        clip = ir.Path((("M", 150, 30), ("L", 180, 30), ("L", 180, 80), ("L", 150, 80), ("Z",)))
        node = ir.Group((node,), clip=clip)
    data = raster.encode_png(raster.RasterBuffer(8, 8, 4, _pixels(), 32))
    resource = ir.FileResource(
        "image.png", "png", hashlib.sha256(data).hexdigest(), "static", "source"
    )
    page = ir.Page(240, 180, (node,), resources={"image": resource})
    reference = _source(tmp_path, matrix, crop=crop, outer=outer, opacity=opacity)
    canonical = tmp_path / "canonical.pdf"
    pdfwriter.write_pdf(page, canonical, None, {"image": data})
    vector = tmp_path / "out.svg"
    svgwriter.write_svg(page, vector, None, {"image": data})
    # Real crop and the independently owned parent clip remain; there is no
    # coincident unit-image clip in either writer for an uncropped image.
    clips = ET.parse(vector).findall(".//{http://www.w3.org/2000/svg}clipPath")
    files = {}
    replay, notes = svgir.svg_to_ir(vector.read_bytes(), 240, 180, None, files=files)
    assert notes == []
    svg_pdf = tmp_path / "svg-replay.pdf"
    pdfwriter.write_pdf(replay, svg_pdf, None, files)
    expected = host.render(reference, width_px=720).packed()
    assert host.render(canonical, width_px=720).packed() == expected
    assert host.render(svg_pdf, width_px=720).packed() == expected
    assert len(clips) == int(crop not in (None, (0, 0, 1, 1))) + int(outer)


@pytest.mark.parametrize(
    "par,matrix,viewport,expected_clips",
    [
        ("none", (60, 0, 0, 20, 25, 130), None, 0),
        ("xMidYMid meet", (20, 0, 0, 20, 45, 130), None, 0),
        ("xMinYMax meet", (20, 0, 0, 20, 25, 130), None, 0),
        ("xMidYMid slice", (60, 0, 0, 60, 25, 110), (25, 130, 60, 20), 1),
    ],
)
def test_svg_image_viewport_only_clips_content_that_exceeds_it(
    tmp_path, host, par, matrix, viewport, expected_clips
):
    data = raster.encode_png(raster.RasterBuffer(8, 8, 4, _pixels(), 32))
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="240pt" height="180pt" viewBox="0 0 240 180">'
        '<image x="25" y="30" width="60" height="20" preserveAspectRatio="' + par + '" '
        'href="data:image/png;base64,' + base64.b64encode(data).decode("ascii") + '"/></svg>'
    ).encode()
    files = {}
    page, notes = svgir.svg_to_ir(svg, 240, 180, None, files=files)
    assert notes == []
    assert (
        sum(isinstance(node, ir.Group) and node.clip is not None for node, _ in ir.walk(page))
        == expected_clips
    )
    reference = _source(tmp_path, matrix, viewport=viewport)
    target = tmp_path / "image-viewport.pdf"
    pdfwriter.write_pdf(page, target, None, files)
    assert (
        host.render(target, width_px=720).packed() == host.render(reference, width_px=720).packed()
    )
