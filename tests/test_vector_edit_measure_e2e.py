"""桌面改图助手量子图的端到端（ADR 0208 第 ③ 期 3b）：真 Flask 端点 + 真桌面引擎会话 + 真 matplotlib worker。

量法是桌面引擎会话的 `export(..., data_points=…)`（`app._vector_measure_panel`），与插件 `read_scene` 同一套 manifest / 数据点；
这里验的是**整条链**：快照里子图的 `fileId` / `overrides`（没存盘的覆盖项）→ 量 → 求解 → 新对象的箭头尖，
对照**独立的**真值——同一份覆盖项下 worker 导出的 PDF 里 matplotlib 自己画的纯红点（`test_vectorscene_e2e_support.red_marker_center_mm`，
读 PDF 内容流，不经过任何换算代码），差 ≤ 0.005 mm。另验：不可量（冻结 / 没脚本 / runtime）静默退外框并带原因码。"""

from __future__ import annotations

import json
import math
import subprocess
import tempfile
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import pool, project_watch, vectormeasure
from test_vectorscene_e2e_support import red_marker_center_mm

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None
pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="matplotlib worker unavailable")

SCRIPT = '''\
"""合成子图：80 × 60 mm，一条曲线 + 在 (10, y(10)) 处一个纯红点（对照用）。"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parent
PEAK = (10.0, 1 - np.exp(-10 / 6))


def main():
    x = np.linspace(0, 10, 12)
    fig = plt.figure(figsize=(80 / 25.4, 60 / 25.4))
    ax = fig.add_axes([0.15, 0.15, 0.8, 0.75])
    ax.plot(x, 1 - np.exp(-x / 6), lw=1.0, color="0.5", label="Sample B")
    ax.plot([PEAK[0]], [PEAK[1]], "o", ms=3, mec="none", mfc=(1, 0, 0), zorder=10)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 1.1)
    ax.set_xlabel("Time (min)")
    fig.savefig(OUT / "FigM.pdf")


if __name__ == "__main__":
    main()
'''
REGISTRY = {"scripts": {"figm.py": {"entry": "main", "cost": "light", "stems": ["FigM"]}}}
PEAK_XY = (10.0, 1 - math.exp(-10 / 6))
OVERRIDE = [{"gid": "axes_0", "prop": "position", "value": [0.3, 0.25, 0.6, 0.6]}]


@pytest.fixture
def project(tmp_path, monkeypatch):
    m.reset_projects()
    root = tmp_path / "project"
    root.mkdir()
    (root / "figm.py").write_text(SCRIPT, encoding="utf-8")
    (root / "tavotto_registry.json").write_text(json.dumps(REGISTRY), encoding="utf-8")
    proc = subprocess.run(
        [WORKER_PY, str(root / "figm.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=root,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", WORKER_PY)
    m.open_project(str(root))
    vectormeasure.CACHE.clear()
    try:
        yield m.app.test_client(), root
    finally:
        pool.shutdown_all(figures_dir=str(root), wait=True)
        m.reset_projects()
        project_watch.stop()


def _panel(**over):
    return {
        "id": "o_panel001",
        "type": "panel",
        "x": 10.0,
        "y": 20.0,
        "w": 80.0,
        "h": 60.0,
        "fileId": "FigM.pdf",
        "fileKind": "pdf",
        **over,
    }


def _canvas(panel):
    return {"id": "c1", "page": {"w": 180, "h": 100}, "objects": [panel]}


PEAK_ARROW = {
    "kind": "arrow",
    "name": "peak",
    "from": {
        "anchor": {"data": {"panel": "P1", "axes": "axes_0", "x": 10.0, "y": PEAK_XY[1]}},
        "offset_mm": [-12, -10],
    },
    "to": {"anchor": {"data": {"panel": "P1", "axes": "axes_0", "x": 10.0, "y": PEAK_XY[1]}}},
}
FRAME = {"kind": "box", "name": "frame", "fit": [{"panel": {"id": "P1"}}]}


def _tip(o):
    p = o["start"] if o.get("head") == "start" else o["end"]
    return o["x"] + p["rx"] * o["w"], o["y"] + p["ry"] * o["h"]


def _truth(overrides):
    """独立真值：同一份覆盖项下 worker 导出的 PDF 里红点的中心（画布毫米 = 子图位置 + 图自己的毫米）。"""
    with _bound():
        worker, stem = m._engine_worker("FigM.pdf", safe_only=True)  # noqa: SLF001
        out = Path(tempfile.mkdtemp()) / "truth.pdf"
        worker.export(stem, overrides, str(out), "pdf", 300)
    cx, cy = red_marker_center_mm(out)
    return 10.0 + cx, 20.0 + cy


def _bound():
    return m.bound_project(m.current_ctx())


@pytest.mark.parametrize("overrides", [[], OVERRIDE], ids=["no-override", "unsaved-override"])
def test_the_arrow_tip_lands_on_the_red_marker_even_with_unsaved_overrides(project, overrides):
    client, _root = project
    r = client.post(
        "/api/vector-edit/plan",
        json={
            "canvas": _canvas(_panel(overrides=overrides)),
            "ops": [{"op": "add", "items": [PEAK_ARROW]}],
        },
    ).get_json()
    assert r["errors"] in ([], [{"code": "checks_failed"}]), r
    assert r["unmeasured"] == {}
    arrow = next(o for o in r["objects"] if o["type"] == "arrow")
    tx, ty = _tip(arrow)
    ex, ey = _truth(overrides)
    assert abs(tx - ex) <= 0.005 and abs(ty - ey) <= 0.005, ((tx, ty), (ex, ey))


def test_the_override_really_moves_the_point(project):
    client, _root = project
    a = _truth([])
    b = _truth(OVERRIDE)
    assert abs(a[0] - b[0]) > 1.0  # 覆盖项把坐标轴挪了：不带它量会差出 mm 级


def test_an_unmeasurable_panel_falls_back_to_the_frame_with_a_reason(project):
    client, _root = project
    for panel, code in (
        (_panel(figureSnapshot=True), "composed_from_snapshot"),
        (_panel(fileId="ghost.pdf"), "no_script"),
        (_panel(fileKind="runtime", source=True), "runtime_panel"),
    ):
        peak = client.post(
            "/api/vector-edit/plan",
            json={"canvas": _canvas(panel), "ops": [{"op": "add", "items": [PEAK_ARROW]}]},
        ).get_json()
        assert peak["ok"] is False and peak["errors"][0]["code"] == "needs_measurement", code
        assert peak["unmeasured"] == {"P1": code}
        frame = client.post(
            "/api/vector-edit/plan",
            json={"canvas": _canvas(panel), "ops": [{"op": "add", "items": [FRAME]}]},
        ).get_json()
        assert frame["ok"] is True and frame["objects"], code  # 外框照样能框


def test_a_slow_measure_times_out_to_the_frame(project, monkeypatch):
    import functools
    import time

    client, _root = project
    monkeypatch.setattr(m, "_vector_measure_panel", lambda panel, requests: time.sleep(3))
    monkeypatch.setattr(
        m.engine_vectormeasure, "bounded", functools.partial(vectormeasure.bounded, per_panel_s=0.3)
    )
    t0 = time.monotonic()
    r = client.post(
        "/api/vector-edit/plan",
        json={"canvas": _canvas(_panel()), "ops": [{"op": "add", "items": [FRAME]}]},
    ).get_json()
    assert time.monotonic() - t0 < 2.5
    assert r["ok"] is True and r["unmeasured"] == {"P1": "measure_timeout"}


def test_the_second_plan_does_not_export_again(project, monkeypatch):
    client, _root = project
    n = {"i": 0}
    real = m._vector_measure_panel

    def counting(panel, requests):
        n["i"] += 1
        return real(panel, requests)

    monkeypatch.setattr(m, "_vector_measure_panel", counting)
    body = {"canvas": _canvas(_panel()), "ops": [{"op": "add", "items": [PEAK_ARROW]}]}
    client.post("/api/vector-edit/plan", json=body)
    first = n["i"]
    assert first >= 1
    client.post("/api/vector-edit/plan", json=body)  # 逐条勾选后重算：命中缓存
    assert n["i"] == first


# ------------------------------------------------------------------ 数据文件变了、脚本没变：量要跟着新数据走

DATA_SCRIPT = '''\
"""读 data.csv 的曲线（y 轴自动缩放）+ 固定在数据坐标 (10, 1.0) 的纯红点：数据变了，这个点在页面上的位置跟着变。"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent


def main():
    y = np.loadtxt(HERE / "data.csv")
    fig = plt.figure(figsize=(80 / 25.4, 60 / 25.4))
    ax = fig.add_axes([0.15, 0.15, 0.8, 0.75])
    ax.plot(np.linspace(0, 10, len(y)), y, lw=1.0, color="0.5")
    ax.plot([10.0], [1.0], "o", ms=3, mec="none", mfc=(1, 0, 0), zorder=10)
    fig.savefig(HERE / "FigM.pdf")


if __name__ == "__main__":
    main()
'''
MARKER_ARROW = {
    "kind": "arrow",
    "name": "to_marker",
    "from": {
        "anchor": {"data": {"panel": "P1", "axes": "axes_0", "x": 10.0, "y": 1.0}},
        "offset_mm": [-12, -10],
    },
    "to": {"anchor": {"data": {"panel": "P1", "axes": "axes_0", "x": 10.0, "y": 1.0}}},
}


def _run_script(root: Path) -> None:
    proc = subprocess.run(
        [WORKER_PY, str(root / "figm.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=root,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]


@pytest.fixture
def data_project(tmp_path, monkeypatch):
    m.reset_projects()
    root = tmp_path / "project"
    root.mkdir()
    (root / "figm.py").write_text(DATA_SCRIPT, encoding="utf-8")
    (root / "data.csv").write_text("\n".join(["0.2", "0.5", "0.8", "1.0"]), encoding="utf-8")
    (root / "tavotto_registry.json").write_text(json.dumps(REGISTRY), encoding="utf-8")
    _run_script(root)
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", WORKER_PY)
    m.open_project(str(root))
    vectormeasure.CACHE.clear()
    m._MEASURED_INPUTS.clear()  # noqa: SLF001
    try:
        yield m.app.test_client(), root
    finally:
        pool.shutdown_all(figures_dir=str(root), wait=True)
        m.reset_projects()
        project_watch.stop()


def _plan_tip(client):
    body = {"canvas": _canvas(_panel()), "ops": [{"op": "add", "items": [MARKER_ARROW]}]}
    r = client.post("/api/vector-edit/plan", json=body).get_json()
    assert r["unmeasured"] == {} and r["objects"], r
    return _tip(next(o for o in r["objects"] if o["type"] == "arrow"))


def _marker_on_page(root: Path):
    cx, cy = red_marker_center_mm(root / "FigM.pdf")
    return 10.0 + cx, 20.0 + cy


def test_changing_the_data_file_re_measures_and_the_anchor_follows_the_new_data(data_project):
    client, root = data_project
    first = _plan_tip(client)
    ex, ey = _marker_on_page(root)
    assert abs(first[0] - ex) <= 0.005 and abs(first[1] - ey) <= 0.005
    # 脚本没动，数据变了（y 轴范围跟着变）；用户重跑脚本后磁盘上的图是新的
    (root / "data.csv").write_text("\n".join(["0.2", "1.5", "2.5", "3.0"]), encoding="utf-8")
    _run_script(root)
    nx, ny = _marker_on_page(root)
    assert abs(ny - ey) > 1.0  # 对照：红点确实挪了
    second = _plan_tip(client)
    assert abs(second[0] - nx) <= 0.005 and abs(second[1] - ny) <= 0.005, (second, (nx, ny))


def test_an_unchanged_project_keeps_hitting_the_cache(data_project, monkeypatch):
    client, _root = data_project
    n = {"i": 0}
    real = m._vector_measure_panel  # noqa: SLF001

    def counting(panel, requests):
        n["i"] += 1
        return real(panel, requests)

    monkeypatch.setattr(m, "_vector_measure_panel", counting)
    _plan_tip(client)
    first = n["i"]
    _plan_tip(client)
    assert first >= 1 and n["i"] == first


def test_without_a_trustworthy_input_fingerprint_nothing_is_cached(data_project, monkeypatch):
    client, _root = data_project
    monkeypatch.setattr(m, "_vector_project_digest", lambda root: None)  # 项目太大 / 读不了
    n = {"i": 0}
    real = m._vector_measure_panel  # noqa: SLF001

    def counting(panel, requests):
        n["i"] += 1
        return real(panel, requests)

    monkeypatch.setattr(m, "_vector_measure_panel", counting)
    _plan_tip(client)
    first = n["i"]
    _plan_tip(client)
    assert n["i"] == 2 * first  # 拿不到输入指纹的图不缓存
