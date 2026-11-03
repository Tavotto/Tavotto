"""FigureBench 夹具探针：在装了 matplotlib 的解释器里跑夹具脚本，读出客观事实。

**不是产品代码，也不是 TAF 验证器**：它只给夹具的真值、参考解与反例背书（夹具的真值
不能由被测产品来背书，与 `tests/fixtures/foundation/` 同一条纪律）。Phase 2 的
Figure Verifier 走 Tavotto 自己的 manifest / preflight / inspector；到时以这里的事实
做对拍，两边不一致说明有一边量错了主语。

用法（只由 `tests/test_figurebench_fixtures.py` 调用）::

    python _probe.py jobs.json out.json

`jobs.json` 是 `[{"id": ..., "script": 绝对路径}]`；每个脚本在**它自己的目录**里跑
（cwd 与 sys.path[0] 都是脚本目录，与 `python figure.py` 一致），调用方负责先把夹具
复制进临时目录。所有作业在一个进程里串行跑，作业之间 `plt.close("all")`。
"""

from __future__ import annotations

import hashlib
import json
import os
import runpy
import sys
import warnings

os.environ["MPLBACKEND"] = "Agg"
# 本机字体缺 CJK 字形是 L4（RenderCore 批准字体 / glyphplan）的事，不是探针要量的事实
warnings.filterwarnings("ignore", message="Glyph .* missing from font")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import colors as mcolors  # noqa: E402
from matplotlib.container import BarContainer, ErrorbarContainer  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.legend import Legend  # noqa: E402

_LOC_NAMES = {code: name for name, code in Legend.codes.items()}
_CAPTURED: list[Figure] = []
_ORIGINAL_SAVEFIG = Figure.savefig


def _spy_savefig(self, *args, **kwargs):
    _CAPTURED.append(self)
    return _ORIGINAL_SAVEFIG(self, *args, **kwargs)


Figure.savefig = _spy_savefig


def digest(values) -> dict:
    """数值数组的规范化摘要：形状 + 小端 float64 字节（NaN / 掩码值统一成同一个 NaN 位型）的 sha256。

    与产品侧 `engine/manifest.py::canonical_digest`、`tests/test_figurebench_fixtures.py::_digest` 逐字同一规则——
    后者用标准库从 truth.json 的数字算，这边从原生 matplotlib 的 artist 读出来算，产品侧从 Tavotto worker 的 manifest
    算；三者相等才说明画的就是真值、量的也是同一个东西。"""
    arr = np.ma.filled(np.ma.asarray(values, dtype=float), np.nan).astype("<f8", copy=True)
    arr[np.isnan(arr)] = np.nan
    head = ("x".join(str(n) for n in arr.shape) + ":").encode()
    return {
        "sha256": hashlib.sha256(head + arr.tobytes(order="C")).hexdigest(),
        "shape": list(arr.shape),
    }


def _bbox(artist, fig, renderer) -> list[float]:
    bb = artist.get_window_extent(renderer).transformed(fig.transFigure.inverted())
    return [round(v, 5) for v in (bb.x0, bb.y0, bb.x1, bb.y1)]


def _text(t, fig, renderer) -> dict:
    out = {"text": t.get_text(), "fontsize": round(float(t.get_fontsize()), 4)}
    out["bbox"] = _bbox(t, fig, renderer) if t.get_text() else None
    return out


def _axes_facts(ax, index: int, all_axes, fig, renderer) -> dict:
    lines = []
    for line in ax.get_lines():
        label = line.get_label()
        if label.startswith("_"):
            continue
        lines.append(
            {
                "label": label,
                "color": mcolors.to_hex(line.get_color()),
                "linewidth": round(float(line.get_linewidth()), 4),
                "linestyle": line.get_linestyle(),
                "markersize": round(float(line.get_markersize()), 4),
                "data": digest(line.get_xydata()),
            }
        )
    errorbars = []
    for c in ax.containers:
        if not isinstance(c, ErrorbarContainer):
            continue
        cols = c.lines[2]
        segs = [np.asarray(s, dtype=float) for col in cols for s in col.get_segments()]
        errorbars.append(
            {
                "points": digest(c.lines[0].get_xydata()),
                "linewidth": round(float(cols[0].get_linewidths()[0]), 4) if cols else None,
                "data": digest(np.concatenate(segs) if segs else np.zeros((0, 2))),
            }
        )
    bars = []
    for c in ax.containers:
        if not isinstance(c, BarContainer):
            continue
        patches = list(c.patches)
        bars.append(
            {
                "label": c.get_label(),
                "count": len(patches),
                "color": mcolors.to_hex(patches[0].get_facecolor()) if patches else None,
                "width": round(float(patches[0].get_width()), 4) if patches else None,
                "heights": digest([p.get_height() for p in patches]),
            }
        )
    images = [
        {
            "cmap": im.get_cmap().name,
            "data": digest(im.get_array()),
            "clim": [float(v) for v in im.get_clim()],
        }
        for im in ax.images
    ]
    legend = ax.get_legend()
    loc = getattr(legend, "_loc", None) if legend is not None else None
    shared_x = [
        j
        for j, other in enumerate(all_axes)
        if j != index and ax.get_shared_x_axes().joined(ax, other)
    ]
    return {
        "title": _text(ax.title, fig, renderer),
        "xlabel": _text(ax.xaxis.label, fig, renderer),
        "ylabel": _text(ax.yaxis.label, fig, renderer),
        "xscale": ax.get_xscale(),
        "yscale": ax.get_yscale(),
        "xlim": [float(v) for v in ax.get_xlim()],
        "ylim": [float(v) for v in ax.get_ylim()],
        "shared_x": shared_x,
        "lines": lines,
        "errorbars": errorbars,
        "bars": bars,
        "images": images,
        "legend": None if legend is None else {"loc": _LOC_NAMES.get(loc, loc)},
    }


def figure_facts(fig) -> dict:
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    axes = list(fig.axes)
    w, h = fig.get_size_inches()
    return {
        "size_in": [round(float(w), 4), round(float(h), 4)],
        "axes": [_axes_facts(ax, i, axes, fig, renderer) for i, ax in enumerate(axes)],
    }


def run_job(script: str) -> dict:
    _CAPTURED.clear()
    here = os.path.dirname(script)
    old_cwd, old_path = os.getcwd(), list(sys.path)
    os.chdir(here)
    sys.path.insert(0, here)
    try:
        runpy.run_path(script, run_name="__main__")
        if not _CAPTURED:
            return {"ok": False, "error": "no_savefig"}
        return {"ok": True, "facts": figure_facts(_CAPTURED[-1])}
    except Exception as exc:  # noqa: BLE001 —— 夹具变体会故意写坏，错误本身就是事实
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        os.chdir(old_cwd)
        sys.path[:] = old_path
        plt.close("all")


def main(argv: list[str]) -> int:
    jobs = json.loads(open(argv[1], encoding="utf-8").read())
    results = {job["id"]: run_job(job["script"]) for job in jobs}
    with open(argv[2], "w", encoding="utf-8") as fh:
        json.dump(
            {"matplotlib": matplotlib.__version__, "results": results}, fh, ensure_ascii=False
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
