"""Compatibility Bridge Session 3：任意项目内脚本可列出、可 safe probe。

三层看护：

* **脚本清单**（`probe.script_inventory`）：普通 .py 不因静态分析返回 None
  就从产品里消失；每条带稳定 reason code；被 prune 的目录不列；清单放宽
  **不改变**自动静态起草的候选口径。
* **safe probe**：show-only / 守卫 / main / render / 自定义 entry / 多
  Figure / 缺包 / 超时 / stdout 噪音 / 中文与空格路径 / 子目录 helper 全部
  走得通；成功路径只执行一次；失败不写注册表；stem 冲突不静默覆盖。
* **产品 API**（`/api/registry` 与 `/api/registry/probe`）：路径越界 /
  symlink 逃逸 / 非 .py / 目录逐条拒绝且各有稳定 code；show-only 脚本经
  **产品 API** 可探测可登记（负向反证 #1 的看护对象——重新要求静态
  candidate，这里必须红）。

真执行脚本的用例与 `test_compat_capture_parity.py` 同一条纪律：本进程不
import matplotlib，桌面侧经 pool 起真 worker。
"""
import json
import os
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import discover, figcapture, pool as engine_pool, probe as engine_probe

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）")

SHOW_ONLY = '''\
import matplotlib.pyplot as plt

plt.plot([1, 2, 3], [4, 5, 6])
plt.title("AI generated")
plt.show()
'''


def write(figs: Path, name: str, source: str) -> Path:
    p = figs / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(source, encoding="utf-8")
    return p


@pytest.fixture
def figs(tmp_path):
    d = tmp_path / "figs"
    d.mkdir()
    yield d
    engine_pool.shutdown_all(str(d), wait=True)


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_pool.stop_watcher()


# ===========================================================================
# 一、脚本清单：所有合理项目脚本可见
# ===========================================================================
class TestScriptInventory:
    def test_a_plain_py_without_savefig_is_listed(self, figs):
        """show-only 脚本必须出现在清单里——静态解不出产物 ≠ 产品里不存在。"""
        write(figs, "show_only.py", SHOW_ONLY)
        (entry,) = engine_probe.script_inventory(figs)
        assert entry["script"] == "show_only.py"
        assert entry["reason"] == engine_probe.REASON_NO_STATIC_OUTPUT
        assert entry["can_probe"] is True
        assert entry["static_stems"] == []
        # 顶层直接绘图的脚本：静态候选第一个就是 __main__（不必先盲试 main）
        assert entry["entry_candidates"][0] == "__main__"

    def test_reason_codes_cover_every_category(self, figs):
        write(figs, "show_only.py", SHOW_ONLY)
        write(figs, "static.py",
              'import matplotlib.pyplot as plt\n'
              'def main():\n'
              '    fig, ax = plt.subplots()\n'
              '    fig.savefig("Fig1.pdf")\n')
        write(figs, "dynamic.py",
              'import sys\n'
              'import matplotlib.pyplot as plt\n'
              'def main():\n'
              '    fig, ax = plt.subplots()\n'
              '    fig.savefig(sys.argv[0] + ".pdf")\n')
        write(figs, "test_foo.py", "def test_x():\n    pass\n")
        write(figs, "_helper.py", "X = 1\n")
        write(figs, "paper_style.py", "def save(fig, stem):\n    pass\n")
        write(figs, "broken.py", "def broken(:\n")
        by = {e["script"]: e for e in engine_probe.script_inventory(figs)}
        assert by["show_only.py"]["reason"] == engine_probe.REASON_NO_STATIC_OUTPUT
        assert by["static.py"]["reason"] == engine_probe.REASON_STATIC
        assert by["static.py"]["static_stems"] == ["Fig1"]
        assert by["dynamic.py"]["reason"] == engine_probe.REASON_DYNAMIC
        assert by["test_foo.py"]["reason"] == engine_probe.REASON_INFRASTRUCTURE
        assert by["_helper.py"]["reason"] == engine_probe.REASON_INFRASTRUCTURE
        assert by["paper_style.py"]["reason"] == engine_probe.REASON_INFRASTRUCTURE
        assert by["broken.py"]["reason"] == engine_probe.REASON_UNPARSEABLE
        # 清单里每个 .py 都可探测：后端本来就接受任意项目内脚本
        assert all(e["can_probe"] for e in by.values())

    def test_registered_wins_over_other_reasons(self, figs):
        write(figs, "show_only.py", SHOW_ONLY)
        (entry,) = engine_probe.script_inventory(figs, registered={"show_only.py"})
        assert entry["reason"] == engine_probe.REASON_REGISTERED
        assert entry["registered"] is True

    def test_pruned_directories_are_not_listed(self, figs):
        """环境/构建目录整棵剪掉——那不是用户的绘图脚本。"""
        write(figs, "real.py", SHOW_ONLY)
        write(figs, ".venv/lib/junk.py", "x = 1\n")
        write(figs, "node_modules/pkg/idx.py", "x = 1\n")
        write(figs, "build/gen.py", "x = 1\n")
        write(figs, "tavottofile/export/leftover.py", "x = 1\n")
        scripts = {e["script"] for e in engine_probe.script_inventory(figs)}
        assert scripts == {"real.py"}

    def test_subdirectory_scripts_use_posix_relative_paths(self, figs):
        write(figs, "panels/nested.py", SHOW_ONLY)
        scripts = {e["script"] for e in engine_probe.script_inventory(figs)}
        assert "panels/nested.py" in scripts

    def test_inventory_does_not_widen_the_static_draft(self, figs):
        """发现维放宽只影响「列给用户挑」；自动静态起草的口径**一字不变**。

        show-only 与基础设施脚本进清单，但绝不能进 discover 的候选报告——
        那会改变现有项目打开时自动起草的注册表内容。
        """
        write(figs, "show_only.py", SHOW_ONLY)
        write(figs, "test_foo.py",
              'import matplotlib.pyplot as plt\n'
              'def main():\n'
              '    fig, ax = plt.subplots()\n'
              '    fig.savefig("Sneaky.pdf")\n')
        rep = discover.discover(figs)
        assert rep["scripts"] == {}
        listed = {e["script"] for e in engine_probe.script_inventory(figs)}
        assert listed == {"show_only.py", "test_foo.py"}

    def test_entry_candidates_only_include_callable_entries(self, figs):
        """必填参数的 main 不进候选（worker 零参调用必炸，试它白付冷启动）；
        本 Session 刻意不做「自动构造必填参数」。"""
        write(figs, "argmain.py",
              'import matplotlib.pyplot as plt\n'
              'def main(datafile):\n'
              '    plt.plot([1])\n'
              'def draw():\n'
              '    plt.plot([1])\n')
        (entry,) = engine_probe.script_inventory(figs)
        assert "main" not in entry["entry_candidates"]
        assert "draw" in entry["entry_candidates"]


# ===========================================================================
# 二、API：清单可见 + 路径安全边界
# ===========================================================================
def _make_project(tmp_path, name="figs"):
    figs = tmp_path / name
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "p1.pdf")
    doc.close()
    return figs


class TestRegistryApi:
    def test_all_scripts_reaches_the_product_api(self, client, tmp_path):
        figs = _make_project(tmp_path)
        write(figs, "show_only.py", SHOW_ONLY)
        client.post("/api/projects/open", json={"path": str(figs)})
        view = client.get("/api/registry").get_json()
        by = {e["script"]: e for e in view["all_scripts"]}
        assert by["show_only.py"]["reason"] == "no_static_output"
        assert by["show_only.py"]["can_probe"] is True
        # 旧的 candidates 口径不因清单放宽而改变：show-only 不在 candidates 里
        assert all(c["script"] != "show_only.py" for c in view["candidates"])

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlink 语义")
    def test_symlink_escape_is_rejected(self, client, tmp_path):
        """realpath 之后仍要在项目内——symlink 指到项目外必须拒绝
        （负向反证 #2 的看护对象：去掉 realpath 边界，这里必须红）。"""
        figs = _make_project(tmp_path)
        outside = tmp_path / "outside.py"
        outside.write_text(SHOW_ONLY, encoding="utf-8")
        (figs / "evil.py").symlink_to(outside)
        client.post("/api/projects/open", json={"path": str(figs)})
        resp = client.post("/api/registry/probe", json={"script": "evil.py"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "script_path_outside_project"

    def test_directory_and_non_py_are_rejected(self, client, tmp_path):
        figs = _make_project(tmp_path)
        (figs / "dirlike.py").mkdir()
        write(figs, "notes.txt", "hi")
        client.post("/api/projects/open", json={"path": str(figs)})
        for bad in ("dirlike.py", "notes.txt"):
            resp = client.post("/api/registry/probe", json={"script": bad})
            assert resp.status_code == 400, bad
            assert resp.get_json()["code"] == "unsupported_script_type", bad

    def test_dotdot_and_absolute_paths_outside_are_rejected(self, client, tmp_path):
        figs = _make_project(tmp_path)
        outside = tmp_path / "outside.py"
        outside.write_text(SHOW_ONLY, encoding="utf-8")
        client.post("/api/projects/open", json={"path": str(figs)})
        for bad in ("../outside.py", str(outside)):
            resp = client.post("/api/registry/probe", json={"script": bad})
            assert resp.status_code == 400, bad
            assert resp.get_json()["code"] == "script_path_outside_project", bad


@needs_worker
class TestProductApiProbe:
    def test_show_only_script_probes_via_the_product_api(self, client, tmp_path):
        """负向反证 #1 的看护对象：show-only 脚本**经产品 API** 可探测可登记。

        它没有 savefig、不进静态 candidates——重新要求「必须是静态候选」的
        话，这条当场红。
        """
        figs = _make_project(tmp_path)
        write(figs, "show_only.py", SHOW_ONLY)
        client.post("/api/projects/open", json={"path": str(figs)})
        try:
            resp = client.post("/api/registry/probe",
                               json={"script": "show_only.py"})
            assert resp.status_code == 200
            result = resp.get_json()
            assert result["error"] is None
            assert result["registered"] is True
            assert result["stems"] == ["show_only"]
            (d,) = result["descriptors"]
            assert d["asset_id"] == "runtime:show_only.py#show_only"
            assert d["capture_source"] == "pyplot"
            cfg = json.loads((figs / "tavotto_registry.json")
                             .read_text(encoding="utf-8"))
            assert cfg["scripts"]["show_only.py"]["stems"] == ["show_only"]
        finally:
            engine_pool.shutdown_all(str(figs), wait=True)


# ===========================================================================
# 三、entry 选择与错误模型（真 worker）
# ===========================================================================
@needs_worker
class TestEntrySelection:
    def test_toplevel_script_runs_main_module_first(self, figs):
        """裸顶层绘图：静态候选直指 __main__，一次执行就成功——
        以前要先盲试 main/render，同一份脚本白跑两遍。"""
        write(figs, "show_only.py", SHOW_ONLY)
        result = engine_probe.probe(figs, "show_only.py")
        assert result["error"] is None
        assert result["entry"] == "__main__"
        assert result["tried"] == ["__main__"]

    def test_guarded_main_module(self, figs):
        write(figs, "guarded.py",
              'import matplotlib.pyplot as plt\n'
              'def draw():\n'
              '    plt.plot([1, 2, 3])\n'
              'if __name__ == "__main__":\n'
              '    draw()\n')
        result = engine_probe.probe(figs, "guarded.py")
        assert result["error"] is None
        assert result["entry"] == "__main__"

    def test_main_entry(self, figs):
        write(figs, "with_main.py",
              'import matplotlib.pyplot as plt\n'
              'def main():\n'
              '    plt.plot([1, 2, 3])\n')
        result = engine_probe.probe(figs, "with_main.py")
        assert result["error"] is None
        assert result["entry"] == "main"
        assert result["tried"] == ["main"]

    def test_render_entry(self, figs):
        write(figs, "with_render.py",
              'import matplotlib.pyplot as plt\n'
              'def render():\n'
              '    plt.plot([1, 2, 3])\n')
        result = engine_probe.probe(figs, "with_render.py")
        assert result["error"] is None
        assert result["entry"] == "render"

    def test_custom_zero_arg_entry(self, figs):
        """自定义入口：无必填参数且能到达绘图调用的函数会被静态找出来。"""
        write(figs, "custom.py",
              'import matplotlib.pyplot as plt\n'
              'def make_chart(style="default"):\n'
              '    plt.plot([3, 1, 2])\n')
        result = engine_probe.probe(figs, "custom.py")
        assert result["error"] is None
        assert result["entry"] == "make_chart"

    def test_a_failing_entry_does_not_poison_the_next(self, figs):
        """错误 entry 各自新建 worker；成功后 error 归 None。"""
        write(figs, "twostep.py",
              'import matplotlib.pyplot as plt\n'
              'def main():\n'
              '    raise RuntimeError("boom in main")\n'
              'def render():\n'
              '    plt.plot([1, 2, 3])\n')
        result = engine_probe.probe(figs, "twostep.py")
        assert result["error"] is None
        assert result["entry"] == "render"
        assert result["tried"] == ["main", "render"]

    def test_first_error_is_kept_when_all_entries_fail(self, figs):
        """报错保留**第一个**候选的（静态推断的那个，对用户最有解释力）。"""
        write(figs, "allfail.py",
              'import matplotlib.pyplot as plt\n'
              'def main():\n'
              '    raise RuntimeError("first boom")\n'
              'def render():\n'
              '    raise RuntimeError("second boom")\n')
        result = engine_probe.probe(figs, "allfail.py")
        err = result["error"]
        assert err["code"] == engine_probe.ERROR_PROBE_FAILED
        assert "first boom" in err["message"]
        assert "first boom" in err.get("traceback", "")
        assert result["entry"] is None and result["stems"] == []

    def test_ran_but_no_figure_tries_the_next_entry(self, figs):
        """「跑通但没有 Figure」不算终局——继续试下一个 entry。"""
        write(figs, "quiet_then_draw.py",
              'import matplotlib.pyplot as plt\n'
              'def main():\n'
              '    pass\n'
              'def draw():\n'
              '    plt.plot([1, 2])\n')
        result = engine_probe.probe(figs, "quiet_then_draw.py")
        assert result["error"] is None
        assert result["entry"] == "draw"
        assert result["tried"] == ["main", "draw"]

    def test_invalid_explicit_entries_are_rejected(self, figs):
        write(figs, "x.py", SHOW_ONLY)
        result = engine_probe.probe(figs, "x.py", entries=["not an identifier"])
        assert result["error"]["code"] == engine_probe.ERROR_INVALID_ENTRY


@needs_worker
class TestErrorModel:
    def test_script_no_figure_is_a_stable_code(self, figs):
        write(figs, "quiet.py", 'x = 1 + 1\nprint("nothing to see", x)\n')
        result = engine_probe.probe_and_register(figs, "quiet.py")
        assert result["error"]["code"] == engine_probe.ERROR_NO_FIGURE
        assert result["registered"] is False
        assert result["stems"] == []

    def test_missing_dependency_names_the_module(self, figs):
        write(figs, "needy.py",
              'import tavotto_definitely_missing_pkg\n'
              'import matplotlib.pyplot as plt\n'
              'plt.plot([1])\n')
        result = engine_probe.probe(figs, "needy.py")
        err = result["error"]
        assert err["code"] == engine_probe.ERROR_MISSING_DEPENDENCY
        assert err["params"]["module"] == "tavotto_definitely_missing_pkg"

    def test_timeout_maps_to_execution_timeout(self, figs, monkeypatch):
        monkeypatch.setattr(engine_pool, "BUILD_TIMEOUT", 8)
        write(figs, "sleepy.py", 'import time\ntime.sleep(60)\n')
        result = engine_probe.probe(figs, "sleepy.py")
        assert result["error"]["code"] == engine_probe.ERROR_TIMEOUT

    def test_engine_level_not_found(self, figs):
        result = engine_probe.probe(figs, "missing.py")
        assert result["error"]["code"] == engine_probe.ERROR_NOT_FOUND

    def test_probe_error_codes_have_text_in_both_languages(self):
        """probe 的稳定码表与前端文案同一条纪律（test_error_codes 的延伸）：
        每个 code 两种语言都有文案，占位符与后端 params 对得上。"""
        locales = (Path(__file__).resolve().parent.parent
                   / "web" / "src" / "i18n" / "locales")
        if not (locales / "zh-CN" / "errors.json").is_file():
            pytest.skip("没有 web/（wheel/sdist 里不含前端源码）")
        import re
        codes_and_params = {
            engine_probe.ERROR_OUTSIDE_PROJECT: {"script"},
            engine_probe.ERROR_NOT_FOUND: {"script"},
            engine_probe.ERROR_UNSUPPORTED_TYPE: {"script"},
            engine_probe.ERROR_PROBE_FAILED: {"entry", "reason"},
            engine_probe.ERROR_NO_FIGURE: {"entry"},
            engine_probe.ERROR_MISSING_DEPENDENCY: {"module"},
            engine_probe.ERROR_TIMEOUT: {"entry"},
            engine_probe.ERROR_CANCELLED: set(),
            engine_probe.ERROR_INVALID_ENTRY: {"entry"},
            engine_probe.ERROR_STEM_CONFLICT: {"detail"},
        }
        for locale in ("zh-CN", "en-US"):
            table = json.loads((locales / locale / "errors.json")
                               .read_text(encoding="utf-8"))["backend"]
            for code, params in codes_and_params.items():
                assert code in table, f"{locale} 缺 {code} 的文案"
                used = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", table[code]))
                assert used == params, f"{locale} {code}: {used} != {params}"


# ===========================================================================
# 四、捕获结果：多 Figure、去重、一次执行、注册表效果
# ===========================================================================
@needs_worker
class TestCaptureResults:
    def test_multi_figure_returns_every_descriptor_in_order(self, figs):
        """多张图**完整**返回、顺序确定（负向反证 #4 的看护对象：
        只返回第一张，这里的数量断言必须红）。"""
        write(figs, "multi.py",
              'import matplotlib.pyplot as plt\n'
              'for i in range(3):\n'
              '    plt.figure(figsize=(3, 2))\n'
              '    plt.plot([1, 2, i])\n'
              'plt.show()\n')
        result = engine_probe.probe(figs, "multi.py")
        assert result["error"] is None
        assert len(result["descriptors"]) == 3
        assert [d["stem"] for d in result["descriptors"]] == \
            ["multi", "multi-2", "multi-3"]
        assert result["stems"] == ["multi", "multi-2", "multi-3"]

    def test_savefig_and_live_pyplot_figure_deduplicate(self, figs):
        """同一个 Figure savefig 后仍活在 pyplot 里：一张图一个描述符。"""
        write(figs, "saved.py",
              'import matplotlib.pyplot as plt\n'
              'plt.plot([1, 2, 3])\n'
              'plt.savefig("real_name.pdf")\n')
        result = engine_probe.probe(figs, "saved.py")
        assert result["error"] is None
        (d,) = result["descriptors"]
        assert d["stem"] == "real_name"
        assert d["capture_source"] == "savefig"

    def test_dropped_figures_are_reported_not_silent(self, figs):
        n = figcapture.MAX_PYPLOT_FALLBACK + 2
        write(figs, "many.py",
              'import matplotlib.pyplot as plt\n'
              f'for i in range({n}):\n'
              '    plt.figure(figsize=(2, 1.5))\n'
              '    plt.plot([1, i])\n')
        result = engine_probe.probe(figs, "many.py")
        assert result["error"] is None
        assert len(result["descriptors"]) == figcapture.MAX_PYPLOT_FALLBACK
        assert result["dropped_figures"] == 2

    def test_stdout_noise_does_not_break_the_protocol(self, figs):
        write(figs, "noisy.py",
              'import matplotlib.pyplot as plt\n'
              'print("{\\"ok\\": false, \\"garbage\\": true}")\n'
              'print("随便打印点什么 " * 100)\n'
              'plt.plot([1, 2, 3])\n')
        result = engine_probe.probe(figs, "noisy.py")
        assert result["error"] is None
        assert result["stems"] == ["noisy"]

    def test_unicode_and_space_paths(self, figs):
        write(figs, "子 目录/图 表.py", SHOW_ONLY)
        result = engine_probe.probe(figs, "子 目录/图 表.py")
        assert result["error"] is None
        (d,) = result["descriptors"]
        assert d["asset_id"] == "runtime:子 目录/图 表.py#图 表"
        assert d["script"] == "子 目录/图 表.py"

    def test_subdirectory_script_imports_its_local_helper(self, figs):
        write(figs, "panels/datautil.py", "VALUES = [5, 3, 4]\n")
        write(figs, "panels/chart.py",
              'import datautil\n'
              'import matplotlib.pyplot as plt\n'
              'plt.plot(datautil.VALUES)\n')
        result = engine_probe.probe(figs, "panels/chart.py")
        assert result["error"] is None
        assert result["stems"] == ["chart"]

    def test_probe_returns_the_full_descriptor(self, figs):
        """描述符逐字段完整，且能经 figcapture 工厂 payload 往返重建
        （writeback 能力照样只认派生值）。"""
        write(figs, "show_only.py", SHOW_ONLY)
        result = engine_probe.probe(figs, "show_only.py")
        (d,) = result["descriptors"]
        assert set(d) == {"asset_id", "script", "entry", "stem",
                          "capture_source", "execution_profile",
                          "original_artifact", "size_mm", "source_fingerprint",
                          "can_writeback_artifact", "can_writeback_source"}
        rebuilt = figcapture.descriptor_from_payload(d).to_payload()
        assert rebuilt == d
        assert result["timings"].get("script_build_ms", 0) > 0

    def test_a_successful_probe_executes_exactly_once(self, figs, tmp_path):
        """一次 probe 只执行一次成功路径（负向反证 #3 的看护对象）。

        脚本每执行一次就把项目外的计数文件 +1。probe 成功 → 登记 → 复用
        热会话再取一次 build 结果，计数必须还是 1——
        「probe 执行一次 → 取预览再执行一次 → 登记再执行一次」是被禁止的。
        """
        counter = tmp_path / "exec_count.txt"
        write(figs, "counted.py",
              'from pathlib import Path\n'
              'import matplotlib.pyplot as plt\n'
              'def main():\n'
              f'    p = Path({str(counter)!r})\n'
              '    n = int(p.read_text()) if p.exists() else 0\n'
              '    p.write_text(str(n + 1))\n'
              '    plt.plot([1, 2, 3])\n')
        result = engine_probe.probe_and_register(figs, "counted.py")
        assert result["error"] is None and result["registered"] is True
        assert int(counter.read_text()) == 1
        # 后续结果读取复用 build 好的热会话，不再重跑脚本
        w = engine_pool.get("counted.py", str(figs), result["entry"])
        assert w.built is True
        w.ensure_built()
        assert int(counter.read_text()) == 1

    def test_failed_entries_may_rerun_but_success_only_once(self, figs, tmp_path):
        """失败 entry 允许各自新建 worker 重跑；成功那次之后不再执行。"""
        counter = tmp_path / "exec_count2.txt"
        write(figs, "counted2.py",
              'from pathlib import Path\n'
              'import matplotlib.pyplot as plt\n'
              f'_p = Path({str(counter)!r})\n'
              '_n = int(_p.read_text()) if _p.exists() else 0\n'
              '_p.write_text(str(_n + 1))\n'
              'def main():\n'
              '    raise RuntimeError("wrong entry")\n'
              'def draw():\n'
              '    plt.plot([1, 2])\n')
        result = engine_probe.probe(figs, "counted2.py")
        assert result["error"] is None and result["entry"] == "draw"
        ran = int(counter.read_text())          # main 失败一次 + draw 成功一次
        w = engine_pool.get("counted2.py", str(figs), "draw")
        w.ensure_built()
        assert int(counter.read_text()) == ran   # 成功之后零新增


@needs_worker
class TestRegistryEffects:
    def test_failure_does_not_create_or_touch_the_registry(self, figs):
        write(figs, "quiet.py", "x = 1\n")
        result = engine_probe.probe_and_register(figs, "quiet.py")
        assert result["registered"] is False
        assert not (figs / "tavotto_registry.json").exists()

        write(figs, "good.py", SHOW_ONLY.replace("show_only", "good"))
        assert engine_probe.probe_and_register(figs, "good.py")["registered"]
        before = (figs / "tavotto_registry.json").read_bytes()
        assert engine_probe.probe_and_register(figs, "quiet.py")[
            "registered"] is False
        assert (figs / "tavotto_registry.json").read_bytes() == before

    def test_stem_conflict_with_a_live_script_is_not_silently_stolen(self, figs):
        """产出的 stem 已被另一份**仍存在**的脚本登记：报 code，不写注册表。"""
        write(figs, "a.py", 'print("owner")\n')
        (figs / "tavotto_registry.json").write_text(json.dumps({
            "version": 1,
            "scripts": {"a.py": {"entry": "main", "cost": "medium",
                                 "notes": "", "stems": ["figure"]}},
        }), encoding="utf-8")
        write(figs, "panels/figure.py", SHOW_ONLY)   # fallback stem = "figure"
        before = (figs / "tavotto_registry.json").read_bytes()
        result = engine_probe.probe_and_register(figs, "panels/figure.py")
        assert result["registered"] is False
        assert result["error"]["code"] == engine_probe.ERROR_STEM_CONFLICT
        assert result["stem_conflicts"] == {"figure": "a.py"}
        assert (figs / "tavotto_registry.json").read_bytes() == before

    def test_a_dead_owners_stems_are_reassigned(self, figs):
        """归属脚本已不在磁盘上的旧条目不算冲突——改名/删除后重探测要顺畅。"""
        (figs / "tavotto_registry.json").write_text(json.dumps({
            "version": 1,
            "scripts": {"ghost.py": {"entry": "main", "cost": "medium",
                                     "notes": "", "stems": ["figure"]}},
        }), encoding="utf-8")
        write(figs, "panels/figure.py", SHOW_ONLY)
        result = engine_probe.probe_and_register(figs, "panels/figure.py")
        assert result["error"] is None and result["registered"] is True
        cfg = json.loads((figs / "tavotto_registry.json")
                         .read_text(encoding="utf-8"))
        assert cfg["scripts"]["panels/figure.py"]["stems"] == ["figure"]
        assert "figure" not in cfg["scripts"].get("ghost.py", {}).get("stems", [])
