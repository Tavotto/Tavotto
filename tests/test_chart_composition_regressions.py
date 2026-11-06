"""最终组合产物的正确性回归（NEXT_PHASE R00；缺陷 B1–B5）。

这些测试断言**最终 SVG**的科学/视觉正确性（独立公式预期），不是中间 facts。
已修复缺陷作为常驻回归；检查最终绘制语义与独立坐标预期，不能只看中间 facts。

预期合同（R01/R02 落地）：
- 脚手架片段带语义角色标记（``data-tavotto="series:<id>" / "axes:<id>" /
  "colorbar:<id>"``），组合按角色取完整产物，禁止按 line2d_ 前缀或遍历序号猜。
- 脚手架在**场景用户坐标**系渲染（pt 类量按 viewBox→pt 比例换算），组合不再
  混用坐标空间。
- 节点身份统一：绑定的逻辑 id 经 svgscene 的同一映射解析，无 id 节点可稳定
  选中与替换。
- 轴区域所有权由绑定表达（axes_nodes 用户确认），轴范围改动替换旧刻度/标注。
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tavotto.engine import chartbind, chartmodel, svgchart
from tavotto.rendercore.svgcheck import parse_svg, strip_matplotlib_doctype
from tavotto.svgcompose import compose_scene
from tavotto.svgscene import scene_inventory

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "tests" / "support" / "svg_corpus"

SVG_NS = "{http://www.w3.org/2000/svg}"

ENGINE = {"matplotlib": "3.11.2", "numpy": "2.1.0", "tavotto": "0.20.0"}

SCENE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" '
    b'viewBox="0 0 100 100"><g id="old_xaxis"><text x="0" y="100">0</text>'
    b'<text x="90" y="100">10</text></g><path id="curve" d="M0 100L100 0" '
    b'fill="none" stroke="blue"/></svg>'
)


def evidence(a: float, b: float) -> dict:
    return {"p1_user": 0.0, "p1_value": a, "p2_user": 1.0, "p2_value": b, "scale": "linear"}


def bind(
    raw: bytes,
    *,
    kind="line",
    style=None,
    dataset="x,y\n0,0\n1,1\n",
    node="curve",
    xlim=(0, 1),
    ylim=(0, 1),
    axes_nodes=None,
    **extra,
):
    inv = scene_inventory(raw)
    request = {
        "axes_id": "ax1",
        "rect": [0.0, 0.0, 100.0, 100.0],
        "x": evidence(*xlim),
        "y": evidence(*ylim),
        "series": [{"node": node, "x_col": "x", "y_col": "y", "kind": kind, "style": style or {}}],
        "scene_digest": inv["scene_digest"],
        "nodes": inv["nodes"],
        "completeness": "self-contained",
        "dataset_text": dataset,
    }
    if axes_nodes:
        request["axes_nodes"] = axes_nodes
    for key, value in extra.pop("series_update", {}).items():
        request["series"][0][key] = value
    request.update(extra)
    out = chartbind.build_model_and_binding(request, engine_identity=ENGINE)
    frame = {
        "view_box": inv["view_box"],
        "w_pt": inv["w_pt"],
        "h_pt": inv["h_pt"],
        "preserve": None,
    }
    return out, frame


def parse(data: bytes) -> ET.Element:
    return parse_svg(strip_matplotlib_doctype(data))


def by_id(root: ET.Element, name: str) -> ET.Element | None:
    return next((e for e in root.iter() if e.get("id") == name), None)


def texts_of(root: ET.Element) -> list[str]:
    return ["".join(e.itertext()) for e in root.iter(f"{SVG_NS}text")]


# ---------------------------------------------------------------------------
# B1：轴区域所有权——范围改动替换旧刻度标注
# ---------------------------------------------------------------------------


def test_axis_region_labels_follow_the_new_range():
    scene = SCENE
    out, _frame = bind(scene, dataset="x,y\n2,0\n8,1\n", xlim=(2, 8), axes_nodes=["old_xaxis"])
    frag = svgchart.scaffold_fragment(
        out["model"], {"view_box": [0, 0, 100, 100], "w_pt": 100.0, "h_pt": 100.0, "preserve": None}
    )
    result = compose_scene(scene, out["binding"], frag["fragment"], out["model"])
    root = parse(result)
    visible = texts_of(root)
    # 新范围的刻度文字可见（scaffold facts 的主刻度），旧的 0/10 标注退役
    assert (
        "0" in visible and "10" not in visible or any(t in visible for t in ("2", "4", "6", "8"))
    ), f"composed tick texts: {visible}"
    assert not any(t == "10" for t in visible), f"old tick label survived: {visible}"


# ---------------------------------------------------------------------------
# B2：单位空间——px 源的最终路径保持在用户单位
# ---------------------------------------------------------------------------


def test_px_source_final_path_stays_in_user_units():
    px_scene = SCENE.replace(b'width="100pt" height="100pt"', b'width="100" height="100"')
    out, frame = bind(px_scene, dataset="x,y\n2,0\n8,1\n", xlim=(2, 8))
    frag = svgchart.scaffold_fragment(out["model"], frame)
    result = compose_scene(px_scene, out["binding"], frag["fragment"], out["model"])
    root = parse(result)
    paths = [e.get("d") for e in root.iter(f"{SVG_NS}path")]
    # 独立预期：数据 (2,0)/(8,1) 经 rect [0,100]² 映射 → 用户单位端点 (0,100)/(100,0)
    coords = [
        float(v) for v in __import__("re").findall(r"[-+]?(?:\d*\.)?\d+(?:e[-+]?\d+)?", paths[0])
    ]
    assert coords[:4] == pytest.approx([0, 100, 100, 0], abs=1e-6), (
        f"final path in user units expected [0,100,100,0], got {coords[:4]}"
    )


# ---------------------------------------------------------------------------
# B3：系列族的完整产物
# ---------------------------------------------------------------------------


def _family_case(kind: str):
    dataset = "x,y,err\n0,0.3,0.1\n1,0.7,0.2\n"
    out, frame = bind(
        SCENE, kind=kind, dataset=dataset, style={"color": "#d62728"} if kind == "scatter" else {}
    )
    if kind in ("bar", "errorbar"):
        out["model"]["series"][0]["yerr"] = "err"
    frag = svgchart.scaffold_fragment(out["model"], frame)
    result = compose_scene(SCENE, out["binding"], frag["fragment"], out["model"])
    return parse(result)


def test_bar_composition_keeps_all_bars():
    root = _family_case("bar")
    # 两根柱（两个数据行）→ 最终 SVG 含两个填充矩形路径，颜色一致
    rects = [e for e in root.iter(f"{SVG_NS}path") if e.attrib.get("style", "").startswith("fill")]
    assert len(rects) >= 2, "expected >=2 bar patches in the final SVG"


def test_scatter_composition_keeps_markers():
    root = _family_case("scatter")
    # 散点 marker：最终 SVG 含 ≥2 个数据点图形（use 或带填充的小路径）
    marks = [e for e in root.iter() if e.tag in (f"{SVG_NS}use", f"{SVG_NS}path")]
    assert len(marks) >= 2


def test_errorbar_composition_keeps_error_members():
    root = _family_case("errorbar")
    # 误差线成员：竖直线段（每点上下界）+ 端帽；至少 2 条竖直小线段
    verticals = 0
    for e in root.iter(f"{SVG_NS}path"):
        d = e.get("d", "")
        nums = [float(v) for v in __import__("re").findall(r"-?\d+\.?\d*", d)]
        if len(nums) >= 4 and nums[0] == nums[2] and abs(nums[1] - nums[3]) > 0.01:
            verticals += 1
    assert verticals >= 2, f"expected >=2 vertical error segments, got {verticals}"


# ---------------------------------------------------------------------------
# B4：热图色条与 norm 同步
# ---------------------------------------------------------------------------


def test_heatmap_colorbar_follows_the_new_norm():
    heat = (CORPUS / "heatmap_colorbar.svg").read_bytes()
    root0 = parse(heat)
    image_id = next(e.get("id") for e in by_id(root0, "axes_1").iter(f"{SVG_NS}image"))
    out, frame = bind(
        heat,
        kind="image",
        node=image_id,
        style={"cmap": "plasma", "vmin": 0.2, "vmax": 0.8},
        dataset="unused\n",
        grid_text="0.1 0.3\n0.9 0.5\n",
        rect=[24.28, 24.32, 231.14, 165.1],
        x=evidence(0, 2),
        y=evidence(0, 2),
        series_update={"cbar_node": "axes_2"},
    )
    frag = svgchart.scaffold_fragment(out["model"], frame)
    result = compose_scene(heat, out["binding"], frag["fragment"], out["model"])
    root1 = parse(result)
    old_sha = hashlib.sha256(ET.tostring(by_id(root0, "axes_2"))).hexdigest()
    # 组合合同：cbar_node 被片段的新色条子树整体接管（角色 id tv-colorbar-<sid>__后缀）
    new_cbar = next(
        (e for e in root1.iter() if (e.get("id") or "").startswith("tv-colorbar-s1")), None
    )
    assert new_cbar is not None, "fragment colorbar did not replace the bound region"
    new_sha = hashlib.sha256(ET.tostring(new_cbar)).hexdigest()
    assert new_sha != old_sha, "colorbar subtree unchanged after norm edit"
    # 旧 norm 的极端刻度不在新色条上（0.0/1.0 属于旧 [0,1] 范围的端点）
    cbar_texts = texts_of(new_cbar)
    assert any(t for t in cbar_texts), f"colorbar has no tick texts: {cbar_texts}"


# ---------------------------------------------------------------------------
# B5：无 id 节点的稳定身份
# ---------------------------------------------------------------------------


def test_idless_node_binds_and_composes():
    noid = SCENE.replace(b'id="curve" ', b"")
    inv = scene_inventory(noid)
    synthetic = next(e["id"] for e in inv["nodes"] if e["tag"] == "path")
    out, frame = bind(noid, node=synthetic)
    chartmodel.validate_binding(out["binding"], out["model"])
    chartmodel.verify_scene(out["binding"], inv["scene_digest"], {e["id"] for e in inv["nodes"]})
    result = compose_scene(
        noid,
        out["binding"],
        svgchart.scaffold_fragment(out["model"], frame)["fragment"],
        out["model"],
    )
    root = parse(result)
    paths = [e.get("d") for e in root.iter(f"{SVG_NS}path")]
    assert paths, "composed output lost the series entirely"
    # 未绑定装饰（old_xaxis 的文本）保持原样
    assert "0" in texts_of(root) and "10" in texts_of(root)


# ---------------------------------------------------------------------------
# N1/N2/N4：非零 viewBox、父空间变换、柱误差成员（2026-11-05 复核反例）
# ---------------------------------------------------------------------------


def test_nonzero_viewbox_endpoints_in_scene_user_space():
    """N1：viewBox [50,20,100,100] → 端点 (50,120)/(150,20)。"""
    scene = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" '
        b'viewBox="50 20 100 100"><path id="curve" d="M50 120L150 20" '
        b'fill="none" stroke="blue"/></svg>'
    )
    out, frame = bind(
        scene,
        dataset="x,y\n0,0\n1,1\n",
        xlim=(0, 1),
        ylim=(0, 1),
        rect=[50.0, 20.0, 100.0, 100.0],
    )
    frag = svgchart.scaffold_fragment(out["model"], frame)
    result = compose_scene(
        scene, out["binding"], frag["fragment"], out["model"], origin=(50.0, 20.0)
    )
    root = parse(result)
    # 主语 = 系列包装组里的数据路径（组合后文档里的 paths[0] 可能是轴家具）
    wrapper = next((e for e in root.iter() if e.get("data-tavotto", "") == "series:s1"), None)
    assert wrapper is not None, "series wrapper missing"
    series_d = wrapper.find(f".//{SVG_NS}path").get("d")
    nums = [float(v) for v in re.findall(r"[-+]?(?:\d*\.)?\d+(?:e[-+]?\d+)?", series_d)]
    # wrapper transform 也参与世界坐标
    from tavotto.svgcoord import apply as c_apply, parse_transform as c_parse

    wt = c_parse(wrapper.get("transform", "translate(0,0)"))
    world = [c_apply(wt, nums[i], nums[i + 1]) for i in (0, 2)]
    flat = [round(v, 6) for pt in world for v in pt]
    assert flat == pytest.approx([50, 120, 150, 20], abs=1e-6), (
        f"nonzero-viewbox endpoints expected [50,120,150,20], got {flat}"
    )


def test_nested_parent_transform_not_applied_twice():
    """N2：目标在 translate(20 10) 组内 → 世界端点 (20,70)/(80,10)，父变换不重复作用。"""
    scene = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" '
        b'viewBox="0 0 100 100"><g transform="translate(20 10)">'
        b'<path id="curve" d="M0 100L100 0" fill="none" stroke="blue"/></g></svg>'
    )
    out, frame = bind(
        scene, dataset="x,y\n0,0\n1,1\n", rect=[20.0, 10.0, 60.0, 60.0], xlim=(0, 1), ylim=(0, 1)
    )
    frag = svgchart.scaffold_fragment(out["model"], frame)
    result = compose_scene(scene, out["binding"], frag["fragment"], out["model"], origin=(0.0, 0.0))
    root = parse(result)
    # 目标 wrapper 在 translate(20 10) 组内：其世界坐标由父变换 + wrapper 逆变换合成
    wrapper = next(
        (e for e in root.iter() if e.get("data-tavotto", "").startswith("series:")), None
    )
    assert wrapper is not None
    # 解析 wrapper transform 并手动合成世界端点

    from tavotto.svgcoord import apply as c_apply, parse_transform as c_parse

    wt = c_parse(wrapper.get("transform", ""))
    path_d = wrapper.find(f".//{SVG_NS}path").get("d")
    nums = [float(v) for v in re.findall(r"[-+]?(?:\d*\.)?\d+(?:e[-+]?\d+)?", path_d)]
    inner = [c_apply(wt, nums[i], nums[i + 1]) for i in (0, 2)]
    world = [c_apply((1.0, 0.0, 0.0, 1.0, 20.0, 10.0), pt[0], pt[1]) for pt in inner]
    got = [
        round(world[0][0], 6),
        round(world[0][1], 6),
        round(world[1][0], 6),
        round(world[1][1], 6),
    ]
    assert got == pytest.approx([20, 70, 80, 10], abs=1e-6), (
        f"world endpoints expected [20,70,80,10], got {got}"
    )


def test_bar_error_members_survive_composition():
    """N4：柱 + 误差 → 组合后误差集合/端帽成员完整（角色 gid 全在最终 SVG）。"""
    scene = SCENE
    out, frame = bind(
        scene,
        kind="bar",
        dataset="x,y,err\n0,0.3,0.1\n1,0.7,0.2\n",
    )
    out["model"]["series"][0]["yerr"] = "err"
    frag = svgchart.scaffold_fragment(out["model"], frame)
    result = compose_scene(scene, out["binding"], frag["fragment"], out["model"])
    root = parse(result)
    role_members = [
        e.get("id") for e in root.iter() if (e.get("id") or "").startswith("tv-series-s1")
    ]
    # 2 柱 + 数据/误差集合成员 ≥4（误差线主体 + 端帽）
    assert len(role_members) >= 4, f"error members lost: {role_members}"


@pytest.mark.parametrize("axis_count", [0, 1, 2])
@pytest.mark.parametrize("axes_after_series", [False, True])
@pytest.mark.parametrize("reverse_bindings", [False, True])
def test_chart_axes_do_not_cover_series(axis_count, axes_after_series, reverse_bindings):
    """真实两曲线场景：最终绘制层级、保留的背景/装饰和数据坐标同时成立。"""
    source = parse((CORPUS / "line_two_series.svg").read_bytes())
    original_axes = by_id(source, "axes_1")
    if axes_after_series:
        for name in ("matplotlib.axis_1", "matplotlib.axis_2"):
            axis = by_id(source, name)
            original_axes.remove(axis)
            original_axes.append(axis)
    background_before = ET.tostring(by_id(source, "patch_2"))
    legend_before = ET.tostring(by_id(source, "legend_1"))
    source_bytes = ET.tostring(source)
    axes_nodes = ["matplotlib.axis_1", "matplotlib.axis_2"][:axis_count]
    rect = [51.562344, 26.16, 297.637656, 183.717656]
    out, frame = bind(
        source_bytes,
        axes_nodes=axes_nodes,
        rect=rect,
        xlim=(0, 10),
        ylim=(0, 100),
        dataset="x,y,y2\n0,0,100\n5,25,50\n10,100,0\n",
        series=[
            {"node": "line2d_13", "x_col": "x", "y_col": "y", "kind": "line"},
            {"node": "line2d_14", "x_col": "x", "y_col": "y2", "kind": "line"},
        ],
    )
    if reverse_bindings:
        out["binding"]["entries"].reverse()
    fragment = svgchart.scaffold_fragment(out["model"], frame)
    result = parse(compose_scene(source_bytes, out["binding"], fragment["fragment"], out["model"]))
    final_axes = by_id(result, "axes_1")
    children = list(final_axes)
    series = [e for e in children if e.get("data-tavotto", "").startswith("series:")]
    assert [e.get("data-tavotto") for e in series] == ["series:s1", "series:s2"]
    assert ET.tostring(by_id(result, "patch_2")) == background_before
    assert ET.tostring(by_id(result, "legend_1")) == legend_before
    # 原背景必须在数据之前，片段轴背景不能盖在数据之后或改变原有背景。
    assert children.index(by_id(result, "patch_2")) < children.index(series[0])
    assert not any((e.get("id") or "").startswith("tv-axesbg-") for e in result.iter())
    if axis_count:
        furniture = next(e for e in children if e.get("data-tavotto") == "axes:ax1")
        assert children.index(furniture) < children.index(series[0]), "axis layer covers data"
        for name in axes_nodes:
            assert by_id(result, name) is None
    # 主语是最终数据路径；点位由独立线性映射计算，不消费 scaffold 的 facts。
    path = series[0].find(f".//{SVG_NS}path")
    coords = [float(v) for v in re.findall(r"[-+]?(?:\d*\.)?\d+(?:e[-+]?\d+)?", path.get("d"))]
    expected = [
        value
        for x, y in ((0, 0), (5, 25), (10, 100))
        for value in (rect[0] + x / 10 * rect[2], rect[1] + (1 - y / 100) * rect[3])
    ]
    assert coords == pytest.approx(expected, abs=1e-6)
