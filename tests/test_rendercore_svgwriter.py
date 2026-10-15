"""X03: inspect final XML, then independently render it and compare canonical PDF.

Browser geometry expectations are hand-calculated; neither placement.place nor the
writer's coordinate helpers are used as the oracle. Missing browser/native packages
are reported as not-run, never represented as a successful visual comparison.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from xml.etree import ElementTree as ET

import pytest

from tavotto.rendercore import fonts, ir, raster, rasterio
from tavotto.rendercore.hbshaper import HbFaceProvider
from tavotto.rendercore.svgwriter import SVG, XLINK, UnsupportedCapability, WriterError, write_svg

NS = {"s": SVG}


def _rect(x, y, w, h, color=(1, 0, 0), alpha=1):
    return ir.Path(
        (("M", x, y), ("L", x + w, y), ("L", x + w, y + h), ("L", x, y + h), ("Z",)),
        fill=ir.Paint(color, alpha),
    )


def _resource(data, kind="pdf", svg=None):
    return ir.FileResource(
        "figure." + kind,
        kind,
        hashlib.sha256(data).hexdigest(),
        "execution",
        "snapshot",
        svg_sha256=hashlib.sha256(svg).hexdigest() if svg is not None else None,
    )


def _write(
    tmp_path, children, *, resources=None, files=None, svg=None, provider=None, name="out.svg"
):
    page = ir.Page(120, 90, tuple(children), resources=resources or {})
    path = tmp_path / name
    facts = write_svg(page, path, provider, files, imported_svgs=svg)
    return path, ET.parse(path).getroot(), facts


@pytest.fixture(scope="module")
def provider():
    pytest.importorskip("uharfbuzz", reason="not_run: HarfBuzz unavailable")
    pytest.importorskip("fontTools", reason="not_run: fontTools unavailable")
    registry = fonts.FontRegistry.discover()
    if registry.missing:
        pytest.skip(f"not_run: approved fonts missing: {registry.missing}")
    return HbFaceProvider(registry)


@pytest.fixture(scope="module", params=["chromium", "inkscape"])
def browser(request):
    if request.param == "inkscape":
        if not shutil.which("inkscape"):
            pytest.skip("not_run: independent Inkscape renderer unavailable")
        yield "inkscape"
        return
    playwright = pytest.importorskip(
        "playwright.sync_api", reason="not_run: Playwright unavailable"
    )
    with playwright.sync_playwright() as driver:
        try:
            instance = driver.chromium.launch(
                executable_path=shutil.which("chromium") or shutil.which("google-chrome"),
                headless=True,
                args=["--no-sandbox"],
            )
        except playwright.Error as exc:
            pytest.skip(f"not_run: Chromium unavailable: {exc}")
        yield instance
        instance.close()


def _render(browser, path):
    from PIL import Image

    if browser == "inkscape":
        target = path.with_suffix(".png")
        profile = path.parent / "inkscape-profile"
        profile.mkdir(exist_ok=True)
        subprocess.run(
            [
                shutil.which("inkscape"),
                str(path),
                "--export-type=png",
                "--export-dpi=96",
                f"--export-filename={target}",
            ],
            env={**os.environ, "INKSCAPE_PROFILE_DIR": str(profile)},
            check=True,
            capture_output=True,
            timeout=30,
        )
        return Image.open(target).convert("RGB")
    page = browser.new_page(viewport={"width": 400, "height": 300}, device_scale_factor=1)
    try:
        page.set_content(
            '<html><body style="margin:0">' + path.read_text(encoding="utf-8") + "</body></html>"
        )
        return Image.open(io.BytesIO(page.locator("svg").first.screenshot())).convert("RGB")
    finally:
        page.close()


def _pixel(image, x, y):
    return image.getpixel((round(x * 4 / 3), round((90 - y) * 4 / 3)))


def _near(actual, expected, tolerance=2):
    assert max(abs(a - b) for a, b in zip(actual, expected)) <= tolerance, (actual, expected)


def test_physical_size_paint_order_and_one_coordinate_conversion(tmp_path, browser):
    path, root, facts = _write(tmp_path, [_rect(10, 10, 30, 20), _rect(20, 20, 30, 20, (0, 0, 1))])
    assert root.attrib == {
        "version": "1.1",
        "width": "120pt",
        "height": "90pt",
        "viewBox": "0 0 120 90",
    }
    group = root.find("s:g", NS)
    assert group.get("transform") == "matrix(1 0 0 -1 0 90)"
    assert [p.get("fill") for p in group] == ["rgb(100%,0%,0%)", "rgb(0%,0%,100%)"]
    assert facts.paths == 2 and facts.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    image = _render(browser, path)
    assert image.size == (160, 120)
    _near(_pixel(image, 15, 15), (255, 0, 0))
    _near(_pixel(image, 25, 25), (0, 0, 255))
    _near(_pixel(image, 15, 75), (255, 255, 255))


def test_clip_transform_and_group_alpha_are_not_distributed_to_children(tmp_path, browser):
    node = ir.Group(
        (_rect(0, 0, 30, 25, (0, 0, 1)), _rect(10, 0, 30, 25)),
        transform=(1, 0, 0, 1, 10, 15),
        clip=_rect(0, 0, 25, 20),
        opacity=0.5,
    )
    path, root, facts = _write(tmp_path, [node])
    assert facts.transparency_groups == 1
    group = root.find("s:g/s:g", NS)
    assert group.get("opacity") == "0.5"
    assert all(child.get("fill-opacity") == "1" for child in group)
    image = _render(browser, path)
    _near(_pixel(image, 15, 20), (128, 128, 255))
    _near(_pixel(image, 25, 20), (255, 128, 128))
    _near(_pixel(image, 40, 20), (255, 255, 255))


def test_stroke_style_and_evenodd_clip_are_explicit(tmp_path):
    node = ir.Path(
        (("M", 10, 10), ("C", 10, 20, 20, 30, 30, 30)),
        stroke=ir.Stroke(ir.Paint((0.2, 0.4, 0.6), 0.3), 2, "round", "bevel", (3, 4), 1, 5),
    )
    _, root, _ = _write(
        tmp_path, [ir.Group((node,), clip=replace(_rect(0, 0, 50, 50), fill_rule="evenodd"))]
    )
    shape = root.find("s:g/s:g/s:path", NS)
    assert shape.get("d") == "M10 10 C10 20 20 30 30 30"
    assert shape.get("fill") == "none"
    assert shape.get("stroke-width") == "2"
    assert shape.get("stroke-linecap") == "round"
    assert shape.get("stroke-linejoin") == "bevel"
    assert shape.get("stroke-dasharray") == "3 4"
    assert shape.get("stroke-dashoffset") == "1"
    assert shape.get("stroke-opacity") == "0.3"
    assert root.find("s:defs/s:clipPath/s:path", NS).get("clip-rule") == "evenodd"


def test_shaped_outlines_keep_gid_advance_offset_and_rise(tmp_path, provider):
    face = provider.face_for("sans-serif", False, False)
    gid = face.shape("A")[0].gid
    # Exactly 1pt per 100 font units makes every expected matrix hand-computable.
    run = ir.GlyphRun(
        "f", face.upem / 100, (ir.Glyph(gid, "A", 900, 100, 200), ir.Glyph(gid, "A", 600)), rise=3
    )
    node = ir.ShapedText(10, 20, (run,), ir.Paint((0, 0, 0), 0.5))
    _, root, facts = _write(tmp_path, [node], resources={"f": face.resource}, provider=provider)
    uses = root.findall("s:g/s:g/s:use", NS)
    assert [u.get("transform") for u in uses] == [
        "matrix(0.01 0 0 0.01 11 25)",
        "matrix(0.01 0 0 0.01 19 23)",
    ]
    assert uses[0].get(f"{{{XLINK}}}href") == uses[1].get(f"{{{XLINK}}}href")
    assert len(root.findall("s:defs/s:path", NS)) == 1
    assert not root.findall(".//s:text", NS)
    assert facts.outlined_glyphs == 2 and facts.text_objects == 1
    assert facts.text_policy == "outlines"
    assert facts.fonts[0]["outlined"] is True and facts.fonts[0]["embedded"] is False


def test_text_and_geometry_match_canonical_pdf_in_an_independent_renderer(
    tmp_path, provider, browser
):
    import pypdfium2
    from PIL import ImageChops

    from tavotto.rendercore.pdfwriter import write_pdf

    latin = provider.face_for("sans-serif", False, False)
    chinese = provider.cjk_face()
    text = ir.ShapedText(
        10,
        50,
        (
            ir.GlyphRun("latin", 12, latin.shape("Ag fi 10")),
            ir.GlyphRun("latin", 7, latin.shape("2"), rise=5, actual_text="²"),
            ir.GlyphRun("cjk", 12, chinese.shape("中文")),
        ),
        ir.Paint((0, 0, 0)),
    )
    nodes = (
        _rect(10, 10, 30, 20),
        text,
        ir.Group((_rect(50, 10, 20, 20), _rect(60, 10, 20, 20, (0, 0, 1))), opacity=0.5),
    )
    page = ir.Page(120, 90, nodes, {"latin": latin.resource, "cjk": chinese.resource})
    svg_path, pdf_path = tmp_path / "compare.svg", tmp_path / "compare.pdf"
    write_svg(page, svg_path, provider)
    write_pdf(page, pdf_path, provider)
    svg_image = _render(browser, svg_path)
    with pypdfium2.PdfDocument(pdf_path) as doc:
        pdf_page = doc[0]
        try:
            bitmap = pdf_page.render(scale=4 / 3)
            try:
                pdf_image = bitmap.to_pil().convert("RGB")
            finally:
                bitmap.close()
        finally:
            pdf_page.close()
    assert svg_image.size == pdf_image.size
    # Different antialiasing is expected; wrong baseline, missing text, or mirrored
    # outlines produce much larger regional ink differences than edge antialiasing.
    delta = ImageChops.difference(svg_image, pdf_image)
    mean = sum(
        sum(channel * count for channel, count in enumerate(delta.getchannel(i).histogram()))
        for i in range(3)
    ) / (3 * 160 * 120)
    assert mean < 3.0
    for x, y in ((15, 15), (55, 15), (65, 15), (75, 15)):
        _near(_pixel(svg_image, x, y), _pixel(pdf_image, x, y))


QUADRANTS = b"""<svg xmlns="http://www.w3.org/2000/svg" width="40pt" height="20pt" viewBox="10 20 40 20">
<defs><path id="box" d="M0 0h20v10h-20z"/></defs>
<use href="#box" x="10" y="20" fill="red"/><use href="#box" x="30" y="20" fill="lime"/>
<use href="#box" x="10" y="30" fill="blue"/><use href="#box" x="30" y="30" fill="yellow"/></svg>"""


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize(
    "flip_h,flip_v", [(False, False), (True, False), (False, True), (True, True)]
)
@pytest.mark.parametrize("crop", [None, (0.5, 0, 0.5, 0.5)])
def test_imported_crop_flip_rotation_and_nonzero_viewbox(
    tmp_path, browser, rotation, flip_h, flip_v, crop
):
    data = b"frozen PDF snapshot"
    node = ir.ImportedPage(
        "p", (20, 20, 60, 40), crop=crop, rotate_cw_deg=rotation, flip_h=flip_h, flip_v=flip_v
    )
    path, _, facts = _write(
        tmp_path,
        [node],
        resources={"p": _resource(data, svg=QUADRANTS)},
        files={"p": data},
        svg={"p": QUADRANTS},
    )
    image = _render(browser, path)
    colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))
    for (u, v), color in zip(((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)), colors):
        # Independent normalized top-left geometry, matching the documented CSS order.
        u, v = (1 - u if flip_h else u), (1 - v if flip_v else v)
        u, v = {0: (u, v), 90: (1 - v, u), 180: (1 - u, 1 - v), 270: (v, 1 - u)}[rotation]
        x, y = 20 + u * 60, 20 + (1 - v) * 40
        _near(_pixel(image, x, y), (0, 255, 0) if crop else color)
    for x, y in ((17, 40), (83, 40), (50, 17), (50, 63)):
        _near(_pixel(image, x, y), (255, 255, 255))
    assert facts.imported_pages[0]["object_id"] == ""


def test_imported_ids_refs_and_css_are_isolated_per_instance(tmp_path, browser):
    def source(color):
        return f'''<svg xmlns="{SVG}" width="20pt" height="20pt" viewBox="0 0 20 20"><style>*{{stroke-linejoin:round}} #colored{{fill:{color}}}</style><defs><path id="box" d="M0 0h20v20h-20z"/><clipPath id="cut"><rect width="10" height="20"/></clipPath></defs><use id="colored" href="#box" clip-path="url('#cut')"/></svg>'''.encode()

    red, blue, data = source("red"), source("blue"), b"pdf"
    path, root, _ = _write(
        tmp_path,
        [
            ir.ImportedPage("a", (10, 10, 20, 20)),
            ir.ImportedPage("b", (40, 10, 20, 20)),
            ir.ImportedPage("a", (70, 10, 20, 20)),
        ],
        resources={"a": _resource(data, svg=red), "b": _resource(data, svg=blue)},
        files={"a": data, "b": data},
        svg={"a": red, "b": blue},
    )
    ids = [node.get("id") for node in root.iter() if node.get("id")]
    assert len(ids) == len(set(ids))
    image = _render(browser, path)
    for x, color in ((15, (255, 0, 0)), (45, (0, 0, 255)), (75, (255, 0, 0))):
        _near(_pixel(image, x, 15), color)
        _near(_pixel(image, x + 10, 15), (255, 255, 255))


@pytest.mark.parametrize("kind", ["png", "jpeg", "tiff"])
def test_images_embed_native_pixel_grid_with_correct_orientation(tmp_path, browser, kind):
    from PIL import Image

    image = Image.new("RGB", (20, 10), "red")
    for y in range(5, 10):
        for x in range(20):
            image.putpixel((x, y), (0, 0, 255))
    out = io.BytesIO()
    image.save(out, format=kind.upper())
    data = out.getvalue()
    path, root, facts = _write(
        tmp_path,
        [ir.Image("i", (10, 10, 40, 20))],
        resources={"i": _resource(data, kind)},
        files={"i": data},
    )
    embedded = root.find("s:defs/s:image", NS).get(f"{{{XLINK}}}href")
    decoded = Image.open(io.BytesIO(base64.b64decode(embedded.split(",", 1)[1])))
    assert decoded.size == (20, 10)
    assert facts.images[0]["width"] == 20
    actual = _render(browser, path)
    _near(_pixel(actual, 20, 27), (255, 0, 0), 15 if kind == "jpeg" else 2)
    _near(_pixel(actual, 20, 13), (0, 0, 255), 15 if kind == "jpeg" else 2)


def test_straight_image_alpha_and_zero_group_opacity(tmp_path, browser):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGBA", (4, 4), (255, 0, 0, 128)).save(buffer, format="PNG")
    data = buffer.getvalue()
    path, root, facts = _write(
        tmp_path,
        [
            ir.Image("i", (10, 10, 20, 20), opacity=0.5),
            ir.Group((_rect(40, 10, 20, 20),), opacity=0),
        ],
        resources={"i": _resource(data, "png")},
        files={"i": data},
    )
    assert facts.images[0]["channels"] == 4
    assert root.find("s:g/s:g[@opacity='0']/s:path", NS) is not None
    image = _render(browser, path)
    _near(_pixel(image, 20, 20), (255, 191, 191))
    _near(_pixel(image, 50, 20), (255, 255, 255))


def test_document_image_budget_counts_each_resource_once(tmp_path, monkeypatch):
    from PIL import Image

    first = io.BytesIO()
    second = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(first, format="PNG")
    Image.new("RGB", (4, 4), "blue").save(second, format="PNG")
    a, b = first.getvalue(), second.getvalue()
    resources = {"a": _resource(a, "png"), "b": _resource(b, "png")}
    files = {"a": a, "b": b}
    monkeypatch.setattr(raster, "DOCUMENT_MAX_PIXELS", 20)
    nodes = [ir.Image("a", (10, 10, 20, 20)), ir.Image("a", (40, 10, 20, 20))]
    _, root, _ = _write(tmp_path, nodes, resources=resources, files=files)
    assert len(root.findall("s:defs/s:image", NS)) == 1
    with pytest.raises(WriterError) as exc:
        _write(
            tmp_path, nodes + [ir.Image("b", (70, 10, 20, 20))], resources=resources, files=files
        )
    assert exc.value.params["why"] == "raster_budget_exceeded"


def test_approved_font_identity_and_missing_glyph_outline_are_explicit(tmp_path, provider):
    face = provider.face_for("serif", True, True)
    node = ir.ShapedText(
        10, 20, (ir.GlyphRun("f", 12, (ir.Glyph(0, "missing", 500),)),), ir.Paint((0, 0, 0))
    )
    _, root, facts = _write(tmp_path, [node], resources={"f": face.resource}, provider=provider)
    assert facts.notdef_codes == 1
    # Some approved faces have an empty .notdef; preserve the face's real outline
    # and report the missing glyph rather than drawing a substitute character.
    assert root.find("s:defs/s:path", NS) is not None
    wrong_face = replace(face.resource, sha256="0" * 64)
    with pytest.raises(WriterError) as exc:
        _write(tmp_path, [node], resources={"f": wrong_face}, provider=provider)
    assert exc.value.code == "font_identity"


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "canonical_hash",
        "companion_hash",
        "unverified",
        "opaque",
        "page_index",
        "live_text",
        "unsafe",
    ],
)
def test_unverifiable_sources_fail_without_an_output(tmp_path, failure):
    data, svg = b"pdf snapshot", QUADRANTS
    if failure == "live_text":
        svg = b'<svg viewBox="0 0 20 20"><text>Hello</text></svg>'
    elif failure == "unsafe":
        svg = b'<svg viewBox="0 0 20 20"><script>alert(1)</script></svg>'
    resource = _resource(data, svg=None if failure in ("unverified", "opaque") else svg)
    files = (
        {} if failure == "missing" else {"p": b"changed" if failure == "canonical_hash" else data}
    )
    mapping = (
        {} if failure == "opaque" else {"p": b"changed" if failure == "companion_hash" else svg}
    )
    page = ir.Page(
        120,
        90,
        (ir.ImportedPage("p", (0, 0, 20, 20), page_index=1 if failure == "page_index" else 0),),
        {"p": resource},
    )
    target = tmp_path / "rejected.svg"
    with pytest.raises(WriterError) as exc:
        write_svg(page, target, None, files, imported_svgs=mapping)
    expected = {
        "missing": "source_bytes_missing",
        "canonical_hash": "source_identity",
        "companion_hash": "source_identity",
        "unsafe": "source_unreadable",
    }.get(failure, "unsupported_capability")
    assert exc.value.code == expected
    assert not target.exists()


def test_image_budget_is_checked_before_decode(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(rasterio, "header_size", lambda *_: (raster.SOURCE_MAX_PIXELS + 1, 1))
    monkeypatch.setattr(rasterio, "decode", lambda *_a, **_k: called.append(True))
    with pytest.raises(WriterError) as exc:
        _write(
            tmp_path,
            [ir.Image("i", (0, 0, 20, 20))],
            resources={"i": _resource(b"png", "png")},
            files={"i": b"png"},
        )
    assert exc.value.params["why"] == "raster_too_large"
    assert called == []


def test_invalid_ir_and_hairlines_are_not_silently_written(tmp_path):
    with pytest.raises(ir.IRError):
        write_svg(ir.Page(float("nan"), 10, ()), tmp_path / "invalid.svg", None)
    with pytest.raises(UnsupportedCapability) as exc:
        _write(
            tmp_path,
            [ir.Path((("M", 0, 0), ("L", 10, 10)), stroke=ir.Stroke(ir.Paint((0, 0, 0)), 0))],
        )
    assert exc.value.params["reason"] == "svg_hairline_unsupported"


def test_repeated_and_concurrent_writes_are_deterministic(tmp_path):
    def write(index):
        path, _, _ = _write(tmp_path, [_rect(10, 10, 20, 20)], name=f"{index}.svg")
        return path.read_bytes()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(write, range(8)))
    assert len(set(results)) == 1
