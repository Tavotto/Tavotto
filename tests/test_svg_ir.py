"""SVG translation measured in final IR and independently read PDF pixels.

Expected positions are hand calculated in pt. Flattening only evaluates the
public IR transform contract, never SVG parsing. PDFium reads the written PDF,
including alpha overlap, clipping, raster orientation and readable text.
"""

from __future__ import annotations

import base64
import hashlib
import io
import math
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from tavotto import svgcoord
from tavotto.rendercore import ir
from tavotto.rendercore.svgir import SVGIRError, svg_to_ir


def svg(body, attrs='width="100pt" height="100pt" viewBox="0 0 100 100"'):
    return f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}>{body}</svg>'.encode()


def final_nodes(page):
    def visit(children, ctm):
        for node in children:
            if isinstance(node, ir.Group):
                yield from visit(node.children, ir.compose(node.transform, ctm))
            else:
                yield node, ctm

    return list(visit(page.children, ir.IDENTITY))


def path_points(page, object_id="p"):
    node, matrix = next((n, m) for n, m in final_nodes(page) if n.object_id == object_id)
    assert isinstance(node, ir.Path)
    return [
        (s[0], *(v for i in range(1, len(s), 2) for v in ir.apply(matrix, s[i], s[i + 1])))
        for s in node.segments
    ]


def test_relative_moveto_lineto_horizontal_vertical_close_and_next_subpath():
    page, _ = svg_to_ir(svg('<path id="p" d="m10 10 20 0 h5 v10 z m2 3 l5 6"/>'), 100, 100, None)
    assert path_points(page) == [
        ("M", 10, 90),
        ("L", 30, 90),
        ("L", 35, 90),
        ("L", 35, 80),
        ("Z",),
        ("M", 12, 87),
        ("L", 17, 81),
    ]


def test_relative_cubic_smooth_reflection_and_reset_after_line():
    page, _ = svg_to_ir(
        svg('<path id="p" d="M10 10 c10 0 20 10 30 20 s20 10 30 0 l10 0 s20 0 30 10"/>'),
        100,
        100,
        None,
    )
    points = path_points(page)
    assert points[1] == ("C", 20, 90, 30, 80, 40, 70)
    assert points[2] == ("C", 50, 60, 60, 60, 70, 70)
    assert points[4] == ("C", 80, 70, 100, 70, 110, 60)


def test_quadratic_and_smooth_quadratic_reflect_actual_control():
    page, _ = svg_to_ir(svg('<path id="p" d="M0 0 q15 30 30 0 t30 0"/>'), 100, 100, None)
    actual = path_points(page)
    assert actual[1][1:] == pytest.approx((10, 80, 20, 80, 30, 100))
    assert actual[2][1:] == pytest.approx((40, 120, 50, 120, 60, 100))


@pytest.mark.parametrize(
    "d", ["L1 2", "M1", "M0 0 L2", "M0 0 Q1 2 3", "M0 0 X1 2", "M0 0 Lnan 2", "M0 0 z 1 2"]
)
def test_malformed_path_rejects_without_partial_export(d):
    with pytest.raises(SVGIRError, match="svgir_invalid"):
        svg_to_ir(svg(f'<path d="{d}"/>'), 100, 100, None)


def test_nested_transforms_are_local_and_each_applied_once():
    page, _ = svg_to_ir(
        svg(
            '<g transform="translate(10 0)"><g transform="translate(5 0)"><path id="p" d="M0 0 L10 5"/></g></g>'
        ),
        100,
        100,
        None,
    )
    assert path_points(page) == [("M", 15, 100), ("L", 25, 95)]


def test_transform_list_and_use_xy_match_independent_nested_group_formula():
    source = svg(
        '<defs><path id="p" d="M1 2 L3 4"/></defs><g transform="translate(10 20)"><use href="#p" x="3" y="4" transform="translate(5 6) scale(2)"/></g>'
    )
    page, _ = svg_to_ir(source, 100, 100, None)
    # ((1,2)+(3,4))*2+(5,6)+(10,20) = (23,38).
    assert path_points(page) == [("M", 23, 62), ("L", 27, 58)]
    root = ET.fromstring(source)
    use = next(e for e in root.iter() if e.tag.endswith("use"))
    use.set("id", "instance")
    assert svgcoord.apply(svgcoord.node_matrix(root, "instance"), 1, 2) == pytest.approx((23, 38))


@pytest.mark.parametrize(
    "attrs,w,h,expected",
    [
        ('width="100" height="100" viewBox="0 0 100 100"', 75, 75, (7.5, 60)),
        ('width="100" height="100"', 75, 75, (7.5, 60)),
        ('width="100pt" height="100pt"', 100, 100, (7.5, 85)),
        ('viewBox="10 20 100 100"', 100, 100, (0, 100)),
        ('viewBox="10 20 100 100" preserveAspectRatio="none"', 200, 100, (0, 100)),
        ('viewBox="10 20 100 100" preserveAspectRatio="xMaxYMin"', 200, 100, (100, 100)),
    ],
)
def test_viewbox_origin_physical_units_and_preserve(attrs, w, h, expected):
    page, _ = svg_to_ir(svg('<path id="p" d="M10 20 L30 40"/>', attrs), w, h, None)
    assert path_points(page)[0][1:] == pytest.approx(expected)


def test_default_fill_inherited_style_css_specificity_and_inline_priority():
    source = svg(
        '<style>path {fill: red} .series {fill: blue;stroke-width: 2} g > #p {stroke: green}</style><g fill="white" stroke="black"><path id="p" class="series" d="M1 1 L2 2" style="fill: #ff0;stroke-dasharray: 3 2;stroke-dashoffset: 1"/></g><path id="plain" d="M2 2 L3 3"/>'
    )
    page, _ = svg_to_ir(source, 100, 100, None)
    p = next(n for n, _ in final_nodes(page) if n.object_id == "p")
    assert p.fill.rgb == (1, 1, 0)
    assert p.stroke.paint.rgb == (0, 128 / 255, 0)
    assert p.stroke.width == 2
    assert p.stroke.dash == (3, 2)
    assert p.stroke.dash_phase == 1
    default, _ = svg_to_ir(svg('<path d="M1 1 L2 2"/>'), 100, 100, None)
    assert final_nodes(default)[0][0].fill == ir.Paint((0, 0, 0))


def test_use_inherits_instance_paint_but_explicit_definition_paint_wins():
    page, _ = svg_to_ir(
        svg(
            '<defs><path id="a" d="M0 0 L10 10"/><path id="b" fill="blue" d="M0 0 L10 10"/></defs><use href="#a" fill="red"/><use href="#b" fill="red" x="20"/>'
        ),
        100,
        100,
        None,
    )
    assert [n.fill.rgb for n, _ in final_nodes(page)] == [(1, 0, 0), (0, 0, 1)]


def test_group_alpha_is_not_multiplied_into_child_paint():
    page, _ = svg_to_ir(
        svg(
            '<g opacity=".5"><g opacity=".5"><rect id="p" width="10" height="10" fill-opacity=".5"/></g></g>'
        ),
        100,
        100,
        None,
    )
    groups = [n for n, _ in ir.walk(page) if isinstance(n, ir.Group) and n.opacity != 1]
    assert [g.opacity for g in groups] == [0.5, 0.5]
    assert final_nodes(page)[0][0].fill.alpha == 0.5


def test_clip_multiple_shapes_and_transforms_share_local_coordinates():
    page, _ = svg_to_ir(
        svg(
            '<defs><clipPath id="c" transform="translate(3 4)"><rect x="1" y="2" width="10" height="20"/><path transform="translate(20)" d="M0 0 L5 0 L5 5 Z"/></clipPath></defs><g transform="translate(10 15)" clip-path="url(#c)"><path id="p" d="M0 0 L30 30"/></g>'
        ),
        100,
        100,
        None,
    )
    group = next(n for n, _ in ir.walk(page) if isinstance(n, ir.Group) and n.clip)
    assert group.transform == (1, 0, 0, 1, 10, 15)
    assert isinstance(group.clip, ir.ClipUnion)
    assert group.clip.paths[0].segments[0] == ("M", 4, 6)
    assert group.clip.paths[1].segments[0] == ("M", 23, 4)


def test_arc_and_rounded_rect_are_explicit_cubics_not_silent_missing_shapes():
    page, _ = svg_to_ir(
        svg(
            '<path id="p" d="M10 20 A10 10 0 0 1 20 30"/><rect id="r" x="10" y="10" width="30" height="20" rx="5"/>'
        ),
        100,
        100,
        None,
    )
    p = path_points(page)
    assert p[-1][-2:] == (20, 70)
    assert p[1][1:3] == pytest.approx((15.5228474983, 80))
    assert sum(s[0] == "C" for s in path_points(page, "r")) == 4


@pytest.fixture(scope="module")
def provider():
    from tavotto.rendercore import fonts
    from tavotto.rendercore.hbshaper import CandidatePackagesMissing, HbFaceProvider, require

    try:
        require("pikepdf", "fontTools", "uharfbuzz", "pypdfium2")
    except CandidatePackagesMissing as exc:
        pytest.skip(f"RenderCore dependencies missing (not-run): {exc}")
    registry = fonts.FontRegistry.discover()
    if registry.missing:
        pytest.skip(f"approved fonts missing (not-run): {registry.missing}")
    return HbFaceProvider(registry)


def render_pdf(tmp_path, page, provider, files=None):
    import pypdfium2

    from tavotto.rendercore.pdfwriter import write_pdf

    out = tmp_path / "actual.pdf"
    write_pdf(page, out, provider, files=files)
    document = pypdfium2.PdfDocument(str(out))
    pdf_page = document[0]
    bitmap = pdf_page.render(scale=2)
    image = bitmap.to_pil().convert("RGB")
    textpage = pdf_page.get_textpage()
    text = textpage.get_text_range()
    textpage.close()
    bitmap.close()
    pdf_page.close()
    document.close()
    return image, text


def test_written_pdf_pixels_prove_use_ctm_relative_path_and_group_opacity(tmp_path, provider):
    page, _ = svg_to_ir(
        svg(
            '<defs><path id="tile" d="m0 0 h20 v20 h-20 z"/></defs><g opacity=".5" transform="translate(10 10)"><use href="#tile" fill="red"/><use href="#tile" x="10" fill="red"/></g>'
        ),
        100,
        100,
        provider,
    )
    image, _ = render_pdf(tmp_path, page, provider)
    # Transparent group: overlap has the same alpha as either single region.
    assert image.getpixel((30, 30)) == pytest.approx((255, 127, 127), abs=2)
    assert image.getpixel((50, 30)) == pytest.approx((255, 127, 127), abs=2)
    assert image.getpixel((10, 10)) == (255, 255, 255)


def test_written_pdf_clip_and_px_mapping_are_visible_at_expected_physical_points(
    tmp_path, provider
):
    page, _ = svg_to_ir(
        svg(
            '<defs><clipPath id="c"><rect x="20" y="20" width="20" height="20"/></clipPath></defs><rect width="100" height="100" fill="blue" clip-path="url(#c)"/>',
            'width="100" height="100" viewBox="0 0 100 100"',
        ),
        75,
        75,
        provider,
    )
    image, _ = render_pdf(tmp_path, page, provider)
    assert image.size == (150, 150)
    assert image.getpixel((45, 45)) == (0, 0, 255)
    assert image.getpixel((20, 45)) == (255, 255, 255)
    assert image.getpixel((70, 45)) == (255, 255, 255)


def test_text_baseline_ascends_up_and_rotation_is_retained_in_final_ir_and_pdf(tmp_path, provider):
    page, _ = svg_to_ir(
        svg(
            '<text id="plain" x="10" y="30" font-size="20" font-weight="bold" font-style="italic">HELLO</text><text id="rot" x="80" y="80" font-size="16" transform="rotate(-90 80 80)">AXIS</text>'
        ),
        100,
        100,
        provider,
    )
    text, matrix = next((n, m) for n, m in final_nodes(page) if n.object_id == "plain")
    assert ir.apply(matrix, text.x, text.y) == pytest.approx((10, 70))
    assert ir.apply(matrix, text.x, text.y + 10) == pytest.approx((10, 80))
    resource = page.resources[text.runs[0].font]
    assert resource.bold and resource.italic
    rotated, rotation = next((n, m) for n, m in final_nodes(page) if n.object_id == "rot")
    assert ir.apply(rotation, rotated.x + 10, rotated.y) == pytest.approx((80, 30))
    image, extracted = render_pdf(tmp_path, page, provider)
    assert "HELLO" in extracted and "AXIS" in extracted
    dark_above = sum(image.getpixel((x, y))[0] < 100 for y in range(25, 59) for x in range(20, 145))
    dark_below = sum(image.getpixel((x, y))[0] < 100 for y in range(62, 88) for x in range(20, 145))
    assert dark_above > 100
    assert dark_below == 0


def test_font_shorthand_and_positioned_tspan_use_actual_sizes(provider):
    page, _ = svg_to_ir(
        svg(
            '<text style="font: italic bold 10px DejaVu Sans"><tspan x="10" y="30">A</tspan><tspan x="20" y="25" font-size="50%">2</tspan></text>'
        ),
        100,
        100,
        provider,
    )
    text = [n for n, _ in final_nodes(page) if isinstance(n, ir.ShapedText)]
    assert [n.runs[0].size for n in text] == [10, 5]
    assert all(page.resources[n.runs[0].font].bold for n in text)


def test_css_important_and_font_shorthand_respect_longhand_order(provider):
    source = svg(
        '<style>text {fill: red !important} #p {fill: blue}</style><text id="p" x="10" y="30" style="fill: green;font-weight:normal;font: 700 10px sans-serif;font-size:12px">A</text>'
    )
    page, _ = svg_to_ir(source, 100, 100, provider)
    text = next(n for n, _ in final_nodes(page) if isinstance(n, ir.ShapedText))
    assert text.paint.rgb == (1, 0, 0)
    assert text.runs[0].size == 12
    assert page.resources[text.runs[0].font].bold


def test_mixed_cjk_text_uses_existing_glyph_layer_and_keeps_literal_text(tmp_path, provider):
    page, notes = svg_to_ir(
        svg('<text x="5" y="30" font-size="16">A中文x^2</text>'), 100, 100, provider
    )
    text = next(n for n, _ in final_nodes(page) if isinstance(n, ir.ShapedText))
    assert [page.resources[run.font].family for run in text.runs] == [
        "sans-serif",
        "cjk",
        "sans-serif",
    ]
    _, extracted = render_pdf(tmp_path, page, provider)
    assert "A中文x^2" in extracted
    assert not any(note.startswith("glyph_missing") for note in notes)


def test_viewbox_and_transform_parser_reject_nonfinite_and_trailing_junk():
    for value in ["translate(1e309)", "translate(2junk)", "translate(1) scale(2) junk"]:
        with pytest.raises(ValueError):
            svgcoord.parse_transform(value)
    assert svgcoord.apply(svgcoord.parse_transform("translate (1)"), 0, 0) == (1, 0)


def test_tspan_visibility_display_opacity_and_inherited_rotation(provider):
    page, _ = svg_to_ir(
        svg(
            '<text x="20" y="20" rotate="90" visibility="hidden"><tspan visibility="visible" opacity=".5">A</tspan><tspan display="none">HIDDEN</tspan>B</text>'
        ),
        100,
        100,
        provider,
    )
    visible = [(n, m) for n, m in final_nodes(page) if isinstance(n, ir.ShapedText)]
    assert len(visible) == 1
    text, matrix = visible[0]
    assert "".join(g.text for run in text.runs for g in run.glyphs) == "A"
    assert ir.apply(matrix, text.x + 10, text.y) == pytest.approx((20, 70))
    assert [n.opacity for n, _ in ir.walk(page) if isinstance(n, ir.Group) and n.opacity != 1] == [
        0.5
    ]


def test_embedded_files_receiver_is_unchanged_after_late_failure(provider):
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(stream, format="PNG")
    source = svg(
        f'<defs><linearGradient id="g"><stop offset="0" stop-color="red"/></linearGradient></defs><image width="20" height="20" href="data:image/png;base64,{base64.b64encode(stream.getvalue()).decode()}"/><rect width="10" height="10" fill="url(#g)"/>'
    )
    files = {"prior": b"kept"}
    with pytest.raises(SVGIRError, match="svgir_gradient"):
        svg_to_ir(source, 100, 100, provider, files=files)
    assert files == {"prior": b"kept"}


def test_embedded_image_bytes_delivered_and_pdf_orientation_preserves_top_bottom(
    tmp_path, provider
):
    from PIL import Image

    image = Image.new("RGB", (4, 4), "blue")
    for y in range(2):
        for x in range(4):
            image.putpixel((x, y), (255, 0, 0))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    raw = stream.getvalue()
    source = svg(
        f'<g transform="translate(10 10)"><image id="img" x="10" y="20" width="40" height="40" href="data:image/png;base64,{base64.b64encode(raw).decode()}"/></g>'
    )
    files = {}
    page, _ = svg_to_ir(source, 100, 100, provider, files=files)
    assert list(files.values()) == [raw]
    image_node = next(n for n, _ in final_nodes(page) if isinstance(n, ir.Image))
    assert page.resources[image_node.resource].sha256 == hashlib.sha256(raw).hexdigest()
    rendered, _ = render_pdf(tmp_path, page, provider, files)
    assert rendered.getpixel((60, 70)) == (255, 0, 0)
    assert rendered.getpixel((60, 130)) == (0, 0, 255)
    assert rendered.getpixel((20, 70)) == (255, 255, 255)
    with pytest.raises(SVGIRError, match="files receiver"):
        svg_to_ir(source, 100, 100, provider)


def test_actual_matplotlib_corpus_translates_without_losing_paths(provider):
    corpus = Path(__file__).parent / "support" / "svg_corpus" / "line_two_series.svg"
    page, notes = svg_to_ir(corpus.read_bytes(), 360, 252, provider)
    paths = [n for n, _ in final_nodes(page) if isinstance(n, ir.Path)]
    assert len(paths) > 20
    assert {"line2d_13", "line2d_14"} <= {n.object_id for n, _ in ir.walk(page)}
    assert all("mapped to" in note for note in notes)


@pytest.mark.parametrize(
    "name",
    [
        "bar_error.svg",
        "heatmap_colorbar.svg",
        "text_outlined.svg",
        "plain_shapes.svg",
        "use_and_groups.svg",
        "clip_opacity.svg",
    ],
)
def test_regular_chart_corpus_writes_real_pdf(name, tmp_path, provider):
    from tavotto.svgscene import scene_inventory

    source = (Path(__file__).parent / "support" / "svg_corpus" / name).read_bytes()
    scene = scene_inventory(source)
    files = {}
    page, _ = svg_to_ir(source, scene["w_pt"], scene["h_pt"], provider, files=files)
    image, _ = render_pdf(tmp_path, page, provider, files)
    assert image.width > 0 and image.height > 0
    assert image.getextrema() != ((255, 255),) * 3


def test_shape_em_lengths_use_computed_inherited_font_size():
    page, _ = svg_to_ir(
        svg('<g font-size="40"><rect id="p" width="1em" height="1em"/></g>'), 100, 100, None
    )
    assert path_points(page) == [("M", 0, 100), ("L", 40, 100), ("L", 40, 60), ("L", 0, 60), ("Z",)]


def test_symbol_percent_lengths_use_its_viewbox_not_root_viewport():
    page, _ = svg_to_ir(
        svg(
            '<defs><symbol id="s" viewBox="0 0 10 10"><rect id="p" width="100%" height="100%"/></symbol></defs><use href="#s" width="10" height="10"/>'
        ),
        100,
        100,
        None,
    )
    assert path_points(page) == [("M", 0, 100), ("L", 10, 100), ("L", 10, 90), ("L", 0, 90), ("Z",)]


def test_symbol_default_viewport_clip_prevents_overflow_and_context_restores(tmp_path, provider):
    source = svg(
        '<defs><symbol id="s" viewBox="0 0 10 10"><rect width="20" height="10" fill="red"/></symbol></defs><use href="#s" width="10" height="10"/><rect x="50%" y="50%" width="10" height="10" fill="blue"/>'
    )
    page, _ = svg_to_ir(source, 100, 100, provider)
    image, _ = render_pdf(tmp_path, page, provider)
    assert image.getpixel((10, 10)) == (255, 0, 0)
    assert image.getpixel((30, 10)) == (255, 255, 255)
    assert image.getpixel((110, 110)) == (0, 0, 255)


def test_clip_child_evenodd_rule_keeps_ring_hole(tmp_path, provider):
    source = svg(
        '<defs><clipPath id="c"><path clip-rule="evenodd" d="M0 0H100V100H0Z M25 25H75V75H25Z"/></clipPath></defs><rect width="100" height="100" fill="red" clip-path="url(#c)"/>'
    )
    page, _ = svg_to_ir(source, 100, 100, provider)
    clip = next(n.clip for n, _ in ir.walk(page) if isinstance(n, ir.Group) and n.clip)
    assert clip.fill_rule == "evenodd"
    image, _ = render_pdf(tmp_path, page, provider)
    assert image.getpixel((100, 100)) == (255, 255, 255)
    assert image.getpixel((20, 20)) == (255, 0, 0)


def test_clip_children_form_true_union_with_reverse_winding_and_individual_holes(
    tmp_path, provider
):
    source = svg(
        '<defs><clipPath id="c"><path clip-rule="evenodd" d="M0 0H100V100H0Z M25 25H75V75H25Z"/><path d="M50 0V100H90V0Z"/><rect x="40" y="40" width="20" height="20"/></clipPath></defs><g clip-path="url(#c)" opacity=".5"><rect width="100" height="100" fill="red"/></g>'
    )
    page, _ = svg_to_ir(source, 100, 100, provider)
    image, _ = render_pdf(tmp_path, page, provider)
    assert image.getpixel((60, 100)) == (255, 255, 255)  # ring hole not filled by another child
    assert image.getpixel((100, 100)) == pytest.approx(
        (255, 127, 127), abs=2
    )  # member fills the hole
    assert image.getpixel((160, 20)) == pytest.approx(
        (255, 127, 127), abs=2
    )  # opposite winding overlap stays filled
    import pikepdf

    with pikepdf.open(tmp_path / "actual.pdf") as document:
        objects = list(document.objects)
        assert not any(
            o.get("/Subtype") == pikepdf.Name.Image
            for o in objects
            if isinstance(o, pikepdf.Stream)
        )
        masks = [
            o["/SMask"] for o in objects if isinstance(o, pikepdf.Dictionary) and "/SMask" in o
        ]
        assert all(mask["/S"] == pikepdf.Name.Luminosity for mask in masks)
        assert masks and b"f*" in masks[0]["/G"].read_bytes()


def test_nested_clip_unions_intersect_and_group_alpha_composes_once(tmp_path, provider):
    def rect(x, y, w, h):
        return ir.Path((("M", x, y), ("L", x + w, y), ("L", x + w, y + h), ("L", x, y + h), ("Z",)))

    paint = ir.Path(rect(0, 0, 100, 100).segments, fill=ir.Paint((1, 0, 0)))
    inner = ir.Group(
        (paint,),
        transform=ir.translate(10, 10),
        clip=ir.ClipUnion((rect(0, 0, 20, 50), rect(20, 0, 20, 50))),
        opacity=0.5,
    )
    outer = ir.Group(
        (inner,),
        transform=ir.translate(10, 5),
        clip=ir.ClipUnion((rect(0, 0, 30, 60), rect(30, 0, 30, 60))),
        opacity=0.5,
    )
    page = ir.Page(100, 100, (outer,))
    image, _ = render_pdf(tmp_path, page, provider)
    assert image.getpixel((60, 140)) == pytest.approx((255, 191, 191), abs=2)
    assert image.getpixel((130, 140)) == (255, 255, 255)
    assert image.getpixel((60, 180)) == (255, 255, 255)


def test_clip_union_keeps_searchable_text_and_does_not_repeat_content(tmp_path, provider):
    page, _ = svg_to_ir(
        svg(
            '<defs><clipPath id="c"><rect width="70" height="100"/><rect x="30" width="70" height="100"/></clipPath></defs><text x="10" y="50" font-size="20" clip-path="url(#c)">TEXT</text>'
        ),
        100,
        100,
        provider,
    )
    _, extracted = render_pdf(tmp_path, page, provider)
    assert extracted.count("TEXT") == 1


def test_clip_union_roundtrips_svg_with_each_child_rule(tmp_path, provider):
    from tavotto.rendercore.svgwriter import write_svg

    source = svg(
        '<defs><clipPath id="c"><path clip-rule="evenodd" d="M0 0H100V100H0Z M25 25H75V75H25Z"/><rect x="40" y="40" width="20" height="20"/></clipPath></defs><rect width="100" height="100" fill="red" clip-path="url(#c)"/>'
    )
    page, _ = svg_to_ir(source, 100, 100, provider)
    out = tmp_path / "roundtrip.svg"
    write_svg(page, out, provider)
    root = ET.fromstring(out.read_bytes())
    clip = next(el for el in root.iter() if el.tag.endswith("clipPath"))
    assert [el.get("clip-rule") for el in clip] == ["evenodd", "nonzero"]
    replay, _ = svg_to_ir(out.read_bytes(), 100, 100, provider)
    image, _ = render_pdf(tmp_path, replay, provider)
    assert image.getpixel((60, 100)) == (255, 255, 255)
    assert image.getpixel((100, 100)) == (255, 0, 0)


def test_clip_union_validation_and_eps_overlay_reject_structurally(tmp_path, provider):
    from tavotto.rendercore.epswriter import OverlayWriter, UnsupportedCapability

    clip = ir.Path((("M", 0, 0), ("L", 10, 0), ("L", 10, 10), ("Z",)))
    for value in ([], (), (object(),)):
        with pytest.raises(ir.IRError, match="ClipUnion|裁剪成员"):
            ir.validate(ir.Page(100, 100, (ir.Group((), clip=ir.ClipUnion(value)),)))
    page = ir.Page(100, 100, (ir.Group((), clip=ir.ClipUnion((clip, clip))),))
    writer = OverlayWriter(page, provider)
    with pytest.raises(UnsupportedCapability) as error:
        writer._emit(page.children[0])
    assert error.value.operation == "clip"


def test_svg_page_resource_is_limited_to_self_contained_static_or_declarative_sources():
    assert "svg" not in ir.FILE_KINDS
    # A static/declarative SVG is a self-contained page (ADR-XXXX), never an
    # unpaired execution representation.
    for origin in ("static", "declarative"):
        resource = ir.FileResource("chart.svg", "svg", "a" * 64, origin, "chart")
        ir.validate(ir.Page(100, 100, (ir.ImportedPage("s", (0, 0, 100, 100)),), {"s": resource}))
    resource = ir.FileResource("chart.svg", "svg", "a" * 64, "execution", "chart")
    with pytest.raises(ir.IRError) as error:
        ir.validate(ir.Page(100, 100, (ir.ImportedPage("s", (0, 0, 100, 100)),), {"s": resource}))
    assert error.value.code == "resource_kind_mismatch"


def test_declarative_native_svg_is_imported_page_only_and_pdf_rejects_explicitly(
    tmp_path, provider
):
    from tavotto.rendercore.pdfwriter import UnsupportedCapability, write_pdf

    resource = ir.FileResource(
        "chart.svg", "svg", "a" * 64, "declarative", "chart", svg_sha256="a" * 64
    )
    page = ir.Page(100, 100, (ir.ImportedPage("s", (0, 0, 100, 100)),), {"s": resource})
    assert ir.validate(page) is page
    with pytest.raises(ir.IRError) as error:
        ir.validate(ir.Page(100, 100, (ir.Image("s", (0, 0, 100, 100)),), {"s": resource}))
    assert error.value.code == "resource_kind_mismatch"
    out = tmp_path / "should-not-exist.pdf"
    with pytest.raises(UnsupportedCapability) as error:
        write_pdf(page, out, provider)
    assert error.value.operation == "imported_page"
    assert not out.exists()


@pytest.mark.parametrize(
    "attrs,expected_box,expected_physical",
    [
        (
            'width="200pt" height="100pt" viewBox="10 20 100 100" preserveAspectRatio="xMaxYMin meet"',
            "10 20 100 100",
            ("200", "100"),
        ),
        ('width="100pt" height="50pt"', "0 0 133.3333 66.6667", ("100", "50")),
    ],
)
def test_declarative_native_svg_keeps_intrinsic_physical_frame_and_original_aspect_ratio(
    attrs, expected_box, expected_physical, tmp_path, provider
):
    from tavotto.rendercore.svgwriter import write_svg

    source = svg(
        '<defs><linearGradient id="g"><stop offset="0" stop-color="red"/><stop offset="1" stop-color="blue"/></linearGradient></defs><rect width="100" height="100" fill="url(#g)"/>',
        attrs,
    )
    digest = hashlib.sha256(source).hexdigest()
    resource = ir.FileResource(
        "chart.svg", "svg", digest, "declarative", "chart", svg_sha256=digest
    )
    page = ir.Page(200, 100, (ir.ImportedPage("s", (0, 0, 200, 100)),), {"s": resource})
    out = tmp_path / "native.svg"
    write_svg(page, out, provider, {"s": source}, imported_svgs={"s": source})
    root = ET.fromstring(out.read_bytes())
    nested = [el for el in root.iter() if el.tag.endswith("}svg") and el is not root][0]
    assert [float(v) for v in nested.get("viewBox").split()] == pytest.approx(
        [float(v) for v in expected_box.split()], abs=1e-4
    )
    assert (nested.get("width"), nested.get("height")) == expected_physical
    assert nested.get("preserveAspectRatio") == (
        "xMaxYMin meet" if "preserveAspectRatio" in attrs else None
    )
    assert any(el.tag.endswith("linearGradient") for el in nested.iter())


@pytest.fixture(scope="module")
def chromium():
    playwright = pytest.importorskip(
        "playwright.sync_api", reason="not_run: Playwright unavailable"
    )
    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch()
        except playwright.Error as exc:
            pytest.skip(f"not_run: Chromium unavailable: {exc}")
        try:
            yield browser
        finally:
            browser.close()


def test_chromium_independently_confirms_transform_list_and_use_xy(chromium):
    page = chromium.new_page()
    try:
        page.set_content(
            svg(
                '<defs><path id="p" d="M1 2 L3 4"/></defs><g transform="translate(10 20)"><use href="#p" x="3" y="4" transform="translate(5 6) scale(2)"/></g>',
                'width="100" height="100" viewBox="0 0 100 100"',
            ).decode()
        )
        point = page.locator("use").evaluate(
            "el => { const b = el.getBBox(); const p = new DOMPoint(b.x, b.y).matrixTransform(el.getCTM()); return [p.x, p.y]; }"
        )
        assert point == pytest.approx((23, 38))
    finally:
        page.close()


def test_chromium_native_svg_and_clip_union_keep_original_pixels(tmp_path, provider, chromium):
    from PIL import Image, ImageChops

    from tavotto.rendercore.svgwriter import write_svg

    cases = [
        svg(
            '<defs><clipPath id="c"><path clip-rule="evenodd" d="M0 0H100V100H0Z M25 25H75V75H25Z"/><path d="M50 0V100H90V0Z"/><rect x="40" y="40" width="20" height="20"/></clipPath></defs><g clip-path="url(#c)" opacity=".5"><rect width="100" height="100" fill="red"/></g>'
        ),
        svg(
            '<defs><linearGradient id="g"><stop offset="0" stop-color="red"/><stop offset="1" stop-color="blue"/></linearGradient></defs><rect x="10" y="20" width="100" height="100" fill="url(#g)"/>',
            'width="200pt" height="100pt" viewBox="10 20 100 100" preserveAspectRatio="xMaxYMin meet"',
        ),
    ]
    browser_page = chromium.new_page()
    try:
        for i, source in enumerate(cases):
            if i == 0:
                page, _ = svg_to_ir(source, 100, 100, provider)
                files, representations = {}, {}
            else:
                digest = hashlib.sha256(source).hexdigest()
                resource = ir.FileResource(
                    "chart.svg", "svg", digest, "declarative", "chart", svg_sha256=digest
                )
                page = ir.Page(
                    200,
                    100,
                    (ir.ImportedPage("s", (0, 0, 200, 100)),),
                    {"s": resource},
                    background=None,
                )
                files, representations = {"s": source}, {"s": source}
            out = tmp_path / f"browser-{i}.svg"
            write_svg(page, out, provider, files, imported_svgs=representations)
            reference = source
            if i == 1:
                # Independent, hand-sized placement: a 200×100 pt container
                # holds the original SVG in a 200×100 user-unit viewport.
                # The writer's placement/coordinate helpers are not consulted.
                nested_reference = ET.fromstring(source)
                for key, value in {
                    "width": "200",
                    "height": "100",
                    "x": "0",
                    "y": "0",
                    "overflow": "hidden",
                }.items():
                    nested_reference.set(key, value)
                reference = svg(
                    ET.tostring(nested_reference, encoding="unicode"),
                    'width="200pt" height="100pt" viewBox="0 0 200 100"',
                )
                browser_page.set_content("<style>body {margin: 0}</style>" + source.decode())
                browser_page.locator("svg").first.screenshot(path=tmp_path / "top-level-1.png")
            screenshots = []
            for data in (reference, out.read_bytes()):
                browser_page.set_content("<style>body {margin: 0}</style>" + data.decode())
                screenshots.append(
                    Image.open(io.BytesIO(browser_page.locator("svg").first.screenshot())).convert(
                        "RGB"
                    )
                )
            assert screenshots[0].size == screenshots[1].size
            screenshots[0].save(tmp_path / f"reference-{i}.png")
            screenshots[1].save(tmp_path / f"written-{i}.png")
            difference = ImageChops.difference(*screenshots)
            assert max(high for low, high in difference.getextrema()) <= 1, f"case {i}"
            if i == 1:
                root = ET.fromstring(out.read_bytes())
                nested = [el for el in root.iter() if el.tag.endswith("}svg") and el is not root][0]
                assert nested.get("overflow") == "hidden"
                assert nested.get("preserveAspectRatio") == "xMaxYMin meet"
                assert (nested.get("width"), nested.get("height")) == ("200", "100")
    finally:
        browser_page.close()


TEXT_CHUNKS = [
    ("continuous", 'AB<tspan fill="red">CD</tspan>EF', "", "ABCDEF"),
    ("mixed_fonts", 'A<tspan font-size="12" font-weight="bold" fill="red">BC</tspan>D', "", "ABCD"),
    ("relative", 'AB<tspan dx="8" dy="-4">CD</tspan>EF', "", "ABCDEF"),
    ("absolute_x", 'AB<tspan x="180">CD</tspan>EF', "", "ABCDEF"),
    ("absolute_y", 'AB<tspan y="80">CD</tspan>EF', "", "ABCDEF"),
    ("child_anchor", 'AB<tspan text-anchor="end">CD</tspan>EF', "", "ABCDEF"),
    ("first_anchor_end", '<tspan text-anchor="end">AB<tspan>CD</tspan>EF</tspan>', "", "ABCDEF"),
    (
        "first_anchor_start",
        '<tspan text-anchor="start">AB<tspan dx="-40">CD</tspan>EF</tspan>',
        "",
        "ABCDEF",
    ),
    ("negative_dx", 'AB<tspan dx="-40">CD</tspan>EF', "", "ABCDEF"),
    ("whitespace", " A <tspan>  B </tspan> C ", "", "A B C"),
    ("preserved_space", " A <tspan>  B </tspan> C ", 'xml:space="preserve"', " A   B  C "),
    ("newline", "\n  A\n  <tspan> B </tspan>\n C\n ", "", "A B C"),
    ("nonbreaking_space", "A\u00a0<tspan>B</tspan>", "", "A\u00a0B"),
    ("kerning", 'A<tspan fill="red">V</tspan>', "", "AV"),
    ("relative_kerning", 'A<tspan dx="8" dy="-4" fill="red">V</tspan>', "", "AV"),
    ("rotated_glyphs", "AB<tspan>CD</tspan>", 'rotate="90"', "ABCD"),
    ("rotated_span", 'AB<tspan rotate="90">CD</tspan>EF', "", "ABCDEF"),
    (
        "rotated_element",
        'AB<tspan dx="8" dy="-4">CD</tspan>EF',
        'transform="rotate(-30 100 50)"',
        "ABCDEF",
    ),
    ("nested_dx", '<tspan dx="5">AB</tspan>C', 'dx="10"', "ABC"),
    ("empty_absolute", 'A<tspan x="180"></tspan>B', "", "AB"),
    ("positioned_space", 'A<tspan x="180"> </tspan>B', "", "A B"),
    ("preserve_boundary", 'A<tspan xml:space="preserve">  </tspan> B', "", "A   B"),
]


@pytest.mark.parametrize(
    "body,expected",
    [
        ("AB<tspan>CD</tspan>EF", [(82, 50), (88, 50), (94, 50), (100, 50), (106, 50), (112, 50)]),
        (
            'AB<tspan dx="8" dy="-4">CD</tspan>EF',
            [(78, 50), (84, 50), (98, 46), (104, 46), (110, 46), (116, 46)],
        ),
        (
            'AB<tspan x="180">CD</tspan>EF',
            [(94, 50), (100, 50), (168, 50), (174, 50), (180, 50), (186, 50)],
        ),
        (
            'AB<tspan y="80">CD</tspan>EF',
            [(94, 50), (100, 50), (100, 80), (106, 80), (112, 80), (118, 80)],
        ),
        (
            'AB<tspan dx="-40">CD</tspan>EF',
            [(108, 50), (114, 50), (80, 50), (86, 50), (92, 50), (98, 50)],
        ),
        ('A<tspan font-size="20">BC</tspan>D', [(82, 50), (88, 50), (100, 50), (112, 50)]),
    ],
)
def test_text_chunk_geometry_uses_hand_calculated_font_advances(body, expected):
    from support.fakeface import FakeProvider

    source = svg(f'<text x="100" y="50" font-size="10" text-anchor="middle">{body}</text>')
    page, _ = svg_to_ir(source, 100, 100, FakeProvider())
    actual = _text_glyph_positions(page)
    assert [(char["x"], char["y"]) for char in actual] == pytest.approx(expected)


def test_dom_boundary_does_not_hide_independent_ligature_characters_or_relative_positions():
    from support.fakeface import LIGATURE_GID, FakeProvider

    source = svg(
        '<text x="50" y="50" font-size="10" text-anchor="middle">f<tspan fill="red" dx="3">i</tspan></text>'
    )
    page, _ = svg_to_ir(source, 100, 100, FakeProvider())
    assert [(item["text"], item["x"]) for item in _text_glyph_positions(page)] == [
        ("f", 42.5),
        ("i", 51.5),
    ]
    glyphs = [glyph for node, _ in final_nodes(page) for run in node.runs for glyph in run.glyphs]
    assert all(glyph.gid != LIGATURE_GID for glyph in glyphs)
    same_node, _ = svg_to_ir(
        svg('<text x="50" y="50" font-size="10" text-anchor="middle">fi</text>'),
        100,
        100,
        FakeProvider(),
    )
    glyphs = [
        glyph for node, _ in final_nodes(same_node) for run in node.runs for glyph in run.glyphs
    ]
    assert len(glyphs) == 1 and glyphs[0].gid == LIGATURE_GID


def test_text_chunk_keeps_scoped_opacity_and_unrotated_run_batching():
    from support.fakeface import FakeProvider

    source = svg(
        '<text x="50" y="50" font-size="10" text-anchor="middle">A<tspan opacity=".5">B<tspan opacity=".5">C</tspan>D</tspan>E</text>'
    )
    page, _ = svg_to_ir(source, 100, 100, FakeProvider())
    assert [
        node.opacity
        for node, _ in ir.walk(page)
        if isinstance(node, ir.Group) and node.opacity != 1
    ] == [0.5, 0.5]
    assert "".join(item["text"] for item in _text_glyph_positions(page)) == "ABCDE"
    run, _ = svg_to_ir(
        svg('<text font-size="10">' + "A" * 5000 + "</text>"), 100, 100, FakeProvider()
    )
    texts = [node for node, _ in final_nodes(run) if isinstance(node, ir.ShapedText)]
    assert len(texts) == 1 and len(texts[0].runs[0].glyphs) == 5000


def test_referenced_text_uses_instance_parent_xml_space_inheritance():
    from support.fakeface import FakeProvider

    source = svg(
        '<defs><text id="t"> A <tspan> B </tspan> C </text></defs><g xml:space="preserve"><use href="#t"/></g>'
    )
    page, _ = svg_to_ir(source, 100, 100, FakeProvider())
    assert "".join(item["text"] for item in _text_glyph_positions(page)) == " A  B  C "


@pytest.mark.parametrize(
    "body,attributes,expected",
    [
        ('AB<tspan fill="red" font-weight="bold">CD</tspan>EF', "", "ABCDEF"),
        (" A <tspan>  B </tspan> C ", "", "A B C"),
        ('AB<tspan dx="8" dy="-4" fill="red">CD</tspan>EF', "", "ABCDEF"),
        ("AB<tspan>CD</tspan>", 'rotate="90"', "ABCD"),
    ],
)
def test_anchored_text_pdf_has_source_characters_once(
    body, attributes, expected, tmp_path, provider
):
    from support.pdftext import text as read_text
    from tavotto.rendercore.pdfwriter import write_pdf

    source = svg(
        f'<text x="100" y="50" font-size="20" text-anchor="middle" {attributes}>{body}</text>',
        'width="300" height="120" viewBox="0 0 300 120"',
    )
    page, _ = svg_to_ir(source, 225, 90, provider)
    output = tmp_path / "anchored.pdf"
    write_pdf(page, output, provider)
    # PDFium's reader is independent of the IR converter and PDF writer.
    # Only reader-generated layout spaces/newlines are filtered by this helper;
    # actual source spaces must survive unchanged.
    assert read_text(output) == expected


def _text_glyph_positions(page, user_per_pt=1):
    result = []
    for node, matrix in final_nodes(page):
        if not isinstance(node, ir.ShapedText):
            continue
        cursor = node.x
        for run in node.runs:
            factor = run.size / page.resources[run.font].units_per_em
            for glyph in run.glyphs:
                if glyph.text:
                    p = ir.apply(matrix, cursor, node.y)
                    q = ir.apply(matrix, cursor + 1, node.y)
                    result.append(
                        {
                            "text": glyph.text,
                            "x": p[0] * user_per_pt,
                            "y": (page.height_pt - p[1]) * user_per_pt,
                            "angle": math.degrees(math.atan2(-(q[1] - p[1]), q[0] - p[0])),
                        }
                    )
                cursor += glyph.x_advance * factor
    return result


def _font_faces_for_browser(provider):
    return [
        {
            "family": "Liberation Sans",
            "data": base64.b64encode(
                provider.face_for("sans-serif", bold, italic).path.read_bytes()
            ).decode(),
            "weight": "700" if bold else "400",
            "style": "italic" if italic else "normal",
        }
        for bold, italic in [(False, False), (True, False), (False, True), (True, True)]
    ]


@pytest.mark.parametrize(
    "name,body,attributes,expected", TEXT_CHUNKS, ids=[case[0] for case in TEXT_CHUNKS]
)
def test_text_chunks_match_independent_chromium_characters_and_baselines(
    name, body, attributes, expected, provider, chromium, tmp_path
):
    source = svg(
        f'<text x="100" y="50" font-family="Liberation Sans" font-size="20" text-anchor="middle" {attributes}>{body}</text>',
        'width="300" height="120" viewBox="0 0 300 120"',
    )
    browser_page = chromium.new_page()
    try:
        browser_page.set_content("<html><body></body></html>")
        browser_page.evaluate(
            """async faces => {for (const spec of faces) {
          const face = new FontFace(spec.family, 'url(data:font/ttf;base64,'+spec.data+')', {weight:spec.weight,style:spec.style});
          await face.load(); document.fonts.add(face);
        }}""",
            _font_faces_for_browser(provider),
        )
        browser_page.locator("body").evaluate(
            "(body,source) => body.innerHTML=source", source.decode()
        )
        observed = browser_page.locator("text").evaluate("""el => {
          const local = el.getCTM(), root = el.ownerSVGElement.getCTM().inverse();
          return Array.from({length:el.getNumberOfChars()}, (_,i) => {
            const p=el.getStartPositionOfChar(i).matrixTransform(local).matrixTransform(root);
            const q=el.getEndPositionOfChar(i).matrixTransform(local).matrixTransform(root);
            return {x:p.x,y:p.y,angle:Math.atan2(q.y-p.y,q.x-p.x)*180/Math.PI};
          });
        }""")
    finally:
        browser_page.close()
    # 96 CSS px/in → 72 physical pt/in, independent of the converter's mapping.
    page, _ = svg_to_ir(source, 225, 90, provider)
    actual = _text_glyph_positions(page, user_per_pt=4 / 3)
    assert "".join(item["text"] for item in actual) == expected
    assert len(actual) == len(observed) == len(expected)
    for actual_char, browser_char in zip(actual, observed):
        assert (actual_char["x"], actual_char["y"]) == pytest.approx(
            (browser_char["x"], browser_char["y"]), abs=0.06
        ), name
        assert actual_char["angle"] == pytest.approx(browser_char["angle"], abs=1e-4)
    if name in {
        "continuous",
        "mixed_fonts",
        "relative",
        "absolute_x",
        "absolute_y",
        "negative_dx",
        "rotated_glyphs",
        "rotated_span",
        "rotated_element",
    }:
        from support.pdftext import chars as read_characters
        from tavotto.rendercore.pdfwriter import write_pdf

        output = tmp_path / "chrome-baselines.pdf"
        write_pdf(page, output, provider)
        characters = read_characters(output)
        # These sources have unique ASCII letters. Match by character because
        # the independent PDF reader sorts lines by baseline rather than DOM.
        assert sorted(char.c for char in characters) == sorted(expected)
        written = {char.c: char for char in characters}
        for char, browser_char in zip(expected, observed):
            assert (written[char].x * 4 / 3, written[char].y * 4 / 3) == pytest.approx(
                (browser_char["x"], browser_char["y"]), abs=0.06
            ), name
