"""export 的 `data_points`（ADR 0208 §二）：数据点在**这次导出绘制**里落在哪，以 matplotlib 自己的变换为准。

为什么挂在导出上：tight / constrained 布局下，PNG 预览与 PDF 成品的坐标轴相差零点几毫米（2026-11-24 实测
0.08–0.33 mm），对齐的真值是交出去的成品。两个独立的对照，都比**导出的那份 PDF**：
- manifest：线性轴上，坐标轴范围的四个角必须落在同一次导出绘制量出的 `draw_manifest` 的 `axes` 框角上；
- 像素：在数据点上画纯红小点，把导出的 PDF 用 RenderCore（PDFium）栅格化，红点中心与换算结果相差 ≤ 1.5 像素。
三种布局（无 / tight / constrained）× 有无脚本图幅（frame）都跑；导出必须状态中立。
"""

import io
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from tavotto.engine import pool

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
from figsession import LiveFigureSession
import figcapture, pathgeom
from matplotlib.transforms import Bbox
framed = sys.argv[2] == "True"
out_dir = Path(sys.argv[3])
RED = (1.0, 0.0, 0.0)

def fixture(layout):
  fig, axs = plt.subplots(2, 2, figsize=(6, 4), dpi=100, layout=layout)
  lin, log, inv, cat = axs.flat
  lin.plot([0, 10], [0, 5], color='0.5'); lin.set_xlim(0, 10); lin.set_ylim(0, 5)
  lin.set_ylabel('Signal (a.u.) with a long label')
  log.set_yscale('log'); log.plot([1, 100], [1, 1000], color='0.5'); log.set_xlim(1, 100); log.set_ylim(1, 1000)
  inv.plot([0, 1], [0, 1], color='0.5'); inv.set_xlim(1, 0); inv.set_ylim(0, 1)
  twin = inv.twinx(); twin.set_ylim(0, 100)
  cat.bar(['a', 'b', 'c'], [1, 2, 3], color='0.7')
  if framed:
    pathgeom.set_frame(fig, Bbox.from_bounds(-.2, -.2, 6.4, 4.4))
  return fig, {'lin': lin, 'log': log, 'inv': inv, 'twin': twin, 'cat': cat}

POINTS = [
  ('lin', 3.0, 2.0, True), ('log', 10.0, 100.0, True), ('inv', 0.25, 0.75, True),
  ('twin', 0.5, 80.0, True), ('cat', 'b', 1.5, True),
  ('lin', 20.0, 2.0, False),   # 视野外：照算，in_view false
  ('log', 10.0, -1.0, False),  # 对数轴上 ≤ 0：out_of_domain
]

rows = []
for layout in [None, 'tight', 'constrained']:
  fig, axes = fixture(layout)
  for name, x, y, draw in POINTS:
    if draw:
      axes[name].plot([x], [y], 'o', ms=4, mec='none', mfc=RED, zorder=10)
  s = LiveFigureSession(out_dir / str(layout))
  s.add_figure('F', fig, figcapture.SOURCE_SAVEFIG)
  s.instrument_all()
  s.do_render('F', [])
  gid = {name: ax.get_gid() for name, ax in axes.items()}
  reqs = [{'axes': gid[n], 'x': x, 'y': y} for n, x, y, _ in POINTS]
  reqs += [{'axes': 'no_such_gid', 'x': 0, 'y': 0}, {'axes': 'figure', 'x': 0, 'y': 0}]
  reqs += [{'axes': gid['lin'], 'x': c[0], 'y': c[1]} for c in [(0, 0), (10, 0), (0, 5), (10, 5)]]
  state = s.snapshot('F')
  cats_before = dict(axes['cat'].xaxis.units._mapping)
  pdf = out_dir / f'{layout}.pdf'
  res = s.do_export('F', [], str(pdf), 'pdf', 300, data_points=reqs + [{'axes': gid['cat'], 'x': 'zzz', 'y': 1}])
  assert s.snapshot('F') == state, 'export must stay state-neutral'
  # 没画过的组名：换算不了，而且**不许**被加进分类轴（否则多出一个刻度、还原不回来）
  assert res['data_points'][-1] == {'status': 'unresolvable', 'reason': 'units'}
  assert dict(axes['cat'].xaxis.units._mapping) == cats_before
  res['data_points'] = res['data_points'][:-1]
  plain = s.do_export('F', [], str(out_dir / f'{layout}-plain.pdf'), 'pdf', 300)
  assert 'data_points' not in plain and 'draw_manifest' not in plain
  man = {e['gid']: e for e in res['draw_manifest']['elements']}
  rows.append({'layout': layout, 'pdf': str(pdf), 'points': res['data_points'],
               'drawn': [i for i, p in enumerate(POINTS) if p[3]],
               'lin_axes_bbox': man[gid['lin']]['bbox']})
  plt.close(fig)

fig = plt.figure(figsize=(3, 3), dpi=100)
ax3 = fig.add_subplot(projection='3d')
s = LiveFigureSession(out_dir / '3d')
s.add_figure('G', fig, figcapture.SOURCE_SAVEFIG)
s.instrument_all()
s.do_render('G', [])
res = s.do_export('G', [], str(out_dir / '3d.pdf'), 'pdf', 300, data_points=[{'axes': ax3.get_gid(), 'x': 0, 'y': 0}])
assert res['data_points'] == [{'status': 'unresolvable', 'reason': 'not_axes'}]
print(json.dumps(rows))
"""


@pytest.mark.parametrize("framed", [False, True])
def test_data_points_match_the_exported_pdf(framed, tmp_path):
    from PIL import Image

    from tavotto.rendercore import facade

    proc = subprocess.run(
        [WORKER_PY, "-c", DRIVER, str(Path(pool.__file__).parent), str(framed), str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    rows = json.loads(proc.stdout.strip().splitlines()[-1])
    assert [r["layout"] for r in rows] == [None, "tight", "constrained"]
    for row in rows:
        pts = row["points"]
        # 1) 线性轴的四个角 = 同一次导出绘制的 manifest 的 axes 框角
        bx, by, bw, bh = row["lin_axes_bbox"]
        want = [[bx, by + bh], [bx + bw, by + bh], [bx, by], [bx + bw, by]]
        for got, exp in zip(pts[-4:], want):
            assert got["status"] == "ok" and np.allclose(got["xy"], exp, atol=1e-9), (
                row["layout"],
                got,
                exp,
            )
        # 2) 像素对照：把导出的 PDF 栅格化，找红点
        png = tmp_path / f"{row['layout']}.png"
        facade.render_preview_png(Path(row["pdf"]), 1800, png)
        img = np.asarray(Image.open(io.BytesIO(png.read_bytes())).convert("RGB")).astype(int)
        H, W = img.shape[:2]
        red = (img[:, :, 0] > 200) & (img[:, :, 1] < 60) & (img[:, :, 2] < 60)
        r = 14
        for i in row["drawn"]:
            got = pts[i]
            assert got["status"] == "ok" and got["in_view"], (row["layout"], i, got)
            cx, cy = got["xy"][0] * W, got["xy"][1] * H
            x0, y0 = max(0, int(cx) - r), max(0, int(cy) - r)
            ys, xs = np.where(red[y0 : int(cy) + r, x0 : int(cx) + r])
            assert len(xs), (row["layout"], i, "no red dot near the computed point")
            ox, oy = xs.mean() + x0 + 0.5, ys.mean() + y0 + 0.5
            assert abs(ox - cx) <= 1.5 and abs(oy - cy) <= 1.5, (
                row["layout"],
                i,
                (ox, oy),
                (cx, cy),
            )
        # 3) 其余几种
        assert pts[5]["status"] == "ok" and pts[5]["in_view"] is False
        assert pts[6] == {"status": "unresolvable", "reason": "out_of_domain"}
        assert pts[7] == {"status": "unresolvable", "reason": "not_axes"}
        assert pts[8] == {"status": "unresolvable", "reason": "not_axes"}
        # twin 轴与宿主同一个框，但 y 换算不同
        assert abs(pts[3]["xy"][1] - pts[2]["xy"][1]) > 1e-3
