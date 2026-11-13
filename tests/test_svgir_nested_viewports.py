"""Nested SVG viewports: hand geometry, Chrome and independent PDF/PS readers."""

from __future__ import annotations

import hashlib
import io

import pytest

from tavotto.rendercore import epscontainer, ir, originaleps, svgir
from tavotto.rendercore.svgcheck import SVGError, parse_svg
from test_native_scene_eps import assert_same_ink, pdf_image, ps_image, svg
from test_svg_ir import path_points

pytest_plugins = ("test_native_scene_eps", "test_svg_ir")

PARS = [
    ("none", [(20, 10), (80, 40)]),
    ("xMidYMid meet", [(35, 10), (65, 40)]),
    ("xMaxYMin slice", [(20, 10), (80, 70)]),
]


def nested(body, attrs=""):
    return f'<svg x="20" y="10" width="60" height="30" viewBox="10 20 20 20" {attrs}>{body}</svg>'


def browser_image(chromium, source, width=100, height=100):
    from PIL import Image

    page = chromium.new_page(viewport={"width": width * 4, "height": height * 4})
    try:
        page.set_content(
            "<style>body{margin:0;background:white}body>svg{"
            f"width:{width * 4}px;height:{height * 4}px" + "}</style>" + source.decode()
        )
        return Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    finally:
        page.close()


@pytest.mark.parametrize("par,expected", PARS)
def test_nonzero_viewbox_and_xy_hand_formula_matches_chrome(chromium, par, expected):
    source = svg(nested('<path id="p" d="M10 20L30 40"/>', f'preserveAspectRatio="{par}"'))
    page, notes = svgir.svg_to_ir(source, 100, 100, None)
    assert notes == []
    assert path_points(page) == [(op, x, 100 - y) for op, (x, y) in zip(("M", "L"), expected)]
    browser = chromium.new_page()
    try:
        browser.set_content(
            "<style>body{margin:0}body>svg{width:400px;height:400px}</style>" + source.decode()
        )
        points = browser.locator("#p").evaluate(
            "el => [[10,20],[30,40]].map(p=>{let q=new DOMPoint(...p)"
            ".matrixTransform(el.getScreenCTM()); return [q.x/4,q.y/4]})"
        )
        for actual, target in zip(points, expected):
            assert actual == pytest.approx(target)
    finally:
        browser.close()


def test_local_percent_context_and_siblings_restore_through_two_viewports():
    source = svg(
        nested(
            '<rect id="p" x="50%" y="100%" width="25%" height="50%"/>'
            '<svg x="50%" y="50%" width="50%" height="50%">'
            '<rect id="q" width="100%" height="100%"/></svg>',
            'preserveAspectRatio="none"',
        )
        + '<rect id="r" x="50%" y="50%" width="10%" height="10%"/>'
    )
    page, _ = svgir.svg_to_ir(source, 100, 100, None)
    assert path_points(page) == [("M", 20, 90), ("L", 35, 90), ("L", 35, 75), ("L", 20, 75), ("Z",)]
    assert path_points(page, "q") == [
        ("M", 20, 105),
        ("L", 50, 105),
        ("L", 50, 90),
        ("L", 20, 90),
        ("Z",),
    ]
    assert path_points(page, "r") == [
        ("M", 50, 50),
        ("L", 60, 50),
        ("L", 60, 40),
        ("L", 50, 40),
        ("Z",),
    ]


def test_no_viewbox_default_size_absolute_units_and_em_resolve_in_parent():
    source = svg(
        '<g font-size="10"><svg x="1em" y=".75pt" width=".25in" height="50%">'
        '<rect id="p" width="100%" height="100%"/></svg></g>'
        '<svg x="1" y="2"><path id="q" d="M0 0L100 100"/></svg>'
    )
    page, _ = svgir.svg_to_ir(source, 100, 100, None)
    assert path_points(page) == [("M", 10, 99), ("L", 34, 99), ("L", 34, 49), ("L", 10, 49), ("Z",)]
    assert path_points(page, "q") == [("M", 1, 98), ("L", 101, -2)]


def test_use_svg_dimensions_override_instance_but_not_definition_or_following_sibling(
    chromium, tmp_path, faces, gs
):
    source = svg(
        '<defs><svg id="s" x="2" y="3" width="40" height="40" viewBox="0 0 10 10">'
        '<rect id="p" width="100%" height="100%"/></svg></defs>'
        '<use id="instance" href="#s" x="10" y="20" width="20" height="10"/>'
        '<rect id="r" x="50%" y="50%" width="10" height="10"/>'
    )
    page, _ = svgir.svg_to_ir(source, 100, 100, None)
    assert path_points(page) == [("M", 17, 77), ("L", 27, 77), ("L", 27, 67), ("L", 17, 67), ("Z",)]
    reference = browser_image(chromium, source)
    assert reference.getpixel((18 * 4, 24 * 4)) == (0, 0, 0)
    assert reference.getpixel((14 * 4, 24 * 4)) == (255, 255, 255)
    actual, _ = pdf_image(tmp_path, source, 100, 100, faces)
    prepared = originaleps.prepare_scene_ps(source, 100, 100, faces=faces)
    eps = ps_image(tmp_path, gs, epscontainer.pack_prepared_eps(prepared.to_payload()))
    assert_same_ink(actual, reference)
    assert_same_ink(eps, reference)


def test_svg_own_transform_xy_ancestor_and_nonzero_root_origin_each_apply_once(chromium):
    source = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200pt" height="100pt" '
        'viewBox="10 20 100 100" preserveAspectRatio="none"><g transform="translate(5 7)">'
        + nested(
            '<path id="p" d="M10 20L30 40"/>',
            'transform="translate(3 4) scale(.5)" preserveAspectRatio="none"',
        )
        + "</g></svg>"
    ).encode()
    page, _ = svgir.svg_to_ir(source, 200, 100, None)
    # Local viewBox -> x/y -> own scale/translation -> ancestor -> root.
    assert path_points(page) == [("M", 16, 104), ("L", 76, 89)]
    browser = chromium.new_page()
    try:
        browser.set_content(
            "<style>body{margin:0}body>svg{width:800px;height:400px}</style>" + source.decode()
        )
        actual = browser.locator("#p").evaluate(
            "el=>[[10,20],[30,40]].map(p=>{let q=new DOMPoint(...p).matrixTransform(el.getScreenCTM());return[q.x/4,q.y/4]})"
        )
        assert actual == [[16, -4], [76, 11]]
    finally:
        browser.close()


OVERFLOWS = [
    ("", "", False),
    ('overflow="visible"', "", True),
    ('overflow="hidden"', "", False),
    ('overflow="auto"', "", True),
    ('overflow="scroll"', "", False),
    ('style="overflow:visible"', "", True),
    ('class="open"', "<style>.open{overflow:visible}</style>", True),
    ('overflow="hidden" class="open"', "<style>.open{overflow:visible}</style>", True),
    ('overflow="inherit"', "", True),
    ('overflow="initial"', "", True),
    ('overflow="unset"', "", True),
    ('overflow="revert"', "", False),
]


@pytest.mark.parametrize("attrs,css,visible", OVERFLOWS)
def test_overflow_cascade_defaults_and_user_clip_agree_in_three_readers(
    tmp_path, faces, chromium, gs, attrs, css, visible
):
    source = svg(
        css
        + '<g overflow="visible">'
        + nested('<rect x="-20" y="-20" width="100" height="100" fill="red"/>', attrs)
        + "</g>"
    )
    reference = browser_image(chromium, source)
    actual, _ = pdf_image(tmp_path, source, 100, 100, faces)
    prepared = originaleps.prepare_scene_ps(source, 100, 100, faces=faces)
    eps = ps_image(tmp_path, gs, epscontainer.pack_prepared_eps(prepared.to_payload()))
    expected = (255, 0, 0) if visible else (255, 255, 255)
    for image in (reference, actual, eps):
        assert image.getpixel((15 * 4, 15 * 4)) == expected
        assert image.getpixel((50 * 4, 25 * 4)) == (255, 0, 0)
    assert_same_ink(actual, reference)
    assert_same_ink(eps, reference)


@pytest.mark.parametrize("par,_expected", PARS)
def test_ancestor_transform_clip_and_nested_user_clip_percentages_have_distinct_spaces(
    tmp_path, faces, chromium, gs, par, _expected
):
    source = svg(
        '<defs><clipPath id="outer"><rect x="15" y="5" width="55" height="60"/></clipPath>'
        '<clipPath id="own"><rect x="10" y="20" width="50%" height="100%"/></clipPath></defs>'
        '<g transform="translate(3 4) scale(.8 1.2)" clip-path="url(#outer)">'
        + nested(
            '<rect x="-100" y="-100" width="300" height="300" fill="red"/>',
            f'preserveAspectRatio="{par}" clip-path="url(#own)"',
        )
        + "</g>"
    )
    reference = browser_image(chromium, source)
    actual, _ = pdf_image(tmp_path, source, 100, 100, faces)
    prepared = originaleps.prepare_scene_ps(source, 100, 100, faces=faces)
    eps = ps_image(tmp_path, gs, epscontainer.pack_prepared_eps(prepared.to_payload()))
    assert_same_ink(actual, reference)
    assert_same_ink(eps, reference)
    # The source isn't rewritten to meet the oracle and the physical frame is fixed.
    assert prepared.source_svg_sha256 == hashlib.sha256(source).hexdigest()
    assert (prepared.width_pt, prepared.height_pt) == (100, 100)


def test_nested_opacity_is_one_group_not_per_child_or_twice(tmp_path, faces, chromium):
    source = svg(
        nested(
            '<rect x="10" y="20" width="12" height="20" fill="red"/>'
            '<rect x="18" y="20" width="12" height="20" fill="blue"/>',
            'opacity=".5" preserveAspectRatio="none"',
        )
    )
    page, _ = svgir.svg_to_ir(source, 100, 100, faces)
    assert [
        node.opacity
        for node, _ in ir.walk(page)
        if isinstance(node, ir.Group) and node.opacity != 1
    ] == [0.5]
    actual, _ = pdf_image(tmp_path, source, 100, 100, faces)
    reference = browser_image(chromium, source)
    for image in (actual, reference):
        # PDFium's nested group alpha quantizes by up to two channel levels.
        assert image.getpixel((48 * 4, 25 * 4)) == pytest.approx((128, 128, 255), abs=2)


@pytest.mark.parametrize(
    "where,on", [("root", "svg"), ("root", "g"), ("nested", "svg"), ("nested", "g")]
)
def test_definition_percent_basis_and_explicit_svg_clip_replaces_implicit_overflow(
    tmp_path, faces, chromium, gs, where, on
):
    defs = '<defs><clipPath id="c"><rect x="10" y="20" width="50%" height="20"/></clipPath></defs>'
    body = '<rect x="-100" y="-100" width="300" height="300" fill="red"/>'
    if on == "g":
        body = '<g clip-path="url(#c)">' + body + "</g>"
    source = svg(
        (defs if where == "root" else "")
        + nested(
            (defs if where == "nested" else "") + body,
            'preserveAspectRatio="none"' + (' clip-path="url(#c)"' if on == "svg" else ""),
        )
    )
    reference = browser_image(chromium, source)
    actual, _ = pdf_image(tmp_path, source, 100, 100, faces)
    prepared = originaleps.prepare_scene_ps(source, 100, 100, faces=faces)
    eps = ps_image(tmp_path, gs, epscontainer.pack_prepared_eps(prepared.to_payload()))
    for image in (reference, actual, eps):
        assert image.getpixel((30 * 4, 20 * 4)) == (255, 0, 0)
        assert image.getpixel((65 * 4, 20 * 4)) == (
            (255, 0, 0) if where == "root" else (255, 255, 255)
        )
        assert image.getpixel((90 * 4, 20 * 4)) == (
            (255, 0, 0) if where == "root" and on == "svg" else (255, 255, 255)
        )
    assert_same_ink(actual, reference)
    assert_same_ink(eps, reference)


def test_forward_clip_definition_in_unrendered_nested_viewport_uses_its_own_basis(
    tmp_path, faces, chromium, gs
):
    source = svg(
        '<g clip-path="url(#c)"><rect width="100" height="100" fill="red"/></g>'
        '<defs><svg width="40" height="20" viewBox="0 0 20 10"><defs>'
        '<clipPath id="c"><rect x="5" y="5" width="50%" height="100%"/></clipPath>'
        "</defs></svg></defs>"
    )
    reference = browser_image(chromium, source)
    actual, _ = pdf_image(tmp_path, source, 100, 100, faces)
    prepared = originaleps.prepare_scene_ps(source, 100, 100, faces=faces)
    eps = ps_image(tmp_path, gs, epscontainer.pack_prepared_eps(prepared.to_payload()))
    for image in (reference, actual, eps):
        assert image.getpixel((10 * 4, 10 * 4)) == (255, 0, 0)
        assert image.getpixel((20 * 4, 10 * 4)) == (255, 255, 255)
    assert_same_ink(actual, reference)
    assert_same_ink(eps, reference)


def test_ancestor_clip_intersects_nested_union_with_member_hole(tmp_path, faces, chromium, gs):
    source = svg(
        '<defs><clipPath id="outer"><rect x="20" y="20" width="60" height="60"/></clipPath>'
        '<clipPath id="inner"><path clip-rule="evenodd" d="M0 0H100V100H0Z M25 25H75V75H25Z"/>'
        '<rect x="40" y="40" width="20" height="20"/></clipPath></defs>'
        '<g clip-path="url(#outer)"><svg x="10" y="10" width="80" height="80" viewBox="0 0 100 100" '
        'clip-path="url(#inner)"><rect width="100" height="100" fill="red"/></svg></g>'
    )
    reference = browser_image(chromium, source)
    actual, _ = pdf_image(tmp_path, source, 100, 100, faces)
    prepared = originaleps.prepare_scene_ps(source, 100, 100, faces=faces)
    eps = ps_image(tmp_path, gs, epscontainer.pack_prepared_eps(prepared.to_payload()))
    for image in (reference, actual, eps):
        for x, color in (
            (25, (255, 0, 0)),
            (35, (255, 255, 255)),
            (50, (255, 0, 0)),
            (85, (255, 255, 255)),
        ):
            assert image.getpixel((x * 4, 50 * 4)) == color
    assert_same_ink(actual, reference)
    assert_same_ink(eps, reference)


def test_real_composer_imported_svg_padding_text_and_unbound_decoration(tmp_path, faces, gs):
    from tavotto.rendercore.svgwriter import write_svg

    source = svg(
        '<rect x="10" y="20" width="40" height="3" fill="red"/>'
        '<rect x="94" y="2" width="4" height="4" fill="green"/>'
        '<path d="M70 50H90V70H70Z M75 55H85V65H75Z" fill-rule="evenodd"/>'
        '<text x="12" y="95" font-size="4">原图 Á 42</text>'
    )
    digest = hashlib.sha256(source).hexdigest()
    resource = ir.FileResource(
        "scene.svg", "svg", digest, "declarative", "scene", svg_sha256=digest
    )
    page = ir.Page(
        108,
        111,
        (ir.Group((ir.ImportedPage("s", (0, 0, 100, 100)),), transform=ir.translate(5, 4)),),
        {"s": resource},
    )
    output = tmp_path / "composed.svg"
    write_svg(page, output, faces, {"s": source}, imported_svgs={"s": source})
    composed = output.read_bytes()
    assert sum(el.tag.endswith("}svg") for el in parse_svg(composed).iter()) == 2
    prepared = originaleps.prepare_scene_ps(composed, 108, 111, faces=faces)
    assert prepared.source_svg_sha256 == hashlib.sha256(composed).hexdigest()
    actual, text = pdf_image(tmp_path, composed, 108, 111, faces)
    eps = ps_image(tmp_path, gs, epscontainer.pack_prepared_eps(prepared.to_payload()))
    assert_same_ink(eps, actual)
    for image in (actual, eps):
        assert image.size == (432, 444)
        assert image.getpixel((101 * 4, 11 * 4)) == (0, 128, 0)
        assert image.getpixel((25 * 4, 28 * 4)) == (255, 0, 0)
        assert image.getpixel((85 * 4, 67 * 4)) == (255, 255, 255)
    assert "原图" in text and "42" in text


@pytest.mark.parametrize("dimension", ['width="0"', 'height="0"', 'viewBox="0 0 0 10"'])
def test_zero_viewport_has_no_paint_and_does_not_pollute_following_sibling(dimension):
    page, _ = svgir.svg_to_ir(
        svg(
            f'<svg {dimension}><rect width="100" height="100"/></svg><path id="p" d="M10 20L30 40"/>'
        ),
        100,
        100,
        None,
    )
    assert len([node for node, _ in ir.walk(page) if isinstance(node, ir.Path)]) == 1
    assert path_points(page) == [("M", 10, 80), ("L", 30, 60)]


@pytest.mark.parametrize(
    "attrs",
    [
        'width="-1"',
        'height="-1"',
        'width="NaN"',
        'width="1e999"',
        'viewBox="0 0 -1 10"',
        'viewBox="0 0 10"',
        'viewBox="0 0 10 10" preserveAspectRatio="xBogusYMid"',
        'style="overflow:bogus"',
    ],
)
def test_invalid_viewports_refuse_without_partial_page(attrs):
    with pytest.raises(svgir.SVGIRError):
        svgir.svg_to_ir(svg(f'<svg {attrs}><rect width="10" height="10"/></svg>'), 100, 100, None)


@pytest.mark.parametrize(
    "attrs",
    [
        'overflow="url(https://example.org/a)"',
        'style="overflow:url(https://example.org/a)"',
        'style="overflow:expression(alert(1))"',
    ],
)
def test_overflow_does_not_relax_external_resource_or_active_css_boundary(attrs):
    with pytest.raises(SVGError):
        svgir.svg_to_ir(svg(f'<svg {attrs}><rect width="10" height="10"/></svg>'), 100, 100, None)


def test_nested_ir_depth_budget_is_still_enforced():
    source = svg(
        "<svg>" * ir.MAX_DEPTH + '<rect width="10" height="10"/>' + "</svg>" * ir.MAX_DEPTH
    )
    with pytest.raises(svgir.SVGIRError, match="svgir_invalid"):
        svgir.svg_to_ir(source, 100, 100, None)
