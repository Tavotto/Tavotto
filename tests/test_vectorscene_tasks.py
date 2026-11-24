"""五个真实任务的端到端（ADR 0208）：真 worker 导出子图 → 求解 → 校验 → 核对 → 真画布合成（RenderCore）→ 量成品。

任务：① 给曲线峰值加箭头和说明；② 两根柱子之间的显著性括号（星号来自用户）；③ 用虚线框框住两张子图；
④ 子图上方的示意元件连一根线到峰值；⑤ 两张子图的角标 (a) (b)。另加两个对照：两张子图里各有一个孤立的纯红点
（不叠在任何线上，避免抗锯齿偏差），同一个数据锚点上各叠一个纯蓝圆环；合成的 PDF 栅格化后两者重心对齐。
右边的子图缩放到 0.8，覆盖「图幅分数 → 画布毫米」的缩放路径。

验收：所有核对 `verified`（没有 fail、也没有 unknown）；两处圆环与红点的重心差 ≤ 半像素 + 0.1 pt。
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from tavotto.engine import agentobjects, pool, profiles, vectorcheck, vectorscene

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
req = json.loads(sys.argv[3])
rows = {}

fig, ax = plt.subplots(figsize=(3.0, 2.2), dpi=100, layout='constrained')
x = np.linspace(0, 6, 300)
ax.plot(x, np.exp(-(x - 3.2) ** 2 / 0.3), color='0.55')
ax.plot([5.0], [0.6], 'o', ms=3, mec='none', mfc=(1, 0, 0))
ax.set_xlabel('Energy (eV)'); ax.set_ylabel('Intensity')
figs = {'P1': (fig, ax)}

fig2, ax2 = plt.subplots(figsize=(2.6, 2.2), dpi=100, layout='constrained')
ax2.bar(['ctrl', 'drugA', 'drugB'], [1.0, 3.0, 4.2], color='0.75')
ax2.plot(['ctrl'], [5.5], 'o', ms=3, mec='none', mfc=(1, 0, 0))
ax2.set_ylim(0, 6.5); ax2.set_ylabel('Response')
figs['P2'] = (fig2, ax2)

for pid, (f, a) in figs.items():
  s = LiveFigureSession(out / pid)
  s.add_figure('F', f, figcapture.SOURCE_SAVEFIG)
  s.instrument_all()
  s.do_render('F', [])
  pts = [dict(q, axes=a.get_gid()) for q in req.get(pid, [])]
  pdf = out / f'{pid}.pdf'
  res = s.do_export('F', [], str(pdf), 'pdf', 300, data_points=pts)
  rows[pid] = {'pdf': pdf.name, 'axes': a.get_gid(), 'requests': pts,
               'data_points': res['data_points'], 'draw_manifest': res['draw_manifest']}
  plt.close(f)
print(json.dumps(rows))
"""

AXES = "__axes__"  # 占位：真的 axes gid 由 worker 回来后填上


def _items():
    def data(panel, x, y):
        return {"anchor": {"data": {"panel": panel, "axes": AXES, "x": x, "y": y}}}

    peak = data("P1", 3.2, 1.0)
    return [
        # ① 峰值：箭头从右下 7 mm 处指过来，说明贴在箭头尾巴右边
        {
            "kind": "arrow",
            "name": "peak_arrow",
            "from": {**peak, "offset_mm": [7.0, 3.0]},
            "to": peak,
            "headEnd": "triangle",
            "strokePt": 0.75,
            "color": "#00A000",
        },
        {
            "kind": "text",
            "name": "peak_label",
            "text": "3.2 eV",
            "sizePt": 8,
            "at": {
                "anchor": {"item": {"name": "peak_arrow", "point": "se"}},
                "offset_mm": [0.8, 0.0],
            },
            "attach": "w",
        },
        # ② 显著性括号：drugA 与 drugB 的柱顶之上；星号来自用户
        {
            "kind": "bracket",
            "name": "sig",
            "between": [data("P2", "drugA", 3.0), data("P2", "drugB", 4.2)],
            "gap_mm": 1.5,
            "tick_mm": 1.0,
            "strokePt": 0.6,
        },
        {
            "kind": "text",
            "name": "sig_label",
            "text": "**",
            "provenance": "user",
            "sizePt": 8,
            "at": {"anchor": {"item": {"name": "sig", "point": "n"}}, "offset_mm": [0.0, -0.4]},
            "attach": "s",
        },
        # ④ 子图上方的示意元件，连到峰值
        {
            "kind": "shape",
            "name": "receptor",
            "shape": "ellipse",
            "fill": "#E69F00",
            "strokePt": 0.5,
            "at": {"anchor": {"panel": {"id": "P1", "point": "n"}}, "offset_mm": [0.0, -12.0]},
            "size_mm": [8.0, 4.0],
        },
        {
            "kind": "connector",
            "name": "link",
            "route": "elbow-v",
            "from": {"anchor": {"item": {"name": "receptor", "point": "s"}}},
            "to": peak,
            "strokePt": 0.5,
        },
        # ⑤ 角标：子图框左上角
        {
            "kind": "text",
            "name": "label_a",
            "text": "(a)",
            "bold": True,
            "sizePt": 9,
            "at": {"anchor": {"panel": {"id": "P1", "point": "nw"}}, "offset_mm": [0.6, 0.6]},
            "attach": "nw",
        },
        # 右图缩放 0.8 后纵轴顶端的刻度贴着左上角（放里面会压字，核对拦过）：放在子图框正上方
        {
            "kind": "text",
            "name": "label_b",
            "text": "(b)",
            "bold": True,
            "sizePt": 9,
            "at": {"anchor": {"panel": {"id": "P2", "point": "nw"}}, "offset_mm": [0.0, -0.6]},
            "attach": "sw",
        },
        # ③ 虚线框框住两张子图和它们的角标（只框子图时框线会穿过放在上方的 (b)，核对拦过）
        {
            "kind": "box",
            "name": "frame",
            "fit": [
                {"panel": {"id": "P1"}},
                {"panel": {"id": "P2"}},
                {"item": {"name": "label_a"}},
                {"item": {"name": "label_b"}},
            ],
            "padding_mm": 2.0,
            "dash": "dashed",
            "strokePt": 0.5,
        },
        # 对照：两处孤立红点上各叠一个蓝色圆环（故意圈住数据点）
        {
            "kind": "shape",
            "name": "ring_1",
            "shape": "ellipse",
            "fill": None,
            "color": "#0000FF",
            "strokePt": 0.5,
            "at": data("P1", 5.0, 0.6),
            "size_mm": [2.6, 2.6],
            "over_data": True,
        },
        {
            "kind": "shape",
            "name": "ring_2",
            "shape": "ellipse",
            "fill": None,
            "color": "#0000FF",
            "strokePt": 0.5,
            "at": data("P2", "ctrl", 5.5),
            "size_mm": [2.6, 2.6],
            "over_data": True,
        },
    ]


def _fill_axes(items, axes):
    out = json.loads(json.dumps(items))

    def walk(v):
        if isinstance(v, dict):
            d = v.get("data")
            if isinstance(d, dict) and d.get("axes") == AXES:
                d["axes"] = axes[d["panel"]]
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(out)
    return out


def test_five_real_tasks_end_to_end(tmp_path):
    from PIL import Image

    from tavotto.rendercore import facade, pdfwriter, plan, sources

    items = _items()
    req = {
        pid: [{"x": q["x"], "y": q["y"]} for q in qs]
        for pid, qs in vectorscene.required_data_points(items).items()
    }
    proc = subprocess.run(
        [WORKER_PY, "-c", DRIVER, str(Path(pool.__file__).parent), str(tmp_path), json.dumps(req)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    rows = json.loads(proc.stdout.strip().splitlines()[-1])
    items = _fill_axes(items, {pid: r["axes"] for pid, r in rows.items()})

    page = {"w": 180.0, "h": 90.0}
    w1, h1 = rows["P1"]["draw_manifest"]["size_mm"]
    w2, h2 = rows["P2"]["draw_manifest"]["size_mm"]
    placed = {
        "P1": {"x": 8.0, "y": 26.0, "w": w1, "h": h1},
        "P2": {"x": 8.0 + w1 + 6.0, "y": 26.0, "w": w2 * 0.8, "h": h2 * 0.8},  # 缩放 0.8
    }
    panels = {
        pid: {
            "panel": placed[pid],
            "draw_manifest": r["draw_manifest"],
            "requests": r["requests"],
            "data_points": r["data_points"],
        }
        for pid, r in rows.items()
    }
    solved = vectorscene.solve(items, panels)
    assert solved["ok"], solved["errors"]
    checked = agentobjects.validate_new_objects(solved["objects"])
    assert checked["ok"], checked["errors"]
    profile = profiles.load(
        "journal-base-v1", journal={"min_line_width_pt": 0.25, "min_effective_font_size_pt": 6}
    )
    verdict = vectorcheck.check(
        solved, checked["objects"], panels, items, page=page, profile=profile
    )
    bad = [f for f in verdict["facts"] if f["status"] in ("fail", "unknown")]
    assert verdict["ok"] and verdict["verified"], bad
    assert {f["check"] for f in verdict["facts"]} >= {
        "anchor",
        "data",
        "text",
        "page",
        "journal",
        "fit",
    }

    # 真画布合成：两张导出的 PDF 当子图源，Agent 的对象叠在上面
    objects = [
        {
            "type": "panel",
            "id": rows[pid]["pdf"],
            "x_mm": p["x"],
            "y_mm": p["y"],
            "w_mm": p["w"],
            "h_mm": p["h"],
        }
        for pid, p in placed.items()
    ] + agentobjects.to_export_objects(checked["objects"])
    provider = facade.provider()
    compiled = plan.compile_page(
        page["w"],
        page["h"],
        objects,
        sources=sources.StaticSourceResolver(tmp_path),
        faces=provider,
    )
    assert compiled.problems == ()
    out = tmp_path / "canvas.pdf"
    pdfwriter.write_pdf(
        compiled.page,
        out,
        provider,
        {k: sources.read_frozen(fs) for k, fs in compiled.sources.items()},
    )
    png = tmp_path / "canvas.png"
    facade.render_preview_png(out, 3000, png)
    img = np.asarray(Image.open(io.BytesIO(png.read_bytes())).convert("RGB")).astype(int)
    px_per_mm = img.shape[1] / page["w"]
    red = (img[:, :, 0] > 200) & (img[:, :, 1] < 60) & (img[:, :, 2] < 60)
    blue = (img[:, :, 2] > 200) & (img[:, :, 0] < 60) & (img[:, :, 1] < 60)
    by_id = dict(zip([o["id"] for o in solved["objects"]], checked["objects"]))
    for ring_name in ("ring_1", "ring_2"):
        ring = by_id[ring_name]
        c = np.array([ring["x"] + ring["w"] / 2, ring["y"] + ring["h"] / 2]) * px_per_mm
        win = 2.0 * px_per_mm
        ys, xs = np.where(red)
        near = np.hypot(xs + 0.5 - c[0], ys + 0.5 - c[1]) <= win
        assert near.any(), ring_name
        red_c = np.array([(xs[near] + 0.5).mean(), (ys[near] + 0.5).mean()])
        ys, xs = np.where(blue)
        near = np.hypot(xs + 0.5 - c[0], ys + 0.5 - c[1]) <= win
        blue_c = np.array([(xs[near] + 0.5).mean(), (ys[near] + 0.5).mean()])
        miss_mm = float(np.hypot(*(blue_c - red_c))) / px_per_mm
        assert miss_mm <= 0.5 / px_per_mm + 0.1 * 25.4 / 72, (ring_name, miss_mm)
