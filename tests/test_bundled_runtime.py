"""内置渲染 runtime：定位、校验、解释器优先级、失败路径。

为什么这一整套都必须**平台无关**：内置 runtime 是 Windows 桌面版才发的东西，
但决定它命不命中的逻辑（frozen 判断、路径解析、优先级、大小写）在三个平台上
都得一样，而日常开发在 macOS 上。只能靠「真在磁盘上摆一个假 runtime + 把
os.name / sys.frozen 打桩」来测——所以 engine/runtime.py 全程 os.path 拼字符串，
一个 pathlib 都不用（`Path()` 会按 os.name 分派，在别的平台上直接抛异常）。

**产品承诺**（每条对应下面的用例）：
  * 干净的 Windows 电脑（没有 Python / Conda）装完就能渲染，首次不联网；
  * 用户显式指定的解释器永远压过内置的；
  * 内置的缺了/坏了要报「安装文件不完整」，不能悄悄回退到别的东西；
  * 源码模式的自建 venv 行为一点不变。
"""
import json
import os
import sys

import pytest

from magplot.engine import bootstrap, config, pool, runtime

MANIFEST = {
    "schema": 1,
    "product": "Magplot",
    "kind": "windows-embeddable",
    "python": {"version": "3.13.15", "implementation": "cpython",
               "source": "https://www.python.org/ftp/python/3.13.15/x.zip"},
    "platform": {"os": "windows", "arch": "amd64", "tag": "cp313-win_amd64"},
    "top_level": ["numpy", "matplotlib", "pillow"],
    "packages": {"numpy": "2.5.2", "matplotlib": "3.11.1", "pillow": "12.3.0"},
    "build": {"id": "test", "built_at": "2026-08-17T00:00:00Z"},
}


def make_runtime(root, manifest=MANIFEST, with_python=True):
    """在磁盘上摆一个「看起来像内置 runtime」的目录，返回解释器路径。"""
    root = str(root)
    os.makedirs(root, exist_ok=True)
    py = runtime.runtime_python(root)
    if with_python:
        os.makedirs(os.path.dirname(py), exist_ok=True)
        with open(py, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(py, 0o755)
    if manifest is not None:
        with open(runtime.manifest_path(root), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
    return py


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    """每个用例都从「没有内置 runtime、没有任何覆盖、数据目录全新」开始。

    数据目录必须逐例隔离：自建 venv 就落在那儿，上一个用例摆下的假 venv
    会让下一个用例的 install() 直接跳过建 venv 那一步（真踩过）。
    """
    monkeypatch.setenv("MAGPLOT_DATA_DIR", str(tmp_path / "_data"))
    monkeypatch.delenv("MAGPLOT_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("MM_WORKER_PYTHON", raising=False)
    pool.reset_worker_python()
    bootstrap._progress.update(state="idle", log="", error=None)
    yield
    pool.reset_worker_python()


# ---------------- 定位 --------------------------------------------------------
def test_env_override_wins_over_everything(tmp_path, monkeypatch):
    """CI 与单测靠它把 runtime 指到临时目录，不必真去打包一次。"""
    make_runtime(tmp_path / "rt")
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(tmp_path / "rt"))
    assert runtime.runtime_root() == str(tmp_path / "rt")
    assert runtime.status()["valid"] is True


def test_frozen_app_finds_runtime_next_to_meipass(tmp_path, monkeypatch):
    """onedir 布局：`Magplot.exe` + `_internal/`，_MEIPASS 就是 `_internal`，
    runtime 经 spec 的 datas 落在 `_internal/runtime`。"""
    internal = tmp_path / "_internal"
    make_runtime(internal / "runtime")
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Magplot.exe"))
    assert runtime.runtime_root() == str(internal / "runtime")


def test_frozen_app_falls_back_to_exe_dir_layouts(tmp_path, monkeypatch):
    """换 PyInstaller 版本 / 手工摆产物时布局可能变；exe 同级与
    exe/_internal 两条兜底不能少，否则一次升级就让内置环境集体失灵。"""
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Magplot.exe"))

    make_runtime(tmp_path / "runtime")
    assert runtime.runtime_root() == str(tmp_path / "runtime")


def test_source_tree_looks_at_repo_root(monkeypatch):
    """源码模式下 scripts/build_worker_runtime.py 默认产出到仓库根的 runtime/。"""
    monkeypatch.setattr(runtime, "is_frozen", lambda: False)
    roots = runtime._candidate_roots()
    assert roots, "源码模式至少要有一个候选位置"
    assert roots[0].endswith(os.path.join("magic_matpliot", "runtime")) or \
        roots[0].endswith(os.sep + "runtime")


def test_no_runtime_is_not_an_error_outside_windows_desktop(monkeypatch):
    """macOS 桌面版 / pip 安装 / 源码模式都不带 runtime——那是正常状态，
    绝不能报「安装文件不完整」把用户吓一跳。"""
    monkeypatch.setattr(runtime, "is_frozen", lambda: False)
    st = runtime.status()
    assert st["present"] is False and st["code"] == ""
    assert runtime.ships_bundled_runtime() is False


def test_missing_runtime_on_windows_desktop_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Magplot.exe"))
    st = runtime.status()
    assert st["code"] == runtime.CODE_MISSING
    assert st["valid"] is False


def test_broken_runtime_on_windows_desktop_is_invalid_not_missing(
        tmp_path, monkeypatch):
    """目录在但内容不对（装了一半、被杀毒软件掏空）与「整个没带」要分开报：
    前者提示重装能修，后者可能是包本身就没打对。"""
    root = tmp_path / "rt"
    make_runtime(root, manifest=None)          # 有解释器没清单
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(root))
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setattr(os, "name", "nt")
    st = runtime.status()
    assert st["code"] == runtime.CODE_INVALID and st["present"] is True


def test_manifest_with_unknown_schema_is_rejected(tmp_path, monkeypatch):
    """清单比本程序新 = 用户装了个更新的包但主程序是旧的。硬着头皮往下跑
    等于拿一份读不懂的清单当真，宁可报损坏。"""
    make_runtime(tmp_path / "rt", manifest={**MANIFEST, "schema": 99})
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(tmp_path / "rt"))
    assert runtime.read_manifest(str(tmp_path / "rt")) is None
    assert runtime.status()["valid"] is False


@pytest.mark.parametrize("bad", [
    {**MANIFEST, "packages": {}},                     # 一个包都没有
    {**MANIFEST, "packages": "numpy"},                # 类型不对
    {**MANIFEST, "python": {}},                       # 没版本号
])
def test_manifest_shape_is_validated(tmp_path, bad):
    make_runtime(tmp_path / "rt", manifest=bad)
    assert runtime.read_manifest(str(tmp_path / "rt")) is None


def test_corrupt_manifest_json_does_not_crash(tmp_path, monkeypatch):
    make_runtime(tmp_path / "rt")
    with open(runtime.manifest_path(str(tmp_path / "rt")), "w") as fh:
        fh.write("{ this is not json")
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(tmp_path / "rt"))
    assert runtime.read_manifest(str(tmp_path / "rt")) is None
    assert runtime.status()["valid"] is False


def test_manifest_reports_pinned_package_versions(tmp_path, monkeypatch):
    make_runtime(tmp_path / "rt")
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(tmp_path / "rt"))
    info = runtime.manifest()
    assert info["python"]["version"] == "3.13.15"
    assert info["packages"]["matplotlib"] == "3.11.1"


# ---------------- Windows 路径习惯 --------------------------------------------
def test_runtime_python_layout_per_platform(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "name", "nt")
    assert runtime.runtime_python(r"C:\Program Files\Magplot\runtime") == \
        r"C:\Program Files\Magplot\runtime\python.exe"
    monkeypatch.setattr(os, "name", "posix")
    assert runtime.runtime_python("/opt/magplot/runtime") == \
        "/opt/magplot/runtime/bin/python3"


def test_same_python_is_case_and_separator_insensitive_on_windows(monkeypatch):
    """`C:/Users/x/Python.exe` 与 `C:\\Users\\x\\python.exe` 是同一个文件。
    按字符串比会当成两个，来源标签立刻错位（内置的被当成 system）。"""
    monkeypatch.setattr(os, "path", os.path)     # 明确不动 os.path
    if os.name == "nt":                          # 真 Windows 上才有大小写不敏感
        assert pool.same_python(r"C:\X\Python.exe", "C:/x/python.exe")
    # 三平台都必须成立的：同一条路径的不同写法
    assert pool.same_python("/a/b/../b/python3", "/a/b/python3")
    assert pool.same_python(None, "/a") is False
    assert pool.same_python("/a", None) is False


def test_paths_with_chinese_and_spaces_work(tmp_path, monkeypatch):
    """国内最常见的一档：用户名是中文、路径带空格。"""
    root = tmp_path / "我的 文档" / "Program Files" / "runtime"
    py = make_runtime(root)
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(root))
    st = runtime.status()
    assert st["valid"] is True and st["python"] == py
    assert pool.same_python(st["python"], py)


# ---------------- 解释器优先级 -------------------------------------------------
def _bundled(tmp_path, monkeypatch):
    py = make_runtime(tmp_path / "rt")
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(tmp_path / "rt"))
    return py


def test_bundled_runtime_is_used_when_nothing_else_configured(
        tmp_path, monkeypatch):
    """**这条就是产品承诺本身**：没装过 Python 的电脑上，渲染照样跑起来。"""
    py = _bundled(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)   # 桌面版：跳过 sys.executable
    monkeypatch.setattr(pool, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "_has_matplotlib", lambda p: True)
    assert pool.select_worker_python() == (py, pool.SOURCE_BUNDLED)


def test_env_override_beats_bundled(tmp_path, monkeypatch):
    """高级用户的应急出口：MM_WORKER_PYTHON 永远第一。"""
    _bundled(tmp_path, monkeypatch)
    mine = tmp_path / "mine" / "python3"
    mine.parent.mkdir(parents=True)
    mine.write_text("#!/bin/sh\n")
    monkeypatch.setenv("MM_WORKER_PYTHON", str(mine))
    monkeypatch.setattr(pool, "_has_matplotlib", lambda p: True)
    assert pool.select_worker_python() == (str(mine), pool.SOURCE_ENV)


def test_user_configured_interpreter_beats_bundled(tmp_path, monkeypatch):
    """用户在设置里挑过的环境，任何时候都压过我们的默认——他挑它是有理由的
    （脚本要 rdkit / 自家实验室的库，内置环境里没有）。"""
    _bundled(tmp_path, monkeypatch)
    mine = tmp_path / "conda" / "python3"
    mine.parent.mkdir(parents=True)
    mine.write_text("#!/bin/sh\n")
    config.set_worker_python(str(mine))
    monkeypatch.setattr(pool, "_has_matplotlib", lambda p: True)
    assert pool.select_worker_python() == (str(mine), pool.SOURCE_CONFIGURED)


def test_managed_venv_is_labelled_apart_from_user_choice(tmp_path, monkeypatch):
    """两者都存在用户配置里，但排障含义不同：managed_venv 是我们建的，
    出问题该我们负责；configured 是用户自己的环境。"""
    managed = bootstrap.venv_python()
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_text("#!/bin/sh\n")
    config.set_worker_python(str(managed))
    monkeypatch.setattr(pool, "_has_matplotlib", lambda p: True)
    assert pool.select_worker_python() == (str(managed), pool.SOURCE_MANAGED)


def test_bundled_sits_before_system_python(tmp_path, monkeypatch):
    """机器上有 Conda 也照样用内置的：内置那套是我们测过的，
    用户的 Conda 里 matplotlib 是什么版本谁也不知道。"""
    py = _bundled(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "is_frozen", lambda: True)
    order = [p for p, _ in pool._prioritized_candidates()]
    sources = {p: s for p, s in pool._prioritized_candidates()}
    assert py in order
    later = [p for p in order[order.index(py) + 1:]]
    assert all(sources[p] == pool.SOURCE_SYSTEM for p in later), \
        "内置 runtime 之后只允许剩系统探测这一档兼容回退"


def test_source_tree_still_prefers_its_own_interpreter(monkeypatch):
    """源码 / pip 安装模式不受影响：没有内置 runtime 时仍然先用自己。"""
    monkeypatch.setattr(runtime, "is_frozen", lambda: False)
    monkeypatch.setattr(pool, "is_frozen", lambda: False)
    cands = [p for p, _ in pool._prioritized_candidates()]
    assert cands[0] == sys.executable


def test_system_probe_remains_as_compatibility_fallback(monkeypatch):
    """内置的坏了也不能一头撞死：用户机器上的 Conda 还能救场。"""
    monkeypatch.setattr(runtime, "bundled_python", lambda: None)
    sources = {s for _, s in pool._prioritized_candidates()}
    assert pool.SOURCE_SYSTEM in sources


def test_source_of_classifies_without_probing(tmp_path, monkeypatch):
    """贴来源标签不该再跑一遍探测——一次探测最多 30 秒，环境状态接口
    每次刷新都等一轮是不能接受的。"""
    py = _bundled(tmp_path, monkeypatch)
    called = []
    monkeypatch.setattr(pool, "_has_matplotlib",
                        lambda p: called.append(p) or True)
    assert pool.source_of(py) == pool.SOURCE_BUNDLED
    assert called == [], "source_of 不允许启动子进程"


# ---------------- 失败路径 ----------------------------------------------------
def test_desktop_missing_runtime_raises_machine_readable_code(monkeypatch,
                                                              tmp_path):
    """桌面版缺内置环境时的报错**不是**「你没装 Python」——用户该做的是重装，
    两者的 code 必须分开，否则前端只能给一句谁也用不上的通用提示。"""
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "is_frozen", lambda: True)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Magplot.exe"))
    monkeypatch.setattr(pool, "_prioritized_candidates", lambda: [])
    with pytest.raises(pool.WorkerError) as exc:
        pool.select_worker_python()
    assert exc.value.code == runtime.CODE_MISSING
    assert "重新安装" in str(exc.value)


def test_runtime_present_but_imports_fail_is_caught(tmp_path, monkeypatch):
    """文件都在、清单也对，但 import 不了（缺 VC 运行库、.pyd 被隔离）。
    只看清单会以为一切正常，所以选解释器时必须真 import 一次。"""
    _bundled(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "_has_matplotlib", lambda p: False)
    with pytest.raises(pool.WorkerError):
        pool.select_worker_python()


def test_probe_packages_reports_none_for_broken_interpreter(tmp_path):
    fake = tmp_path / "nope"
    out = runtime.probe_packages(str(fake), ["numpy", "scipy"])
    assert out == {"numpy": None, "scipy": None}


def test_missing_dependency_is_recognised_from_traceback():
    """内置 runtime 只带常用科学栈。用户脚本 import rdkit 时甩一段
    ModuleNotFoundError 等于什么都没说，认出包名才谈得上给出口。"""
    tb = ('Traceback (most recent call last):\n'
          '  File "fig.py", line 3, in <module>\n'
          '    import rdkit.Chem\n'
          "ModuleNotFoundError: No module named 'rdkit'\n")
    assert pool.missing_module(tb) == "rdkit"
    assert pool.missing_module("ModuleNotFoundError: No module named "
                               '"astropy.io"') == "astropy"
    assert pool.missing_module("ValueError: 随便什么别的错") == ""


def test_bundled_worker_never_writes_into_the_install_dir():
    """安装目录可能在 Program Files（没写权限），而且卸载后不该留垃圾。

    **`-B` 是这条纪律的真正保证**，不是环境变量：embeddable 靠 `._pth` 定路径，
    而 CPython 的 getpath 找到 `._pth` 就 `use_environment = 0`
    （"Its presence also implies isolated mode"），PYTHONDONTWRITEBYTECODE /
    PYTHONPYCACHEPREFIX 那条路不可靠。命令行参数任何时候都算数。
    """
    assert "-B" in runtime.child_args()

    # matplotlib 自己读 os.environ，不受 ._pth 隔离影响，这条一定生效
    env = runtime.child_env({"PATH": "/usr/bin"})
    data = str(config.data_dir())
    assert env["MPLCONFIGDIR"].startswith(data)
    assert env["PATH"] == "/usr/bin", "不该动用户原有的 PATH"


def test_only_the_bundled_runtime_gets_b_flag(tmp_path, monkeypatch):
    """`-B` 只加给内置 runtime。用户自己的环境是他的地盘——替他关掉字节码缓存
    会让每次冷启动都变慢，而我们没有理由那么做。"""
    py = _bundled(tmp_path, monkeypatch)
    monkeypatch.setattr(pool, "_has_matplotlib", lambda p: True)
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "is_frozen", lambda: True)

    seen: list[list[str]] = []

    class FakeProc:
        def poll(self):
            return None

    def fake_popen(cmd, **kw):
        seen.append(cmd)
        return FakeProc()

    monkeypatch.setattr(pool.subprocess, "Popen", fake_popen)
    figs = tmp_path / "figs"
    figs.mkdir()
    pool.EngineWorker("f.py", str(figs), "main")
    assert seen[0][0] == py and seen[0][1] == "-B"

    seen.clear()
    pool.reset_worker_python()
    mine = tmp_path / "mine" / "python3"
    mine.parent.mkdir(parents=True)
    mine.write_text("#!/bin/sh\n")
    monkeypatch.setenv("MM_WORKER_PYTHON", str(mine))
    pool.EngineWorker("f.py", str(figs), "main")
    assert seen[0][0] == str(mine) and "-B" not in seen[0]


# ---------------- bootstrap：不再劝用户装 Python -------------------------------
def test_bootstrap_status_says_bundled_and_offers_no_install(
        tmp_path, monkeypatch):
    """有内置环境时界面必须显示「Magplot 内置环境」，且**不出现任何安装引导**
    ——那时什么都不缺，弹窗只会让人以为出了问题。"""
    py = _bundled(tmp_path, monkeypatch)
    monkeypatch.setattr(pool, "find_worker_python", lambda: py)
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p: "3.11.1")
    st = bootstrap.status()
    assert st["ok"] is True
    assert st["source"] == pool.SOURCE_BUNDLED and st["bundled"] is True
    assert st["runtime"]["valid"] is True
    assert st["runtime"]["packages"]["numpy"] == "2.5.2"
    assert "can_install" not in st or st["can_install"] is False


def test_bootstrap_never_builds_a_venv_out_of_the_embedded_python(
        tmp_path, monkeypatch):
    """官方 embeddable 不带 ensurepip，`-m venv` 会建到一半失败。
    把它选成基础解释器只会给用户一段看不懂的报错。"""
    py = _bundled(tmp_path, monkeypatch)
    monkeypatch.setattr(bootstrap, "_probe", lambda p, expr: "(3, 13)")
    base = bootstrap.find_base_python()
    assert base != py


def test_desktop_install_refuses_and_tells_user_to_reinstall(monkeypatch):
    """桌面版缺内置环境时，现场联网建 venv 是在把包装问题伪装成用户的环境问题。"""
    monkeypatch.setattr(runtime, "ships_bundled_runtime", lambda: True)
    out = bootstrap.install()
    assert out["ok"] is False and "重新安装" in out["error"]


def test_source_mode_bootstrap_behaviour_is_unchanged(monkeypatch):
    """源码模式的自建 venv 是另一条路，本次改动不许碰它。"""
    monkeypatch.setattr(runtime, "ships_bundled_runtime", lambda: False)
    monkeypatch.setattr(bootstrap, "find_base_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p: "3.11.1")
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        if "venv" in cmd:
            bootstrap.venv_python().parent.mkdir(parents=True, exist_ok=True)
            bootstrap.venv_python().write_text("#!/bin/sh\n")
        return 0, ""

    monkeypatch.setattr(bootstrap, "_run", fake_run)
    assert bootstrap.install()["ok"] is True
    assert calls[0][:3] == ["/usr/bin/python3", "-m", "venv"]


# ---------------- HTTP -------------------------------------------------------
@pytest.fixture
def client():
    from magplot import app as m
    m.app.config["TESTING"] = True
    return m.app.test_client()


def test_environment_endpoint_exposes_source_and_runtime(client, tmp_path,
                                                         monkeypatch):
    py = _bundled(tmp_path, monkeypatch)
    monkeypatch.setattr(pool, "find_worker_python", lambda: py)
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p: "3.11.1")
    body = client.get("/api/engine/environment").get_json()
    assert body["source"] == "bundled" and body["bundled"] is True
    assert body["runtime"]["python"] == "3.13.15"


def test_environment_probe_reports_each_package(client, tmp_path, monkeypatch):
    """「内置环境能导入并报告所有固定科学包版本」——冒烟就是断言这一条。"""
    py = _bundled(tmp_path, monkeypatch)
    monkeypatch.setattr(pool, "find_worker_python", lambda: py)
    monkeypatch.setattr(bootstrap, "matplotlib_version", lambda p: "3.11.1")
    monkeypatch.setattr(runtime, "probe_packages",
                        lambda p, names=None: {n: "1.0" for n in
                                               (names or ["numpy"])})
    body = client.get("/api/engine/environment?probe=numpy,PIL").get_json()
    assert body["imports"] == {"numpy": "1.0", "PIL": "1.0"}


def test_install_endpoint_tells_desktop_users_to_reinstall(client, monkeypatch):
    def boom():
        raise pool.WorkerError("no", code=runtime.CODE_MISSING)
    monkeypatch.setattr(pool, "find_worker_python", boom)
    monkeypatch.setattr(runtime, "ships_bundled_runtime", lambda: True)
    resp = client.post("/api/engine/environment/install")
    assert resp.status_code == 400
    assert "重新安装" in resp.get_json()["error"]


def test_render_failure_carries_missing_module(client, monkeypatch, tmp_path):
    """前端据此给「换成你自己的环境」，而不是一段 ModuleNotFoundError。"""
    from magplot import app as m

    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "p1.pdf").write_bytes(b"%PDF-1.4\n")
    m.open_project(str(figs))
    monkeypatch.setattr(
        m.engine_registry.Registry, "for_stem",
        lambda self, s: {"script": "x.py", "entry": "main", "cost": "light"})

    def boom(*a, **kw):
        raise pool.WorkerError("缺 rdkit", "tb", code="missing_dependency",
                               module="rdkit")
    monkeypatch.setattr(m.engine_pool, "get", boom)

    body = client.post("/api/engine/render",
                       json={"id": "p1.pdf", "patches": []}).get_json()
    assert body["code"] == "missing_dependency" and body["module"] == "rdkit"
