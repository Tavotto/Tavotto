"""Native 计算图表：原生 pt/manifest、实际场景上下文及旧协议隔离。"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tavotto.engine import chartmodel, svgchart
from tavotto.engine.chartprofile import NATIVE_PROFILE
from tavotto.rendercore.svgcheck import parse_svg
from tavotto.svgcompose import compose_native_scene
from tavotto.svgscene import scene_inventory

NS = "{http://www.w3.org/2000/svg}"


def setup_chart(body=None, *, preserve="none", size=(100, 100), vb=(0, 0, 100, 100)):
    body = body or (
        '<g id="original"><path id="oldaxis" d="M10 10V90H90"/>'
        '<path id="curve1" d="M10 70H90"/><path id="curve2" d="M10 30H90"/></g>'
    )
    source = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size[0]}pt" height="{size[1]}pt" '
        f'viewBox="{" ".join(map(str, vb))}" preserveAspectRatio="{preserve}">{body}</svg>'
    ).encode()
    inventory = scene_inventory(source)
    data = {"columns": {"x": [0, 1], "y1": [0.5, 0.5], "y2": [0.5, 0.5]}}
    model = {
        "schema": chartmodel.MODEL_SCHEMA,
        "rev": 1,
        "axes": [
            {
                "id": "ax1",
                "rect": [vb[0] + 10, vb[1] + 10, 80, 80],
                "xlim": [0, 1],
                "ylim": [0, 1],
                "xscale": "linear",
                "yscale": "linear",
                "invert_x": False,
                "invert_y": False,
            }
        ],
        "series": [
            {
                "id": f"s{i}",
                "kind": "line",
                "axes": "ax1",
                "node": f"curve{i}",
                "x": "x",
                "y": f"y{i}",
                "style": {"color": color, "linewidth": 3},
            }
            for i, color in [(1, "red"), (2, "blue")]
        ],
        "dataset": data,
        "scene_digest": inventory["scene_digest"],
        "data_digest": chartmodel.digest_of(data),
        "engine": {"matplotlib": "test", "numpy": "test", "tavotto": "test"},
    }
    binding = {
        "model_rev": 1,
        "scene_digest": model["scene_digest"],
        "entries": [
            {"object": "axes:ax1", "node": "oldaxis"},
            {"object": "series:s1", "node": "curve1"},
            {"object": "series:s2", "node": "curve2"},
        ],
    }
    frame = {"view_box": list(vb), "w_pt": size[0], "h_pt": size[1], "preserve": preserve}
    return source, model, binding, frame


def native(model, frame, patches=()):
    return svgchart.scaffold_fragment(model, frame, render_profile=NATIVE_PROFILE, patches=patches)


def element(result, gid):
    return next(e for e in result["manifest"]["elements"] if e["gid"] == gid)


@pytest.mark.parametrize(
    "preserve,expected",
    [
        ("xMidYMid meet", [[0.3, 0.9], [0.7, 0.9]]),
        ("xMidYMid slice", [[0.1, 1.3], [0.9, 1.3]]),
        ("none", [[0.1, 0.9], [0.9, 0.9]]),
        ("xMaxYMin meet", [[0.55, 0.9], [0.95, 0.9]]),
    ],
)
def test_native_physical_figure_uses_one_coordinate_and_property_contract(preserve, expected):
    _, model, _, frame = setup_chart(preserve=preserve, size=(200, 100), vb=(50, 20, 100, 100))
    model["dataset"]["columns"]["y1"] = [0, 0]
    model["data_digest"] = chartmodel.digest_of(model["dataset"])
    result = native(
        model,
        frame,
        [
            {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 4},
            {"gid": "axes_0.xlabel", "prop": "text", "value": "Native label"},
        ],
    )
    line = element(result, "axes_0.lines_0")
    for actual, want in zip(line["geometry"]["paths"][0]["points"], expected):
        assert actual == pytest.approx(want)
    assert line["geometry"]["stroke_pt"] == 4
    assert result["coordinates"]["kind"] == "scene-viewport-figure-fraction"
    assert result["coordinates"]["composed"] is False
    assert result["manifest"]["size_mm"] == [70.56, 35.28]
    assert result["unrestored"] == 0 and not result["warnings"]


@pytest.mark.parametrize("preserve", ["xMidYMid meet", "xMidYMid slice", "none", "xMaxYMin meet"])
def test_native_preview_marker_belongs_only_to_generated_physical_wrappers(preserve):
    marker = "data-tavotto-native-viewport-pt"
    body = (
        f'<g id="original" {marker}="999 999">'
        '<path id="oldaxis" d="M60 30V110H140"/>'
        '<path id="curve1" d="M60 70H140"/><path id="curve2" d="M60 80H140"/>'
        f'<text id="note" {marker}="10 10" x="70" y="40">Keep me</text></g>'
    )
    source, model, binding, frame = setup_chart(
        body, preserve=preserve, size=(200, 100), vb=(50, 20, 100, 100)
    )
    original = bytes(source)
    result = compose_native_scene(source, binding, native(model, frame), model)
    root = parse_svg(result["svg"])
    wrappers = [node for node in root.iter() if marker in node.attrib]
    assert wrappers
    assert all(list(map(float, node.get(marker).split())) == [200, 100] for node in wrappers)
    assert sum(e.get("id") == "axes_0.lines_0" for w in wrappers for e in w.iter()) == 1
    note = next(node for node in root.iter() if node.text == "Keep me")
    assert marker not in note.attrib
    assert source == original


def test_native_creation_and_draw_do_not_inherit_user_tex_rc(monkeypatch):
    import matplotlib
    from matplotlib.text import Text

    _, model, _, frame = setup_chart()
    original_draw = Text.draw
    seen = []

    def draw(self, renderer):
        seen.append(self.get_usetex())
        assert self.get_usetex() is False
        return original_draw(self, renderer)

    monkeypatch.setattr(Text, "draw", draw)
    with matplotlib.rc_context({"text.usetex": True}):
        native(model, frame)
        assert matplotlib.rcParams["text.usetex"] is True
    assert seen


def test_native_composition_has_true_dom_gids_and_ancestor_rect_clip_opacity():
    body = (
        '<defs><clipPath id="c"><rect width="40" height="80"/></clipPath></defs>'
        "<style>g {opacity:.5} * {stroke-linejoin:bevel}</style>"
        '<g id="axes_0" transform="translate(20 10)" clip-path="url(#c)">'
        '<path id="oldaxis" d="M0 0H80"/><path id="curve1" d="M0 50H80"/>'
        '<path id="curve2" d="M0 50H80"/></g>'
        '<path id="axes_0.lines_0" d="M2 2L4 6L6 2" fill="none" stroke="green"/>'
    )
    source, model, binding, frame = setup_chart(body)
    original = bytes(source)
    result = compose_native_scene(source, binding, native(model, frame), model)
    assert source == original
    assert result["composition"]["source_sha256"] == model["scene_digest"]
    assert result["composition"]["composed"] is True
    root = parse_svg(result["svg"])
    ids = [e.get("id") for e in root.iter() if e.get("id")]
    assert ids.count("axes_0.lines_0") == ids.count("axes_0") == 1
    axes = next(e for e in root.iter() if e.get("id") == "axes_0")
    assert any(e.get("id") == "axes_0.lines_0" for e in axes.iter())
    assert any(e.get("id") == "axes_0.lines_1" for e in axes.iter())
    line = element(result, "axes_0.lines_0")
    assert line["geometry"]["clip"] == pytest.approx([0.2, 0.1, 0.4, 0.8])
    assert all(p["opacity"] == 0.5 for p in result["composition"]["placements"])
    assert (
        element(result, "axes_0.lines_1")["geometry"]["paint_order"]
        > line["geometry"]["paint_order"]
    )


def test_native_axes_without_old_axis_binding_uses_first_series_anchor():
    source, model, binding, frame = setup_chart()
    binding["entries"] = [e for e in binding["entries"] if e["object"].startswith("series:")]
    result = compose_native_scene(source, binding, native(model, frame), model)
    assert result["composition"]["composed"] is True
    root = ET.fromstring(result["svg"])
    old_axis = result["composition"]["source_id_map"]["oldaxis"]
    assert any(e.get("id") == old_axis for e in root.iter())
    axes = next(e for e in root.iter() if e.get("id") == "axes_0")
    assert any(e.get("id") == "axes_0.lines_0" for e in axes.iter())
    placement = next(p for p in result["composition"]["placements"] if p["role"] == "axes:ax1")
    assert placement["source_node"] is None
    assert placement["anchor_source_node"] == "curve1"


def test_native_background_is_an_editable_patch_and_keeps_original_background():
    body = (
        '<rect id="original_background" width="100" height="100" fill="#00ff00"/>'
        '<g><path id="oldaxis"/><path id="curve1"/><path id="curve2"/></g>'
    )
    source, model, binding, frame = setup_chart(body)
    for patches, expected_fill in [
        ([], "none"),
        (
            [
                {"gid": "axes_0", "prop": "facecolor", "value": "#ff0000"},
            ],
            "#ff0000",
        ),
    ]:
        scaffold = native(model, frame, patches)
        result = compose_native_scene(source, binding, scaffold, model)
        assert result["composition"]["composed"] is True
        root = ET.fromstring(result["svg"])
        background_id = result["composition"]["source_id_map"]["original_background"]
        original_background = next(e for e in root.iter() if e.get("id") == background_id)
        assert original_background.get("fill") == "#00ff00"
        background = next(e for e in root.iter() if e.get("id") == "axes_0.background")
        path = next(background.iter(NS + "path"))
        assert f"fill: {expected_fill}" in path.get("style", "")
        assert source == setup_chart(body)[0]


def test_native_wrong_known_identity_does_not_become_editable_success():
    source, model, binding, frame = setup_chart()
    scaffold = native(
        model,
        frame,
        [
            {
                "gid": "axes_0.lines_0",
                "prop": "color",
                "value": "#ff0000",
                "identity": "l1:0000000000000000",
            }
        ],
    )
    assert scaffold["warnings"] and scaffold["unrestored"]
    result = compose_native_scene(source, binding, scaffold, model)
    assert result["composition"]["composed"] is False
    assert "native_patch_unrestored" in result["composition"]["limitations"]


@pytest.mark.parametrize("gid", ["axes_0", "axes_0.lines_0"])
def test_native_visible_false_and_restore_remain_valid_snapshots(gid):
    source, model, binding, frame = setup_chart()
    for visible in (False, True):
        result = compose_native_scene(
            source,
            binding,
            native(
                model,
                frame,
                [
                    {"gid": gid, "prop": "visible", "value": visible},
                ],
            ),
            model,
        )
        assert result["composition"]["composed"] is True
        target = element(result, gid)
        assert next(f["value"] for f in target["editable"] if f["prop"] == "visible") is visible
        root = ET.fromstring(result["svg"])
        node = next(e for e in root.iter() if e.get("id") == gid)
        if not visible:
            assert node.get("display") == "none"
            assert not any(
                e.tag in {NS + tag for tag in ("path", "rect", "text", "image", "use")}
                for e in node.iter()
            )
            if gid == "axes_0":
                child = element(result, "axes_0.lines_0")
                assert child["geometry"]["paths"] == []
                assert next(f["value"] for f in child["editable"] if f["prop"] == "visible") is True
        else:
            assert node.get("display") is None
            assert any(e.tag == NS + "path" for e in node.iter())


@pytest.mark.parametrize(
    "clip,reason",
    [
        ('<circle cx="50" cy="50" r="25"/>', "native_clip_nonrectangular"),
        (
            '<rect width="40" height="100"/><rect x="60" width="40" height="100"/>',
            "native_clip_union",
        ),
    ],
)
def test_native_nonrect_clip_keeps_svg_without_fake_editable_qualification(clip, reason):
    body = f'<defs><clipPath id="c">{clip}</clipPath></defs><g clip-path="url(#c)"><path id="oldaxis"/><path id="curve1"/><path id="curve2"/></g>'
    source, model, binding, frame = setup_chart(body)
    result = compose_native_scene(source, binding, native(model, frame), model)
    assert result["composition"]["composed"] is False
    assert reason in result["composition"]["limitations"]
    assert b"axes_0.lines_0" in result["svg"]
    parse_svg(result["svg"])


@pytest.mark.parametrize(
    "body",
    [
        '<g><path id="oldaxis"/><path id="curve1"/></g><g><path id="curve2"/></g>',
        '<g><path id="oldaxis"/><path id="curve1"/><rect id="unowned" width="20" height="20"/><path id="curve2"/></g>',
    ],
)
def test_native_split_placements_remain_explicit_and_do_not_move_unowned_nodes(body):
    source, model, binding, frame = setup_chart(body)
    result = compose_native_scene(source, binding, native(model, frame), model)
    assert result["composition"]["composed"] is True
    assert not result["composition"]["limitations"]
    assert result["manifest"]["svg_targets"]["axes_0"]["transform"] == [
        "axes_0",
        "axes_0.lines_0",
        "axes_0.lines_1",
    ]
    assert len(result["composition"]["placements"]) == 3
    if 'id="unowned"' in body:
        actual_id = result["composition"]["source_id_map"]["unowned"]
        root = ET.fromstring(result["svg"])
        axes = next(e for e in root.iter() if e.get("id") == "axes_0")
        assert not any(e.get("id") == actual_id for e in axes.iter())


def test_native_main_passes_paired_manifest_and_roles_over_json():
    _, model, _, frame = setup_chart()
    request = {
        "model": model,
        "frame": frame,
        "render_profile": NATIVE_PROFILE,
        "patches": [
            {"gid": "axes_0.lines_0", "prop": "color", "value": "green"},
        ],
    }
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys,runpy;sys.path.insert(0,sys.argv[1]);runpy.run_module('tavotto.engine.svgchart',run_name='__main__')",
            str(Path(__file__).resolve().parents[1] / "src"),
        ],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["ok"] and response["fragment_b64"]
    assert response["roles"]["series:s1"]["primary_gid"] == "axes_0.lines_0"
    assert response["coordinates"]["composed"] is False
    assert element(response, "axes_0.lines_0")["geometry"]


@pytest.fixture(scope="module")
def chromium():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        yield browser
        browser.close()


def test_native_actual_pixels_and_css_ownership_match_scene_context(chromium):
    from PIL import Image

    body = (
        '<defs><clipPath id="c"><rect width="40" height="80"/></clipPath></defs>'
        "<style>g {opacity:.5}</style>"
        '<g id="axes_0" transform="translate(20 10)" clip-path="url(#c)">'
        '<path id="oldaxis"/><path id="curve1"/><path id="curve2"/></g>'
        '<path id="axes_0.lines_0" d="M2 2L4 6L6 2" fill="none" stroke="green" stroke-linejoin="bevel"/>'
    )
    source, model, binding, frame = setup_chart(body)
    result = compose_native_scene(source, binding, native(model, frame), model)
    page = chromium.new_page(viewport={"width": 200, "height": 200}, device_scale_factor=3)
    try:
        page.set_content('<body style="margin:0">' + result["svg"].decode() + "</body>")
        page.evaluate(
            "async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))}"
        )
        image = Image.open(io.BytesIO(page.locator("svg").first.screenshot())).convert("RGB")
        # 1pt = 4 device pixels at 96 CSS dpi and deviceScaleFactor=3.
        reference = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" viewBox="0 0 100 100">'
            '<defs><clipPath id="reference-clip"><rect x="20" y="10" width="40" height="80"/></clipPath></defs>'
            '<g opacity=".5" clip-path="url(#reference-clip)"><path d="M10 50H90" '
            'stroke="blue" stroke-width="3" fill="none"/></g></svg>'
        )
        # Independent handwritten geometry and alpha, same physical viewport.
        # Nested transformed clipping can differ by one compositor color value.
        reference_page = chromium.new_page(device_scale_factor=3)
        try:
            reference_page.set_content('<body style="margin:0">' + reference + "</body>")
            reference_image = Image.open(
                io.BytesIO(reference_page.locator("svg").screenshot())
            ).convert("RGB")
            assert image.getpixel((40 * 4, 50 * 4)) == pytest.approx(
                reference_image.getpixel((40 * 4, 50 * 4)), abs=1
            )
        finally:
            reference_page.close()
        assert image.getpixel((75 * 4, 50 * 4)) == (255, 255, 255)
        original_id = result["composition"]["source_id_map"]["axes_0.lines_0"]
        assert (
            page.locator(f'[id="{original_id}"]').evaluate(
                "el=>getComputedStyle(el).strokeLinejoin"
            )
            == "bevel"
        )
        assert page.locator('[id="axes_0.lines_0"]').evaluate(
            'el=>el.closest("[id=axes_0]")!==null'
        )
    finally:
        page.close()


@pytest.mark.parametrize("kind", ["bar", "errorbar"])
def test_native_series_container_does_not_swallow_an_interleaved_other_artist(kind):
    source, model, binding, frame = setup_chart()
    model["series"][0]["kind"] = kind
    if kind == "errorbar":
        model["dataset"]["columns"]["err"] = [0.1, 0.2]
        model["series"][0]["yerr"] = "err"
        model["data_digest"] = chartmodel.digest_of(model["dataset"])
    scaffold = native(model, frame)
    result = compose_native_scene(source, binding, scaffold, model)
    if kind == "errorbar":
        # Native errorbar's data line has zorder 2.1 while its errors and the
        # second independent line have zorder 2.0: a single DOM group cannot
        # own only this series without changing actual paint order.
        assert result["composition"]["composed"] is True
        gid = scaffold["roles"]["series:s1"]["primary_gid"]
        targets = result["manifest"]["svg_targets"][gid]["transform"]
        assert len(targets) == 4
        assert set(targets) == set(scaffold["roles"]["series:s1"]["svg_ids"])
        assert "paint_order" not in element(result, gid)["geometry"]
        return
    assert result["composition"]["composed"] is True
    primary = scaffold["roles"]["series:s1"]["primary_gid"]
    root = ET.fromstring(result["svg"])
    container = next(e for e in root.iter() if e.get("id") == primary)
    actual = {e.get("id") for e in container.iter()}
    assert set(scaffold["roles"]["series:s1"]["svg_ids"]) <= actual
    assert scaffold["roles"]["series:s2"]["primary_gid"] not in actual


@pytest.mark.parametrize("preserve", ["xMidYMid meet", "xMidYMid slice", "none", "xMaxYMin meet"])
def test_native_production_graft_matches_original_physical_figure_pixels(preserve, chromium):
    from PIL import Image, ImageChops

    source, model, binding, frame = setup_chart(
        preserve=preserve, size=(200, 100), vb=(50, 20, 100, 100)
    )
    scaffold = native(
        model,
        frame,
        [
            {"gid": "axes_0.xlabel", "prop": "text", "value": "Native frame"},
            {"gid": "axes_0.xlabel", "prop": "fontsize", "value": 12},
            {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 4},
        ],
    )
    result = compose_native_scene(source, binding, scaffold, model)
    assert result["composition"]["composed"] is True
    page = chromium.new_page(device_scale_factor=2)
    try:
        images = []
        for data in (scaffold["fragment"], result["svg"]):
            page.set_content('<body style="margin:0">' + data.decode() + "</body>")
            page.evaluate(
                "async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))}"
            )
            images.append(
                Image.open(io.BytesIO(page.locator("svg").first.screenshot())).convert("RGB")
            )
        assert images[0].size == images[1].size
        delta = max(high for low, high in ImageChops.difference(*images).getextrema())
        assert delta <= 1
    finally:
        page.close()
