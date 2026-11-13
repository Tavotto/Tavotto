"""Computed frame（native-manifest/2）闭式合同、物理政策与真实 /1×/2 配对。

移植自原型 `E:/0 - Project/svg-review-20261106/n5-tick-clipping-review/prototype/
tests/test_complete_frame.py`（PLAN.md §2 第 1 步 / §7；产品落地名
`tests/test_chart_complete_frame.py`）。相对原型的调整点：

- 夹具：删掉 importlib 按绝对路径加载 `tests/test_chart_native.py` 的原型
  harness；改为同目录直接 import `setup_chart`（产品既有跨测试模块引用模式，
  见 `tests/test_chart_native_export.py`）。临时目录全部走 pytest 内建
  `tmp_path`，不再有 OUT basetemp / 绝对路径。
- 渲染入口：原型经 `chartprocess.run_request` 真子进程并断言 overlay 模块
  出处（`_prototype_modules`）；产品里 overlay 就是产品模块，改为进程内
  `svgchart.scaffold_fragment(...)`（与 `tests/test_chart_native.py` 同法），
  模块出处断言随之删除。
- 删除 `owned_overlay` 夹具与末尾的 numpy/matplotlib `sys.modules` 隔离断言、
  `chartprocess.active_count() == 0`：进程内渲染必然 import matplotlib，且
  完整套件同进程跑时该断言随测试顺序抖动；worker 隔离由
  `tests/test_chart_process.py` 与 `tests/test_import_architecture.py` 看护。
- 字体改为模块级 fixture（仿 `tests/test_native_scene_eps.py` 的 `faces`）：
  缺 uharfbuzz/fontTools 或缺已批准字体时显式 skip，不冒充像素通过。
- PDF 位图读取（pypdfium2/PIL）保持原型判据，改为测试内 importorskip。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from tavotto import chartbundle
from tavotto.engine import chartframe, chartprofile, svgchart
from tavotto.rendercore.pdfwriter import write_pdf
from tavotto.rendercore.svgir import svg_to_ir
from tavotto.svgcompose import compose_native_scene
from tavotto.svgscene import scene_inventory
from test_chart_native import setup_chart

NS = "{http://www.w3.org/2000/svg}"


@pytest.fixture(scope="module")
def provider():
    """同一份已批准字体铸出的 shaper；缺依赖/缺字体时明确 skip。"""
    from tavotto.rendercore.fonts import FontRegistry
    from tavotto.rendercore.hbshaper import HbFaceProvider

    pytest.importorskip("uharfbuzz")
    pytest.importorskip("fontTools")
    registry = FontRegistry.discover()
    if registry.missing:
        pytest.skip(f"approved fonts missing (not-run): {registry.missing}")
    return HbFaceProvider(registry)


def render(model, frame, patches=(), profile=chartprofile.COMPLETE_PROFILE):
    """进程内同一渲染入口：/1 与 /2 只差 render_profile，其余完全同参。"""
    return svgchart.scaffold_fragment(model, frame, render_profile=profile, patches=patches)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        True,
        {"version": True},
        {
            "version": 1,
            "source": "savefig",
            "source_viewport_pt": [100, 100],
            "rect_pt": [-20, 0, 120, 110],
            "edit_basis": "source-viewport-pt",
        },
    ],
)
def test_frame_closed_shape_rejects(bad):
    with pytest.raises(ValueError):
        chartframe.validate_frame(bad)


def test_frame_policy_is_physical_not_source_viewbox():
    f = chartframe.from_ink([200, 100], [-0.1, 0, 1, 1.1])
    assert f["rect_pt"] == [-21, -1, 222, 113]
    assert chartframe.source_fraction_rect(f, [0, 0, 1, 1]) == pytest.approx(
        [21 / 222, 1 / 113, 200 / 222, 100 / 113]
    )
    assert chartprofile.validate_profile("native-manifest/2") == "native-manifest/2"
    assert chartprofile.identity("base", chartprofile.NATIVE_PROFILE, []) != chartprofile.identity(
        "base", chartprofile.COMPLETE_PROFILE, []
    )


@pytest.mark.parametrize(
    "size,vb,preserve",
    [
        ((100, 100), (0, 0, 100, 100), "none"),
        ((200, 100), (50, 20, 100, 100), "none"),
        ((200, 100), (50, 20, 100, 100), "xMidYMid meet"),
        ((100, 100), None, "none"),
    ],
)
def test_real_complete_pair_source_points_units_and_C(provider, tmp_path, size, vb, preserve):
    source, model, binding, frame = setup_chart(
        size=size, vb=vb or (0, 0, 100, 100), preserve=preserve
    )
    if vb is None:
        raw = ET.fromstring(source)
        raw.attrib.pop("viewBox")
        source = ET.tostring(raw)
        scene = scene_inventory(source)
        model["scene_digest"] = scene["scene_digest"]
        binding["scene_digest"] = scene["scene_digest"]
        frame["view_box"] = [0, 0, size[0] * 96 / 72, size[1] * 96 / 72]
    model["axes"][0].update(xlabel="Sample time (seconds)", ylabel="Response (a.u.)")
    patches = [{"gid": "axes_0.lines_0", "prop": "linewidth", "value": 5}]
    old = render(model, frame, patches, chartprofile.NATIVE_PROFILE)
    new = render(model, frame, patches)
    assert "render_frame" not in old and "render_frame" not in old["manifest"]
    assert "frame" not in new["manifest"]
    F = chartframe.validate_frame(new["render_frame"])
    L, T, W, H = F["rect_pt"]
    assert F == new["manifest"]["render_frame"]
    a = next(e for e in old["manifest"]["elements"] if e["gid"] == "axes_0.lines_0")["geometry"]
    b = next(e for e in new["manifest"]["elements"] if e["gid"] == "axes_0.lines_0")["geometry"]
    assert a["stroke_pt"] == b["stroke_pt"] == 5
    for p, q in zip(a["paths"][0]["points"], b["paths"][0]["points"]):
        assert [L + q[0] * W, T + q[1] * H] == pytest.approx(
            [p[0] * size[0], p[1] * size[1]], abs=0.003
        )
    composed = compose_native_scene(source, binding, new, model)
    assert composed["composition"]["composed"], composed["composition"]
    root = ET.fromstring(composed["svg"])
    assert list(map(float, root.get("viewBox").split())) == F["rect_pt"]
    assert "data-tavotto-svg-targets" in root.attrib
    assert all(
        n.get(chartframe.MARKER) is not None
        for n in root.iter()
        if "data-tavotto-native-viewport-pt" in n.attrib
    )
    provider_page, notes = svg_to_ir(composed["svg"], W, H, provider)
    assert provider_page.width_pt == W and provider_page.height_pt == H
    from tavotto.rendercore import ir

    red = []

    def paths(nodes, matrix=ir.IDENTITY):
        for node in nodes:
            if isinstance(node, ir.Group):
                paths(node.children, ir.compose(node.transform, matrix))
            elif isinstance(node, ir.Path) and node.stroke and node.stroke.paint.rgb == (1, 0, 0):
                red.extend(ir.apply(matrix, *s[1:]) for s in node.segments if s[0] in {"M", "L"})

    paths(provider_page.children)
    assert red
    for actual, expected in zip(red, a["paths"][0]["points"]):
        assert [actual[0] + L, H - actual[1] + T] == pytest.approx(
            [expected[0] * size[0], expected[1] * size[1]], abs=0.003
        )
    write_pdf(provider_page, tmp_path / "result.pdf", provider)
    bundle = chartbundle.embed_chart(
        composed["svg"],
        source,
        {"model": model, "binding": binding},
        render_profile=chartprofile.COMPLETE_PROFILE,
        overrides=patches,
    )
    restored = chartbundle.extract_chart(bundle)
    assert restored.render_profile == chartprofile.COMPLETE_PROFILE
    assert restored.source_svg == source and restored.overrides == patches
    chartbundle.verify_replay(restored, composed["svg"])


def test_original_cascade_own_is_not_inherited_computed_default():
    from tavotto.chartframecompose import freeze_source_presentation
    from tavotto.rendercore import svgir

    raw = b'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt"><defs><rect id="a" width="10" height="10"/></defs><use href="#a" fill="red"/></svg>'
    root = ET.fromstring(raw)
    resolver = svgir._Translator(root, 100, 100, None, None)
    target = next(n for n in root.iter() if n.get("id") == "a")
    assert resolver.cascade_declarations(target) == {}
    freeze_source_presentation(root, resolver)
    assert "fill" not in target.get("style")


def test_real_source_css_percentage_opacity_and_source_clip(provider, tmp_path):
    body = (
        '<style>#original > rect {fill:cyan}</style><defs><rect id="unit" width="3" height="3"/></defs>'
        '<g id="original" opacity=".6" transform="rotate(11 50 50)"><rect id="pct" x="80%" y="20%" width="10%" height="4%"/>'
        '<rect id="outside" x="-18" y="42" width="6" height="6"/>'
        '<path id="oldaxis" d="M10 10V90H90"/><path id="curve1" d="M10 70H90"/><path id="curve2" d="M10 80H90"/>'
        '<use href="#unit" fill="red" x="35" y="40"/><use href="#unit" fill="blue" x="45" y="40"/></g>'
    )
    source, model, binding, frame = setup_chart(body)
    new = render(model, frame)
    combined = compose_native_scene(source, binding, new, model)
    assert combined["composition"]["composed"], combined["composition"]
    root = ET.fromstring(combined["svg"])
    pct = next(n for n in root.iter() if n.get("id", "").endswith("_pct"))
    assert "fill:cyan" in pct.get("style")
    assert pct.get("x") == "80%"
    source_group = next(n for n in root.iter() if n.get("id", "").endswith("_original"))
    assert "opacity:.6" in source_group.get("style")
    unit = next(n for n in root.iter() if n.get("id", "").endswith("_unit"))
    assert "fill" not in unit.get("style")
    assert not [n for n in root.iter(NS + "style") if n.text and "#original" in n.text]

    # 同一次真实科学 draw，两个组合消费者：基线持有原 source viewport；
    # 更大的 IR 载体揭示它完整的原生标签，而不重执行/重画科学输入。
    pdfium = pytest.importorskip("pypdfium2")
    pytest.importorskip("PIL")
    from PIL import ImageChops

    from tavotto.rendercore import ir

    unframed = {k: v for k, v in new.items() if k != "render_frame"}
    baseline = compose_native_scene(source, binding, unframed, model)
    p0, _ = svg_to_ir(baseline["svg"], 100, 100, provider)
    left, top, w, h = new["render_frame"]["rect_pt"]
    p1, _ = svg_to_ir(combined["svg"], w, h, provider)
    carrier = ir.Page(
        w,
        h,
        (ir.Group(p0.children, transform=ir.translate(-left, top + h - 100)),),
        p0.resources,
        background=None,
    )
    images = []
    for name, page in [("truth", carrier), ("candidate", p1)]:
        path = tmp_path / (name + ".pdf")
        write_pdf(page, path, provider)
        document = pdfium.PdfDocument(str(path))
        page_handle = document[0]
        bitmap = page_handle.render(scale=4)
        images.append(bitmap.to_pil().convert("RGB"))
        bitmap.close()
        page_handle.close()
        document.close()
    difference = ImageChops.difference(*images).getbbox()
    assert difference is not None and difference[2] <= -left * 4, difference
    # viewport 内的原 source/原生像素逐位不变，包括直接子 CSS、百分比
    # 尺寸、opacity 与两个 use 的颜色。
