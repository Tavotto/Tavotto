"""Final native DOM membership preserves source slots, native draw order and units."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET

import pytest

from tavotto.engine import chartmodel
from tavotto.svgcompose import compose_native_scene
from test_chart_native import chromium as chromium, element, native, setup_chart

NS = "{http://www.w3.org/2000/svg}"
MIRROR = "data-tavotto-svg-targets"
VIEWPORT = "data-tavotto-native-viewport-pt"


def case(kind):
    body = {
        "split": '<g id="a" opacity=".5" transform="translate(5 0) scale(2)" clip-path="url(#clip)"><path id="oldaxis"/><path id="curve1"/></g><g id="b" opacity=".8" transform="translate(0 10)"><path id="curve2"/></g>',
        "interleaved": '<g id="a"><path id="oldaxis"/><path id="curve1"/><path id="keep" d="M0 50H100" stroke="green"/><path id="curve2"/></g>',
        "errorbar": None,
        "errorbar_split": '<g id="a"><path id="oldaxis"/><path id="curve1"/></g><path id="keep" d="M0 50H100" stroke="green"/><g id="b"><path id="curve2"/></g>',
        "bar_split": '<g id="a"><path id="oldaxis"/><path id="curve1"/></g><g id="b"><path id="curve2"/></g>',
        "colorbar": '<g id="a"><path id="oldaxis"/><path id="curve1"/></g><g id="b" transform="translate(20 5) scale(2)"><path id="oldcbar"/></g>',
        "colorbar_reverse": '<g><path id="oldcbar"/><path id="oldaxis"/><path id="curve1"/></g>',
    }[kind]
    if kind == "split":
        body = '<defs><clipPath id="clip"><rect width="30" height="100"/></clipPath></defs>' + body
    source, model, binding, frame = setup_chart(body)
    if kind.startswith("errorbar"):
        model["series"][0]["kind"] = "errorbar"
        model["series"][0]["yerr"] = "err"
        model["dataset"]["columns"]["err"] = [0.1, 0.2]
    elif kind == "bar_split":
        model["series"][0]["kind"] = "bar"
    elif kind.startswith("colorbar"):
        model["series"] = [
            {
                "id": "s1",
                "kind": "image",
                "axes": "ax1",
                "node": "curve1",
                "grid": "g",
                "cbar_node": "oldcbar",
            }
        ]
        model["dataset"]["grids"] = {"g": {"values": [[0, 1], [2, 3]]}}
        binding["entries"] = [entry for entry in binding["entries"] if entry["node"] != "curve2"]
        binding["entries"].append({"object": "axes:ax1", "node": "oldcbar"})
    model["data_digest"] = chartmodel.digest_of(model["dataset"])
    return source, model, binding, frame


def assert_targets(result):
    root = ET.fromstring(result["svg"])
    targets = result["manifest"]["svg_targets"]
    assert json.loads(root.get(MIRROR)) == targets
    assert set(targets) == {el["gid"] for el in result["manifest"]["elements"]}
    nodes = {node.get("id"): node for node in root.iter() if node.get("id")}
    assert len(nodes) == sum(node.get("id") is not None for node in root.iter())
    parents = {child: parent for parent in root.iter() for child in parent}
    order = {node.get("id"): i for i, node in enumerate(root.iter())}
    for item in targets.values():
        assert set(item) == {"transform", "style"}
        for ids in item.values():
            assert ids == sorted(set(ids), key=order.get)
            for value in ids:
                assert value in nodes
                node = nodes[value]
                ancestor = parents.get(node)
                found_pt = False
                while ancestor is not None:
                    assert ancestor.get("id") not in ids, (
                        "one transform must not hit nested targets twice"
                    )
                    found_pt |= ancestor.get(VIEWPORT) == "100.0 100.0"
                    ancestor = parents.get(ancestor)
                assert found_pt, (
                    "a target must be inside the pt wrapper, not the source-unit wrapper"
                )
    return root, nodes


@pytest.mark.parametrize(
    "kind",
    [
        "split",
        "interleaved",
        "errorbar",
        "errorbar_split",
        "bar_split",
        "colorbar",
        "colorbar_reverse",
    ],
)
def test_final_targets_are_real_ordered_antichains_and_mirror_the_manifest(kind):
    source, model, binding, frame = case(kind)
    result = compose_native_scene(source, binding, native(model, frame), model)
    assert result["composition"]["composed"] is (kind != "errorbar_split")
    if kind == "errorbar_split":
        assert result["composition"]["limitations"] == ["native_member_slots_underdetermined"]
    assert_targets(result)
    assert result["composition"]["svg_elements"] == sum(
        1 for _ in ET.fromstring(result["svg"]).iter()
    )


def test_each_split_role_uses_its_own_actual_ancestor_clip_and_opacity():
    source, model, binding, frame = case("split")
    model["series"][0]["kind"] = "bar"
    result = compose_native_scene(source, binding, native(model, frame), model)
    bar = next(el for el in result["manifest"]["elements"] if el["role"] == "bar_series")
    assert bar["geometry"]["clip"] == pytest.approx([0.1, 0.1, 0.55, 0.8])
    assert bar["geometry"]["fill_alpha"] == 0.5
    assert element(result, "axes_0.lines_0")["geometry"]["clip"] == pytest.approx(
        [0.1, 0.1, 0.8, 0.8]
    )


def test_source_ids_and_forged_target_mirrors_do_not_claim_native_targets():
    body = f'<g {MIRROR}=\'{{"axes_0":{{"transform":["keep"],"style":["keep"]}}}}\'><path id="oldaxis"/><path id="curve1"/><path id="curve2"/></g><path id="axes_0"/><path id="keep"/>'
    source, model, binding, frame = setup_chart(body)
    result = compose_native_scene(source, binding, native(model, frame), model)
    root, nodes = assert_targets(result)
    assert sum(MIRROR in node.attrib for node in root.iter()) == 1
    original = result["composition"]["source_id_map"]["axes_0"]
    assert original in nodes and original != "axes_0"
    assert result["manifest"]["svg_targets"]["axes_0"]["transform"] == ["axes_0"]


@pytest.mark.parametrize("kind", ["interleaved", "errorbar_split"])
def test_final_source_paint_slots_and_native_member_order_are_both_preserved(kind):
    source, model, binding, frame = case(kind)
    scaffold = native(model, frame)
    result = compose_native_scene(source, binding, scaffold, model)
    root, nodes = assert_targets(result)
    order = [node.get("id") for node in root.iter() if node.get("id")]
    ids_a = result["manifest"]["svg_targets"][scaffold["roles"]["series:s1"]["primary_gid"]][
        "transform"
    ]
    ids_b = result["manifest"]["svg_targets"][scaffold["roles"]["series:s2"]["primary_gid"]][
        "transform"
    ]
    keep = result["composition"]["source_id_map"]["keep"]
    assert max(map(order.index, ids_a)) < order.index(keep) < min(map(order.index, ids_b))
    fragment_order = [
        node.get("id")
        for node in ET.fromstring(scaffold["fragment"]).iter()
        if node.get("id") in ids_a
    ]
    assert ids_a == fragment_order
    assert nodes[keep].get("d") == "M0 50H100"
    assert nodes[keep].get("stroke") == "green"


@pytest.mark.parametrize(
    "kind,gid",
    [("errorbar_split", "axes_0.errorbar_0"), ("split", "axes_0"), ("colorbar", "axes_1.colorbar")],
)
def test_real_chrome_multitarget_preview_moves_all_owned_targets_once_and_no_decoration(
    kind, gid, chromium
):
    source, model, binding, frame = case(kind)
    result = compose_native_scene(source, binding, native(model, frame), model)
    assert_targets(result)
    page = chromium.new_page()
    try:
        page.set_content('<body style="margin:0">' + result["svg"].decode() + "</body>")
        before = page.content()
        facts = page.evaluate(
            """({gid, keep}) => {
          const svg = document.querySelector('svg');
          const ids = JSON.parse(svg.getAttribute('data-tavotto-svg-targets'))[gid].transform;
          const nodes = ids.map(id => svg.querySelector(`[id="${id}"]`));
          const original = nodes.map(n => n.getAttribute('transform'));
          const xs = nodes.map(n => n.getBoundingClientRect().x);
          const trackedIds = gid === 'axes_0' ? ['axes_0.lines_0','axes_0.lines_1'] : [];
          const tracked = trackedIds.map(id=>svg.querySelector(`[id="${id}"]`));
          const trackedXs = tracked.map(n=>n.getBoundingClientRect().x);
          const decoration = keep && svg.querySelector(`[id="${keep}"]`);
          const dx0 = decoration?.getBoundingClientRect().x;
          nodes.forEach((n,i) => n.setAttribute('transform', 'translate(10,0)' + (original[i] ? ' ' + original[i] : '')));
          const dx = nodes.map((n,i) => n.getBoundingClientRect().x-xs[i]);
          const keepDx = decoration ? decoration.getBoundingClientRect().x-dx0 : 0;
          const trackedDx = tracked.map((n,i)=>n.getBoundingClientRect().x-trackedXs[i]);
          nodes.forEach((n,i) => original[i] === null ? n.removeAttribute('transform') : n.setAttribute('transform',original[i]));
          return {dx,keepDx,trackedDx,count:nodes.length};
        }""",
            {"gid": gid, "keep": result["composition"]["source_id_map"].get("keep")},
        )
        assert facts["count"] > 0
        assert facts["dx"] == pytest.approx([40 / 3] * facts["count"], abs=0.02)
        assert facts["keepDx"] == 0
        assert facts["trackedDx"] == pytest.approx([40 / 3] * len(facts["trackedDx"]), abs=0.02)
        assert page.content() == before
        if kind == "colorbar":
            assert page.locator('[id="axes_1"] text').count() > 0
            assert page.locator('[id="axes_1"] image,[id="axes_1"] path').count() > 0
    finally:
        page.close()


def test_colorbar_proxy_hidden_and_full_restore_preserve_its_actual_axis_target():
    source, model, binding, frame = case("colorbar")
    first = compose_native_scene(source, binding, native(model, frame), model)
    hidden = compose_native_scene(
        source,
        binding,
        native(model, frame, [{"gid": "axes_1.colorbar", "prop": "visible", "value": False}]),
        model,
    )
    assert hidden["composition"]["composed"] is True
    root, nodes = assert_targets(hidden)
    assert hidden["manifest"]["svg_targets"]["axes_1.colorbar"]["transform"] == ["axes_1"]
    assert nodes["axes_1"].get("display") == "none"
    assert not list(nodes["axes_1"])
    assert element(hidden, "axes_1.colorbar")["geometry"]["paths"] == []
    restored = compose_native_scene(source, binding, native(model, frame), model)
    assert restored["svg"] == first["svg"]
    assert restored["manifest"] == first["manifest"]


def test_split_context_pixels_match_independent_physical_clip_and_alpha(chromium):
    from PIL import Image

    source, model, binding, frame = case("split")
    result = compose_native_scene(source, binding, native(model, frame), model)
    reference = '<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" viewBox="0 0 100 100"><defs><clipPath id="c"><rect x="5" width="60" height="100"/></clipPath></defs><g opacity=".5" clip-path="url(#c)"><path d="M10 50H90" stroke="red" stroke-width="3"/></g><g opacity=".8"><path d="M10 50H90" stroke="blue" stroke-width="3"/></g></svg>'
    shots = []
    for svg in (result["svg"].decode(), reference):
        page = chromium.new_page(device_scale_factor=3)
        try:
            page.set_content('<body style="margin:0">' + svg + "</body>")
            shots.append(Image.open(io.BytesIO(page.locator("svg").screenshot())).convert("RGB"))
        finally:
            page.close()
    for x in (40, 80):
        assert shots[0].getpixel((x * 4, 50 * 4)) == pytest.approx(
            shots[1].getpixel((x * 4, 50 * 4)), abs=1
        )


def test_reversed_source_slots_are_kept_when_native_roles_have_contiguous_draw_intervals(chromium):
    from PIL import Image

    source, model, binding, frame = setup_chart(
        '<g><path id="oldaxis"/><path id="curve2"/><path id="curve1"/></g>'
    )
    result = compose_native_scene(source, binding, native(model, frame), model)
    assert result["composition"]["composed"] is True
    assert result["composition"]["placement_mode"] == "source-slots"
    assert result["composition"]["native_dom_shared"] is False
    root, _ = assert_targets(result)
    order = [node.get("id") for node in root.iter()]
    assert order.index("axes_0.lines_1") < order.index("axes_0.lines_0")
    page = chromium.new_page(device_scale_factor=3)
    try:
        page.set_content('<body style="margin:0">' + result["svg"].decode() + "</body>")
        image = Image.open(io.BytesIO(page.locator("svg").screenshot())).convert("RGB")
        # Handwritten source stacking: curve2 blue is before curve1 red.
        assert image.getpixel((50 * 4, 50 * 4)) == (255, 0, 0)
    finally:
        page.close()


def test_native_interleaved_members_and_unbound_decor_have_no_invented_member_slots():
    source, model, binding, frame = case("errorbar_split")
    result = compose_native_scene(source, binding, native(model, frame), model)
    assert result["composition"]["composed"] is False
    assert "native_member_slots_underdetermined" in result["composition"]["limitations"]
    assert result["composition"]["native_series_draw_order"] == [
        "series:s1",
        "series:s2",
        "series:s1",
    ]
    # The original compound source has one slot, not four member slots. The
    # delivered fallback keeps that source slot and decor, without claiming it
    # reproduces the unknown old cross-member layering.
    root, _ = assert_targets(result)
    order = [node.get("id") for node in root.iter()]
    keep = result["composition"]["source_id_map"]["keep"]
    members = result["manifest"]["svg_targets"]["axes_0.errorbar_0"]["transform"]
    assert max(map(order.index, members)) < order.index(keep) < order.index("axes_0.lines_3")


def test_applied_zorder_conflicting_with_source_slots_is_explicitly_unqualified():
    source, model, binding, frame = setup_chart()
    scaffold = native(model, frame, [{"gid": "axes_0.lines_0", "prop": "zorder", "value": 5}])
    assert scaffold["unrestored"] == 0 and not scaffold["warnings"]
    assert scaffold["paint_order_overrides"] == ["axes_0.lines_0"]
    result = compose_native_scene(source, binding, scaffold, model)
    assert result["composition"]["composed"] is False
    assert result["composition"]["limitations"] == ["native_zorder_source_slots_conflict"]
    assert result["composition"]["native_series_draw_order"] == ["series:s2", "series:s1"]
    assert_targets(result)


def test_auxiliary_colorbar_keeps_its_source_slot_when_native_role_order_differs():
    source, model, binding, frame = case("colorbar_reverse")
    result = compose_native_scene(source, binding, native(model, frame), model)
    assert result["composition"]["composed"] is True
    assert result["composition"]["native_dom_shared"] is False
    assert result["composition"]["native_role_draw_order"] == ["series:s1", "axes:colorbar_s1"]
    root, nodes = assert_targets(result)
    order = [node.get("id") for node in root.iter()]
    assert order.index("axes_1") < order.index("axes_0") < order.index("axes_0.images_0")
    assert any(node.tag == NS + "image" for node in nodes["axes_0.images_0"].iter())
