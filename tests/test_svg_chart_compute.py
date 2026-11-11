"""最小计算闭环测试（SG07 / ADR 0166）：脚手架 → 组合 → 独立核对。

- `scaffold_fragment`：片段与场景坐标 1:1 对齐；点位置与独立公式一致；
  确定性（同输入同字节）；log/倒轴；坏模型拒绝。
- `compose_scene`：系列节点被片段替换；轴区域节点移除并接入片段的轴子树；
  未绑定内容逐字节保留；跨区域 defs 上提；产物过 svgcheck。
- worker 分发：真实子进程里 `svg_scaffold` 不建会话、不 import 用户脚本。
"""

from __future__ import annotations

import base64
import hashlib
import subprocess
import sys
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


def test_worker_svg_scaffold_does_not_build_a_session(tmp_path):
    """子进程里 svg_scaffold 不 import 用户脚本：假脚本里放哨兵，命中即失败。"""
    script = tmp_path / "boobytrap.py"
    script.write_text("raise RuntimeError('user script must not run')\n")
    child = (
        "import json,sys;"
        "sys.path.insert(0, r'%s');"
        "sys.argv=['worker','--script',r'%s'];"
        "from tavotto.engine import worker;"
        "w = worker.Worker.__new__(worker.Worker);"
        "import types;"
        "w.session = types.SimpleNamespace(artifact_source=None);"
        "req = json.load(sys.stdin);"
        "print(json.dumps(w.handle(req)))" % (REPO / "src", script)
    )
    req = {
        "cmd": "svg_scaffold",
        "model": model(),
        "frame": FRAME,
        "salt": "tavotto",
    }
    proc = subprocess.run(
        [sys.executable, "-c", child],
        input=__import__("json").dumps(req),
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "PYTHONPATH": str(REPO / "src"),
            "TAVOTTO_NO_TELEMETRY": "1",
            "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
            "USERPROFILE": __import__("os").environ.get("USERPROFILE", ""),
        },
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr[-400:]
    payload = __import__("json").loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    frag = base64.b64decode(payload["fragment_b64"])
    assert b"<svg" in frag
    # 与进程内脚手架逐字节一致（同 salt 同输入）
    assert (
        hashlib.sha256(frag).hexdigest()
        == hashlib.sha256(svgchart.scaffold_fragment(model(), FRAME)["fragment"]).hexdigest()
    )
