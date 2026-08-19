"""内置渲染 runtime：定位、校验、解释器优先级、失败路径。

为什么这一整套都必须**平台无关**：内置 runtime 是桌面版才发的东西
（Windows 的官方 embeddable、macOS 的 python-build-standalone），但决定它
命不命中的逻辑（frozen 判断、路径解析、布局识别、架构核对、优先级、大小写）
在三个平台上都得一样，而日常开发只在其中一台机器上。只能靠「真在磁盘上摆一个
假 runtime + 把 os.name / sys.platform / sys.frozen / host_arch 打桩」来测——
所以 engine/runtime.py 全程 os.path 拼字符串，一个 pathlib 都不用
（`Path()` 会按 os.name 分派，在别的平台上直接抛异常），架构与平台也做成了
可打桩的函数（`host_os()` / `host_arch()`）而不是在判断里直接读 platform。

**产品承诺**（每条对应下面的用例）：
  * 干净的 Windows / macOS 电脑（没有 Python / Conda / Homebrew）装完就能渲染，
    首次不联网；
  * 用户显式指定的解释器永远压过内置的；
  * 内置的缺了 / 坏了 / **架构装错了**要报「安装文件不完整」，
    不能悄悄回退到别的东西；
  * pip / 源码模式不受影响，也不该被误判成「缺 runtime」；
  * 内置 runtime 绝不往安装目录写东西，也绝不吃用户环境里的 PYTHONHOME/PYTHONPATH。
"""
import json
import os
import sys

import pytest

from magplot.engine import bootstrap, config, pool, runtime


def _host_platform_block():
    """默认的 manifest 平台段 = **当前这台机器**。

    写死成 windows/amd64 的话，macOS 上跑测试时每个用例都会撞上「架构不符」
    这条新校验。要测不匹配的那一档，用下面的 `foreign_manifest()` 显式构造——
    默认值应当表示「一切正常」，不匹配是特例。
    """
    return {"os": runtime.host_os(), "arch": runtime.host_arch(),
            "tag": "test", "pip_platforms": ["test"]}


MANIFEST = {
    "schema": 2,
    "product": "Magplot",
    "kind": "test",
    "target": "test",
    "python": {"version": "3.13.15", "implementation": "cpython",
               "source": "https://example.invalid/x.tar.gz"},
    "platform": _host_platform_block(),
    "top_level": ["numpy", "matplotlib", "pillow"],
    "packages": {"numpy": "2.5.2", "matplotlib": "3.11.1", "pillow": "12.3.0"},
    "build": {"id": "test", "built_at": "2026-08-17T00:00:00Z", "smoke": "passed"},
}

#: 两种真实布局里解释器的相对位置（与 engine/runtime._INTERPRETER_RELPATHS 对应）
LAYOUTS = {"windows": ("python.exe",), "posix": ("bin", "python3")}


def manifest_claiming(os_name=None, arch=None):
    """一份「声称自己是给某平台/某架构」的 manifest（不给的字段沿用本机）。"""
    plat = dict(MANIFEST["platform"])
    if os_name:
        plat["os"] = os_name
    if arch:
        plat["arch"] = arch
    return {**MANIFEST, "platform": plat}


#: 与本机不符的那一份——用来验平台/架构核对真的会拦。
foreign_manifest = manifest_claiming

#: 配合「把 sys.platform 打桩成 darwin 来模拟 macOS」的用例。
#:
#: **打桩了平台，清单就得跟着打桩**：MANIFEST 默认按**真实**宿主生成，而
#: schema 2 会校验平台——在 Linux/Windows 上模拟 macOS 时，一份写着 linux 的
#: 清单会被判成「平台不符」，于是 `bundled_python()` 回 None，用例悄悄退到
#: 系统 Python 上去。CI 上「macOS 绿、Linux/Windows 红」正是这么来的：
#: 本机恰好是 macOS，两边对得上，于是本地怎么跑都发现不了。
def macos_manifest():
    return manifest_claiming(os_name="macos")


def make_runtime(root, manifest=MANIFEST, with_python=True, layout=None):
    """在磁盘上摆一个「看起来像内置 runtime」的目录，返回解释器路径。

    `layout` 不给就按本机习惯（Windows 是 `python.exe`，别处是 `bin/python3`）；
    给了就强制用那一种——这样能在一台机器上同时验两种布局都认得出来。
    """
    root = str(root)
    os.makedirs(root, exist_ok=True)
    if layout:
        py = os.path.join(root, *LAYOUTS[layout])
    else:
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

    `MAGPLOT_RUNTIME_DIR` 指向一个**不存在**的目录而不是删掉它：开发机上
    仓库根真的可能躺着一份构建好的 `runtime/`（跑过 build_worker_runtime.py
    之后就有），删掉环境变量的话源码模式的候选里就会冒出那一份，
    「没有内置 runtime」这个前提当场不成立——本机绿、CI 红，或者反过来。
    覆盖是排他的（见 runtime._candidate_roots），指到空处即等于「没有」。
    要验非覆盖路径的用例自己 delenv，那也顺便把意图写明了。
    """
    monkeypatch.setenv("MAGPLOT_DATA_DIR", str(tmp_path / "_data"))
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(tmp_path / "_no_runtime_here"))
    monkeypatch.delenv("MM_WORKER_PYTHON", raising=False)
    monkeypatch.delenv("MAGPLOT_RUNTIME_HOST_ARCH", raising=False)
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
    monkeypatch.delenv("MAGPLOT_RUNTIME_DIR")   # 验的正是非覆盖的定位路径
    internal = tmp_path / "_internal"
    make_runtime(internal / "runtime")
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Magplot.exe"))
    assert runtime.runtime_root() == str(internal / "runtime")


def test_frozen_app_falls_back_to_exe_dir_layouts(tmp_path, monkeypatch):
    """换 PyInstaller 版本 / 手工摆产物时布局可能变；exe 同级与
    exe/_internal 两条兜底不能少，否则一次升级就让内置环境集体失灵。"""
    monkeypatch.delenv("MAGPLOT_RUNTIME_DIR")   # 验的正是非覆盖的定位路径
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Magplot.exe"))

    make_runtime(tmp_path / "runtime")
    assert runtime.runtime_root() == str(tmp_path / "runtime")


def test_source_tree_looks_at_repo_root(monkeypatch):
    """源码模式下 scripts/build_worker_runtime.py 默认产出到仓库根的 runtime/。"""
    monkeypatch.delenv("MAGPLOT_RUNTIME_DIR")   # 验的正是非覆盖的定位路径
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
    monkeypatch.setattr(pool, "_has_matplotlib", lambda p, **kw: True)
    assert pool.select_worker_python() == (py, pool.SOURCE_BUNDLED)


def test_env_override_beats_bundled(tmp_path, monkeypatch):
    """高级用户的应急出口：MM_WORKER_PYTHON 永远第一。"""
    _bundled(tmp_path, monkeypatch)
    mine = tmp_path / "mine" / "python3"
    mine.parent.mkdir(parents=True)
    mine.write_text("#!/bin/sh\n")
    monkeypatch.setenv("MM_WORKER_PYTHON", str(mine))
    monkeypatch.setattr(pool, "_has_matplotlib", lambda p, **kw: True)
    assert pool.select_worker_python() == (str(mine), pool.SOURCE_ENV)


def test_user_configured_interpreter_beats_bundled(tmp_path, monkeypatch):
    """用户在设置里挑过的环境，任何时候都压过我们的默认——他挑它是有理由的
    （脚本要 rdkit / 自家实验室的库，内置环境里没有）。"""
    _bundled(tmp_path, monkeypatch)
    mine = tmp_path / "conda" / "python3"
    mine.parent.mkdir(parents=True)
    mine.write_text("#!/bin/sh\n")
    config.set_worker_python(str(mine))
    monkeypatch.setattr(pool, "_has_matplotlib", lambda p, **kw: True)
    assert pool.select_worker_python() == (str(mine), pool.SOURCE_CONFIGURED)


def test_managed_venv_is_labelled_apart_from_user_choice(tmp_path, monkeypatch):
    """两者都存在用户配置里，但排障含义不同：managed_venv 是我们建的，
    出问题该我们负责；configured 是用户自己的环境。"""
    managed = bootstrap.venv_python()
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_text("#!/bin/sh\n")
    config.set_worker_python(str(managed))
    monkeypatch.setattr(pool, "_has_matplotlib", lambda p, **kw: True)
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
                        lambda p, **kw: called.append(p) or True)
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
    monkeypatch.setattr(pool, "_has_matplotlib", lambda p, **kw: False)
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
    monkeypatch.setattr(pool, "_has_matplotlib", lambda p, **kw: True)
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


# ---------------- macOS 也开始发内置 runtime（2026-08-18）----------------------
#
# 这一节全是**跨平台可跑**的：靠打桩 os.name / sys.platform / host_arch 模拟
# 另一台机器。原因还是那句——「装错架构」「Windows 的 runtime 混进 .app」
# 这类故障只有在别人的电脑上才复现，而它们必须在这里就被钉住。
@pytest.mark.parametrize("os_name,plat,frozen,expected", [
    ("nt",    "win32",  True,  True),    # Windows 桌面版：NSIS 带 runtime
    ("posix", "darwin", True,  True),    # macOS 桌面版：.app 带 runtime
    ("posix", "linux",  True,  False),   # Linux 没有桌面发行形态
    ("nt",    "win32",  False, False),   # pip / 源码：本来就不带
    ("posix", "darwin", False, False),
])
def test_which_install_shapes_are_expected_to_ship_a_runtime(
        monkeypatch, os_name, plat, frozen, expected):
    """`ships_bundled_runtime()` 回答的是「**本该**有吗」，不是「有没有」。

    判错的代价是两头的：判成 True 而实际不带（pip 安装被当成损坏的桌面版），
    用户会被劝去「重新安装 Magplot」——而他根本没装过安装包；判成 False 而
    实际该带（macOS 桌面版在这次改动之前就是这样），runtime 没打进去时
    一句提示都没有，只会安静地去找用户机器上的 Python。
    """
    monkeypatch.setattr(os, "name", os_name)
    monkeypatch.setattr(sys, "platform", plat)
    monkeypatch.setattr(runtime, "is_frozen", lambda: frozen)
    assert runtime.ships_bundled_runtime() is expected


def test_macos_desktop_missing_runtime_reports_reinstall_not_install_python(
        monkeypatch, tmp_path):
    """macOS 桌面版缺 runtime 时的提示必须与 Windows 对等：**重装**，
    而不是「请先安装 Python」——用户装的是「开箱即用」的 dmg。"""
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "_prioritized_candidates", lambda: [])

    st = runtime.status()
    assert st["code"] == runtime.CODE_MISSING and st["valid"] is False

    with pytest.raises(pool.WorkerError) as exc:
        pool.select_worker_python()
    assert exc.value.code == runtime.CODE_MISSING
    assert "重新安装" in str(exc.value)
    assert "安装 Python" not in str(exc.value)


def test_wrong_arch_runtime_is_invalid_not_silently_used(tmp_path, monkeypatch):
    """Intel 的机器上装了 arm64 的包（或反过来）。

    不拦的话，第一次渲染才会炸，而错误是一句
    "mach-o file, but is an incompatible architecture"——没有任何用户能
    从那句话推出「你下错安装包了」。
    """
    root = tmp_path / "rt"
    make_runtime(root, manifest=foreign_manifest(arch="x86_64"))
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(root))
    monkeypatch.setattr(runtime, "host_arch", lambda: "arm64")
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)

    st = runtime.status()
    assert st["valid"] is False and st["code"] == runtime.CODE_INVALID
    assert "x86_64" in st["error"] and "arm64" in st["error"]
    # 清单仍要交出来：诊断包得说清楚「拿到的是哪一份」，
    # 只回一句「损坏」等于让排障的人自己去翻文件
    assert st["manifest"]["platform"]["arch"] == "x86_64"
    assert runtime.bundled_python() is None


def test_wrong_os_runtime_is_invalid(tmp_path, monkeypatch):
    """构建链把 Windows 的 runtime 打进了 .app——发出去之前必须炸，
    但万一漏到用户手里，启动时也要说人话而不是等渲染时崩。"""
    root = tmp_path / "rt"
    make_runtime(root, manifest=foreign_manifest(os_name="windows"))
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(root))
    monkeypatch.setattr(runtime, "host_os", lambda: "macos")
    st = runtime.status()
    assert st["valid"] is False and st["code"] == runtime.CODE_INVALID
    assert "windows" in st["error"] and "macos" in st["error"]


def test_arch_aliases_are_normalised_before_comparing(tmp_path, monkeypatch):
    """锁文件写 amd64、`platform.machine()` 回 AMD64、wheel 标签写 x86_64——
    三边用词不同。不归一就会「明明是对的却报架构不符」。"""
    assert runtime.normalize_arch("AMD64") == "x86_64"
    assert runtime.normalize_arch("x86_64") == "x86_64"
    assert runtime.normalize_arch("aarch64") == "arm64"
    assert runtime.normalize_arch("arm64") == "arm64"

    root = tmp_path / "rt"
    make_runtime(root, manifest=foreign_manifest(arch="amd64"))
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(root))
    monkeypatch.setattr(runtime, "host_arch", lambda: "x86_64")
    assert runtime.status()["valid"] is True


def test_unknown_host_arch_does_not_condemn_a_good_runtime(tmp_path, monkeypatch):
    """`platform.machine()` 在冷门平台上可能是空串。
    把「我不知道」当成「不匹配」会让一份本来能用的 runtime 被判死刑。"""
    root = tmp_path / "rt"
    make_runtime(root)
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(root))
    monkeypatch.setattr(runtime, "host_arch", lambda: "")
    assert runtime.status()["valid"] is True


def test_host_arch_can_be_overridden_for_cross_checks(monkeypatch):
    """构建机上要能问「这份 runtime 会被 arm64 的 app 接受吗」。"""
    monkeypatch.setenv("MAGPLOT_RUNTIME_HOST_ARCH", "AMD64")
    assert runtime.host_arch() == "x86_64"


def test_manifest_without_platform_block_is_rejected(tmp_path):
    """schema 2 起 platform.os / platform.arch 是硬要求：没有它们就无从判断
    这份 runtime 是不是给本机的，而那正是要挡的一档。"""
    bads = [{**MANIFEST, "platform": {}},
            {**MANIFEST, "platform": {"os": "macos"}},      # 缺 arch
            {**MANIFEST, "platform": {"arch": "arm64"}},    # 缺 os
            {**MANIFEST, "platform": "macos"}]              # 类型不对
    for i, bad in enumerate(bads):
        root = tmp_path / f"rt{i}"
        make_runtime(root, manifest=bad)
        assert runtime.read_manifest(str(root)) is None


# ---------------- 两种布局都要认得 --------------------------------------------
@pytest.mark.parametrize("layout,tail", [
    ("windows", "python.exe"),                    # 官方 embeddable
    ("posix", os.path.join("bin", "python3")),    # python-build-standalone
])
def test_both_runtime_layouts_are_recognised(tmp_path, monkeypatch, layout, tail):
    """定位不能只认本平台那一种布局。

    构建机会在 Linux/macOS 上交叉产出 Windows 的 runtime，冒烟脚本也会在
    一台机器上检视另一份产物；只认本平台的话，那些场景一律误报「不完整」。
    """
    root = tmp_path / "rt"
    py = make_runtime(root, layout=layout)
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(root))
    assert runtime.resolve_python(str(root)) == py
    assert py.endswith(tail)
    assert runtime.runtime_root() == str(root)
    assert runtime.status()["python"] == py


def test_runtime_python_stays_a_pure_function(monkeypatch):
    """`runtime_python()` 回答「该长什么样」，不碰磁盘——它要能在任何平台上
    被单测。真实落点由 `resolve_python()` 去 stat。"""
    monkeypatch.setattr(os, "name", "nt")
    assert runtime.runtime_python(r"C:\Program Files\Magplot\runtime") == \
        r"C:\Program Files\Magplot\runtime\python.exe"
    monkeypatch.setattr(os, "name", "posix")
    assert runtime.runtime_python("/Applications/Magplot.app/runtime") == \
        "/Applications/Magplot.app/runtime/bin/python3"


def test_explicit_runtime_dir_override_is_exclusive(tmp_path, monkeypatch):
    """指了 MAGPLOT_RUNTIME_DIR 就只认这一个——指到一个空目录也不许悄悄
    回退到别处那份。「覆盖了却被别处顶掉」是最难查的一种：你以为在验刚构建的
    那份，实际验的是上一次留下的产物，两边日志一模一样。"""
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(tmp_path / "nothing_here"))
    monkeypatch.setattr(runtime, "is_frozen", lambda: False)
    assert runtime._candidate_roots() == [str(tmp_path / "nothing_here")]
    assert runtime.runtime_root() is None


# ---------------- macOS 上的解释器优先级与不写安装目录 ------------------------
#: 在 Windows 上模拟 POSIX 是**做不到**的，只要那条路径会碰到 pathlib：
#: `pool.select_worker_python()` 里有 `Path(cand)`，而把 `os.name` 打桩成
#: "posix" 之后它会分派到 PosixPath，在 Windows 上直接抛
#: `UnsupportedOperation: cannot instantiate 'PosixPath'`（反方向同理，
#: 在 macOS 上伪装 nt 会抛 WindowsPath）。这正是 `engine/runtime.py` 全程
#: os.path 拼字符串、一个 pathlib 都不用的原因——**它**因此三平台可测。
#:
#: 所以下面两条「跑完整优先级链」的用例只在 POSIX 宿主上跑。macOS 特有的那几条
#: 保证并没有因此失去看护，它们由**不碰 pathlib**、因而 Windows 上照跑的用例
#: 分别覆盖：`ships_bundled_runtime()` 的形态矩阵、两种布局的识别、平台/架构核对。
posix_host_only = pytest.mark.skipif(
    os.name == "nt",
    reason="Windows 上无法模拟 POSIX 宿主：pool 里的 Path() 会分派到 PosixPath")


@posix_host_only
def test_macos_desktop_uses_bundled_runtime_by_default(tmp_path, monkeypatch):
    """**macOS 的产品承诺本身**：没有 Python / Homebrew / Conda 的 Mac 上，
    装完 dmg 就能渲染。"""
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "platform", "darwin")
    py = make_runtime(tmp_path / "rt", manifest=macos_manifest(), layout="posix")
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(tmp_path / "rt"))
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "_has_matplotlib", lambda p, **kw: True)
    assert pool.select_worker_python() == (py, pool.SOURCE_BUNDLED)


@posix_host_only
def test_macos_user_choice_still_beats_the_bundled_runtime(tmp_path, monkeypatch):
    """高级用户的脚本要 rdkit / astropy，内置环境里没有——他挑的 conda
    必须继续赢。内置 runtime 是**默认**，不是**强制**。"""
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "platform", "darwin")
    make_runtime(tmp_path / "rt", manifest=macos_manifest(), layout="posix")
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(tmp_path / "rt"))
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "_has_matplotlib", lambda p, **kw: True)

    mine = tmp_path / "miniconda3" / "bin" / "python3"
    mine.parent.mkdir(parents=True)
    mine.write_text("#!/bin/sh\n")
    config.set_worker_python(str(mine))
    assert pool.select_worker_python() == (str(mine), pool.SOURCE_CONFIGURED)


def test_bundled_child_env_drops_hostile_python_vars():
    """macOS 上没有 `._pth` 那层隔离：用户从终端启动 Magplot 时，shell 里为
    Conda / 自家项目设的 PYTHONHOME、PYTHONPATH 会原样传给内置解释器——
    轻则 import 到别的 numpy，重则解释器根本起不来。

    这一档还只在「从终端启动」时复现，从 Finder 双击一切正常，最难查。
    """
    hostile = {
        "PYTHONHOME": "/opt/homebrew/opt/python@3.12",
        "PYTHONPATH": "/Users/me/myproject",
        "PYTHONSTARTUP": "/Users/me/.pythonrc",
        "PYTHONUSERBASE": "/Users/me/.local",
        "PATH": "/usr/bin",
        "LANG": "zh_CN.UTF-8",
    }
    env = runtime.child_env(hostile)
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE"):
        assert key not in env, f"{key} 会把内置 runtime 带跑偏，必须摘掉"
    # 与 Python 无关的变量一个都不许动——那是用户的环境
    assert env["PATH"] == "/usr/bin" and env["LANG"] == "zh_CN.UTF-8"
    assert env["PYTHONNOUSERSITE"] == "1"


def test_bundled_runtime_never_writes_into_a_signed_app_bundle():
    """`.app` 是**签过名**的：往里面写一个 __pycache__ 当场破坏代码签名，
    用户下次启动看到的是「应用已损坏」，而不是「多了几个缓存文件」。

    保证这条的是 `-B`（命令行参数任何时候都算数），环境变量只是补充。
    """
    assert "-B" in runtime.child_args()
    env = runtime.child_env({})
    data = str(config.data_dir())
    assert env["MPLCONFIGDIR"].startswith(data)


def test_child_env_never_redirects_the_bytecode_cache():
    """**不许再设 `PYTHONPYCACHEPREFIX`。**

    它改的不只是写的位置，读的位置也跟着改（实测 `__cached__` 变成
    `<prefix>/<绝对路径镜像>/mod.cpython-313.pyc`，源码旁边那份
    `__pycache__` 再也不看）。而 `-B` 又禁止写入——两条合起来，构建期
    编好随包发出的 UNCHECKED_HASH 字节码一份都用不上，每个冷启动的 worker
    都要把整个科学栈从源码重编一遍，预编译要省的钱全花回去了。

    Windows 上 `._pth` 的隔离模式会忽略它，所以症状只在 macOS 上出现——
    也就是最容易被「本机试了没问题」漏掉的那一格。
    """
    assert "PYTHONPYCACHEPREFIX" not in runtime.child_env({})
    # 用户 shell 里设了也要摘掉：它同样会让内置 runtime 读错地方
    assert "PYTHONPYCACHEPREFIX" not in runtime.child_env(
        {"PYTHONPYCACHEPREFIX": "/somewhere/else"})
