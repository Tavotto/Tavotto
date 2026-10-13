"""逐图隔离的失败事实要穿过 safe 控制面（P08a，Codex #978）。

一个脚本出一张健康图 + 一张预览捕获失败的图：probe / preparation 的结果必须带出失败事实
（`partial` + `failed_figures`），登记不能把失败那张图此前的登记整条替换掉。
"""

from __future__ import annotations

import pytest

from tavotto.engine import discover, pool as engine_pool, preparation, probe as engine_probe

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(WORKER_PY is None, reason="找不到装有 matplotlib 的解释器")

PARTIAL = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.artist import Artist


class AggOnly(Artist):
    def draw(self, renderer):
        renderer.buffer_rgba()


good, ax = plt.subplots()
ax.plot([0, 1], [1, 2])
good.savefig("good.png")
bad, ax2 = plt.subplots()
ax2.add_artist(AggOnly())
bad.savefig("bad.png")
"""


@needs_worker
def test_probe_carries_the_partial_capture_and_registration_keeps_the_failed_stem(tmp_path):
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "s.py").write_text(PARTIAL, encoding="utf-8")
    # 此前登记过 bad（例如静态扫描 / 上一次完整运行）
    discover.register(figs, "s.py", ["good", "bad"], entry="__main__")
    try:
        result = engine_probe.probe_and_register(figs, "s.py")
    finally:
        engine_pool.shutdown_all() if hasattr(engine_pool, "shutdown_all") else None
    assert result["stems"] == ["good"], result
    assert result.get("partial") is True
    assert [f["stem"] for f in result["failed_figures"]] == ["bad"]
    assert result["failed_figures"][0]["code"] == "preview_capture_failed"
    assert result["registered"] is True
    # 部分成功不能把失败那张图此前的登记整条替换掉
    assert sorted(discover.registered_stems(figs, "s.py")) == ["bad", "good"]


def test_a_complete_probe_result_has_no_partial_marker():
    assert engine_probe._partial_fields({"stems": {"a": {}}}) == {}
    assert engine_probe._partial_fields({"failed_figures": []}) == {}


def test_register_probed_without_failures_still_replaces_the_scripts_stems(tmp_path):
    """对照：没有失败事实时行为不变——这次没产出的图名仍被整条替换。"""
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "s.py").write_text("print()\n", encoding="utf-8")
    discover.register(figs, "s.py", ["good", "gone"], entry="main")
    engine_probe.register_probed(figs, "s.py", {"entry": "main", "stems": ["good"]})
    assert discover.registered_stems(figs, "s.py") == ["good"]


def test_preparation_captured_of_carries_failed_figures():
    resp = {
        "stems": {"good": {}},
        "descriptors": [],
        "failed_figures": [{"stem": "bad", "code": "preview_capture_failed", "message": "x"}],
    }
    captured = preparation._captured_of(object(), resp)
    assert captured["stems"] == ["good"]
    assert captured["partial"] is True
    assert [f["stem"] for f in captured["failed_figures"]] == ["bad"]
    assert "partial" not in (preparation._captured_of(object(), {"stems": {"g": {}}}) or {})


def test_the_finalizer_registers_failed_stems_too_and_reports_partial(tmp_path):
    from tavotto import app as m  # noqa: PLC0415

    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "s.py").write_text("print()\n", encoding="utf-8")
    discover.register(figs, "s.py", ["good", "bad"], entry="main")

    class Ctx:
        path = str(figs)

    class Plan:
        script = "s.py"
        entry = "main"
        run = None

    class Res:
        captured = {
            "stems": ["good"],
            "descriptors": [],
            "remap_generation": None,
            "partial": True,
            "failed_figures": [{"stem": "bad", "code": "preview_capture_failed", "message": "m"}],
        }

    import types  # noqa: PLC0415

    mp = pytest.MonkeyPatch()
    mp.setattr(m, "_materialize_runtime", lambda *a, **k: None)
    mp.setattr(m, "refresh_project", lambda *a, **k: None)
    mp.setattr(m, "_plan_pin", lambda plan: None)
    mp.setattr(m.engine_runconfig, "set_default", lambda *a, **k: None)
    try:
        out = m._finalize_script_attempt(
            Ctx(), Plan(), types.SimpleNamespace(captured=Res.captured)
        )
    finally:
        mp.undo()
    assert out["registered"] is True and out["partial"] is True
    assert out["failed_figures"] == [{"stem": "bad", "code": "preview_capture_failed"}]
    assert sorted(discover.registered_stems(figs, "s.py")) == ["bad", "good"]
