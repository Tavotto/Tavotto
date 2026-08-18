"""检查更新：版本比较、节流与开关、升级命令随安装方式变化。

全部离线跑——_fetch_latest_release 一律 monkeypatch，测试进程不联网。
"""
import time

import pytest

from magplot.engine import brand, updater


# ---------------- 版本比较 ---------------------------------------------------
@pytest.mark.parametrize("latest,current,expected", [
    ("0.2.0", "0.1.0", True),
    ("0.1.1", "0.1.0", True),
    ("1.0.0", "0.9.9", True),
    ("0.1.0", "0.1.0", False),
    ("0.1.0", "0.2.0", False),
    ("0.10.0", "0.9.0", True),      # 数字比较，不是字典序
    ("1.0.0", "1.0.0rc1", True),    # 正式版 > 同号预发布
    ("1.0.0rc1", "1.0.0", False),
    ("v0.2.0", "0.1.0", True),      # tag 带 v 前缀
    ("garbage", "0.1.0", False),    # 畸形 tag 绝不催更新
    ("", "0.1.0", False),
])
def test_is_newer(latest, current, expected):
    assert updater.is_newer(latest, current) is expected


def test_current_version_matches_package():
    import magplot
    assert updater.current_version() == magplot.__version__


# ---------------- 升级命令 ---------------------------------------------------
def _release(assets=()):
    return {"tag_name": "v9.9.9", "body": "note", "html_url": "https://example/r",
            "assets": [{"name": n, "browser_download_url": u} for n, u in assets]}


def test_upgrade_command_pip_prefers_release_wheel(monkeypatch):
    monkeypatch.setattr(updater, "install_method", lambda: "pip")
    url = "https://example/magplot-9.9.9-py3-none-any.whl"
    cmd = updater.upgrade_command(_release([("magplot-9.9.9-py3-none-any.whl", url)]))
    assert cmd[1:] == ["-m", "pip", "install", "--upgrade", url]


def test_upgrade_command_pip_falls_back_to_package_name(monkeypatch):
    monkeypatch.setattr(updater, "install_method", lambda: "pip")
    cmd = updater.upgrade_command(_release())
    assert cmd[-1] == brand.DIST_NAME


def test_upgrade_command_pipx(monkeypatch):
    monkeypatch.setattr(updater, "install_method", lambda: "pipx")
    assert updater.upgrade_command(_release()) == ["pipx", "upgrade", brand.DIST_NAME]
    url = "https://example/magplot-9.9.9-py3-none-any.whl"
    cmd = updater.upgrade_command(_release([("magplot-9.9.9-py3-none-any.whl", url)]))
    assert cmd == ["pipx", "install", "--force", url]


def test_source_checkout_never_self_updates(monkeypatch):
    """源码树里跑 pip 会覆盖用户的工作副本，只能提示 git pull。"""
    monkeypatch.setattr(updater, "install_method", lambda: "source")
    assert updater.upgrade_command(_release()) is None
    out = updater.apply_upgrade()
    assert out["ok"] is False and out["restart_required"] is False
    assert "git pull" in out["command"]


def test_install_method_detects_this_source_checkout():
    """本仓库是 src 布局的源码检出，探测结果必须是 source。"""
    assert updater.install_method() == "source"


# ---------------- 检查：节流、开关、离线 --------------------------------------
def test_check_reports_update(monkeypatch):
    monkeypatch.setattr(updater, "_fetch_latest_release", lambda: _release())
    out = updater.check(force=True)
    assert out["latest"] == "9.9.9" and out["update_available"] is True
    assert out["current"] == updater.current_version()


def test_check_offline_is_not_fatal(monkeypatch):
    def boom():
        raise OSError("no network")
    monkeypatch.setattr(updater, "_fetch_latest_release", boom)
    out = updater.check(force=True)
    assert out["update_available"] is False and "检查失败" in out["error"]


def test_auto_check_off_means_no_network(monkeypatch):
    calls = []
    monkeypatch.setattr(updater, "_fetch_latest_release",
                        lambda: calls.append(1) or _release())
    updater.set_settings({"auto_check": False})
    updater.check(force=False)
    assert calls == []                      # 关掉后后台检查一个包都不发
    updater.check(force=True)
    assert calls == [1]                     # 手动「立即检查」仍然可用


def test_check_is_throttled(monkeypatch):
    calls = []
    monkeypatch.setattr(updater, "_fetch_latest_release",
                        lambda: calls.append(1) or _release())
    updater.set_settings({"auto_check": True,
                          "last_check_ms": int(time.time() * 1000),
                          "last_result": {"latest": "9.9.9",
                                          "update_available": True}})
    out = updater.check(force=False)
    assert calls == [] and out["cached"] is True and out["latest"] == "9.9.9"


def test_cached_result_recomputes_against_running_version(monkeypatch):
    """缓存回放必须按当前运行版本现算 update_available。

    升级并重启后，缓存里还是按旧版本比出来的 True——原样回放会出现
    「有新版本 X（当前 X）」，纠缠用户直到 24h 节流过期。"""
    updater.set_settings({"auto_check": True,
                          "last_check_ms": int(time.time() * 1000),
                          "last_result": {"latest": updater.current_version(),
                                          "update_available": True}})
    out = updater.check(force=False)
    assert out["cached"] is True
    assert out["update_available"] is False  # latest == 正在跑的版本

    # 缓存里的 latest 确实更新时照常提示
    updater.set_settings({"last_result": {"latest": "9.9.9",
                                          "update_available": False}})
    out = updater.check(force=False)
    assert out["update_available"] is True


# ---------------- worker 解释器探测（跨平台） --------------------------------
def test_worker_python_candidates_prefer_env_then_self(monkeypatch, tmp_path):
    """单环境安装（pip install magplot[worker]）时，跑 Flask 的解释器自己就带
    科学栈——sys.executable 必须排在系统路径之前，否则会去用别的 python。

    前提是「这台机器上没有内置 runtime」，所以这里把 MAGPLOT_RUNTIME_DIR 指到
    一个空处显式声明它（覆盖是排他的）。不声明的话，开发机上只要跑过一次
    scripts/build_worker_runtime.py，仓库根就真的躺着一份 runtime——它**本来
    就该**排在 sys.executable 前面，于是这条用例在本机红、在 CI 绿。
    """
    import sys

    from magplot.engine import pool

    monkeypatch.delenv("MM_WORKER_PYTHON", raising=False)
    monkeypatch.setenv("MAGPLOT_RUNTIME_DIR", str(tmp_path / "_no_runtime_here"))
    cands = [c for c in pool._candidate_pythons() if c]
    assert cands[0] == sys.executable

    monkeypatch.setenv("MM_WORKER_PYTHON", "/custom/python")
    cands = [c for c in pool._candidate_pythons() if c]
    assert cands[0] == "/custom/python" and cands[1] == sys.executable


def test_worker_python_candidates_on_windows(monkeypatch):
    """Windows 上没有 python3 这个名字，也没有 /opt/homebrew。"""
    import os

    from magplot.engine import pool

    monkeypatch.delenv("MM_WORKER_PYTHON", raising=False)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(pool.shutil, "which", lambda n: f"C:\\Python\\{n}.exe")
    # 本例只验证候选清单的平台分支：把读配置这步短路掉，否则 pathlib 会按被
    # 篡改的 os.name 去构造 WindowsPath，在 macOS 上直接抛 UnsupportedOperation
    monkeypatch.setattr(pool.config, "worker_python", lambda: None)
    cands = [c for c in pool._candidate_pythons() if c]
    assert not any("homebrew" in c or c.startswith("/usr/bin") for c in cands)
    assert "C:\\Python\\python.exe" in cands


def test_worker_py_ships_with_the_package():
    """worker.py 是按路径 spawn 的，装成 wheel 后这个文件必须真实存在。"""
    from magplot.engine import pool

    assert pool.WORKER_PY.is_file()
