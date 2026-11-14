"""自由路径对象 `path`（ADR 0185 §二 / §四）的后端一侧。

* 共享向量 `tests/golden/path_vectors.json`：`rendercore.geometry.path_segments` 产出前端 `pathGeom.pathGeometry`
  对拍的同一份（前端一侧是 `web/src/lib/pathGeom.golden.test.ts`）；锚点经 `plan._object_transform` 落到页面上的位置
  与前端的旋转（`pathGeom.rotateAbout`）一致。
* 经契约层 `pdfbackend.compose()` 合成真实 PDF，再从内容流的矢量指令里把坐标抽回来（与 `test_compose_arrow.py` 同一口径）：
  锚点、贝塞尔控制点、端型回缩、旋转、evenodd 全部是几何级看护。
* SVG / EPS 各一条导出用例；消费者闭集（`exportreq.ORIGINAL_OBJECT_TYPES`、`facade.place`、版本草图）。
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path

import pytest

from tavotto import pdfbackend
from tavotto.engine import exportreq
from tavotto.rendercore import geometry, ir, plan
from tavotto.rendercore.ir import mm2pt

ROOT = Path(__file__).resolve().parent
SUPPORT = ROOT / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import pdfread  # noqa: E402

VECTORS = json.loads((ROOT / "golden" / "path_vectors.json").read_text(encoding="utf-8"))
HAS = all(importlib.util.find_spec(m) is not None for m in ("pikepdf", "uharfbuzz", "PIL"))
needs_rc = pytest.mark.skipif(not HAS, reason="RenderCore 依赖未装（not_run，不是绿）")
_NUM = r"-?\d+(?:\.\d+)?"


def _flat(seq) -> list[float]:
    out: list[float] = []
    for v in seq:
        if isinstance(v, (list, tuple)):
            out += _flat(v)
        elif not isinstance(v, str):
            out.append(float(v))
    return out


def _ops(segs) -> list[str]:
    return [s[0] for s in segs]


def _head_json(hd) -> dict:
    if hd[0] == "dot":
        return {"kind": "dot", "cx": hd[1], "cy": hd[2], "r": hd[3]}
    return {"kind": hd[0], "points": [list(p) for p in hd[1]]}


# ---------------------------------------------------------------------------
# 共享向量
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("vec", VECTORS["cases"], ids=lambda v: v["name"])
def test_path_segments_match_the_shared_vectors(vec):
    g = geometry.path_segments(vec["payload"], vec["w"], vec["h"])
    for key in ("fill", "stroke"):
        assert _ops(g[key]) == [s[0] for s in vec[key]], key
        assert _flat(g[key]) == pytest.approx(_flat(vec[key]), abs=1e-9), key
    assert g["head_half"] == pytest.approx(vec["head_half"], abs=1e-9)
    got = [_head_json(h) for h in g["heads"]]
    assert [h["kind"] for h in got] == [h["kind"] for h in vec["heads"]]
    for a, b in zip(got, vec["heads"], strict=True):
        assert _flat(a.values()) == pytest.approx(_flat(b.values()), abs=1e-9)


@pytest.mark.parametrize("vec", VECTORS["cases"], ids=lambda v: v["name"])
def test_anchor_page_positions_follow_the_object_transform(vec):
    """锚点经 `plan._object_transform`（框空间 → 页面，含绕中心旋转）落到页面上的位置 == 向量（前端 rotateAbout 出的同一份）。"""
    pl = vec["payload"]
    page_h = mm2pt(vec["page_h_mm"])
    a, b, c, d, e, f = plan._object_transform(pl, page_h)
    w, h = vec["w"], vec["h"]
    got = []
    for sp in geometry.read_path(pl)["subpaths"]:
        for n in sp["nodes"]:
            bx, by = n["p"][0] * w, n["p"][1] * h
            px, py = a * bx + c * by + e, b * bx + d * by + f
            got.append([px * 25.4 / 72, (page_h - py) * 25.4 / 72])
    assert _flat(got) == pytest.approx(_flat(vec["page_points"]), abs=1e-9)


def test_the_vector_set_covers_what_adr_0185_names():
    names = {c["name"] for c in VECTORS["cases"]}
    assert {
        "closed_corner",
        "closed_smooth",
        "open_heads",
        "multi_evenodd",
        "handles_outside_box",
        "rotated",
    } <= names
    rotated = next(c for c in VECTORS["cases"] if c["name"] == "rotated")
    assert rotated["payload"].get("rotation_deg")
    evenodd = next(c for c in VECTORS["cases"] if c["name"] == "multi_evenodd")
    assert evenodd["payload"].get("fill_rule") == "evenodd"


# ---------------------------------------------------------------------------
# 缺省值只有 read_path 一处
# ---------------------------------------------------------------------------
def test_read_path_defaults():
    r = geometry.read_path(
        {"subpaths": [{"nodes": [{"p": [0, 0]}, {"p": [1, "x"]}, {"p": [1, 1]}]}]}
    )
    assert r["fill_rule"] == "nonzero" and r["stroke_on"] is True
    assert r["head_start"] == "none" and r["head_end"] == "none"
    [sp] = r["subpaths"]
    assert sp["closed"] is False  # 缺省开放
    assert [n["p"] for n in sp["nodes"]] == [(0.0, 0.0), (1.0, 1.0)]  # 坏锚点整个丢
    assert sp["nodes"][0]["in"] == sp["nodes"][0]["p"] == sp["nodes"][0]["out"]  # 手柄缺省 = 锚点


def test_straight_segments_are_lines_and_any_handle_makes_a_curve():
    g = geometry.path_segments(
        {
            "subpaths": [
                {
                    "closed": False,
                    "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1], "in": [1, 0.5]}],
                }
            ]
        },
        10.0,
        10.0,
        1.0,
    )
    assert _ops(g["fill"]) == ["M", "L", "C"]


def test_no_stroke_and_no_fill_draws_nothing():
    o = {
        "stroke": False,
        "fill": None,
        "subpaths": [{"closed": True, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [0, 1]}]}],
    }
    assert geometry.path_paths(o, 10.0, 10.0) == []


def test_translucent_stroke_with_heads_is_one_transparency_group():
    """有端型时描边 + 端型放进一个透明组（与箭头同一做法：杆伸进实心端型的重叠处不叠深）；没有端型时 alpha 在描边 Paint 上。"""
    base = {
        "stroke_pt": 2.0,
        "color": "#000000",
        "fill": "#ff0000",
        "stroke_opacity": 0.5,
        "subpaths": [{"closed": False, "nodes": [{"p": [0, 0]}, {"p": [1, 1]}]}],
    }
    nodes = geometry.path_paths({**base, "head_end": "triangle"}, 50.0, 50.0)
    fill, group = nodes
    assert isinstance(fill, ir.Path) and fill.fill is not None and fill.stroke is None
    assert isinstance(group, ir.Group) and group.opacity == pytest.approx(0.5)
    assert all(
        (p.stroke is None or p.stroke.paint.alpha == 1) and (p.fill is None or p.fill.alpha == 1)
        for p in group.children
    )
    [plain] = geometry.path_paths(base, 50.0, 50.0)
    assert isinstance(plain, ir.Path) and plain.stroke.paint.alpha == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 合成真实 PDF，从内容流抽几何
# ---------------------------------------------------------------------------
PAGE_MM = (100.0, 80.0)


def _compose_pdf(o: dict) -> tuple[bytes, str]:
    out = Path(tempfile.mkdtemp(prefix="compose-path-")) / "a.pdf"
    canvas = pdfbackend.compose(*PAGE_MM)
    try:
        canvas.place(
            {"type": "path", "id": "p", "stroke_pt": 2.0, "color": "#000000", **o},
            150,
            lambda x, d: None,
        )
        canvas.save_pdf(out)
    finally:
        canvas.close()
    raw = out.read_bytes()
    _head, content = pdfread.page(pdfread.objects(raw))
    return raw, content.decode("latin-1")


def _drawings(content: str) -> list[dict]:
    """内容流 → [{'points': [(x, y) 页面顶原点 mm…], 'op': 绘制指令}]；`c` 的三个点都记（控制点也是几何）。"""
    tokens = content.split()
    stack: list[tuple] = []
    ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    page_h = mm2pt(PAGE_MM[1])
    pts: list[tuple[float, float]] = []
    out: list[dict] = []
    nums: list[float] = []

    def apply(x: float, y: float) -> tuple[float, float]:
        a, b, c, d, e, f = ctm
        px, py = a * x + c * y + e, b * x + d * y + f
        return (px * 25.4 / 72, (page_h - py) * 25.4 / 72)

    for t in tokens:
        if re.fullmatch(_NUM, t):
            nums.append(float(t))
            continue
        if t == "q":
            stack.append(ctm)
        elif t == "Q":
            ctm = stack.pop()
        elif t == "cm":
            a, b, c, d, e, f = nums[-6:]
            A, B, C, D, E, F = ctm
            ctm = (
                a * A + b * C,
                a * B + b * D,
                c * A + d * C,
                c * B + d * D,
                e * A + f * C + E,
                e * B + f * D + F,
            )
        elif t in ("m", "l"):
            pts.append(apply(*nums[-2:]))
        elif t == "c":
            for k in range(3):
                pts.append(apply(*nums[-6 + 2 * k : -4 + 2 * k] if k < 2 else nums[-2:]))
        elif t == "re":
            x, y, w, h = nums[-4:]
            pts += [apply(x, y), apply(x + w, y), apply(x + w, y + h), apply(x, y + h)]
        elif t in ("S", "f", "B", "f*", "B*", "s", "b", "b*"):
            out.append({"points": pts, "op": t})
            pts = []
        nums = []
    return out[1:]  # 第一个是整页白底


def _close(got, want, tol=0.01):
    assert len(got) == len(want)
    for (gx, gy), (wx, wy) in zip(got, want, strict=True):
        assert abs(gx - wx) < tol and abs(gy - wy) < tol, (got, want)


@needs_rc
def test_pdf_anchor_and_control_points_land_where_the_document_says():
    """闭合、带一段贝塞尔：内容流里的锚点与控制点 == 比例坐标 × 框 + 框原点（mm，顶原点）。"""
    o = {
        "x_mm": 10,
        "y_mm": 20,
        "w_mm": 40,
        "h_mm": 30,
        "fill": "#ffcc00",
        "subpaths": [
            {
                "closed": True,
                "nodes": [
                    {"p": [0, 0], "out": [0.5, -0.4]},
                    {"p": [1, 0], "in": [0.8, -0.4]},
                    {"p": [0.5, 1]},
                ],
            }
        ],
    }
    _raw, content = _compose_pdf(o)
    [d] = _drawings(content)
    assert d["op"] == "B"  # 又填又描、nonzero
    X = lambda fx: 10 + fx * 40  # noqa: E731
    Y = lambda fy: 20 + fy * 30  # noqa: E731
    want = [
        (X(0), Y(0)),
        (X(0.5), Y(-0.4)),
        (X(0.8), Y(-0.4)),
        (X(1), Y(0)),
        (X(0.5), Y(1)),
        (X(0), Y(0)),
    ]
    _close(d["points"], want)
    assert " c " in content and " h " in content


@needs_rc
def test_pdf_evenodd_uses_the_star_operator():
    o = {
        "x_mm": 10,
        "y_mm": 10,
        "w_mm": 40,
        "h_mm": 40,
        "fill": "#336699",
        "fill_rule": "evenodd",
        "stroke": False,
        "subpaths": [
            {"closed": True, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}, {"p": [0, 1]}]},
            {
                "closed": True,
                "nodes": [
                    {"p": [0.25, 0.25]},
                    {"p": [0.75, 0.25]},
                    {"p": [0.75, 0.75]},
                    {"p": [0.25, 0.75]},
                ],
            },
        ],
    }
    _raw, content = _compose_pdf(o)
    assert [d["op"] for d in _drawings(content)] == ["f*"]
    _raw, nonzero = _compose_pdf({k: v for k, v in o.items() if k != "fill_rule"})
    assert [d["op"] for d in _drawings(nonzero)] == ["f"]


@needs_rc
def test_pdf_open_path_heads_trim_the_stroke_like_an_arrow():
    """开放直线段 + 终点三角：描边回缩 0.75×帽长、帽尖在端点、帽底 ±1.7×线宽——与 `test_compose_arrow` 同一合同。"""
    sw_mm = 2.0 * 25.4 / 72
    o = {
        "x_mm": 10,
        "y_mm": 30,
        "w_mm": 40,
        "h_mm": 0,
        "head_end": "triangle",
        "fill": None,
        "subpaths": [{"closed": False, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}]}],
    }
    _raw, content = _compose_pdf(o)
    strokes = [d for d in _drawings(content) if d["op"] == "S"]
    fills = [d for d in _drawings(content) if d["op"] == "f"]
    assert len(strokes) == 1 and len(fills) == 1
    trim = sw_mm * 4 * 0.75
    _close(strokes[0]["points"], [(10, 30), (50 - trim, 30)])
    _close(
        fills[0]["points"],
        [(50, 30), (50 - sw_mm * 4, 30 + sw_mm * 1.7), (50 - sw_mm * 4, 30 - sw_mm * 1.7)],
    )


@needs_rc
def test_pdf_rotated_path_matches_the_shared_vector_page_points():
    """旋转对象：内容流里（套上组变换后）的锚点 == 共享向量的 `page_points`（前端 rotateAbout 出的同一份）。"""
    vec = next(c for c in VECTORS["cases"] if c["name"] == "rotated")
    assert [s[0] for s in vec["fill"]] == ["M", "C", "L", "C", "L", "Z"]
    _raw, content = _compose_pdf(vec["payload"])  # 顶原点坐标与页面高度无关
    [d] = _drawings(content)
    anchors = [d["points"][i] for i in (0, 3, 4, 7)]  # M、C 终点、L、C 终点（各锚点）
    _close(anchors, [tuple(p) for p in vec["page_points"]], tol=0.005)


# ---------------------------------------------------------------------------
# SVG / EPS
# ---------------------------------------------------------------------------
def _page_with(o: dict) -> ir.Page:
    compiled = plan.compile_page(60.0, 40.0, [o], sources=None, faces=None)  # type: ignore[arg-type]
    return compiled.page


PATH_OBJ = {
    "type": "path",
    "id": "p",
    "x_mm": 5,
    "y_mm": 5,
    "w_mm": 30,
    "h_mm": 20,
    "stroke_pt": 1.5,
    "color": "#224466",
    "fill": "#aaccee",
    "fill_rule": "evenodd",
    "subpaths": [
        {
            "closed": True,
            "nodes": [
                {"p": [0, 0.5], "in": [0, 1], "out": [0, 0]},
                {"p": [1, 0.5], "in": [1, 0], "out": [1, 1]},
            ],
        },
    ],
}


@needs_rc
def test_svg_export_writes_the_curve_and_the_fill_rule(tmp_path):
    from tavotto.rendercore import fonts, svgwriter
    from tavotto.rendercore.hbshaper import HbFaceProvider

    out = tmp_path / "p.svg"
    svgwriter.write_svg(_page_with(PATH_OBJ), out, HbFaceProvider(fonts.FontRegistry.discover()))
    text = out.read_text(encoding="utf-8")
    [d] = re.findall(r'<path[^>]* d="([^"]+)"[^>]*fill-rule="evenodd"', text)
    assert d.count("C") == 2 and d.rstrip().endswith("Z")
    assert 'stroke="rgb(' in text and 'stroke-width="1.5"' in text


@needs_rc
def test_eps_export_writes_curveto_and_eofill():
    from tavotto.rendercore import epswriter, fonts
    from tavotto.rendercore.hbshaper import HbFaceProvider

    ps, _facts = epswriter.overlay_ps(
        _page_with(PATH_OBJ), HbFaceProvider(fonts.FontRegistry.discover())
    )
    assert " curveto" in ps and "eofill" in ps and "stroke" in ps
    # 半透明在 EPS 里没有：与形状同一条（逐项失败，交给导出前提醒），不静默变不透明
    with pytest.raises(epswriter.UnsupportedCapability):
        epswriter.overlay_ps(
            _page_with({**PATH_OBJ, "fill_opacity": 0.5}),
            HbFaceProvider(fonts.FontRegistry.discover()),
        )


# ---------------------------------------------------------------------------
# 消费者闭集
# ---------------------------------------------------------------------------
def test_original_export_accepts_path_annotations():
    assert "path" in exportreq.ORIGINAL_OBJECT_TYPES
    objs = exportreq._original_objects({"objects": [PATH_OBJ], "w_mm": 80, "h_mm": 60})
    assert objs[0]["type"] == "path"


@needs_rc
def test_facade_place_keeps_path_objects():
    canvas = pdfbackend.compose(60.0, 40.0)
    try:
        canvas.place(PATH_OBJ, 150, lambda x, d: None)
        assert [o["type"] for o in canvas._objects] == ["path"]
    finally:
        canvas.close()


def test_plan_compiles_path_into_a_transformed_group():
    page = _page_with({**PATH_OBJ, "rotation_deg": 30})
    [g] = page.children
    assert isinstance(g, ir.Group) and g.object_id == "p"
    [p] = g.children
    assert isinstance(p, ir.Path) and p.fill_rule == "evenodd"
    assert [s[0] for s in p.segments] == ["M", "C", "C", "Z"]


def test_version_sketch_carries_small_paths_and_drops_huge_ones():
    from tavotto import app

    small = app._sketch_subpaths(PATH_OBJ["subpaths"])
    assert small == [
        {
            "closed": True,
            "nodes": [
                {"p": [0.0, 0.5], "in": [0.0, 1.0], "out": [0.0, 0.0]},
                {"p": [1.0, 0.5], "in": [1.0, 0.0], "out": [1.0, 1.0]},
            ],
        }
    ]
    huge = [
        {
            "closed": False,
            "nodes": [{"p": [i / 100, 0]} for i in range(app.VERSION_SKETCH_MAX_PATH_NODES + 1)],
        }
    ]
    assert app._sketch_subpaths(huge) is None
    assert app._sketch_subpaths("nope") is None
