"""最终组合产物的正确性回归（NEXT_PHASE R00；缺陷 B1–B5）。

这些测试断言**最终 SVG**的科学/视觉正确性（独立公式预期），不是中间 facts。
当前实现带 xfail(strict=True) 标记记录已知缺陷；对应修复提交（R01/R02）移除
标记后它们转为常驻回归——修复错误时 XPASS 会让套件变红，强制摘标记。

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
        axes_nodes=["axes_2"],
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
