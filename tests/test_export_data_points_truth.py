"""`export(..., data_points=…)` 量的那次绘制 = 成品真正用的那份（ADR 0208 §二）。真池子、真脚本，三种布局。

画布合成时，没改动的子图取**磁盘上脚本自己存的原件**，有改动的由 worker 现画（`rendercore.sources`）。
所以要证三件事（2026-11-24 实测都在 0.004 mm 以内，0.1 pt = 0.035 mm）：
1. 换算出的数据点 = 这次导出 PDF 里 matplotlib 实画的点；
2. 这次导出 = 脚本自己 `savefig(bbox_inches="tight")` 存下的原件（没改动的子图合成时用它）；
3. 连续导出（中间夹一次预览）稳定；带改动导出时，数据点 = 那份带改动的 PDF 里的点。
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import pool, workerd_client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_vectorscene_e2e_support import red_marker_center_mm  # noqa: E402

TOL_MM = 0.1 * 25.4 / 72

SCRIPT = """\
import matplotlib.pyplot as plt, numpy as np

def main():
    fig, (a, b) = plt.subplots(1, 2, figsize=(6, 3), layout={layout})
    x = np.linspace(0, 6, 200)
    a.plot(x, np.exp(-(x - 3.2) ** 2 / 0.3), color="0.6")
    a.set_ylabel("Intensity (a.u.) long label")
    a.set_title("Spectrum")
    b.semilogy([1, 10, 100], [1, 100, 1e4])
    a.plot([5.0], [0.6], "o", ms=3, mec="none", mfc=(1, 0, 0))
    fig.savefig("Fig.pdf", bbox_inches="tight")
"""


@pytest.fixture
def python_plane(monkeypatch):
    monkeypatch.setenv("TAVOTTO_WORKERD", "0")
    workerd_client.reset_client()
    yield
    workerd_client.reset_client()


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


@pytest.mark.parametrize("layout", ["None", "'tight'", "'constrained'"])
def test_measured_draw_is_the_artifact_the_canvas_uses(layout, tmp_path, python_plane):
    try:
        py = pool.find_worker_python()
    except pool.WorkerError:
        pytest.skip("matplotlib worker unavailable")
    d = tmp_path / "figs"
    d.mkdir()
    (d / "fig.py").write_text(SCRIPT.format(layout=layout), encoding="utf-8")
    # 磁盘原件：用户自己跑脚本存下的那份
    subprocess.run(
        [py, "-c", "import matplotlib; matplotlib.use('Agg'); import fig; fig.main()"],
        cwd=d,
        check=True,
    )
    w = pool.get("fig.py", str(d), "main")
    try:
        w.ensure_built()
        probe = w.export("Fig", [], str(tmp_path / "probe.pdf"), "pdf", 300, data_points=[])
        man = probe["draw_manifest"]
        axes = next(e for e in man["elements"] if e["role"] == "axes")["gid"]
        title = next(e for e in man["elements"] if e["role"] == "title")["gid"]
        q = [{"axes": axes, "x": 5.0, "y": 0.6}]
        w_mm, h_mm = man["size_mm"]

        def point(res):
            x, y = res["data_points"][0]["xy"]
            return (x * w_mm, y * h_mm)

        r1 = w.export("Fig", [], str(tmp_path / "e1.pdf"), "pdf", 300, data_points=q)
        w.preview_png_snapshot("Fig", [], 600)
        r2 = w.export("Fig", [], str(tmp_path / "e2.pdf"), "pdf", 300, data_points=q)
        red1 = red_marker_center_mm(tmp_path / "e1.pdf")
        assert _dist(point(r1), red1) <= TOL_MM
        assert _dist(point(r1), point(r2)) <= TOL_MM
        assert _dist(red_marker_center_mm(d / "Fig.pdf"), red1) <= TOL_MM
        # 带改动（标题字号变大会让 constrained / tight 重排）：数据点 = 那份带改动的 PDF 里的点
        patch = [{"gid": title, "prop": "fontsize", "value": 16}]
        r3 = w.export("Fig", patch, str(tmp_path / "e3.pdf"), "pdf", 300, data_points=q)
        m3 = r3["draw_manifest"]["size_mm"]
        x3, y3 = r3["data_points"][0]["xy"]
        assert _dist((x3 * m3[0], y3 * m3[1]), red_marker_center_mm(tmp_path / "e3.pdf")) <= TOL_MM
    finally:
        pool.shutdown_all(figures_dir=str(d), wait=True)


def test_data_points_refuse_the_paired_svg_capture(tmp_path, python_plane):
    d = tmp_path / "figs"
    d.mkdir()
    (d / "fig.py").write_text(SCRIPT.format(layout="None"), encoding="utf-8")
    w = pool.get("fig.py", str(d), "main")
    try:
        w.ensure_built()
        with pytest.raises(pool.WorkerError) as exc:
            w.export(
                "Fig",
                [],
                str(tmp_path / "x.pdf"),
                "pdf",
                300,
                svg_path=str(tmp_path / "x.svg"),
                data_points=[],
            )
        assert getattr(exc.value, "code", None) == "bad_request"
    finally:
        pool.shutdown_all(figures_dir=str(d), wait=True)
