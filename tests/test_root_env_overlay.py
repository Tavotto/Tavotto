"""脚本 `import ROOT` 时叠加用户装的 CERN ROOT 环境（ADR 0079 修订 2026-10-21 b）。

判据的主语：

* **发现**（`rootenv.discover_installs`）：只认"有 thisroot + 有 PyROOT 标志"的目录，来源 = `ROOTSYS` / 注册表卸载项 /
  常见位置；有界、按真实路径去重；纯 stat，假目录树 + 注入的注册表读取器，不依赖真 Windows；
* **叠加层**（`rootenv.apply_overlay`）：thisroot 做的那几件事；不改入参；安装根没了就不叠；持久化的只有安装根；
* **选择**（`deprepair._detect_environment_unlocked`）：只在脚本 import ROOT、且没有候选裸起就导入得了它时才试"解释器 × ROOT"
  组合；用假体检（按 Python 版本 × ROOT 版本决定 ROOT 导不导得入）验证选中谁、探了几个、记了什么；
* **持久化与再启动**：叠加层随项目记录；记着的解释器按带叠加层量，不再每次重新发现；worker 起进程时叠上，会话复用前核对；
* **落选原因**：`root_python_mismatch` + 哪个 ROOT（只有版本，不带路径）。
"""

from __future__ import annotations

import ast
import ntpath
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from support.dependency_repair import clean_state  # noqa: F401 — 夹具
from tavotto.engine import (
    deprepair,
    envlease,
    launcher,
    pool as engine_pool,
    projectenv,
    rootenv,
    runtime,
    userenvs,
)

POSIX = pytest.mark.skipif(os.name == "nt", reason="假目录树按 POSIX 布局造")


# ===========================================================================
# 假 ROOT 安装
# ===========================================================================
def make_root(base: Path, name: str, *, nt: bool, version: str = "6.38.02", tags=(), pyroot="ok"):
    """造一个像官方包的目录树。`pyroot`：`ok` = 带 PyROOT 包；`script` = 只有 ROOT.py；`none` = 没有 PyROOT 标志。"""
    root = base / name
    (root / "bin").mkdir(parents=True)
    (root / "bin" / ("thisroot.bat" if nt else "thisroot.sh")).write_text("rem\n", encoding="utf-8")
    (root / "include").mkdir()
    (root / "include" / "RVersion.h").write_text(
        f'#define ROOT_RELEASE "{version}"\n', encoding="utf-8"
    )
    pydir = root / ("bin" if nt else "lib")
    pydir.mkdir(exist_ok=True)
    if pyroot == "ok":
        (pydir / "ROOT").mkdir()
        (pydir / "ROOT" / "__init__.py").write_text("", encoding="utf-8")
    elif pyroot == "script":
        (pydir / "ROOT.py").write_text("", encoding="utf-8")
    for tag in tags:
        (pydir / f"libcppyy3_{tag}.{'pyd' if nt else 'so'}").write_text("", encoding="utf-8")
    return root


class FakeReg:
    """`userenvs._WinregReader` 的替身：只有 Uninstall 键。"""

    def __init__(self, entries: dict[str, dict[str, str]]):
        self.entries = entries

    def subkeys(self, hive, view, path):
        return list(self.entries) if hive == "HKLM" and view == 64 else []

    def values(self, hive, view, path):
        return (
            self.entries.get(path.rsplit("\\", 1)[-1], {}) if hive == "HKLM" and view == 64 else {}
        )


# ===========================================================================
# 发现
# ===========================================================================
@POSIX
def test_a_posix_install_needs_thisroot_and_a_pyroot_marker(tmp_path):
    good = make_root(tmp_path, "root", nt=False)
    no_py = make_root(tmp_path, "nopy", nt=False, pyroot="none")
    no_this = make_root(tmp_path, "nothis", nt=False)
    (no_this / "bin" / "thisroot.sh").unlink()
    info = rootenv.inspect_root(str(good), nt=False)
    assert info and info["root"] == str(good) and info["version"] == "6.38.02"
    assert info["python_dir"] == str(good / "lib")
    assert rootenv.inspect_root(str(no_py), nt=False) is None
    assert rootenv.inspect_root(str(no_this), nt=False) is None
    assert rootenv.inspect_root(str(tmp_path / "missing"), nt=False) is None
    assert rootenv.inspect_root(str(tmp_path), nt=False) is None  # 一个普通目录


@POSIX
def test_windows_layout_uses_thisroot_bat_and_bin_and_a_bare_sh_is_not_enough(tmp_path):
    win = make_root(tmp_path, "root_v6.38.02", nt=True, tags=(11,))
    info = rootenv.inspect_root(str(win), nt=True)
    assert info and info["python_dir"] == str(win / "bin") and info["py_tags"] == frozenset({11})
    # 同一棵树按 POSIX 看：没有 thisroot.sh
    assert rootenv.inspect_root(str(win), nt=False) is None
    # 单文件 ROOT.py 也算 PyROOT 标志
    script = make_root(tmp_path, "root_script", nt=True, pyroot="script")
    assert rootenv.inspect_root(str(script), nt=True) is not None


@POSIX
def test_homebrew_layout_python_dir_is_lib_root(tmp_path):
    cellar = tmp_path / "Cellar" / "root" / "6.36.04_1"
    (cellar / "bin").mkdir(parents=True)
    (cellar / "bin" / "thisroot.sh").write_text("", encoding="utf-8")
    (cellar / "lib" / "root" / "ROOT").mkdir(parents=True)
    (cellar / "lib" / "root" / "ROOT" / "__init__.py").write_text("", encoding="utf-8")
    info = rootenv.inspect_root(str(cellar), nt=False)
    assert info and info["python_dir"] == str(cellar / "lib" / "root")
    assert info["version"] == "6.36.04_1"[:7] or info["version"].startswith(
        "6.36.04"
    )  # 目录名里的版本
    assert str(cellar / "lib" / "root") in info["lib_dirs"]


@POSIX
def test_version_falls_back_to_the_directory_name_and_old_style_release(tmp_path):
    no_header = make_root(tmp_path, "root_v6.30.02", nt=False)
    (no_header / "include" / "RVersion.h").unlink()
    assert rootenv.inspect_root(str(no_header), nt=False)["version"] == "6.30.02"
    old = make_root(tmp_path, "oldroot", nt=False, version="6.26/10")
    assert rootenv.inspect_root(str(old), nt=False)["version"] == "6.26.10"
    assert rootenv.short_version("6.38.02") == "6.38"


@POSIX
def test_rootsys_comes_first_and_everything_is_deduplicated_by_real_path(tmp_path, monkeypatch):
    a = make_root(tmp_path, "a", nt=False)
    link = tmp_path / "alink"
    link.symlink_to(a)
    b = make_root(tmp_path, "b", nt=False, version="6.30.00")
    monkeypatch.setattr(rootenv, "_globbed", lambda pattern: [str(link), str(b)])
    got = rootenv.discover_installs({"ROOTSYS": str(a)}, nt=False, home=str(tmp_path / "h"))
    assert [i["root"] for i in got] == [str(a), str(b)]
    assert got[0]["source"] == "rootsys" and got[1]["source"] == "common"


@POSIX
def test_install_list_is_bounded(tmp_path, monkeypatch):
    roots = [make_root(tmp_path, f"r{i}", nt=False, version=f"6.{i:02d}.00") for i in range(12)]
    monkeypatch.setattr(rootenv, "_globbed", lambda pattern: [str(r) for r in roots])
    assert len(rootenv.discover_installs({}, nt=False, home=str(tmp_path))) == rootenv.MAX_INSTALLS
    top = rootenv.newest(rootenv.discover_installs({}, nt=False, home=str(tmp_path), limit=12))
    assert [i["version"] for i in top] == ["6.11.00", "6.10.00"]


@POSIX
def test_common_windows_locations_and_rootsys(tmp_path, monkeypatch):
    fixed = make_root(tmp_path, "root_v6.38.02", nt=True)
    pf = tmp_path / "Program Files"
    pf.mkdir()
    in_pf = make_root(pf, "ROOT6", nt=True, version="6.36.00")
    local = tmp_path / "Local"
    local.mkdir()
    in_local = make_root(local, "root", nt=True, version="6.34.00")
    monkeypatch.setattr(rootenv, "_WINDOWS_FIXED", (str(tmp_path / "root_v*"),))
    got = rootenv.discover_installs(
        {"ProgramFiles": str(pf), "LOCALAPPDATA": str(local)}, nt=True, home=str(tmp_path)
    )
    assert [i["root"] for i in got] == [str(fixed), str(in_pf), str(in_local)]


@POSIX
def test_registry_uninstall_entries_must_look_like_root_and_have_the_install_structure(tmp_path):
    real = make_root(tmp_path, "ROOT_6.38", nt=True)
    no_py = make_root(tmp_path, "ROOT_nopy", nt=True, pyroot="none")
    via_uninstaller = make_root(tmp_path, "ROOT_u", nt=True, version="6.36.00")
    reader = FakeReg(
        {
            "ROOT_is1": {"DisplayName": "ROOT 6.38.02 (VC17)", "InstallLocation": str(real)},
            # 名字像但没有 PyROOT 标志：不收
            "nopy": {"DisplayName": "ROOT 6.38.02", "InstallLocation": str(no_py)},
            # 名字不像 ROOT：不收（即便目录恰好是个 ROOT 安装）
            "kit": {"DisplayName": "Rootkit Hunter", "InstallLocation": str(real)},
            "other": {"DisplayName": "Python 3.12", "InstallLocation": str(real)},
            # 没有 InstallLocation：取卸载程序所在目录
            "u": {
                "DisplayName": "ROOT",
                "UninstallString": '"' + str(via_uninstaller / "uninstall.exe") + '" /S',
            },
        }
    )
    got = rootenv.discover_installs(
        {}, nt=True, registry_reader=reader, home=str(tmp_path), limit=8
    )
    roots = {i["root"] for i in got}
    assert str(real) in roots and str(via_uninstaller) in roots
    assert str(no_py) not in roots and len(got) == 2
    assert all(i["source"] == "registry" for i in got)
    assert ntpath.isabs("C:\\root")  # 注册表里存的是 Windows 路径


@POSIX
def test_registry_is_ignored_on_posix(tmp_path):
    real = make_root(tmp_path, "r", nt=False)
    reader = FakeReg({"x": {"DisplayName": "ROOT 6", "InstallLocation": str(real)}})
    assert rootenv.discover_installs({}, nt=False, registry_reader=reader, home=str(tmp_path)) == []


# ===========================================================================
# 叠加层
# ===========================================================================
@POSIX
def test_overlay_does_what_thisroot_sh_does_without_touching_the_input(tmp_path):
    root = make_root(tmp_path, "root", nt=False)
    spec = rootenv.overlay_spec(rootenv.inspect_root(str(root), nt=False))
    base = {"PATH": "/usr/bin", "PYTHONPATH": "/mine", "OTHER": "1"}
    got = rootenv.apply_overlay(base, spec, nt=False, platform="linux")
    assert base == {"PATH": "/usr/bin", "PYTHONPATH": "/mine", "OTHER": "1"}
    assert got["ROOTSYS"] == str(root)
    assert got["PATH"] == f"{root / 'bin'}:/usr/bin"
    assert got["PYTHONPATH"] == f"{root / 'lib'}:/mine"
    # 首项是 <root>/lib（大小写不敏感的文件系统上 lib/ROOT 与 lib/root 是同一个目录，后面可能多一项同目录）
    assert got["LD_LIBRARY_PATH"].split(":")[0] == str(root / "lib")
    assert "DYLD_LIBRARY_PATH" not in got
    assert got["CMAKE_PREFIX_PATH"] == str(root)
    assert got["ROOT_INCLUDE_PATH"] == str(root / "include")
    assert got["OTHER"] == "1"
    mac = rootenv.apply_overlay(base, spec, nt=False, platform="darwin")
    assert "DYLD_LIBRARY_PATH" in mac and "LD_LIBRARY_PATH" not in mac
    # 幂等：叠两次不重复前置
    assert rootenv.apply_overlay(got, spec, nt=False, platform="linux") == got


@POSIX
def test_windows_overlay_uses_semicolons_bin_and_no_library_path(tmp_path):
    root = make_root(tmp_path, "root", nt=True)
    spec = rootenv.overlay_spec(rootenv.inspect_root(str(root), nt=True))
    got = rootenv.apply_overlay({"PATH": "C:\\Windows"}, spec, nt=True)
    assert got["PATH"] == f"{root / 'bin'};C:\\Windows"
    assert got["PYTHONPATH"] == str(root / "bin")
    assert not any(k in got for k in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"))


@POSIX
def test_bundled_runtime_overlay_replaces_pythonpath_instead_of_prepending(tmp_path):
    root = make_root(tmp_path, "root", nt=False)
    spec = rootenv.overlay_spec(rootenv.inspect_root(str(root), nt=False))
    got = rootenv.apply_overlay(
        {"PYTHONPATH": "/shell/stuff"}, spec, nt=False, replace_pythonpath=True
    )
    assert got["PYTHONPATH"] == str(root / "lib")


@POSIX
def test_a_vanished_or_malformed_overlay_is_a_no_op(tmp_path):
    root = make_root(tmp_path, "root", nt=False)
    spec = rootenv.overlay_spec(rootenv.inspect_root(str(root), nt=False))
    import shutil

    shutil.rmtree(root)
    assert rootenv.apply_overlay({"PATH": "/x"}, spec, nt=False) == {"PATH": "/x"}
    for junk in (
        None,
        "root",
        {"kind": "other", "root": "/x"},
        {"kind": "root", "root": "rel"},
        {"kind": "root"},
    ):
        assert rootenv.valid_spec(junk) is None
        assert rootenv.apply_overlay({"A": "1"}, junk) == {"A": "1"}
    assert rootenv.valid_spec({"kind": "root", "root": "/abs", "version": 3, "extra": "x"}) == {
        "kind": "root",
        "root": "/abs",
        "version": "",
    }


def test_only_scripts_that_import_root_want_it():
    assert rootenv.wants_root(["numpy", "ROOT"]) and not rootenv.wants_root(["numpy", "root"])


# ===========================================================================
# 选择：解释器 × ROOT（假体检）
# ===========================================================================
class _Joint:
    status = "nothing_needed"

    def to_payload(self) -> dict:
        return {
            "status": "nothing_needed",
            "missing": [],
            "satisfied": [],
            "unknown": ["ROOT"],
        }


def _touch(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", "utf-8")
    return str(path)


@pytest.fixture
def rig(tmp_path, monkeypatch, clean_state):  # noqa: F811
    """默认链条（"内置"）= py3.13；一个用户环境 = py3.11。ROOT 6.38 配 3.11，ROOT 6.36 配 3.13（`compat`）。
    假体检按 (解释器版本, 叠加的 ROOT 版本) 决定 ROOT 导不导得入；记下每一次体检。"""
    monkeypatch.delenv("TAVOTTO_ENV_ADOPTION", raising=False)
    monkeypatch.delenv("TAVOTTO_WORKER_PYTHON", raising=False)
    monkeypatch.delenv("TAVOTTO_USER_ENV_DISCOVERY", raising=False)
    for name in ("_conda_prefixes", "_pyenv_pythons"):
        monkeypatch.setattr(userenvs, name, lambda *a, **k: [])
    monkeypatch.setattr(userenvs, "login_shell_pythons", lambda: [])
    monkeypatch.setattr(userenvs, "cached_login_shell_pythons", lambda: [])
    monkeypatch.setattr(engine_pool, "system_python_candidates", lambda **_: [])
    userenvs.reset_cache()
    envlease.reset_for_tests()

    root = tmp_path / "proj"
    root.mkdir()
    builtin = _touch(tmp_path / "builtin" / "bin" / "python")
    versions = {builtin: "3.13.1"}
    box: dict = {
        "root": root,
        "builtin": builtin,
        "versions": versions,
        "candidates": [],
        "compat": {("3.13", "6.36"): True, ("3.11", "6.38"): True},
        "probes": [],
        "installs": [],
        "bare_ok": set(),
    }

    def resolve(figures_dir=None, *, script=None, discover=True):
        record = projectenv.remembered_record(root)
        if record and record.get("exists") and record.get("mode") != "default":
            return record["path"], engine_pool.remembered_source(root, record["path"])
        return builtin, engine_pool.SOURCE_BUNDLED

    def joint_plan_for(r, s, **kw):
        return _Joint(), "managed", resolve(r, script=s)[0]

    def probe_environment(python, *args, modules=(), env_overlay=None, bundled=False, **kw):
        box["probes"].append({"python": python, "overlay": env_overlay, "bundled": bundled})
        version = versions[python]
        minor = ".".join(version.split(".")[:2])
        if env_overlay:
            ok = box["compat"].get((minor, rootenv.short_version(env_overlay["version"])), False)
        else:
            ok = python in box["bare_ok"]
        return {
            "ok": True,
            "code": "",
            "python": python,
            "python_version": version,
            "matplotlib_version": "3.9.0",
            "support": "verified",
            "modules_ok": {m: ok for m in modules},
            "modules_detail": {m: ("ok" if ok else "import_error") for m in modules},
        }

    monkeypatch.setattr(engine_pool, "resolve_worker_python", resolve)
    monkeypatch.setattr(deprepair, "joint_plan_for", joint_plan_for)
    monkeypatch.setattr(deprepair, "private_python_target", lambda r, s: None)
    monkeypatch.setattr(
        deprepair,
        "user_environment_candidates",
        lambda r, s, exclude="": [c for c in box["candidates"] if c["python"] != exclude],
    )
    monkeypatch.setattr(projectenv, "probe_environment", probe_environment)
    monkeypatch.setattr(userenvs, "root_installs", lambda budget=None: list(box["installs"]))
    monkeypatch.setattr(runtime, "bundled_python", lambda: builtin)
    monkeypatch.setattr(launcher, "launcher_for_interpreter", lambda python: None)

    def add_python(name: str, version: str) -> str:
        python = _touch(tmp_path / name / "bin" / "python")
        versions[python] = version
        box["candidates"].append({"python": python, "source": userenvs.SOURCE_CONDA, "label": name})
        return python

    def add_root(name: str, version: str, tags=()) -> dict:
        info = rootenv.inspect_root(
            str(make_root(tmp_path, name, nt=False, version=version, tags=tags)), nt=False
        )
        box["installs"].append(info)
        return info

    box.update(add_python=add_python, add_root=add_root)
    yield box
    userenvs.reset_cache()
    envlease.reset_for_tests()


def _decide(rig, **kw):
    return deprepair.decide_environment_pinned(rig["root"], "fig.py", **kw)


@POSIX
def test_overlay_pairs_the_right_python_with_the_right_root_and_remembers_it(rig):
    py311 = rig["add_python"]("py311", "3.11.9")
    rig["add_root"]("root638", "6.38.02")

    decision = _decide(rig)

    entry = decision.adopted
    assert entry is not None and entry["python"] == py311
    assert entry["source"] == userenvs.SOURCE_ROOT_OVERLAY
    assert entry["label"] == "Python 3.11 + ROOT 6.38"
    assert decision.env_overlay is not None and decision.env_overlay["version"] == "6.38.02"
    # 持久化：解释器 + 叠加层；叠加层只有三个字段，环境变量不进设置
    record = projectenv.remembered_record(rig["root"])
    assert record["path"] == py311 and record["automatic"] is True
    assert set(record["env_overlay"]) == {"kind", "root", "version"}
    assert projectenv.remembered_overlay(rig["root"], py311) == decision.env_overlay
    assert projectenv.remembered_overlay(rig["root"], rig["builtin"]) is None
    # 公开投影不带路径
    public = userenvs.public(entry)
    assert public["overlay"] == {"kind": "root", "version": "6.38.02"}
    assert str(rig["root"]) not in str(public) and "root638" not in str(public)


@POSIX
def test_second_start_runs_with_the_remembered_overlay_and_does_not_rediscover(rig):
    py311 = rig["add_python"]("py311", "3.11.9")
    rig["add_root"]("root638", "6.38.02")
    assert _decide(rig).adopted is not None
    rig["probes"].clear()

    again = _decide(rig)

    assert again.adopted is None  # 不再换
    assert again.python == py311 and again.env_overlay is not None
    # 唯一的体检是"按带叠加层量记着的那个"，没有重新发现 / 组合探测
    # （体检结论走缓存，可能一次都不再起；起的话只能是"带叠加层量记着的那个"）
    assert all(p["python"] == py311 and p["overlay"] is not None for p in rig["probes"])


@POSIX
def test_a_bare_candidate_that_already_imports_root_wins_and_no_overlay_is_probed(rig):
    py311 = rig["add_python"]("py311", "3.11.9")
    rig["bare_ok"].add(py311)
    rig["add_root"]("root638", "6.38.02")

    entry = _decide(rig).adopted

    assert entry["python"] == py311 and "overlay" not in entry
    assert projectenv.remembered_overlay(rig["root"]) is None
    assert not any(p["overlay"] for p in rig["probes"])


@POSIX
def test_scripts_that_do_not_import_root_never_get_an_overlay(rig, monkeypatch):
    rig["add_python"]("py311", "3.11.9")
    rig["add_root"]("root638", "6.38.02")

    class NoRoot(_Joint):
        def to_payload(self):
            return {**super().to_payload(), "unknown": ["qa_probe_pkg"]}

    monkeypatch.setattr(
        deprepair, "joint_plan_for", lambda r, s, **kw: (NoRoot(), "managed", rig["builtin"])
    )
    assert _decide(rig).adopted is None
    assert not any(p["overlay"] for p in rig["probes"])


@POSIX
def test_no_root_installed_means_no_overlay_and_no_extra_probes(rig):
    rig["add_python"]("py311", "3.11.9")
    assert _decide(rig).adopted is None
    assert not any(p["overlay"] for p in rig["probes"])


@POSIX
def test_combinations_are_bounded_and_prefer_the_newest_roots(rig):
    rig["compat"].clear()  # 3.12 与内置 3.13 都谁也不配
    for i in range(4):
        rig["add_python"](f"py{i}", "3.12.0")
    for ver in ("6.30.00", "6.34.00", "6.38.02", "6.36.00"):
        rig["add_root"](f"root{ver}", ver)

    assert _decide(rig).adopted is None  # 3.12 谁都不配

    overlaid = [p for p in rig["probes"] if p["overlay"]]
    assert len(overlaid) <= rootenv.MAX_COMBOS
    assert {p["overlay"]["version"] for p in overlaid} == {"6.38.02", "6.36.00"}


@POSIX
def test_bare_python_is_listed_before_the_overlay_pair_and_the_builtin_can_be_a_base(rig):
    # 内置 3.13 配 ROOT 6.36：内置作为底，体检带 bundled 标记（摘 PYTHONPATH 之后才叠）
    rig["add_root"]("root636", "6.36.00")
    decision = _decide(rig)
    entry = decision.adopted
    assert entry is not None and entry["python"] == rig["builtin"]
    assert entry["label"] == "Python 3.13 + ROOT 6.36"
    assert any(p["overlay"] and p["bundled"] for p in rig["probes"])
    # 记着的就是内置 runtime：来源仍是内置，worker 按内置的那套起
    assert decision.source == engine_pool.SOURCE_BUNDLED


@POSIX
def test_before_run_nothing_is_started_and_nothing_is_adopted(rig):
    rig["add_python"]("py311", "3.11.9")
    rig["add_root"]("root638", "6.38.02")
    assert _decide(rig, project_exec=False).adopted is None
    assert not any(p["overlay"] for p in rig["probes"])
    assert projectenv.remembered_record(rig["root"]) is None


@POSIX
def test_rejections_name_the_root_and_flag_a_python_mismatch(rig):
    py311 = rig["add_python"]("py311", "3.11.9")
    rig["compat"].pop(("3.13", "6.36"))  # 内置 3.13 不再配 6.36：6.36 对 3.11 就是错配
    rig["add_root"]("root638", "6.38.02")
    rig["add_root"]("root636", "6.36.00")
    rejected: list[dict] = []
    with deprepair._detect_lock(str(rig["root"])):
        entry = deprepair._detect_environment_unlocked(
            str(rig["root"]), "fig.py", rejected=rejected
        )
    assert entry["python"] == py311 and entry["overlay"]["version"] == "6.38.02"
    by_label = {r["label"]: r for r in rejected}
    wrong = by_label["Python 3.11 + ROOT 6.36"]
    assert wrong["reason"] == userenvs.REJECT_ROOT_PYTHON_MISMATCH
    assert wrong["root_version"] == "6.36.00" and wrong["source"] == "root_overlay"
    # 同一个解释器的裸起与叠加是不同的候选：id 各自不同；被选中的那一对不在落选里
    ids = [r["id"] for r in rejected]
    assert len(ids) == len(set(ids))
    assert "Python 3.11 + ROOT 6.38" not in by_label
    assert userenvs.REJECT_ROOT_PYTHON_MISMATCH in userenvs.REJECT_CODES


@POSIX
def test_identity_separates_a_python_from_the_same_python_with_a_root(rig):
    py311 = rig["add_python"]("py311", "3.11.9")
    spec = {"kind": "root", "root": "/r", "version": "6.38.02"}
    bare = {"python": py311, "source": "conda"}
    over = {"python": py311, "source": "root_overlay", "overlay": spec}
    assert userenvs.entry_key(bare) != userenvs.entry_key(over)
    assert userenvs.env_id(py311) != userenvs.env_id(py311, spec)
    assert userenvs.env_id(py311) == projectenv.env_id(py311)  # 没有叠加层时与以前逐字相同


# ===========================================================================
# 项目记录
# ===========================================================================
@POSIX
def test_remember_with_and_without_an_overlay(tmp_path, clean_state):  # noqa: F811
    proj = tmp_path / "p"
    proj.mkdir()
    py = _touch(tmp_path / "e" / "bin" / "python")
    spec = {"kind": "root", "root": str(tmp_path / "r"), "version": "6.38.02"}
    assert projectenv.remember(proj, py, automatic=True, env_overlay=spec)
    assert projectenv.remembered_overlay(proj, py) == spec
    # 换解释器 / 不带叠加层重记 = 旧叠加层清掉
    assert projectenv.remember(proj, py, automatic=True)
    assert projectenv.remembered_overlay(proj) is None
    projectenv.remember(proj, py, automatic=True, env_overlay=spec)
    other = _touch(tmp_path / "o" / "bin" / "python")
    assert projectenv.remembered_overlay(proj, other) is None
    projectenv.remember_default(proj)
    assert projectenv.remembered_overlay(proj) is None
    # 垃圾不被收
    projectenv.remember(proj, py, automatic=True, env_overlay={"kind": "root", "root": "rel"})
    assert projectenv.remembered_overlay(proj) is None


# ===========================================================================
# 起进程 / 体检的环境
# ===========================================================================
@POSIX
def test_launch_env_applies_the_overlay_last(tmp_path):
    root = make_root(tmp_path, "root", nt=False)
    spec = rootenv.overlay_spec(rootenv.inspect_root(str(root), nt=False))
    # 内置：env 已被 child_env 摘了 PYTHONPATH；叠加层替换而不是把 shell 的吃回来
    env = engine_pool.launch_env(None, "/p", {"PATH": "/bin"}, spec, bundled=True)
    assert env["PYTHONPATH"] == str(root / "lib") and env["ROOTSYS"] == str(root)
    # 用户解释器 + env=None（继承）：基于当前进程环境
    got = engine_pool.launch_env(None, "/p", None, spec, bundled=False)
    assert got["ROOTSYS"] == str(root) and got["PATH"].startswith(str(root / "bin"))
    # 没有叠加层：原样（None 仍是 None = 继承）
    assert engine_pool.launch_env(None, "/p", None, None, bundled=False) is None


@POSIX
def test_overlay_for_prefers_the_pinned_decision_over_the_shared_record(tmp_path, clean_state):  # noqa: F811
    proj = tmp_path / "p"
    proj.mkdir()
    py = _touch(tmp_path / "e" / "bin" / "python")
    rec = {"kind": "root", "root": str(tmp_path / "rec"), "version": "6.30.00"}
    pin_spec = {"kind": "root", "root": str(tmp_path / "pin"), "version": "6.38.02"}
    projectenv.remember(proj, py, automatic=True, env_overlay=rec)
    assert engine_pool._overlay_for(str(proj), py, None) == rec
    pinned = engine_pool.EnvironmentDecision(python=py, source="system", env_overlay=pin_spec)
    assert engine_pool._overlay_for(str(proj), py, pinned) == pin_spec
    # 钉下的是另一个解释器：叠加层不适用
    other = _touch(tmp_path / "o" / "bin" / "python")
    assert engine_pool._overlay_for(str(proj), other, pinned) is None
    assert "叠加环境已变" in engine_pool._REBUILD_REASONS


@POSIX
def test_probe_environment_overlays_after_the_base_env(tmp_path, monkeypatch):
    root = make_root(tmp_path, "root", nt=False)
    spec = rootenv.overlay_spec(rootenv.inspect_root(str(root), nt=False))
    seen: list[dict | None] = []

    def fake_run(argv, **kw):
        seen.append(kw.get("env"))
        out = (
            '{"version_info": [3, 13, 1], "python_version": "3.13.1", "matplotlib_version": "3.9.0",'
            ' "tavotto_worker_ok": true, "modules_ok": {}}'
        )
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(projectenv.subprocess, "run", fake_run)
    monkeypatch.setattr(launcher, "launcher_for_interpreter", lambda python: None)
    monkeypatch.delenv("ROOTSYS", raising=False)
    py = _touch(tmp_path / "e" / "bin" / "python")
    projectenv._probe_environment(py)
    projectenv._probe_environment(py, env_overlay=spec)
    assert seen[0] is None or "ROOTSYS" not in seen[0]
    assert seen[1]["ROOTSYS"] == str(root) and seen[1]["PYTHONPATH"].startswith(str(root / "lib"))


@POSIX
def test_remembered_bundled_python_keeps_the_bundled_source(tmp_path, monkeypatch, clean_state):  # noqa: F811
    proj = tmp_path / "p"
    proj.mkdir()
    bundled = _touch(tmp_path / "app" / "bin" / "python")
    monkeypatch.setattr(runtime, "bundled_python", lambda: bundled)
    assert engine_pool.remembered_source(proj, bundled) == engine_pool.SOURCE_BUNDLED
    other = _touch(tmp_path / "o" / "bin" / "python")
    assert engine_pool.remembered_source(proj, other) != engine_pool.SOURCE_BUNDLED


def test_pin_of_worker_carries_the_hot_sessions_overlay(tmp_path, clean_state):  # noqa: F811
    """写回重放 / 冷重放钉的是热会话自己的环境：带 ROOT 叠加的热会话，钉下的决定也带它，
    重放 worker 起来时 `_overlay_for` 回同一份（不是回头读项目记录，更不是 None）。"""
    proj = tmp_path / "p"
    proj.mkdir()
    py = _touch(tmp_path / "e" / "bin" / "python")
    spec = {"kind": "root", "root": str(tmp_path / "r"), "version": "6.38.02"}

    class _Hot:
        python = py
        python_source = "system"
        python_generation = ""
        launch_context = ""
        env_overlay = spec

    pin = engine_pool.pin_of_worker(_Hot())
    assert pin.env_overlay == spec
    assert engine_pool._overlay_for(str(proj), py, pin) == spec

    class _Bare(_Hot):
        env_overlay = None

    assert engine_pool.pin_of_worker(_Bare()).env_overlay is None


# ===========================================================================
# Windows 真机修订（2026-10-21，Win11 + ROOT 6.38.06 官方 zip + conda-forge Python 3.14）
# ===========================================================================
def make_official_win_root(base: Path, name: str = "root", *, build_python="3.14.0", macros=True):
    """官方 Windows 包的真实布局：`bin\\cppyy\\libcppyy.pyd`、`bin\\ROOT\\_python_version.py`、
    `include\\ROOT\\RVersion.hxx`（宏拼出版本，`include\\RVersion.h` 里没有版本号）。"""
    root = base / name
    (root / "bin" / "ROOT").mkdir(parents=True)
    (root / "bin" / "cppyy").mkdir()
    (root / "bin" / "thisroot.bat").write_text("rem\n", encoding="utf-8")
    (root / "bin" / "ROOT" / "__init__.py").write_text("", encoding="utf-8")
    (root / "bin" / "cppyy" / "libcppyy.pyd").write_text("", encoding="utf-8")
    (root / "bin" / "ROOT" / "_python_version.py").write_text(
        f'_root_python_version = "{build_python}"\n' if build_python else "# nothing\n",
        encoding="utf-8",
    )
    (root / "include" / "ROOT").mkdir(parents=True)
    (root / "include" / "RVersion.h").write_text("#include <ROOT/RVersion.hxx>\n", encoding="utf-8")
    if macros:
        (root / "include" / "ROOT" / "RVersion.hxx").write_text(
            "#define ROOT_VERSION_MAJOR 6\n#define ROOT_VERSION_MINOR 38\n"
            "#define ROOT_VERSION_PATCH 6\n"
            '#define ROOT_RELEASE_DATE "Jun 12 2026"\n'
            '#define ROOT_RELEASE ROOT_VERSION_MAJOR_STR "." ROOT_VERSION_MINOR_STR\n',
            encoding="utf-8",
        )
    return root


@POSIX
def test_version_comes_from_rversion_hxx_macros_and_is_empty_not_an_error_when_unreadable(tmp_path):
    root = make_official_win_root(tmp_path, "somewhere")
    assert rootenv.inspect_root(str(root), nt=True)["version"] == "6.38.06"
    # 宏读不出 -> 目录名兜底 -> 再没有就空串，不抛
    bare = make_official_win_root(tmp_path, "bare", macros=False)
    assert rootenv.inspect_root(str(bare), nt=True)["version"] == ""
    named = make_official_win_root(tmp_path, "root_v6.38.06.win64.python314.vc18", macros=False)
    assert rootenv.inspect_root(str(named), nt=True)["version"] == "6.38.06"
    # 字面 ROOT_RELEASE 仍优先
    lit = make_official_win_root(tmp_path, "lit", macros=False)
    (lit / "include" / "ROOT" / "RVersion.hxx").write_text(
        '#define ROOT_RELEASE "6.40.01"\n', encoding="utf-8"
    )
    assert rootenv.inspect_root(str(lit), nt=True)["version"] == "6.40.01"


@POSIX
def test_build_python_is_read_statically_from_python_version_py(tmp_path):
    root = make_official_win_root(tmp_path)
    assert rootenv.inspect_root(str(root), nt=True)["build_python"] == "3.14"
    for text, want in (
        ("_root_python_version = (3, 11, 9)\n", "3.11"),
        ("_root_python_version: str = '3.12.4'\n", "3.12"),
        ("_root_python_version = sys.version\n", ""),  # 认不出 -> 空，不执行
        ("raise SystemExit(1)\n", ""),  # 不执行文件
    ):
        (root / "bin" / "ROOT" / "_python_version.py").write_text(text, encoding="utf-8")
        assert rootenv.inspect_root(str(root), nt=True)["build_python"] == want
    (root / "bin" / "ROOT" / "_python_version.py").unlink()
    assert rootenv.inspect_root(str(root), nt=True)["build_python"] == ""


@POSIX
def test_windows_overlay_keeps_cppyy_out_of_pythonpath_and_passes_it_by_private_variables(tmp_path):
    root = make_official_win_root(tmp_path)
    # bin\\cppyy 里有与标准库同名的 .py：进了 PYTHONPATH 就在启动时遮住标准库（Win11 真机：体检 json -> re 崩）
    (root / "bin" / "cppyy" / "types.py").write_text("raise RuntimeError\n", encoding="utf-8")
    spec = rootenv.overlay_spec(rootenv.inspect_root(str(root), nt=True))
    got = rootenv.apply_overlay({"PATH": "C:\\Windows", "PYTHONPATH": "C:\\mine"}, spec, nt=True)
    assert got["PYTHONPATH"] == f"{root / 'bin'};C:\\mine"
    assert str(root / "bin" / "cppyy") not in got["PYTHONPATH"]
    assert got[rootenv.DLL_DIR_ENV] == str(root / "bin")
    assert got[rootenv.CPPYY_DIR_ENV] == str(root / "bin" / "cppyy")
    rep = rootenv.apply_overlay({}, spec, nt=True, replace_pythonpath=True)
    assert rep["PYTHONPATH"] == str(root / "bin")
    assert rootenv.apply_overlay(got, spec, nt=True) == got  # 幂等
    px = make_root(tmp_path, "px", nt=False)
    pspec = rootenv.overlay_spec(rootenv.inspect_root(str(px), nt=False))
    plain = rootenv.apply_overlay({}, pspec, nt=False)
    assert rootenv.DLL_DIR_ENV not in plain and rootenv.CPPYY_DIR_ENV not in plain


def _worker_dll_hook():
    """从 worker.py 的 AST 取出 `_startup_dll_directory` 与它的变量名常量（worker 是平铺 import，导入它会装一堆东西）。"""
    path = Path(rootenv.__file__).with_name("worker.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [
        n
        for n in tree.body
        if (isinstance(n, ast.FunctionDef) and n.name == "_startup_dll_directory")
        or (
            isinstance(n, ast.Assign)
            and any(
                getattr(t, "id", None)
                in ("_ROOT_DLL_DIR_ENV", "_ROOT_CPPYY_DIR_ENV", "_dll_dir_handles")
                for t in n.targets
            )
        )
    ]
    assert len(nodes) == 4
    return ast.Module(body=nodes, type_ignores=[])


def test_worker_dll_hook_name_mirrors_rootenv_and_registers_the_directory(tmp_path):
    added: list[str] = []
    fake_os = types.SimpleNamespace(
        environ={rootenv.DLL_DIR_ENV: str(tmp_path)},
        name="nt",
        path=os.path,
        listdir=os.listdir,
        add_dll_directory=added.append,
    )
    ns: dict = {"os": fake_os, "sys": sys}
    exec(compile(_worker_dll_hook(), "worker_hook", "exec"), ns)  # noqa: S102
    assert ns["_ROOT_DLL_DIR_ENV"] == rootenv.DLL_DIR_ENV
    assert ns["_ROOT_CPPYY_DIR_ENV"] == rootenv.CPPYY_DIR_ENV
    ns["_startup_dll_directory"]()
    assert added == [str(tmp_path)]
    # 没设 / 不是 Windows / 目录不在 / 登记抛错：什么都不做、不抛
    for env, name in (
        ({}, "nt"),
        ({rootenv.DLL_DIR_ENV: str(tmp_path)}, "posix"),
        ({rootenv.DLL_DIR_ENV: str(tmp_path / "gone")}, "nt"),
    ):
        added.clear()
        fake_os.environ, fake_os.name = env, name
        ns["_startup_dll_directory"]()
        assert added == []
    fake_os.environ, fake_os.name = {rootenv.DLL_DIR_ENV: str(tmp_path)}, "nt"

    def boom(_):
        raise OSError("nope")

    fake_os.add_dll_directory = boom
    ns["_startup_dll_directory"]()


def test_worker_keeps_the_dll_directory_handles_for_the_process_lifetime(tmp_path):
    """Codex #1282 P2：`os.add_dll_directory()` 返回的句柄要被持有，不能当场丢掉（丢了登记可能随对象收尾撤销）。"""
    handle = object()
    fake_os = types.SimpleNamespace(
        environ={rootenv.DLL_DIR_ENV: str(tmp_path)},
        name="nt",
        path=os.path,
        listdir=os.listdir,
        add_dll_directory=lambda p: handle,
    )
    ns: dict = {"os": fake_os, "sys": sys}
    exec(compile(_worker_dll_hook(), "worker_hook", "exec"), ns)  # noqa: S102
    ns["_startup_dll_directory"]()
    assert any(h is handle for h in ns["_dll_dir_handles"])


def test_probe_script_keeps_the_dll_directory_handle_in_a_module_variable():
    """体检脚本（`_PROBE_SRC`）里的同一处登记：返回值必须被赋给变量，而不是一条丢弃结果的表达式语句。"""
    tree = ast.parse(projectenv._PROBE_SRC)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "add_dll_directory"
    ]
    assert calls, "前提：体检脚本里有 add_dll_directory 调用"
    discarded = [n for n in ast.walk(tree) if isinstance(n, ast.Expr) and n.value in calls]
    assert not discarded
    kept = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign) and n.value in calls and isinstance(n.targets[0], ast.Name)
    ]
    assert len(kept) == len(calls)


def test_probe_script_registers_the_dll_directory_before_importing_anything():
    src = Path(projectenv.__file__).read_text(encoding="utf-8")
    hook = src.index(f'os.environ.get("{rootenv.DLL_DIR_ENV}")')
    assert "add_dll_directory" in src[hook : hook + 400]
    # 体检脚本里：登记发生在逐个 import 被问模块（`_state(module)`）之前
    assert hook < src.index('out["requested_module_ok"] = _state(module)')


# ---- 发现：没设 ROOTSYS


@POSIX
def test_discovery_without_rootsys_finds_path_entry_home_desktop_and_downloads(tmp_path):
    home = tmp_path / "home"
    # 官方 zip 解出来：外层带版本、里层 root 才是安装根
    inner = make_official_win_root(
        home / "Downloads" / "root_v6.38.06.win64.python314.vc18", "root", build_python="3.14.0"
    )
    desk = make_official_win_root(home / "Desktop", "root638", build_python="3.11.0")
    via_path = make_official_win_root(tmp_path / "apps", "cern", build_python="3.13.0")
    got = rootenv.discover_installs(
        {"PATH": f"C:\\Windows;{via_path / 'bin'};{tmp_path / 'nothing'}"}, nt=True, home=str(home)
    )
    roots = [i["root"] for i in got]
    assert {str(via_path), str(inner), str(desk)} <= set(roots)
    # PATH 来源优先于常见位置；每条都带版本与构建 Python
    assert got[0]["root"] == str(via_path) and got[0]["source"] == "path"
    assert {i["build_python"] for i in got} == {"3.13", "3.14", "3.11"}
    assert {i["version"] for i in got} == {"6.38.06"}


@POSIX
def test_discovery_path_entries_without_thisroot_are_ignored_and_bounded(tmp_path):
    plain = tmp_path / "bin"
    plain.mkdir()
    assert rootenv.discover_installs({"PATH": str(plain)}, nt=False, home=str(tmp_path / "h")) == []
    assert rootenv._PATH_MAX >= 8


# ---- 组合：静态落选（版本 / VS）与真实流程


@pytest.fixture
def has_msvc(monkeypatch):
    monkeypatch.setattr(rootenv, "msvc_available", lambda *a, **k: True)


@POSIX
def test_msvc_probe_uses_vswhere_with_a_bounded_timeout_and_no_vswhere_means_none(tmp_path):
    rootenv.reset_msvc_cache()
    pf86 = tmp_path / "pf86"
    vswhere = pf86 / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    seen: list = []

    def runner(argv, timeout):
        seen.append((argv, timeout))
        return "C:\\BuildTools\n"

    env = {"ProgramFiles(x86)": str(pf86)}
    # 没有 vswhere.exe（这里用注入的 runner 时仍要求不存在也能判；真实路径不存在 = 没有）
    assert rootenv.msvc_available(env, nt=True) is False
    rootenv.reset_msvc_cache()
    assert rootenv.msvc_available(env, nt=True, runner=runner) is True
    argv, timeout = seen[0]
    assert argv[0] == str(vswhere) and 0 < timeout <= 30
    assert argv[1:] == [
        "-products",
        "*",
        "-requires",
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
        "-property",
        "installationPath",
    ]
    assert rootenv.msvc_available(env, nt=True, runner=lambda a, t: "  \n") is False
    assert (
        rootenv.msvc_available({}, nt=True, runner=lambda a, t: "x") is False
    )  # 没有 ProgramFiles

    def hang(argv, timeout):
        raise subprocess.TimeoutExpired(argv, timeout)

    assert rootenv.msvc_available(env, nt=True, runner=hang) is False
    assert rootenv.msvc_available(env, nt=False) is True  # 非 Windows 不适用
    rootenv.reset_msvc_cache()


@POSIX
def test_build_python_mismatch_is_rejected_statically_without_starting_a_process(rig, has_msvc):
    rig["compat"].clear()
    py311 = rig["add_python"]("py311", "3.11.9")
    install = rootenv.inspect_root(
        str(make_official_win_root(rig["root"].parent, "r314", build_python="3.14.0")), nt=True
    )
    rig["installs"].append(install)
    rejected: list[dict] = []
    with deprepair._detect_lock(str(rig["root"])):
        entry = deprepair._detect_environment_unlocked(
            str(rig["root"]), "fig.py", rejected=rejected
        )
    assert entry is None
    # 3.11 底 x 3.14 的 ROOT：未起进程就落选；内置 3.13 的版本此刻未知，交给体检
    static = [r for r in rejected if r["reason"] == userenvs.REJECT_ROOT_PYTHON_MISMATCH]
    assert static and all(r["root_version"] == "6.38.06" for r in static)
    # 两边版本都读得出：落选原因点名（只到 主.次）；内置 3.13 那条是体检后才判定的，同样点名
    assert {(r["root_python"], r["env_python"]) for r in static} == {
        ("3.14", "3.11"),
        ("3.14", "3.13"),
    }
    assert not any(p["python"] == py311 and p["overlay"] for p in rig["probes"])


@POSIX
def test_missing_visual_studio_rejects_the_combination_without_starting_a_process(rig, monkeypatch):
    monkeypatch.setattr(rootenv, "msvc_available", lambda *a, **k: False)
    py314 = rig["add_python"]("py314", "3.14.8")
    install = rootenv.inspect_root(
        str(make_official_win_root(rig["root"].parent, "r314", build_python="3.14.0")), nt=True
    )
    rig["installs"].append(install)
    rig["compat"][("3.14", "6.38")] = True  # 假体检本来会放行：证明是静态拦下的
    rejected: list[dict] = []
    with deprepair._detect_lock(str(rig["root"])):
        entry = deprepair._detect_environment_unlocked(
            str(rig["root"]), "fig.py", rejected=rejected
        )
    assert entry is None
    assert not any(p["overlay"] for p in rig["probes"])
    by_label = {r["label"]: r for r in rejected}
    assert by_label["Python 3.14 + ROOT 6.38"]["reason"] == userenvs.REJECT_ROOT_NEEDS_MSVC
    assert userenvs.REJECT_ROOT_NEEDS_MSVC in userenvs.REJECT_CODES
    assert py314  # 候选在场


@POSIX
def test_real_flow_a_conda_python_with_a_discovered_root_forms_and_adopts_the_combination(
    rig, monkeypatch, has_msvc
):
    """真机的流程：解释器是 Conda 的（`launcher_for_interpreter` 非空）、ROOT 解压在下载目录、没设 ROOTSYS——
    真发现（不是替身）-> `_root_overlay_entries` -> `best()` -> 记入项目记录。"""
    py314 = rig["add_python"]("py314", "3.14.8")
    home = rig["root"].parent / "home"
    make_official_win_root(home / "Downloads" / "root_v6.38.06.win64.python314.vc18", "root")
    monkeypatch.setattr(
        userenvs,
        "root_installs",
        lambda budget=None: rootenv.discover_installs({"PATH": ""}, nt=True, home=str(home)),
    )
    conda = types.SimpleNamespace(conda_exe="conda", prefix="p")
    monkeypatch.setattr(
        launcher, "launcher_for_interpreter", lambda python: conda if python == py314 else None
    )
    rig["compat"][("3.14", "6.38")] = True
    # 裸起候选的 ROOT 导入不了，但解释器健康

    decision = _decide(rig)

    entry = decision.adopted
    assert entry is not None and entry["python"] == py314
    assert entry["source"] == userenvs.SOURCE_ROOT_OVERLAY
    assert entry["label"] == "Python 3.14 + ROOT 6.38"
    record = projectenv.remembered_record(rig["root"])
    assert record["env_overlay"]["version"] == "6.38.06"
    assert record["env_overlay"]["root"].endswith("root")


@POSIX
def test_probe_overlays_after_the_conda_activation_capture(tmp_path, monkeypatch):
    """Win11 真机：Conda 环境不激活时 matplotlib savefig 自己就崩，所以叠加必须在激活捕获**之后**（激活的 PATH 在 ROOT 的后面）。"""
    root = make_root(tmp_path, "root", nt=True)
    spec = rootenv.overlay_spec(rootenv.inspect_root(str(root), nt=True))
    seen: list[dict] = []

    def fake_run(argv, **kw):
        seen.append(kw["env"])
        out = '{"version_info": [3, 14, 8], "python_version": "3.14.8", "modules_ok": {}}'
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(projectenv.subprocess, "run", fake_run)
    monkeypatch.setattr(launcher, "launcher_for_interpreter", lambda python: object())
    monkeypatch.setattr(
        launcher, "activate_env", lambda python, base, timeout=None: {"PATH": "ACTIVATED"}
    )
    py = _touch(tmp_path / "e" / "python")
    monkeypatch.setattr(rootenv, "_is_nt", lambda nt: True)
    projectenv._probe_environment(py, env_overlay=spec)
    assert seen[0]["PATH"] == f"{root / 'bin'};ACTIVATED"
    assert seen[0]["ROOTSYS"] == str(root)


def test_worker_hook_installs_a_finder_that_only_knows_libcppyy(tmp_path, monkeypatch):
    cppyy = tmp_path / "cppyy"
    cppyy.mkdir()
    (cppyy / "libcppyy.pyd").write_text("", encoding="utf-8")
    (cppyy / "types.py").write_text("raise RuntimeError('shadowed')\n", encoding="utf-8")
    fake_os = types.SimpleNamespace(
        environ={rootenv.DLL_DIR_ENV: str(tmp_path), rootenv.CPPYY_DIR_ENV: str(cppyy)},
        name="nt",
        path=os.path,
        listdir=os.listdir,
        add_dll_directory=lambda p: None,
    )
    ns: dict = {"os": fake_os, "sys": sys}
    exec(compile(_worker_dll_hook(), "worker_hook", "exec"), ns)  # noqa: S102
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    path_before = list(sys.path)
    ns["_startup_dll_directory"]()
    finder = sys.meta_path[-1]  # 末尾：标准库与用户模块先赢
    assert finder.find_spec("libcppyy").origin == str(cppyy / "libcppyy.pyd")
    for other in ("types", "cppyy", "libcppyy2", "ll", "reflex", "json", "ROOT"):
        assert finder.find_spec(other) is None
    assert sys.path == path_before  # 目录没进 sys.path：标准库的 types 没被遮住
    import importlib

    assert importlib.import_module("types").__file__ != str(cppyy / "types.py")
    # 没有 libcppyy*.pyd：不装
    empty = tmp_path / "empty"
    empty.mkdir()
    fake_os.environ[rootenv.CPPYY_DIR_ENV] = str(empty)
    n = len(sys.meta_path)
    ns["_startup_dll_directory"]()
    assert len(sys.meta_path) == n


def test_probe_script_installs_the_same_libcppyy_finder_not_a_path_entry():
    src = Path(projectenv.__file__).read_text(encoding="utf-8")
    hook = src.index(f'os.environ.get("{rootenv.DLL_DIR_ENV}")')
    block = src[hook : src.index("def _norm(path):")]
    assert rootenv.CPPYY_DIR_ENV in block and "sys.meta_path.append" in block
    assert 'name != "libcppyy"' in block and "sys.path" not in block


# ===========================================================================
# 准备会话路径（Win11 真机 2026-10-21）：计划钉下的决定带着叠加环境
# ===========================================================================
def _plan(rig):
    from tavotto.engine import preparation

    (rig["root"] / "fig.py").write_text("import ROOT\n", encoding="utf-8")
    return preparation.plan_for(
        project_id="pj",
        project_root=str(rig["root"]),
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target="script",
    )


@POSIX
def test_a_preparation_plan_pins_the_remembered_overlay_and_the_worker_env_carries_it(rig):
    """第二次运行（记录里已有 env_overlay）：计划快照带叠加层，`app._plan_pin` 钉下的决定也带，
    `_overlay_for` 回同一份，起 worker 的 env 里有 ROOTSYS / PYTHONPATH 含 `<root>/lib`——
    漏掉的话 worker 在 r314 里裸起、脚本报缺 ROOT。"""
    from tavotto import app as app_mod

    py311 = rig["add_python"]("py311", "3.11.9")
    info = rig["add_root"]("root638", "6.38.02")
    assert _decide(rig).adopted is not None  # 第一次：自动采用，记下 env_overlay

    plan = _plan(rig)

    assert plan.interpreter == py311
    assert plan.environment_overlay is not None and plan.environment_overlay["root"] == info["root"]
    # 公开投影不带安装路径
    assert info["root"] not in str(plan.to_payload())
    pin = app_mod._plan_pin(plan)
    assert pin.env_overlay == plan.environment_overlay
    overlay = engine_pool._overlay_for(str(rig["root"]), py311, pin)
    assert overlay == plan.environment_overlay
    popen_env = engine_pool.launch_env(None, py311, {}, overlay, bundled=False)
    assert popen_env["ROOTSYS"] == info["root"]
    assert popen_env["PYTHONPATH"].split(os.pathsep)[0] == str(Path(info["root"]) / "lib")


@POSIX
def test_a_plan_without_an_overlay_pins_none_and_a_cleared_record_makes_the_plan_stale(rig):
    from tavotto.engine import preparation

    py311 = rig["add_python"]("py311", "3.11.9")
    rig["add_root"]("root638", "6.38.02")
    assert _decide(rig).adopted is not None
    plan = _plan(rig)
    assert preparation.PreparationService._stale_reason(plan, source=False) is None
    # 计划之后记录换成不带叠加层的同一解释器：同一路径带不带 ROOT 不是同一个环境
    projectenv.remember(rig["root"], py311, automatic=True)
    stale = preparation.PreparationService._stale_reason(plan, source=False)
    assert stale == (preparation.STALE_ENVIRONMENT, {})
    bare = _plan(rig)
    assert bare.environment_overlay is None


@POSIX
def test_environment_state_exposes_the_overlay_without_a_path(rig):
    py311 = rig["add_python"]("py311", "3.11.9")
    info = rig["add_root"]("root638", "6.38.02")
    assert projectenv.state(rig["root"]).get("env_overlay") is None
    assert _decide(rig).adopted is not None
    state = projectenv.state(rig["root"])
    assert state["python"] == py311
    assert state["env_overlay"] == {"kind": "root", "version": "6.38.02", "label": "ROOT 6.38"}
    assert info["root"] not in str(state)


# ===========================================================================
# 安装内容指纹（Codex #1282 P2）：身份键只按路径，代次键带指纹
# ===========================================================================
def _bump(path: Path, content: str = "x") -> None:
    """原地改写：内容长度变（size 变），mtime 也往后推，规避粗时间戳。"""
    path.write_text(content, encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_generation_changes_when_the_install_is_upgraded_in_place_but_the_identity_key_does_not(
    tmp_path,
):
    root = make_official_win_root(tmp_path)
    spec = rootenv.overlay_spec(rootenv.inspect_root(str(root), nt=True))
    key, gen = rootenv.key_of(spec), rootenv.generation_of(spec, nt=True)
    assert key and gen.startswith(key + "#") and gen != key
    assert rootenv.generation_of(spec, nt=True) == gen  # 没动：稳定
    for rel, text in (
        (("bin", "thisroot.bat"), "rem upgraded\n"),
        (("include", "ROOT", "RVersion.hxx"), "#define ROOT_VERSION_MAJOR 6\n"),
        (("bin", "ROOT", "_python_version.py"), '_root_python_version = "3.13.0"\n'),
        (("bin", "cppyy", "libcppyy.pyd"), "new build"),
    ):
        before = rootenv.generation_of(spec, nt=True)
        _bump(root.joinpath(*rel), text)
        after = rootenv.generation_of(spec, nt=True)
        assert after != before, rel
        # 身份键（持久化 / pin 对账用）一字不变：已存的项目记录不会因为升级失效
        assert rootenv.key_of(spec) == key
    assert rootenv.generation_of(None) == "" and rootenv.key_of(None) == ""


def test_probe_cache_key_and_hot_worker_identity_use_the_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(rootenv, "_is_nt", lambda nt: True)  # 假的官方 Windows 布局
    root = make_official_win_root(tmp_path)
    spec = rootenv.overlay_spec(rootenv.inspect_root(str(root), nt=True))
    py = _touch(tmp_path / "e" / "bin" / "python")
    mods = ("ROOT",)
    old = userenvs._cache_key(py, mods, False, overlay=spec)
    assert userenvs._cache_key(py, mods, False, overlay=spec) == old
    _bump(root / "bin" / "thisroot.bat", "rem upgraded\n")
    assert userenvs._cache_key(py, mods, False, overlay=spec) != old  # 原地升级：体检缓存不再命中
    # 候选身份仍只按路径（持久化 / 采用对账）
    cand = {"python": py, "source": "root_overlay", "overlay": spec}
    assert userenvs.entry_key(cand) == (userenvs._key(py), rootenv.key_of(spec))
    assert userenvs.env_id(py, spec) == projectenv.env_id(py, spec)
    # 热 worker：创建时记的、复用前比的都是代次键，不是身份键
    src = Path(engine_pool.__file__).read_text(encoding="utf-8")
    assert "self.env_overlay_key = rootenv.generation_of(self.env_overlay)" in src
    assert 'getattr(w, "env_overlay_key", "") != rootenv.generation_of(' in src
