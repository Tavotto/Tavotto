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

    import threading

    client, _root = project
    release = threading.Event()  # 可唤醒的等待：用例结束就放走在飞的那条 worker，不留到会话末尾
    monkeypatch.setattr(m, "_vector_measure_panel", lambda panel, requests: release.wait(3))
    monkeypatch.setattr(
        m.engine_vectormeasure, "bounded", functools.partial(vectormeasure.bounded, per_panel_s=0.3)
    )
    t0 = time.monotonic()
    r = client.post(
        "/api/vector-edit/plan",
        json={"canvas": _canvas(_panel()), "ops": [{"op": "add", "items": [FRAME]}]},
    ).get_json()
    release.set()
    assert time.monotonic() - t0 < 2.5
    assert r["ok"] is True and r["unmeasured"] == {"P1": "measure_timeout"}
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(
        t.name.startswith("vector-measure") and t.is_alive() for t in threading.enumerate()
    ):
        time.sleep(0.01)


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


# ------------------------------------------------------------------ Codex #2344：热会话吃的是哪份输入，我们不知道


def test_a_hot_session_built_on_the_old_data_does_not_leak_into_the_measure(
    data_project,
):
    client, root = data_project
    _plan_tip(client)  # 把热会话建起来（吃的是旧数据）
    vectormeasure.CACHE.clear()
    (root / "data.csv").write_text("\n".join(["0.2", "1.5", "2.5", "3.0"]), encoding="utf-8")
    _run_script(root)
    nx, ny = _marker_on_page(root)
    tip = _plan_tip(client)  # 第一次请求标注：不能复用旧会话
    assert abs(tip[0] - nx) <= 0.005 and abs(tip[1] - ny) <= 0.005, (tip, (nx, ny))


def test_data_changed_while_the_model_was_thinking_voids_the_plan(data_project, monkeypatch):
    from tavotto.engine import ai_structured, changeplan as cp

    client, root = data_project
    monkeypatch.setattr(cp, "_PRECHECK", lambda cid=None: (True, ""))
    monkeypatch.setattr(cp, "_RESOLVE", lambda budget=None, **kw: ({"kind": "fake"}, "", ""))
    monkeypatch.setattr(cp, "_TASKS", {})
    monkeypatch.setattr(cp, "_BY_FIGURE", {})
    monkeypatch.setattr(cp, "_BY_RID", {})

    def model(**kw):
        # 答题期间用户改了数据并重跑了脚本
        (root / "data.csv").write_text("\n".join(["0.2", "1.5", "2.5", "3.0"]), encoding="utf-8")
        _run_script(root)
        value = kw["validate"]({"ops": [{"op": "add", "items": [MARKER_ARROW]}], "unhandled": []})
        return ai_structured.StructuredResult(True, value=value)

    monkeypatch.setattr(cp, "_CALL", model)
    start = client.post(
        "/api/vector-edit/plan", json={"canvas": _canvas(_panel()), "instruction": "箭头指向红点"}
    ).get_json()
    import time

    end = time.time() + 120
    while time.time() < end:
        body = client.get(f"/api/vector-edit/plan/{start['plan_id']}").get_json()
        if body["state"] != "running":
            break
        time.sleep(0.1)
    assert body["state"] == "failed" and body["code"] == "change_plan_figure_changed", body


def test_each_pass_takes_the_project_state_again():
    calls = []

    def fake(panel, requests):
        return {"draw_manifest": {}, "data_points": []}

    fake.new_pass = lambda: calls.append(1)  # type: ignore[attr-defined]
    vectormeasure.new_pass(fake)
    vectormeasure.new_pass(lambda panel, requests: None)  # 没有钩子的假函数：什么都不做
    assert calls == [1]


def test_an_export_dir_that_cannot_be_removed_is_logged_and_retried_next_time(
    tmp_path, monkeypatch, caplog
):
    import logging
    import shutil

    left = tmp_path / "vector-measure-x"
    left.mkdir()
    (left / "m.pdf").write_bytes(b"%PDF")
    m._VECTOR_MEASURE_LEFTOVERS.clear()  # noqa: SLF001
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    real = shutil.rmtree

    def locked(path, *a, **k):
        raise PermissionError("locked")

    monkeypatch.setattr(m.shutil, "rmtree", locked)
    with caplog.at_level(logging.WARNING):
        assert m._vector_measure_discard(left) is False  # noqa: SLF001
    assert "没删掉" in caplog.text and left in m._VECTOR_MEASURE_LEFTOVERS  # noqa: SLF001
    assert left.exists()  # 没有假装删掉了
    monkeypatch.setattr(m.shutil, "rmtree", real)
    m._vector_measure_sweep()  # noqa: SLF001  下一次量的开头再试
    assert not left.exists() and not m._VECTOR_MEASURE_LEFTOVERS  # noqa: SLF001


class _FakePool:
    """`_VectorFresh` 用的池函数的替身：记下创建 / 收掉了几条一次性 worker，以及有没有碰池里的会话（`invalidate`）。"""

    def __init__(self, monkeypatch):
        self.made, self.discarded, self.invalidated = [], [], []
        monkeypatch.setattr(
            m, "_disk_panel_rel", lambda fid: ("FigM", {"script": "figm.py", "entry": "main"})
        )
        monkeypatch.setattr(m, "_disk_panel_run", lambda script: None)
        monkeypatch.setattr(m, "require_project", lambda: Path("/proj"))
        monkeypatch.setattr(m.engine_pool, "peek", lambda *a, **k: None)
        monkeypatch.setattr(m.engine_pool, "one_shot", self._one_shot)
        monkeypatch.setattr(m.engine_pool, "discard", self.discarded.append)
        monkeypatch.setattr(
            m.engine_pool, "invalidate", lambda *a, **k: self.invalidated.append((a, k))
        )

    def _one_shot(self, script, root, entry, **kw):
        w = object()
        self.made.append(w)
        return w


def test_one_fresh_worker_per_script_is_reused_until_the_project_state_changes(monkeypatch):
    fake = _FakePool(monkeypatch)
    fresh = m._VectorFresh()  # noqa: SLF001
    w1, _ = fresh.worker_for("FigM.pdf", "d1")
    w2, _ = fresh.worker_for("FigM.pdf", "d1")  # 同一份项目状态：同一条
    assert w1 is w2 and len(fake.made) == 1
    w3, _ = fresh.worker_for("FigM.pdf", "d2")  # 模型答题期间数据变了：旧的收掉、换新的
    assert w3 is not w1 and fake.discarded == [w1] and len(fake.made) == 2
    fresh.close()
    assert fake.discarded == [w1, w3]
    assert fake.invalidated == []  # 从头到尾没碰池里（画布在用）的会话
    with pytest.raises(m.engine_layoutannotate.PanelUnavailable):
        fresh.worker_for("FigM.pdf", "d2")  # 收掉之后不再创建


def test_an_unknown_fingerprint_keeps_one_fresh_worker_for_the_whole_plan(monkeypatch):
    fake = _FakePool(monkeypatch)
    fresh = m._VectorFresh()  # noqa: SLF001
    a, _ = fresh.worker_for("FigM.pdf", None)
    b, _ = fresh.worker_for("FigM.pdf", None)
    assert a is b and len(fake.made) == 1


def test_the_fingerprint_is_not_reused_across_passes_but_is_within_one(project, monkeypatch):
    client, _root = project
    taken = []
    _FakePool(monkeypatch)
    monkeypatch.setattr(m, "_vector_project_digest", lambda root: taken.append(1) or "d1")
    monkeypatch.setattr(m, "_vector_measure_key", lambda *a, **k: None)
    monkeypatch.setattr(
        m, "_vector_measure_panel", lambda panel, requests: {"draw_manifest": {}, "data_points": []}
    )
    with m.bound_project(m.current_ctx()):
        measure = m._vector_measure_for(m.current_ctx())  # noqa: SLF001
        panel = _panel()
        measure(panel, [])
        measure(panel, [{"axes": "axes_0", "x": 1, "y": 1}])
        assert len(taken) == 1  # 一轮之内各子图 / 各次共用一次指纹
        measure.new_pass()
        measure(panel, [])
        assert len(taken) == 2  # 新一轮重新取
        measure.close()


def test_measuring_never_kills_a_canvas_render_in_flight(data_project):
    """用户画布在用的热会话不能被量图碰：画布一直在渲染时触发量图，那些渲染一次都不许失败，会话还是同一条。"""
    import threading
    import time

    client, root = data_project
    ctx = m.current_ctx()
    errors, renders, stop = [], [0], threading.Event()
    with m.bound_project(ctx):
        hot, stem = m._engine_worker("FigM.pdf", safe_only=True)  # noqa: SLF001
        hot.ensure_built()

    def canvas():
        with m.bound_project(ctx):
            while not stop.is_set():
                try:
                    w, s = m._engine_worker("FigM.pdf", safe_only=True)  # noqa: SLF001
                    out = Path(tempfile.mkdtemp()) / "c.pdf"
                    value = [0.2 + 0.01 * (renders[0] % 5), 0.2, 0.6, 0.6]
                    w.export(
                        s,
                        [{"gid": "axes_0", "prop": "position", "value": value}],
                        str(out),
                        "pdf",
                        300,
                    )
                    renders[0] += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(repr(exc)[:200])

    thread = threading.Thread(target=canvas)
    thread.start()
    time.sleep(0.5)
    (root / "data.csv").write_text("\n".join(["0.2", "1.5", "2.5", "3.0"]), encoding="utf-8")
    _run_script(root)  # 数据也变了：量图要读到新的，而不是去动画布的会话
    nx, ny = _marker_on_page(root)
    tip = _plan_tip(client)
    time.sleep(1.0)
    stop.set()
    thread.join()
    assert renders[0] >= 5 and not errors, errors
    assert abs(tip[0] - nx) <= 0.005 and abs(tip[1] - ny) <= 0.005
    with m.bound_project(ctx):
        assert m._engine_worker("FigM.pdf", safe_only=True)[0] is hot  # noqa: SLF001  还是原来那条


def test_the_model_step_asks_for_a_fresh_pass_before_the_dry_run():
    from tavotto.engine import ai_structured, changeplan as cp

    seen = []

    def measure(panel, requests):
        raise AssertionError("不该真量")

    measure.new_pass = lambda: seen.append("pass")  # type: ignore[attr-defined]
    orig = cp._structured  # noqa: SLF001
    try:
        cp._structured = lambda system, user, validate, selection, **kw: validate(  # noqa: SLF001
            {"ops": [], "unhandled": []}
        )
        cp.ask_vector_edits(
            "x",
            {"id": "c", "page": {"w": 10, "h": 10}, "objects": []},
            [],
            None,
            None,
            measure=measure,
            measured={"scene": {}, "unmeasured": {}, "fingerprint": "f"},
        )
    finally:
        cp._structured = orig  # noqa: SLF001
    assert seen == ["pass"]
    del ai_structured
