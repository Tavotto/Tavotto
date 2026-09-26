"""回执「已观察的输入」的路径判据（`figcapture._within`）：Windows 布局下也得成立。

#498 第四轮：真 worker 的 `inputs.local_modules` 在 Windows 上恒空——`observed_local_modules` 用 `os.path.commonpath`
判「模块文件在项目内 / 在引擎目录内」，Windows runner 的临时目录在 C:、checkout 在 D:，跨盘的 `commonpath` 抛
ValueError，`except … continue` 把本来该记的模块吞了；POSIX 只有一个根，所以本机怎么跑都绿。判据改成与
`projectenv.contained_path` 同一形状（realpath 之后按前缀判、`+ sep`、`normcase`），文件观察与本地模块共用一份。
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import sys
import sysconfig
import types

import pytest

from tavotto.engine import figcapture

WIN_ROOT = r"C:\Users\runneradmin\AppData\Local\Temp\pytest-0\proj"


@pytest.mark.parametrize(
    ("real", "root", "expected"),
    [
        (WIN_ROOT + r"\labhelp.py", WIN_ROOT, True),
        (WIN_ROOT, WIN_ROOT, True),
        # 跨盘：临时目录在 C:、引擎目录在 D:——不抛异常，就是「不在里面」
        (WIN_ROOT + r"\labhelp.py", r"D:\a\Tavotto\Tavotto\src\tavotto\engine", False),
        # 大小写：Windows 不敏感，两边 normcase 之后再比
        (r"c:\users\RUNNERADMIN\AppData\Local\Temp\pytest-0\proj\x.py", WIN_ROOT, True),
        # 同前缀不同目录不算（`+ sep` 那一条）
        (WIN_ROOT + r"-evil\x.py", WIN_ROOT, False),
        # 根带尾分隔符也一样
        (WIN_ROOT + r"\sub\y.py", WIN_ROOT + "\\", True),
    ],
)
def test_within_on_windows_layouts(real, root, expected):
    assert figcapture._within(real, root, pathmod=ntpath) is expected


def test_within_on_posix_layouts():
    assert figcapture._within("/tmp/p/x.py", "/tmp/p", pathmod=posixpath)
    assert not figcapture._within("/tmp/p-evil/x.py", "/tmp/p", pathmod=posixpath)
    assert not figcapture._within("/opt/engine/worker.py", "/tmp/p", pathmod=posixpath)


def _module(name: str, file: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__file__ = file
    return mod


def test_local_modules_survive_an_exclude_dir_on_another_drive(tmp_path, monkeypatch):
    """项目里的模块要记、引擎目录里的不记；引擎目录与项目「不在同一个盘」时前者照样记。
    POSIX 造不出第二个盘，这里把 `os.path.commonpath` 换成一律抛 ValueError 的替身：判据若退回 commonpath，
    这条当场红（#498 第四轮 Windows 的形状）。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "labhelp.py").write_bytes(b"def scale(x):\n    return 2 * x\n")
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "figcapture.py").write_bytes(b"# engine\n")
    modules = {
        "labhelp": _module("labhelp", str(proj / "labhelp.py")),
        "figcapture": _module("figcapture", str(engine / "figcapture.py")),
        "builtins_like": types.ModuleType("builtins_like"),  # 没有 __file__
        "elsewhere": _module("elsewhere", str(tmp_path / "elsewhere.py")),
    }

    def boom(paths):
        raise ValueError("Paths don't have the same drive")

    monkeypatch.setattr(os.path, "commonpath", boom)
    out = figcapture.observed_local_modules(str(proj), modules, exclude_dir=str(engine))
    assert [m["name"] for m in out] == ["labhelp"], out
    assert (
        out[0]["path"] == "labhelp.py"
        and out[0]["sha256"] == figcapture.hash_file(str(proj / "labhelp.py"))[0]
    )


def test_observer_notes_a_file_inside_the_root_without_commonpath(tmp_path, monkeypatch):
    """文件观察那一半用的是同一份判据：`commonpath` 炸了也照样记项目内的文件、不记项目外的。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "data.csv").write_bytes(b"x\n1\n")
    (tmp_path / "outside.txt").write_bytes(b"no\n")
    monkeypatch.setattr(
        os.path, "commonpath", lambda paths: (_ for _ in ()).throw(ValueError("drive"))
    )
    obs = figcapture.InputObserver(str(proj))
    obs._note(str(proj / "data.csv"))
    obs._note(str(tmp_path / "outside.txt"))
    assert list(obs._seen.values()) == ["data.csv"], obs._seen


# ---------------------------------------------------------------- 项目根里的 .venv（QA 2026-09-24 PATH-B2）
def _venv_project(tmp_path):
    """项目根里放 `.venv`（ADR 0057 首开第 4 条的标准形态）：site-packages 里有库自己读的文件。"""
    proj = tmp_path / "proj"
    site = proj / ".venv" / "lib" / "python3.13" / "site-packages"
    (site / "matplotlib" / "mpl-data").mkdir(parents=True)
    (site / "matplotlib" / "_path.cpython-313-darwin.so").write_bytes(b"\x7fELF")
    (site / "matplotlib" / "mpl-data" / "matplotlibrc").write_bytes(b"backend: Agg\n")
    (site / "matplotlib" / "__init__.py").write_bytes(b"# lib\n")
    (proj / "analysis").mkdir()
    (proj / "analysis" / "points.csv").write_bytes(b"x,y\n0,0\n")
    (proj / "labhelp.py").write_bytes(b"def f():\n    return 1\n")
    return proj, site


def _as_interpreter(monkeypatch, prefix, site):
    """观察器所在进程的解释器 = `prefix`（主语：跑用户脚本的那个进程的 `sys.prefix`）。"""
    for name in ("prefix", "exec_prefix"):
        monkeypatch.setattr(sys, name, str(prefix))
    real_get_paths = sysconfig.get_paths
    monkeypatch.setattr(
        sysconfig,
        "get_paths",
        lambda *a, **k: {**real_get_paths(*a, **k), "purelib": str(site), "platlib": str(site)},
    )


def test_observer_does_not_count_the_project_venv_as_data(tmp_path, monkeypatch):
    """项目 `.venv` 里的扩展模块 / mplstyle / rc 不是脚本的数据输入，也不占预算：预算被库文件挤满时（曾经一个
    脚本 41–67 条、`truncated=true`），之后读的真数据会从回执里丢掉。"""
    proj, site = _venv_project(tmp_path)
    _as_interpreter(monkeypatch, proj / ".venv", site)
    monkeypatch.setattr(figcapture, "INPUT_OBSERVER_MAX_FILES", 2)
    obs = figcapture.InputObserver(str(proj))
    obs._note(str(site / "matplotlib" / "_path.cpython-313-darwin.so"))
    obs._note(str(site / "matplotlib" / "mpl-data" / "matplotlibrc"))
    obs._note(str(proj / "analysis" / "points.csv"))
    report = obs.report()
    assert [f["path"] for f in report["files"]] == ["analysis/points.csv"], report
    assert report["truncated"] is False


def test_local_modules_do_not_list_the_project_venv_packages(tmp_path, monkeypatch):
    proj, site = _venv_project(tmp_path)
    _as_interpreter(monkeypatch, proj / ".venv", site)
    modules = {
        "matplotlib": _module("matplotlib", str(site / "matplotlib" / "__init__.py")),
        "labhelp": _module("labhelp", str(proj / "labhelp.py")),
    }
    out = figcapture.observed_local_modules(str(proj), modules)
    assert [m["name"] for m in out] == ["labhelp"], out


@pytest.mark.parametrize("where", ["root", "above_root"])
def test_an_interpreter_prefix_that_is_or_contains_the_root_excludes_nothing(
    tmp_path, monkeypatch, where
):
    """前缀**等于**项目根、或把项目根包在里面（项目放在某个环境目录底下）时不排除：否则项目里每一个数据
    文件都跟着消失——宁可多记，不能全丢。"""
    proj, _site = _venv_project(tmp_path)
    prefix = proj if where == "root" else tmp_path
    site = prefix / "lib" / "python3.13" / "site-packages"
    _as_interpreter(monkeypatch, prefix, site)
    dirs = figcapture.interpreter_dirs_within(os.path.realpath(proj))
    # 前缀本身不进排除表；它底下真正的 site-packages 落在项目根里才进
    assert dirs == ((os.path.realpath(site),) if where == "root" else ()), dirs
    obs = figcapture.InputObserver(str(proj))
    obs._note(str(proj / "analysis" / "points.csv"))
    assert list(obs._seen.values()) == ["analysis/points.csv"]
