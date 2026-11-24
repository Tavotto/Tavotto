"""贯通切片（ADR 0208）：数据点锚点 → 箭头 → 校验 → 导出，量**成品**里箭头尖与数据点的距离。

一条链上的四段一起验：worker 在导出绘制上换算数据点（`export(..., data_points=…)`）、图幅分数 → 画布毫米
（`vectorscene.panel_point_to_page`）、求解与对象校验（`vectorscene.solve` → `agentobjects.validate_new_objects`）、
导出（`agentobjects.to_export_objects` → `rendercore.facade.annotate_asset` 叠到同一份 PDF 上）。
对照完全独立：图里在那个数据点画一个纯红点（matplotlib 自己画的）。
- 矢量（精确）：从**导出的 PDF 内容流**里读出红点那条路径的中心（matplotlib 实际写进成品的坐标），与锚点解出的画布坐标比，
  ≤ 0.005 mm；
- 像素（粗查）：同一个锚点上叠一个只有描边的纯蓝圆环，把注好的 PDF 栅格化，圆环重心与红点重心差 ≤ 1.5 像素
  （红点叠在曲线峰顶上，抗锯齿会把红色像素的重心带偏约 1 像素，所以只作粗查）；
- 箭头尖从**导出载荷**的几何（`rendercore.geometry.arrow_geometry`）读出，必须就是同一个点。
tight / constrained / 无布局都跑。
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from tavotto.engine import agentobjects, pool, vectorscene

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None
pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="matplotlib worker unavailable")

DRIVER = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from figsession import LiveFigureSession
import figcapture
out = Path(sys.argv[2])
rows = []
for layout in [None, 'tight', 'constrained']:
  fig, (a, b) = plt.subplots(1, 2, figsize=(6, 3), dpi=100, layout=layout)
  x = np.linspace(0, 6, 200)
  a.plot(x, np.exp(-(x - 3.2) ** 2 / 0.3), color='0.6'); a.set_ylabel('Intensity (a.u.) long label')
  b.semilogy([1, 10, 100], [1, 100, 1e4], color='0.6')
  a.plot([3.2], [1.0], 'o', ms=3, mec='none', mfc=(1, 0, 0), zorder=10)
  s = LiveFigureSession(out / str(layout))
  s.add_figure('F', fig, figcapture.SOURCE_SAVEFIG)
  s.instrument_all()
  s.do_render('F', [])
  req = [{'axes': a.get_gid(), 'x': 3.2, 'y': 1.0}]
  pdf = out / f'{layout}.pdf'
  res = s.do_export('F', [], str(pdf), 'pdf', 300, data_points=req)
  rows.append({'layout': layout, 'pdf': str(pdf), 'requests': req, 'data_points': res['data_points'],
               'draw_manifest': res['draw_manifest']})
  plt.close(fig)
print(json.dumps(rows))
"""


def test_arrow_tip_lands_on_the_data_point_in_the_exported_pdf(tmp_path):
    from PIL import Image

    from tavotto.rendercore import facade

    proc = subprocess.run(
        [WORKER_PY, "-c", DRIVER, str(Path(pool.__file__).parent), str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    for row in json.loads(proc.stdout.strip().splitlines()[-1]):
        w_mm, h_mm = row["draw_manifest"]["size_mm"]
        # 子图铺满一页、缩放 1：画布毫米 = 这张图自己的毫米（annotate_asset 的坐标系）
        panel = {"x": 0.0, "y": 0.0, "w": w_mm, "h": h_mm}
        axes_gid = row["requests"][0]["axes"]
        peak = {"data": {"panel": "P1", "axes": axes_gid, "x": 3.2, "y": 1.0}}
        items = [
            {
                "kind": "arrow",
                "name": "peak_arrow",
                "from": {"anchor": peak, "offset_mm": [12.0, -9.0]},
                "to": {"anchor": peak},
                "head": "end",
                "headEnd": "triangle",
                "strokePt": 0.75,
                "color": "#00A000",
            },
            {
                "kind": "text",
                "name": "peak_label",
                "text": "Peak 3.2 eV",
                "at": {
                    "anchor": {"item": {"name": "peak_arrow", "point": "ne"}},
                    "offset_mm": [1.0, 0.0],
                },
                "attach": "w",
                "sizePt": 7,
            },
            {
                "kind": "shape",
                "name": "peak_ring",
                "shape": "ellipse",
                "at": {"anchor": peak},
                "size_mm": [2.4, 2.4],
                "fill": None,
                "strokePt": 0.5,
                "color": "#0000FF",
            },
        ]
        panels = {
            "P1": {
                "panel": panel,
                "draw_manifest": row["draw_manifest"],
                "requests": row["requests"],
                "data_points": row["data_points"],
            }
        }
        assert vectorscene.required_data_points(items) == {"P1": row["requests"]}
        solved = vectorscene.solve(items, panels)
        assert solved["ok"], solved["errors"]
        checked = agentobjects.validate_new_objects(solved["objects"])
        assert checked["ok"], checked["errors"]
        pdf = Path(row["pdf"])
        red_mm = _red_marker_center_mm(pdf)
        facade.annotate_asset(pdf, None, agentobjects.to_export_objects(checked["objects"]))
        png = tmp_path / f"{row['layout']}.png"
        width = 2400
        facade.render_preview_png(pdf, width, png)
        img = np.asarray(Image.open(io.BytesIO(png.read_bytes())).convert("RGB")).astype(int)
        H, W = img.shape[:2]
        px_per_mm = W / w_mm
        red = (img[:, :, 0] > 200) & (img[:, :, 1] < 60) & (img[:, :, 2] < 60)
        blue = (img[:, :, 2] > 200) & (img[:, :, 0] < 60) & (img[:, :, 1] < 60)
        ys, xs = np.where(red)
        assert len(xs), row["layout"]
        dot = np.array([xs.mean() + 0.5, ys.mean() + 0.5])
        by, bx = np.where(blue)
        assert len(bx), row["layout"]
        # 圆环的像素重心 = 圆心（只看圆环附近，箭头另算）
        ring = next(o for o in checked["objects"] if o["type"] == "shape")
        rc = np.array(
            [(ring["x"] + ring["w"] / 2) * px_per_mm, (ring["y"] + ring["h"] / 2) * px_per_mm]
        )
        near = np.hypot(bx + 0.5 - rc[0], by + 0.5 - rc[1]) <= 2.0 * px_per_mm
        ring_c = np.array([(bx[near] + 0.5).mean(), (by[near] + 0.5).mean()])
        miss_mm = float(np.hypot(*(ring_c - dot))) / px_per_mm
        # 箭头尖：从导出载荷算（与画布 / 导出同一个几何函数），必须就是圆环中心
        from tavotto.rendercore import geometry

        exp = agentobjects.to_export_objects(checked["objects"])
        arrow = next(o for o in exp if o["type"] == "arrow")
        g = geometry.arrow_geometry(arrow, arrow["w_mm"], arrow["h_mm"])
        tip = (arrow["x_mm"] + g["b"][0], arrow["y_mm"] + g["b"][1])
        assert np.allclose(tip, rc / px_per_mm, atol=1e-6), (row["layout"], tip, rc / px_per_mm)
        # 矢量：锚点 = matplotlib 实际画红点的位置
        assert np.allclose(rc / px_per_mm, red_mm, atol=0.005), (
            row["layout"],
            rc / px_per_mm,
            red_mm,
        )
        # 像素粗查
        assert miss_mm <= 1.5 / px_per_mm, (row["layout"], miss_mm, ring_c, dot)


def _red_marker_center_mm(pdf: Path) -> tuple[float, float]:
    """导出的 PDF 里纯红填充的那条路径（红点）的外接框中心，换成 top-origin 的毫米。"""
    import pikepdf

    with pikepdf.open(pdf) as doc:
        page = doc.pages[0]
        height = float(page.MediaBox[3]) - float(page.MediaBox[1])
        red, pts = False, []
        for operands, op in pikepdf.parse_content_stream(page):
            name = str(op)
            if name == "rg":
                red = [float(v) for v in operands] == [1.0, 0.0, 0.0]
            elif red and name in ("m", "l"):
                pts.append((float(operands[0]), float(operands[1])))
            elif red and name == "c":
                pts.append((float(operands[4]), float(operands[5])))
            elif red and pts and name in ("f", "f*", "B", "b"):
                break
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    return (cx * 25.4 / 72, (height - cy) * 25.4 / 72)
