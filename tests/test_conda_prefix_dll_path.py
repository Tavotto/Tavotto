"""不可信的 Conda 形状 prefix（`conda create -p <任意目录>`）：不激活，但补上 DLL 搜索用的 PATH（ADR 0142 修订）。

Windows Lab 环境矩阵 conda-prefix 腿：不激活就裸起，PATH 上没有 `Library\\bin`，numpy 加载 MKL/BLAS 时
渲染进程以 `0xC06D007F` 崩溃。修法只补 PATH、**不执行** `etc/conda/activate.d`（那是环境里的代码，不可信 prefix 不许跑）。
可信安装根里的环境仍走激活捕获，路径行为不变。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tavotto.engine import launcher, pool


@pytest.fixture
def as_windows(monkeypatch):
    monkeypatch.setattr(launcher, "_is_windows", lambda: True)
    monkeypatch.delenv("CONDA_EXE", raising=False)
    monkeypatch.delenv("TAVOTTO_CONDA_ACTIVATION", raising=False)
    monkeypatch.setattr(launcher, "_raw_conda_roots", lambda: [])


def _prefix(tmp_path: Path, name: str = "任意 目录/conda-mpl") -> Path:
    prefix = tmp_path / name
    (prefix / "conda-meta").mkdir(parents=True)
    for sub in ("Library/mingw-w64/bin", "Library/usr/bin", "Library/bin", "Scripts"):
        (prefix / sub).mkdir(parents=True)
    (prefix / "python.exe").write_text("", encoding="utf-8")
    return prefix


def _expected(prefix: Path) -> list[str]:
    """conda 自己激活时往 PATH 前面加的顺序（去掉不存在的）。"""
    return [
        str(prefix),
        str(prefix / "Library" / "mingw-w64" / "bin"),
        str(prefix / "Library" / "usr" / "bin"),
        str(prefix / "Library" / "bin"),
        str(prefix / "Scripts"),
    ]


def test_untrusted_prefix_gets_the_dll_dirs_in_conda_order_ahead_of_path(tmp_path, as_windows):
    prefix = _prefix(tmp_path)
    assert launcher.launcher_for_interpreter(prefix / "python.exe") is None  # 不激活
    env = launcher.merge_env(None, prefix / "python.exe", {"PATH": "OLD"})
    assert env["PATH"].split(os.pathsep) == [*_expected(prefix), "OLD"]


def test_only_existing_dirs_are_added_and_nothing_is_added_twice(tmp_path, as_windows):
    prefix = _prefix(tmp_path)
    (prefix / "Library" / "usr" / "bin").rmdir()
    (prefix / "Library" / "usr").rmdir()
    once = launcher.merge_env(None, prefix / "python.exe", {"PATH": "OLD"})
    assert str(prefix / "Library" / "usr" / "bin") not in once["PATH"]
    twice = launcher.merge_env(None, prefix / "python.exe", once)
    assert twice["PATH"] == once["PATH"]


def test_activate_d_is_never_executed_for_an_untrusted_prefix(tmp_path, as_windows, monkeypatch):
    prefix = _prefix(tmp_path)
    hook = prefix / "etc" / "conda" / "activate.d"
    hook.mkdir(parents=True)
    (hook / "boom.bat").write_text("@echo off\n", encoding="utf-8")

    def forbidden(*a, **k):
        raise AssertionError("不可信 prefix 不许起任何进程去激活")

    monkeypatch.setattr(launcher.subprocess, "Popen", forbidden)
    monkeypatch.setattr(launcher, "_capture", forbidden)
    env = launcher.activate_env(prefix / "python.exe", {"PATH": "OLD"})
    assert env["PATH"].startswith(str(prefix))
    launcher.prewarm(prefix / "python.exe")  # 同样什么都不起


def test_delta_mode_for_workerd_spec_carries_only_path(tmp_path, as_windows, monkeypatch):
    """workerd 的 spawn 规格只收增量：只多一个 PATH，不把整份父进程环境（含密钥）带进去。"""
    prefix = _prefix(tmp_path)
    monkeypatch.setenv("PATH", "PARENT")
    monkeypatch.setenv("SECRET_TOKEN", "x")
    delta = pool.worker_env(str(prefix / "python.exe"), pool.SOURCE_ENV, base={})
    assert set(delta) == {"PATH"}
    assert delta["PATH"].split(os.pathsep) == [*_expected(prefix), "PARENT"]


def test_trusted_prefix_still_activates_and_its_path_is_untouched(
    tmp_path, as_windows, monkeypatch
):
    root = tmp_path / "conda 安装"
    prefix = root / "envs" / "mpl"
    (prefix / "conda-meta").mkdir(parents=True)
    (prefix / "Library" / "bin").mkdir(parents=True)
    (prefix / "python.exe").write_text("", encoding="utf-8")
    (root / "conda-meta").mkdir()
    (root / "Scripts").mkdir()
    exe = root / "Scripts" / "conda.exe"
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(launcher, "_raw_conda_roots", lambda: [os.path.realpath(root)])
    lau = launcher.launcher_for_interpreter(prefix / "python.exe")
    assert lau is not None
    monkeypatch.setattr(launcher, "activated_delta", lambda *a, **k: {"PATH": "ACTIVATED"})
    env = launcher.merge_env(lau, prefix / "python.exe", {"PATH": "OLD"})
    assert env["PATH"] == "ACTIVATED"  # 激活的结果原样，不再叠加补丁
    assert pool.worker_env(str(prefix / "python.exe"), pool.SOURCE_ENV, base={}) in (None, {})


def test_non_windows_and_non_conda_and_disabled_are_untouched(tmp_path, monkeypatch):
    prefix = _prefix(tmp_path)
    monkeypatch.setattr(launcher, "_raw_conda_roots", lambda: [])
    monkeypatch.setattr(launcher, "_is_windows", lambda: False)  # 不依赖宿主平台
    assert launcher.merge_env(None, prefix / "python.exe", {"PATH": "OLD"}) == {
        "PATH": "OLD"
    }  # 非 Windows
    monkeypatch.setattr(launcher, "_is_windows", lambda: True)
    plain = tmp_path / "venv"
    plain.mkdir()
    (plain / "python.exe").write_text("", encoding="utf-8")
    assert launcher.merge_env(None, plain / "python.exe", None) is None  # 没有 conda-meta
    monkeypatch.setenv("TAVOTTO_CONDA_ACTIVATION", "0")
    assert launcher.merge_env(None, prefix / "python.exe", {"PATH": "OLD"}) == {"PATH": "OLD"}
