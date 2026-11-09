"""Original PDF facts -> existing IR, independently read and rasterized.

Source content/font dictionaries are authored directly with qpdf. PDFium is
owned by RenderHost; no user script, whole-page bitmap, or Figure receipt exists.
"""

from __future__ import annotations

import io
import subprocess
import sys
from dataclasses import replace
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from tavotto.rendercore import ir, pdfcontent, pdfwriter, svgir, svgwriter
from tavotto.rendercore.hbshaper import HbFaceProvider
from tavotto.rendercore.renderhost import RenderHost


@pytest.fixture(scope="module")
def provider():
    instance = HbFaceProvider()
    if instance.registry.missing:
        pytest.skip("not_run: approved fonts missing")
    return instance


@pytest.fixture(scope="module")
def host():
    instance = RenderHost()
    try:
        yield instance
    finally:
        instance.close()


def _document(content=b"", resources=None, *, size=(200, 120), page_options=None):
    import pikepdf as q

    pdf = q.Pdf.new()
    page = pdf.add_blank_page(page_size=size)
    page.Resources = resources(pdf) if resources else q.Dictionary()
    page.Contents = q.Stream(pdf, content)
    for key, value in (page_options or {}).items():
        page.obj[key] = value
    out = io.BytesIO()
    pdf.save(out)
    pdf.close()
    return out.getvalue()


def _font_resources(pdf, provider, text, *, spoof=False, alias=False, width_shift=0):
    import pikepdf as q
    from fontTools.ttLib import TTFont

    if spoof:
        from fontTools.fontBuilder import FontBuilder
        from fontTools.pens.ttGlyphPen import TTGlyphPen

        fb = FontBuilder(1000, isTTF=True)
        order = [".notdef", "zero", "one"]
        fb.setupGlyphOrder(order)
        glyphs = {}
        for name, contours in [
            (".notdef", []),
            (
                "zero",
                [
                    [(0, 0), (500, 0), (500, 700), (0, 700)],
                    [(100, 100), (100, 600), (400, 600), (400, 100)],
                ],
            ),
            ("one", [[(200, 0), (300, 0), (300, 700), (200, 700)]]),
        ]:
            pen = TTGlyphPen(None)
            for contour in contours:
                pen.moveTo(contour[0])
                for point in contour[1:]:
                    pen.lineTo(point)
                pen.closePath()
            glyphs[name] = pen.glyph()
        fb.setupGlyf(glyphs)
        fb.setupHorizontalMetrics({name: (600, 0) for name in order})
        fb.setupHorizontalHeader(ascent=800, descent=-200)
        fb.setupCharacterMap({48: "zero", 49: "one"})
        fb.setupNameTable(
            {
                "familyName": "Liberation Sans",
                "styleName": "Regular",
                "uniqueFontIdentifier": "SelfOwnedCounterexample",
                "fullName": "Liberation Sans",
                "psName": "LiberationSans",
            }
        )
        fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
        fb.setupPost()
        encoded = io.BytesIO()
        fb.save(encoded)
        program = encoded.getvalue()
    else:
        face = provider.face_for("sans-serif", False, False)
        wanted = {face.record.info.cmap[ord(ch)] for ch in text} | {0}
        program, _, _ = face.subset(wanted)
    tt = TTFont(io.BytesIO(program))
    cps, order = tt.getBestCmap(), tt.getGlyphOrder()
    codes = [order.index(cps[ord(ch)]) for ch in text]
    cmap = {}
    for ch, code in zip(text, codes):
        cmap[code] = "0" if alias else ch
    fontfile = q.Stream(pdf, program)
    descriptor = q.Dictionary(
        Type=q.Name.FontDescriptor,
        FontName=q.Name.LiberationSans,
        Flags=4,
        FontBBox=q.Array([0, -200, 1000, 800]),
        ItalicAngle=0,
        Ascent=800,
        Descent=-200,
        CapHeight=700,
        StemV=80,
        FontFile2=fontfile,
    )
    widths = []
    for code in sorted(set(codes)):
        advance = tt["hmtx"].metrics[order[code]][0]
        value = (Decimal(advance) * 1000 / Decimal(tt["head"].unitsPerEm)).quantize(
            Decimal(".001"), rounding=ROUND_HALF_EVEN
        )
        if code == codes[0]:
            value += Decimal(width_shift)
        widths += [code, q.Array([value])]
    cid = q.Dictionary(
        Type=q.Name.Font,
        Subtype=q.Name.CIDFontType2,
        BaseFont=q.Name.LiberationSans,
        CIDSystemInfo=q.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
        FontDescriptor=descriptor,
        CIDToGIDMap=q.Name.Identity,
        DW=1000,
        W=q.Array(widths),
    )
    mappings = "\n".join(
        f"<{code:04X}> <{ch.encode('utf-16-be').hex()}>" for code, ch in cmap.items()
    )
    tu = f"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def /CMapName /Own-UCS def /CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange {len(cmap)} beginbfchar {mappings} endbfchar endcmap CMapName currentdict /CMap defineresource pop end end"
    font = q.Dictionary(
        Type=q.Name.Font,
        Subtype=q.Name.Type0,
        BaseFont=q.Name.LiberationSans,
        Encoding=q.Name("/Identity-H"),
        DescendantFonts=q.Array([cid]),
        ToUnicode=q.Stream(pdf, tu.encode()),
    )
    tt.close()
    return q.Dictionary(Font=q.Dictionary(F1=font)), codes


def _text_document(
    provider,
    text="Source keep",
    *,
    spoof=False,
    alias=False,
    width_shift=0,
    suffix=b"",
    origin=(13.0493, 95.5977),
):
    import pikepdf as q

    pdf = q.Pdf.new()
    page = pdf.add_blank_page(page_size=(200, 120))
    page.Resources, codes = _font_resources(
        pdf, provider, text, spoof=spoof, alias=alias, width_shift=width_shift
    )
    parts = " ".join(f"<{code:04X}>" for code in codes)
    page.Contents = q.Stream(
        pdf,
        (
            f"q 1 0 0 -1 0 120 cm q 1 0 0 -1 {origin[0]} {origin[1]} cm BT /F1 20 Tf 1 0 0 1 0 0 Tm ["
            + parts
            + "] TJ ET Q Q"
        ).encode()
        + suffix,
    )
    out = io.BytesIO()
    pdf.save(out)
    pdf.close()
    return out.getvalue()


def _replay(tmp_path, data, host, provider=None, *, exact_pixels=True):
    result = pdfcontent.pdf_to_ir(data, provider=provider)
    original = tmp_path / "original.pdf"
    original.write_bytes(data)
    direct = tmp_path / "direct.pdf"
    vector = tmp_path / "result.svg"
    replay = tmp_path / "replay.pdf"
    pdfwriter.write_pdf(result.page, direct, provider, result.files)
    facts = svgwriter.write_svg(result.page, vector, provider, result.files)
    files = {}
    reread, notes = svgir.svg_to_ir(
        vector.read_bytes(), result.page.width_pt, result.page.height_pt, provider, files=files
    )
    assert not notes
    pdfwriter.write_pdf(reread, replay, provider, files)
    width = round(result.page.width_pt * 3)
    if exact_pixels:
        expected = host.render(original, width_px=width).packed()
        assert host.render(direct, width_px=width).packed() == expected
        assert host.render(replay, width_px=width).packed() == expected
    return result, ET.parse(vector).getroot(), facts, original, replay


def test_native_text_preserves_actual_continuous_pdf_run(tmp_path, provider, host):
    data = _text_document(provider)
    result, xml, facts, original, replay = _replay(tmp_path, data, host, provider)
    assert result.facts["counts"]["text_glyphs"] == len("Source keep")
    assert result.facts["counts"]["outlined_glyphs"] == 0
    assert facts.text_runs == 1
    assert [el.text for el in xml.iter("{http://www.w3.org/2000/svg}text")] == ["Source keep"]
    assert (
        host.inspect(original)["text"].strip()
        == host.inspect(replay)["text"].strip()
        == "Source keep"
    )
    assert result.source_sha256 == result.facts["source_sha256"]
    assert result.page.background is None


def test_same_font_name_and_wrong_tounicode_preserve_original_gids_not_guessed_text(
    tmp_path, provider, host
):
    data = _text_document(provider, "01", spoof=True, alias=True)
    # Original embedded-font hinting and SVG path rasterization have different
    # edge pixels. This test proves original GID/vector geometry, not pixel parity.
    result, xml, facts, _, _ = _replay(tmp_path, data, host, provider, exact_pixels=False)
    assert result.facts["counts"]["text_glyphs"] == 0
    assert result.facts["counts"]["outlined_glyphs"] == 2
    assert not list(xml.iter("{http://www.w3.org/2000/svg}text"))
    assert facts.paths == 2

    def paths(node, transform=ir.IDENTITY):
        if isinstance(node, ir.Group):
            transform = ir.compose(node.transform, transform)
            for child in node.children:
                yield from paths(child, transform)
        elif isinstance(node, ir.Path):
            yield node, transform

    original_glyphs = list(paths(result.page.children[0]))
    assert len(original_glyphs) == 2
    first, second = original_glyphs
    assert first[0].segments == (
        ("M", 0, 0),
        ("L", 500, 0),
        ("L", 500, 700),
        ("L", 0, 700),
        ("Z",),
        ("M", 100, 100),
        ("L", 100, 600),
        ("L", 400, 600),
        ("L", 400, 100),
        ("Z",),
    )
    assert second[0].segments == (
        ("M", 0, 0),
        ("L", 100, 0),
        ("L", 100, 700),
        ("L", 0, 700),
        ("Z",),
    )
    assert first[1] == (0.02, 0, 0, 0.02, 13.0493, 120 - 95.5977)
    assert second[1] == (0.02, 0, 0, 0.02, 13.0493 + 600 * 20 / 1000, 120 - 95.5977)


def test_actual_source_width_is_required_for_native_text(provider):
    result = pdfcontent.pdf_to_ir(_text_document(provider, "01", width_shift=1), provider=provider)
    assert result.facts["counts"]["outlined_glyphs"] == 1
    assert result.facts["counts"]["text_glyphs"] == 1


def test_matching_name_unicode_and_pdf_width_cannot_replace_an_unapproved_font_program(provider):
    import pikepdf as q

    with q.open(io.BytesIO(_text_document(provider, "01", spoof=True))) as pdf:
        page = pdf.pages[0]
        cid = page.Resources.Font.F1.DescendantFonts[0]
        normal, _ = _font_resources(pdf, provider, "01")
        cid.W = normal.Font.F1.DescendantFonts[0].W
        out = io.BytesIO()
        pdf.save(out)
    result = pdfcontent.pdf_to_ir(out.getvalue(), provider=provider)
    assert result.facts["counts"]["text_glyphs"] == 0
    assert result.facts["counts"]["outlined_glyphs"] == 2
    assert not any(isinstance(r, ir.FontResource) for r in result.page.resources.values())


def test_direct_font_dictionaries_do_not_alias_a_previous_temporary_wrapper(provider):
    import pikepdf as q

    def resources(pdf):
        normal, codes = _font_resources(pdf, provider, "0")
        changed, _ = _font_resources(pdf, provider, "0", width_shift=1)
        return q.Dictionary(Font=q.Dictionary(F1=normal.Font.F1, F2=changed.Font.F1))

    data = _document(b"BT /F1 20 Tf <0001> Tj /F2 20 Tf <0001> Tj ET", resources)
    result = pdfcontent.pdf_to_ir(data, provider=provider)
    assert result.facts["counts"]["text_glyphs"] == 1
    assert result.facts["counts"]["outlined_glyphs"] == 1


def test_unrequested_kerning_does_not_change_actual_original_glyph_spacing(provider):
    result = pdfcontent.pdf_to_ir(_text_document(provider, "AV"), provider=provider)
    assert result.facts["counts"]["text_glyphs"] == 0
    assert result.facts["counts"]["outlined_glyphs"] == 2


def test_explicit_cid_to_gid_map_controls_actual_glyphs_and_unicode(tmp_path, provider, host):
    import pikepdf as q

    with q.open(io.BytesIO(_text_document(provider, "01"))) as pdf:
        page = pdf.pages[0]
        font = page.Resources.Font.F1
        cid = font.DescendantFonts[0]
        old_codes = [int(cid.W[0]), int(cid.W[2])]
        # Character codes are unrelated to actual subset GIDs.
        mapping = bytearray(52)
        for code, gid in zip((24, 25), old_codes):
            mapping[code * 2 : code * 2 + 2] = gid.to_bytes(2, "big")
        cid.CIDToGIDMap = q.Stream(pdf, bytes(mapping))
        cid.W = q.Array([24, cid.W[1], 25, cid.W[3]])
        font.ToUnicode = q.Stream(
            pdf,
            b"1 begincodespacerange <0000> <FFFF> endcodespacerange "
            b"2 beginbfchar <0018> <0030> <0019> <0031> endbfchar",
        )
        content = page.Contents.read_bytes()
        for old, new in zip(old_codes, (24, 25)):
            content = content.replace(f"<{old:04X}>".encode(), f"<{new:04X}>".encode())
        page.Contents = q.Stream(pdf, content)
        out = io.BytesIO()
        pdf.save(out)
    result, _, facts, _, _ = _replay(tmp_path, out.getvalue(), host, provider)
    assert result.facts["counts"]["text_glyphs"] == 2
    assert facts.text_runs == 1


def test_q_restore_does_not_restore_text_matrix_advance(tmp_path, provider, host):
    import pikepdf as q

    with q.open(io.BytesIO(_text_document(provider, "0"))) as pdf:
        page = pdf.pages[0]
        code = int(page.Resources.Font.F1.DescendantFonts[0].W[0])
        advance = float(page.Resources.Font.F1.DescendantFonts[0].W[1][0]) * 20 / 1000
        raw = f"<{code:04X}>"
        page.Contents = q.Stream(
            pdf, f"BT /F1 20 Tf 1 0 0 1 30 50 Tm {raw} Tj q {raw} Tj Q {raw} Tj ET".encode()
        )
        out = io.BytesIO()
        pdf.save(out)
    # This isolates q/Q's ownership of text advances. The existing writer rounds
    # separately emitted origins to 4 decimals; it is not an exact pixel oracle
    # for multiple Tj operators whose implicit origins have more decimals.
    result, _, facts, _, _ = _replay(tmp_path, out.getvalue(), host, provider, exact_pixels=False)
    assert result.facts["counts"]["text_glyphs"] == 3
    assert facts.text_runs == 3
    origins = [node.transform[4] for node in result.page.children[0].children]
    assert origins == [30, 30 + advance, 30 + advance + advance]


@pytest.mark.parametrize("rule", ["W", "W*"])
def test_real_clip_rule_replays_exactly(tmp_path, host, rule):
    data = _document(
        f"q 10 10 180 100 re 50 30 50 50 re {rule} n .2 .65 .3 rg 0 0 200 120 re f Q".encode()
    )
    result, _, _, _, _ = _replay(tmp_path, data, host)
    assert result.facts["counts"]["paths"] == 1


def test_nonzero_page_box_rotation_and_user_unit_are_applied_once(tmp_path, host):
    data = _document(
        b".2 .5 .8 rg 20 30 60 40 re f",
        page_options={
            "/MediaBox": [-20, -10, 220, 130],
            "/CropBox": [0, 0, 200, 120],
            "/Rotate": 90,
            "/UserUnit": 1.25,
        },
    )
    result, _, _, _, _ = _replay(tmp_path, data, host)
    assert (result.page.width_pt, result.page.height_pt) == (150, 250)


def _image_resources(pdf):
    import pikepdf as q

    mask = q.Stream(pdf, bytes(x * 36 for _ in range(8) for x in range(8)))
    image = q.Stream(
        pdf, bytes(v for y in range(8) for x in range(8) for v in (255, x * 32, y * 32))
    )
    for obj, cs in [(mask, q.Name.DeviceGray), (image, q.Name.DeviceRGB)]:
        obj.Type, obj.Subtype = q.Name.XObject, q.Name.Image
        obj.Width = obj.Height = 8
        obj.BitsPerComponent, obj.ColorSpace = 8, cs
    image.SMask = mask
    return q.Dictionary(XObject=q.Dictionary(Im=image))


def test_actual_smask_image_grid_and_skew_survive(tmp_path, host):
    data = _document(b"q 60 10 -5 60 100 20 cm /Im Do Q", _image_resources)
    result, xml, facts, _, _ = _replay(tmp_path, data, host)
    assert result.facts["counts"]["images"] == 1
    assert len(result.files) == len(facts.images) == 1
    assert len(list(xml.iter("{http://www.w3.org/2000/svg}image"))) == 1


def test_inherited_page_resources_are_the_original_drawing_authority(tmp_path, provider, host):
    import pikepdf as q

    data = _text_document(provider)
    with q.open(io.BytesIO(data)) as pdf:
        page = pdf.pages[0]
        page.obj.Parent.Resources = page.Resources
        del page.obj["/Resources"]
        out = io.BytesIO()
        pdf.save(out)
    result, _, _, _, _ = _replay(tmp_path, out.getvalue(), host, provider)
    assert result.facts["counts"]["text_glyphs"] == len("Source keep")


def _form_resources(pdf):
    import pikepdf as q

    form = q.Stream(pdf, b".1 .2 .9 rg 0 0 40 40 re f .9 .2 .1 rg 20 10 40 40 re f")
    form.Type, form.Subtype = q.Name.XObject, q.Name.Form
    form.BBox, form.Resources = q.Array([0, 0, 60, 50]), q.Dictionary()
    form.Group = q.Dictionary(S=q.Name.Transparency, I=True, K=False, CS=q.Name.DeviceRGB)
    return q.Dictionary(
        XObject=q.Dictionary(Fm=form), ExtGState=q.Dictionary(A=q.Dictionary(ca=0.5, CA=0.25))
    )


def test_actual_isolated_transparency_form_is_not_distributed_per_child(tmp_path, host):
    data = _document(b"q /A gs 1 0 0 1 30 20 cm /Fm Do Q", _form_resources)
    result, _, _, _, _ = _replay(tmp_path, data, host)
    assert result.facts["counts"]["forms"] == 1


@pytest.mark.parametrize("kind", ["Form", "Image"])
def test_optional_content_xobject_cannot_silently_show_default_hidden_paint(tmp_path, host, kind):
    import pikepdf as q

    def resources(pdf):
        layer = pdf.make_indirect(q.Dictionary(Type=q.Name.OCG, Name="Hidden"))
        pdf.Root.OCProperties = q.Dictionary(
            OCGs=q.Array([layer]), D=q.Dictionary(BaseState=q.Name.ON, OFF=q.Array([layer]))
        )
        if kind == "Form":
            obj = q.Stream(pdf, b"1 0 0 rg 0 0 80 80 re f")
            obj.BBox, obj.Resources = q.Array([0, 0, 100, 100]), q.Dictionary()
        else:
            obj = q.Stream(pdf, bytes([255, 0, 0]) * 4)
            obj.Width = obj.Height = 2
            obj.BitsPerComponent, obj.ColorSpace = 8, q.Name.DeviceRGB
        obj.Type, obj.Subtype, obj.OC = q.Name.XObject, q.Name("/" + kind), layer
        return q.Dictionary(XObject=q.Dictionary(Hidden=obj))

    op = b"/Hidden Do" if kind == "Form" else b"q 80 0 0 80 0 0 cm /Hidden Do Q"
    data = _document(op, resources, size=(100, 100))
    original = tmp_path / "original-hidden.pdf"
    original.write_bytes(data)
    assert host.render(original, width_px=300).packed() == bytes([255]) * (300 * 300 * 3)
    with pytest.raises(pdfcontent.PDFContentError) as error:
        pdfcontent.pdf_to_ir(data)
    assert error.value.code == "pdfcontent_unsupported"
    assert error.value.operation == "Do"


def test_recursive_form_is_rejected_before_reentering_stream():
    import pikepdf as q

    def resources(pdf):
        form = q.Stream(pdf, b"/Self Do")
        form.Type, form.Subtype = q.Name.XObject, q.Name.Form
        form.BBox = q.Array([0, 0, 10, 10])
        form.Resources = q.Dictionary(XObject=q.Dictionary(Self=form))
        return q.Dictionary(XObject=q.Dictionary(Self=form))

    with pytest.raises(pdfcontent.PDFContentError) as error:
        pdfcontent.pdf_to_ir(_document(b"/Self Do", resources))
    assert error.value.code == "pdfcontent_invalid"
    assert error.value.detail == "recursive Form XObject"


@pytest.mark.parametrize("change", ["blend", "smask", "text_render_mode", "hairline"])
def test_unrepresented_paint_semantics_fail_before_returning_a_partial_page(provider, change):
    import pikepdf as q

    with q.open(io.BytesIO(_text_document(provider, "0"))) as pdf:
        page = pdf.pages[0]
        if change == "text_render_mode":
            content = page.Contents.read_bytes().replace(b"BT", b"BT 1 Tr")
        elif change == "hairline":
            content = b"0 w 10 10 m 100 100 l S"
        else:
            gs = (
                q.Dictionary(BM=q.Name.Multiply)
                if change == "blend"
                else q.Dictionary(SMask=q.Dictionary())
            )
            page.Resources.ExtGState = q.Dictionary(A=gs)
            content = b"/A gs 10 10 10 10 re f"
        page.Contents = q.Stream(pdf, content)
        out = io.BytesIO()
        pdf.save(out)
    with pytest.raises(pdfcontent.PDFContentError) as error:
        pdfcontent.pdf_to_ir(out.getvalue(), provider=provider)
    assert error.value.code == "pdfcontent_unsupported"


@pytest.mark.parametrize(
    "content", [b"0 0 10 10 re sh", b"/P scn", b"0 0 0 1 k", b"BI /W 1 /H 1 ID x EI"]
)
def test_unsupported_drawing_is_not_silently_dropped(content):
    with pytest.raises(pdfcontent.PDFContentError) as error:
        pdfcontent.pdf_to_ir(_document(content))
    assert error.value.code == "pdfcontent_unsupported"


@pytest.mark.parametrize(
    "content", [b"Q", b"1 0 cm", b"true 0 m", b"BT 100000000 Tj ET", b"BT 100000000 TJ ET"]
)
def test_malformed_operators_have_stable_errors_without_large_byte_allocations(content):
    with pytest.raises(pdfcontent.PDFContentError) as error:
        pdfcontent.pdf_to_ir(_document(content))
    assert error.value.code == "pdfcontent_invalid"


@pytest.mark.parametrize(
    "field,limit,content",
    [
        ("max_source_bytes", 1, b""),
        ("max_decoded_bytes", 1, b"0 0 m 1 1 l S"),
        ("max_ops", 1, b"0 0 m 1 1 l S"),
        ("max_segments", 1, b"0 0 m 1 1 l S"),
        ("max_depth", 1, b"q q Q Q"),
        ("max_clips", 1, b"0 0 2 2 re W n 0 0 1 1 re W n"),
    ],
)
def test_source_and_graph_budgets_are_enforced(field, limit, content):
    budget = replace(pdfcontent.PDFContentBudget(), **{field: limit})
    with pytest.raises(pdfcontent.PDFContentError) as error:
        pdfcontent.pdf_to_ir(_document(content), budget=budget)
    assert error.value.code == "pdfcontent_budget"


@pytest.mark.parametrize("field", ["max_glyphs", "max_font_bytes"])
def test_actual_embedded_font_budgets(field, provider):
    with pytest.raises(pdfcontent.PDFContentError) as error:
        pdfcontent.pdf_to_ir(
            _text_document(provider, "01"),
            budget=replace(pdfcontent.PDFContentBudget(), **{field: 1}),
        )
    assert error.value.code == "pdfcontent_budget"


@pytest.mark.parametrize("field", ["max_image_pixels", "max_total_image_pixels"])
def test_image_budget_precedes_native_decode(field, monkeypatch):
    import pikepdf as q

    data = _document(b"/Im Do", _image_resources)
    monkeypatch.setattr(
        q.PdfImage, "as_pil_image", lambda *a: pytest.fail("decoded before pixel admission")
    )
    with pytest.raises(pdfcontent.PDFContentError) as error:
        pdfcontent.pdf_to_ir(data, budget=replace(pdfcontent.PDFContentBudget(), **{field: 1}))
    assert error.value.code == "pdfcontent_budget"


def test_absent_resources_empty_page_and_inclusive_source_budget():
    data = _document()
    result = pdfcontent.pdf_to_ir(
        data, budget=replace(pdfcontent.PDFContentBudget(), max_source_bytes=len(data))
    )
    assert result.facts["counts"]["paths"] == 0
    assert result.facts["size_pt"] == [200, 120]
    assert result.files == {}


@pytest.mark.parametrize("value", [None, b"", b"not a pdf", bytearray(b"pdf")])
def test_invalid_frozen_inputs(value):
    with pytest.raises(pdfcontent.PDFContentError):
        pdfcontent.pdf_to_ir(value)


@pytest.mark.parametrize("page_index", [True, -1, 0.5, 1])
def test_page_selection_is_explicit_and_bounded(page_index):
    with pytest.raises(pdfcontent.PDFContentError) as error:
        pdfcontent.pdf_to_ir(_document(), page_index=page_index)
    assert error.value.code == "pdfcontent_invalid"


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_budget_does_not_coerce_invalid_limits(limit):
    with pytest.raises(ValueError, match="invalid PDF content budget"):
        pdfcontent.pdf_to_ir(
            _document(), budget=replace(pdfcontent.PDFContentBudget(), max_ops=limit)
        )


def test_importing_adapter_loads_no_native_packages():
    code = f"""
import sys
sys.path.insert(0, {str(Path(pdfcontent.__file__).resolve().parents[2])!r})
import builtins
original = builtins.__import__
def guarded(name,*a,**kw):
    if name.split('.')[0] in {{'pikepdf','fontTools','uharfbuzz','pypdfium2','PIL','matplotlib','numpy','flask'}}:
        raise AssertionError(name)
    return original(name,*a,**kw)
builtins.__import__=guarded
import tavotto.rendercore.pdfcontent
"""
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
