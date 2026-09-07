"""换过类型的标注，导出侧画得出来（cap-shape-switch）。

**消费者那一侧**：向量 `tests/golden/shape_switch_payloads.json` 是前端
`lib/shapeSwitch` 换完类型、`lib/exportPayload` 序列化之后真正上线的载荷，
生产者那一侧由 `web/src/lib/shapeSwitch.golden.test.ts` 断言。这里只问一件事
——`pdfbackend` 拿到这些字段集合，画不画得出正确的矢量。

为什么不在这里手抄一份载荷：抄出来的形状是**自己捏的**，它证明的是「我以为切换
会产出这样的字段」，不是「切换真的产出了这样的字段」。两侧共读同一份向量，
改了前端那半就得更新向量，更新了向量这边立刻按新形状跑一遍。

全部用 get_drawings() 做几何级验证，形状判据沿用 test_compose_annotations 里
同名形状那几条（三角形 3 条线 / 六边形 6 条 / 圆角与椭圆有曲线段 / 实心三角头
是一条带 fill 的路径）。
"""

import json
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m

MM = 72 / 25.4
VECTORS = json.loads(
    (Path(__file__).parent / "golden" / "shape_switch_payloads.json").read_text(encoding="utf-8")
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path)
    # 与 test_compose_annotations 同一条理由：纯标注导出不依赖项目，清掉别的
    # 测试模块残留的已打开项目，否则产物会落进那个项目的导出目录
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    m.app.config["TESTING"] = True
    return m.app.test_client()


def _export(client, tmp_path, stem, objects):
    resp = client.post(
        "/api/export",
        json={
            "page_w_mm": VECTORS["page_w_mm"],
            "page_h_mm": VECTORS["page_h_mm"],
            "formats": ["pdf"],
            "stem": stem,
            "objects": objects,
        },
    )
    assert resp.status_code == 200, resp.get_json()
    name = resp.get_json()["files"][0]["name"]
    # 从内存开：Windows 上进程持着 PDF 句柄时覆盖同名文件会 Permission denied
    return pymupdf.open(stream=(tmp_path / name).read_bytes(), filetype="pdf")


def _drawings(page):
    """过滤掉整页白底矩形，只留标注本体的矢量路径。"""
    return [
        d
        for d in page.get_drawings()
        if not (d["rect"].width >= page.rect.width - 1 and d["rect"].height >= page.rect.height - 1)
    ]


def _chain(name):
    return next(c for c in VECTORS["chains"] if c["name"] == name)


def _steps(chain):
    """(载荷, 这一步之后的类型) —— 第 0 步是链条的起点，用它自己的类型标注。"""
    kinds = [chain["payloads"][0].get("shape") or chain["payloads"][0]["type"], *chain["steps"]]
    return list(zip(chain["payloads"], kinds, strict=True))


ALL_STEPS = [
    pytest.param(payload, kind, id=f"{chain['name']}-{i}-{kind}")
    for chain in VECTORS["chains"]
    for i, (payload, kind) in enumerate(_steps(chain))
]


@pytest.mark.parametrize(("payload", "kind"), ALL_STEPS)
def test_every_switched_shape_draws_as_vector(client, tmp_path, payload, kind, request):
    """切换链条上的每一档都画得出来，且包围盒还是对象自己那个框。

    判据的主语是**画出来的那些路径**，不是「导出接口有没有返回 200」——
    形状字段不认得时后端不会报错，它会安静地少画一笔（`_draw_shape` 的最后一个
    分支是 line），那时接口照样 200。
    """
    doc = _export(client, tmp_path, request.node.name.replace("[", "_").replace("]", ""), [payload])
    drawings = _drawings(doc[0])
    assert drawings, f"{kind} 一笔都没画出来"

    # 包围盒：几何在切换里一个字不动，所以每一档都该落在同一个框上
    # （描边居中，外沿最多外扩半个线宽；箭头帽再宽一点，给 2pt 余量）
    box = pymupdf.Rect(
        payload["x_mm"] * MM,
        payload["y_mm"] * MM,
        (payload["x_mm"] + payload["w_mm"]) * MM,
        (payload["y_mm"] + payload["h_mm"]) * MM,
    )
    union = drawings[0]["rect"]
    for d in drawings[1:]:
        union |= d["rect"]
    pad = 2 + payload["stroke_pt"]
    assert union.x0 > box.x0 - pad and union.y0 > box.y0 - pad
    assert union.x1 < box.x1 + pad and union.y1 < box.y1 + pad


def test_box_chain_geometry_matches_each_kind(client, tmp_path):
    """矩形 → 椭圆 → 多边形 → 回矩形：每一档画的确实是那一种形状。

    只断言「画出来了」是不够的——后端认不出 shape 时会掉进最后那个 line 分支，
    照样有一条矢量路径。所以逐档看**形状特征**：椭圆是曲线、六边形是 6 条直线、
    矩形是 4 条直线且没有曲线段。
    """
    chain = _chain("box")
    rect0, ellipse, polygon, rect1 = chain["payloads"]

    def items(payload, stem):
        d = _drawings(_export(client, tmp_path, stem, [payload])[0])
        assert len(d) == 1
        return d[0]["items"]

    # 起点是圆角矩形：圆角画成曲线段 + 直边，不是一个 "re"
    assert {it[0] for it in items(rect0, "sw_rect0")} == {"c", "l"}
    # 椭圆：四段贝塞尔，一条直线都没有
    ell = items(ellipse, "sw_ellipse")
    assert {it[0] for it in ell} == {"c"}
    assert len(ell) == 4
    # 六边形：6 条直线（`sides` 的缺省值确实传到了后端）
    poly = items(polygon, "sw_polygon")
    assert {it[0] for it in poly} == {"l"}
    assert len(poly) == 6
    # 回到矩形：一个纯 "re"，一段曲线都没有——**圆角没跟着绕一圈回来**。
    # 判据落在 "re" 上而不是「没有 c」：后者在椭圆退化、少画一笔的情况下也成立
    back = items(rect1, "sw_rect1")
    assert [it[0] for it in back] == ["re"]


def test_linear_chain_head_appears_and_disappears(client, tmp_path):
    """直线 → 箭头 → 回直线：箭头那一档多一个实心三角，回到直线时它没了。"""
    chain = _chain("linear")
    line0, arrow, line1 = chain["payloads"]

    def filled(payload, stem):
        return [d for d in _drawings(_export(client, tmp_path, stem, [payload])[0]) if d["fill"]]

    assert filled(line0, "sw_line0") == []
    assert len(filled(arrow, "sw_arrow")) == 1  # headEnd=triangle 的那个实心三角
    assert filled(line1, "sw_line1") == []


def test_switched_line_keeps_its_endpoints(client, tmp_path):
    """端点在链条里一路不动：箭头那一档的线段两端仍是原来那两个比例坐标。

    这一条是「几何保留」在**导出产物上**的兑现——前端断言的是对象字段，这里
    断言的是纸上那条线真的画在同一处。
    """
    chain = _chain("linear")
    arrow = chain["payloads"][1]
    doc = _export(client, tmp_path, "sw_ends", [arrow])
    x, y = arrow["x_mm"] * MM, arrow["y_mm"] * MM
    w, h = arrow["w_mm"] * MM, arrow["h_mm"] * MM
    tip = pymupdf.Point(x + arrow["end"]["rx"] * w, y + arrow["end"]["ry"] * h)
    # 实心三角的尖端就是 end 端点
    head = next(d for d in _drawings(doc[0]) if d["fill"])
    assert (
        min(abs(pt - tip) for it in head["items"] for pt in it[1:] if isinstance(pt, pymupdf.Point))
        < 1
    )
