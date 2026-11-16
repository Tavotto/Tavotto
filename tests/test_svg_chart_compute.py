"""最小计算闭环测试（SG07 / ADR 0182）：脚手架 → 组合 → 独立核对。

- `scaffold_fragment`：片段与场景坐标 1:1 对齐；点位置与独立公式一致；
  确定性（同输入同字节）；log/倒轴；坏模型拒绝。
- `compose_scene`：系列节点被片段替换；轴区域节点移除并接入片段的轴子树；
  未绑定内容逐字节保留；跨区域 defs 上提；产物过 svgcheck。
- worker 分发：真实子进程里 `chart` 不建会话、不 import 用户脚本。
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from tavotto.engine import chartmodel, svgchart
from tavotto.rendercore.svgcheck import parse_svg, strip_matplotlib_doctype
from tavotto.svgcompose import ComposeError, compose_scene

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "tests" / "support" / "svg_corpus"

FRAME = {"view_box": [0.0, 0.0, 360.0, 252.0], "w_pt": 360.0, "h_pt": 252.0, "preserve": None}


def model(**overrides):
    m = {
        "schema": chartmodel.MODEL_SCHEMA,
        "rev": 1,
        "axes": [
            {
                "id": "ax1",
                "rect": [51.562344, 26.16, 297.637656, 183.717656],
                "xlim": [2.0, 8.0],
                "ylim": [-5.0, 105.0],
                "xscale": "linear",
                "yscale": "linear",
                "invert_x": False,
                "invert_y": False,
            }
        ],
        "series": [
            {
                "id": "s1",
                "kind": "line",
                "axes": "ax1",
                "node": "line2d_13",
                "x": "x",
                "y": "y1",
                "style": {"color": "#1f77b4"},
            },
        ],
        "dataset": {"columns": {"x": [2.0, 4.0, 8.0], "y1": [4.0, 16.0, 64.0]}},
        "scene_digest": "a" * 64,
        "data_digest": chartmodel.digest_of(
            {"columns": {"x": [2.0, 4.0, 8.0], "y1": [4.0, 16.0, 64.0]}}
        ),
        "engine": {"matplotlib": "3.11.2", "numpy": "2.1.0", "tavotto": "0.19.2"},
    }
    m.update(overrides)
    return m


def frac(v, lo, hi, scale="linear"):
    if scale == "log":
        import math

        return (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
    return (v - lo) / (hi - lo)


# ---------------------------------------------------------------------------
# 脚手架
# ---------------------------------------------------------------------------


def test_fragment_points_match_independent_formula():
    out = svgchart.scaffold_fragment(model(), FRAME)
    facts = out["facts"]
    pts = facts["series_points"]["s1"]
    ax = model()["axes"][0]
    for p, (xv, yv) in zip(pts, [(2.0, 4.0), (4.0, 16.0), (8.0, 64.0)]):
        assert p["x"] == pytest.approx(frac(xv, *ax["xlim"]), abs=1e-9)
        assert p["y"] == pytest.approx(frac(yv, *ax["ylim"]), abs=1e-9)


def test_fragment_is_deterministic():
    a = svgchart.scaffold_fragment(model(), FRAME)["fragment"]
    b = svgchart.scaffold_fragment(model(), FRAME)["fragment"]
    assert a == b


def _series_stroke_width(fragment: bytes) -> str:
    root = parse_svg(strip_matplotlib_doctype(fragment))
    for el in root.iter():
        style = el.get("style", "")
        if "stroke: #1f77b4" in style:
            return style.split("stroke-width:", 1)[1].split(";", 1)[0].strip()
    raise AssertionError("series path not found in scaffold fragment")


def test_line_series_forwards_linewidth_to_the_scaffold():
    base = svgchart.scaffold_fragment(model(), FRAME)["fragment"]
    thick_model = model()
    thick_model["series"][0]["style"] = {"color": "#1f77b4", "linewidth": 3.0}
    thick = svgchart.scaffold_fragment(thick_model, FRAME)["fragment"]
    assert _series_stroke_width(base) == "2"
    assert _series_stroke_width(thick) == "3"


def test_safe_style_keeps_linewidth():
    assert svgchart._safe_style({"linewidth": 3.0}) == {"linewidth": 3.0}


def test_fragment_has_tick_labels_for_the_new_range():
    out = svgchart.scaffold_fragment(model(), FRAME)
    labels = [t["label"] for t in out["facts"]["ticks"]["x"]["ticks"]]
    assert sorted(set(labels)) == sorted("2345678")


def test_log_scale_facts_use_log_mapping():
    # log 轴范围必须为正（chartmodel 拒绝负范围）；数据点低于下界时分数为负（clip 出界）
    m = model(axes=[dict(model()["axes"][0], yscale="log", ylim=[2.0, 105.0])])
    out = svgchart.scaffold_fragment(m, FRAME)
    pts = out["facts"]["series_points"]["s1"]
    for p, yv in zip(pts, [4.0, 16.0, 64.0]):
        assert p["y"] == pytest.approx(frac(yv, 2.0, 105.0, "log"), abs=1e-9)


def test_bad_model_is_refused():
    bad = model(rev=0)
    with pytest.raises(chartmodel.ChartModelError):
        svgchart.scaffold_fragment(bad, FRAME)


# ---------------------------------------------------------------------------
# 组合
# ---------------------------------------------------------------------------


def binding_for(model_dict, axes_nodes=None):
    entries = [{"object": "series:s1", "node": "line2d_13"}]
    entries += [{"object": "axes:ax1", "node": n} for n in (axes_nodes or [])]
    return {
        "model_rev": model_dict["rev"],
        "scene_digest": model_dict["scene_digest"],
        "entries": entries,
    }


def compose(model_dict, axes_nodes=None):
    """SG07 组合入口：binding + model（图例同步/角色判定的合同自 SG08 起）。"""
    scene = (CORPUS / "line_two_series.svg").read_bytes()
    frag = svgchart.scaffold_fragment(model_dict, FRAME)["fragment"]
    return compose_scene(scene, binding_for(model_dict, axes_nodes), frag, model_dict)


def test_compose_replaces_series_and_keeps_unbound_text():
    composed = compose(model())
    root = parse_svg(strip_matplotlib_doctype(composed))
    texts = {"".join(t.itertext()).strip() for t in root.iter() if t.tag.endswith("text")}
    assert "Two known series" in texts  # 标题（未绑定）保留
    assert "growth" in texts and "decline" in texts  # 图例保留
    # 产物本身过 svgcheck（引用闭合、无活动内容）
    assert root.tag.endswith("svg")


def test_compose_removes_bound_axes_nodes_and_inserts_axis_subtrees():
    composed = compose(model(), axes_nodes=["xtick_1"])
    root = parse_svg(strip_matplotlib_doctype(composed))
    ids = {el.attrib.get("id") for el in root.iter()}
    assert "xtick_1" not in ids  # 被绑定的轴节点退役
    assert any(i and "matplotlib.axis_" in i for i in ids)  # 片段的轴子树接入


def test_compose_rejects_scene_without_the_bound_node():
    scene = (CORPUS / "plain_shapes.svg").read_bytes()
    frag = svgchart.scaffold_fragment(model(), FRAME)["fragment"]
    with pytest.raises(ComposeError) as ei:
        compose_scene(scene, binding_for(model()), frag, model())
    assert ei.value.code == "compose_node_missing"


def test_compose_output_passes_svgcheck_and_is_deterministic():
    a = compose(model())
    b = compose(model())
    assert a == b
    parse_svg(strip_matplotlib_doctype(a))  # 不抛即过


# ---------------------------------------------------------------------------
# worker 分发（真实子进程）
# ---------------------------------------------------------------------------


def test_worker_chart_does_not_build_a_session(tmp_path):
    """The pooled v1 task never imports a user script."""
    from tavotto.engine import chartprocess, pool

    script = tmp_path / "boobytrap.py"
    script.write_text("raise RuntimeError('user script must not run')\n", encoding="utf-8")
    try:
        payload = chartprocess.run_request(
            tmp_path, {"model": model(), "frame": FRAME, "salt": "tavotto"}, timeout=120
        )
        assert payload["ok"] is True
        frag = base64.b64decode(payload["fragment_b64"])
        assert b"<svg" in frag
        assert (
            hashlib.sha256(frag).hexdigest()
            == hashlib.sha256(svgchart.scaffold_fragment(model(), FRAME)["fragment"]).hexdigest()
        )
        worker = pool.acquire_chart(tmp_path)
        assert not worker.built and worker.execution_id is None
    finally:
        pool.shutdown_all(str(tmp_path), wait=True)
