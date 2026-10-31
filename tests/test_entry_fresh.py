"""登记的入口过时（TVD-CHR5）：脚本被改写成顶层直接画图后，注册表里的函数入口要跟着换。

判据与改写的唯一出处是 `engine/entryfresh`；这里钉：
* 判定（函数被删 -> 过时；函数还在 / `__main__` / import 或赋值提供同名 / 解析不了 -> 不动）；
* 改写只动 `entry` 一个字段（stems / cost / notes / artifacts / 手写的其它键原样）；
* 准备会话规划、AI 修复工具 `fix_entry` 与运行报告的 `entry_stale` 都走同一条；
* worker 找不到入口时是闭集码 `script_entry_missing`，不是笼统的 `script_error`；
* 端到端（真 worker）：先带函数登记并跑通，改写成顶层脚本后再跑，自动换成 `__main__` 出图且注册表更新。
"""

import json
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import (
    entryfresh,
    pool as engine_pool,
    prepsession,
    project_watch as engine_watch,
)
from tavotto.engine.fixtools import REGISTRY, FixContext, run_tool
from tavotto.engine.fixtools.runreport import project_run_report

FUNC_SCRIPT = """\
import matplotlib.pyplot as plt


def plot_fig5():
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [3, 1, 2])
    fig.savefig("fig5.pdf")


if __name__ == "__main__":
    plot_fig5()
"""

TOP_LEVEL_SCRIPT = """\
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([1, 2, 3], [3, 1, 2])
fig.savefig("fig5.pdf")
"""

REGISTRY_BODY = {
    "version": 1,
    "scripts": {
        "图片.py": {
            "entry": "plot_fig5",
            "cost": "heavy",
            "notes": "手工裁决",
            "stems": ["fig5"],
            "artifacts": {"fig5.pdf": "fig5"},
            "hand_written": {"keep": True},
        },
        "other.py": {"entry": "main", "stems": ["other"]},
    },
}


def _project(tmp_path, source=FUNC_SCRIPT):
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "图片.py").write_text(source, encoding="utf-8")
    (figs / "other.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(json.dumps(REGISTRY_BODY), encoding="utf-8")
    return figs


def _reg(figs: Path) -> dict:
    return json.loads((figs / "tavotto_registry.json").read_text(encoding="utf-8"))["scripts"]


def _stale(tmp_path, source: str, entry: str) -> bool:
    p = tmp_path / "s.py"
    p.write_text(source, encoding="utf-8")
    return entryfresh.is_stale(p, entry)


class TestStaleness:
    def test_a_deleted_function_is_stale(self, tmp_path):
        assert _stale(tmp_path, TOP_LEVEL_SCRIPT, "plot_fig5") is True

    def test_a_present_function_is_not(self, tmp_path):
        assert _stale(tmp_path, FUNC_SCRIPT, "plot_fig5") is False

    def test_async_def_and_class_count_as_present(self, tmp_path):
        assert _stale(tmp_path, "async def go():\n    pass\n", "go") is False
        assert _stale(tmp_path, "class Go:\n    pass\n", "Go") is False

    def test_inline_main_is_never_stale(self, tmp_path):
        assert _stale(tmp_path, TOP_LEVEL_SCRIPT, "__main__") is False

    def test_a_name_provided_by_import_or_assignment_is_present(self, tmp_path):
        assert _stale(tmp_path, "from helpers import plot_fig5\n", "plot_fig5") is False
        assert _stale(tmp_path, "import helpers as plot_fig5\n", "plot_fig5") is False
        assert _stale(tmp_path, "plot_fig5 = lambda: None\n", "plot_fig5") is False
        assert _stale(tmp_path, "a, plot_fig5 = 1, 2\n", "plot_fig5") is False
        guarded = "try:\n    from h import plot_fig5\nexcept ImportError:\n    plot_fig5 = None\n"
        assert _stale(tmp_path, guarded, "plot_fig5") is False
        assert (
            _stale(tmp_path, "if True:\n    def plot_fig5():\n        pass\n", "plot_fig5") is False
        )

    def test_a_name_only_inside_a_function_body_is_stale(self, tmp_path):
        assert _stale(tmp_path, "def other():\n    plot_fig5 = 1\n", "plot_fig5") is True

    def test_star_import_and_module_getattr_are_undecidable_so_not_stale(self, tmp_path):
        assert _stale(tmp_path, "from helpers import *\n", "plot_fig5") is False
        assert _stale(tmp_path, "def __getattr__(name):\n    return None\n", "plot_fig5") is False

    def test_unparseable_source_is_not_stale(self, tmp_path):
        assert _stale(tmp_path, "def plot_fig5(:\n", "plot_fig5") is False

    def test_unreadable_script_is_not_stale(self, tmp_path):
        assert entryfresh.is_stale(tmp_path / "missing.py", "plot_fig5") is False


class TestReconcile:
    def test_rewrites_only_the_entry_field(self, tmp_path):
        figs = _project(tmp_path, TOP_LEVEL_SCRIPT)
        before = _reg(figs)
        assert entryfresh.reconcile(figs) == {"图片.py": "__main__"}
        after = _reg(figs)
        assert after["图片.py"] == {**before["图片.py"], "entry": "__main__"}
        assert after["other.py"] == before["other.py"]  # 别的脚本一个字节不动

    def test_nothing_to_change_does_not_touch_the_file(self, tmp_path):
        figs = _project(tmp_path, FUNC_SCRIPT)
        path = figs / "tavotto_registry.json"
        before = path.read_bytes()
        mtime = path.stat().st_mtime_ns
        assert entryfresh.reconcile(figs) == {}
        assert path.read_bytes() == before and path.stat().st_mtime_ns == mtime

    def test_unparseable_script_keeps_the_registered_entry(self, tmp_path):
        figs = _project(tmp_path, "def plot_fig5(:\n")
        assert entryfresh.reconcile(figs) == {}
        assert _reg(figs)["图片.py"]["entry"] == "plot_fig5"

    def test_effective_entry_only_corrects_the_registered_one(self, tmp_path):
        figs = _project(tmp_path, TOP_LEVEL_SCRIPT)
        # 调用方显式给的入口（与登记的不同）原样返回，不碰注册表
        assert entryfresh.effective_entry(figs, "图片.py", "something_else") == "something_else"
        assert _reg(figs)["图片.py"]["entry"] == "plot_fig5"
        assert entryfresh.effective_entry(figs, "图片.py", "plot_fig5") == "__main__"
        assert _reg(figs)["图片.py"]["entry"] == "__main__"

    def test_prepsession_plans_with_the_corrected_entry(self, tmp_path, monkeypatch):
        figs = _project(tmp_path, TOP_LEVEL_SCRIPT)
        seen = {}
        monkeypatch.setattr(
            prepsession.preparation, "plan_for", lambda **kw: seen.update(kw) or object()
        )
        target = {"kind": "script", "script": "图片.py", "entry": "plot_fig5"}
        prepsession.SessionService._plan_of("p", str(figs), target)
        assert seen["entry"] == "__main__"

    def test_the_report_names_a_stale_entry(self, tmp_path):
        figs = _project(tmp_path, TOP_LEVEL_SCRIPT)
        ctx = FixContext(session_id="s", project_root=figs, script_path=figs / "图片.py")
        out = project_run_report({"plan": {"entry": "plot_fig5"}, "outcome": {}}, ctx).content
        assert json.loads(out)["entry_stale"]["registered"] == "plot_fig5"
        assert json.loads(out)["entry_stale"]["suggested"] == "__main__"

    def test_fix_entry_tool_uses_the_same_logic(self, tmp_path):
        figs = _project(tmp_path, TOP_LEVEL_SCRIPT)
        called = []
        ctx = FixContext.for_fix_session(
            "s", figs, script_path=figs / "图片.py", on_registry_changed=called.append
        )
        assert REGISTRY["fix_entry"].tier == "tavotto"
        res = run_tool(REGISTRY["fix_entry"], ctx, {})
        assert not res.is_error and "__main__" in res.content
        assert _reg(figs)["图片.py"]["entry"] == "__main__" and called == ["图片.py"]
        again = run_tool(REGISTRY["fix_entry"], ctx, {})
        assert not again.is_error and called == ["图片.py"]  # 幂等：没有再改


try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


@pytest.mark.skipif(WORKER_PY is None, reason="找不到装有 matplotlib 的解释器")
class TestRewrittenScriptEndToEnd:
    def test_rewritten_to_top_level_runs_as_main_and_updates_the_registry(self, client, tmp_path):
        figs = tmp_path / "figs"
        figs.mkdir()
        doc = pymupdf.open()
        doc.new_page(width=100, height=50)
        doc.save(figs / "p1.pdf")
        doc.close()
        (figs / "图片.py").write_text(FUNC_SCRIPT, encoding="utf-8")
        client.post("/api/projects/open", json={"path": str(figs)})
        try:
            probed = client.post("/api/registry/probe", json={"script": "图片.py"}).get_json()
            assert probed["registered"] is True
            assert _reg(figs)["图片.py"]["entry"] != "__main__"
            asset = probed["descriptors"][0]["asset_id"]
            first = client.post("/api/engine/render", json={"id": asset, "patches": []})
            assert first.status_code == 200, first.get_data(as_text=True)

            # 用户把脚本改写成顶层直接画图；应用重启过（旧 worker 不在了）
            engine_pool.shutdown_all(str(figs), wait=True)
            (figs / "图片.py").write_text(TOP_LEVEL_SCRIPT, encoding="utf-8")

            second = client.post("/api/engine/render", json={"id": asset, "patches": []})
            assert second.status_code == 200, second.get_data(as_text=True)
            assert _reg(figs)["图片.py"]["entry"] == "__main__"
        finally:
            engine_pool.shutdown_all(str(figs), wait=True)

    def test_a_missing_entry_is_the_closed_code_not_a_script_error(self, tmp_path):
        figs = _project(tmp_path, TOP_LEVEL_SCRIPT)
        worker = engine_pool.get("图片.py", str(figs), "plot_fig5")
        try:
            with pytest.raises(engine_pool.WorkerError) as caught:
                worker.ensure_built()
            assert (
                caught.value.code == engine_pool.SCRIPT_ENTRY_MISSING_CODE == "script_entry_missing"
            )
        finally:
            engine_pool.shutdown_all(str(figs), wait=True)
