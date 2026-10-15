"""引擎自家文件缺失 ≠ 用户缺第三方包（issue #961）。

Windows 上 sidecar 从一份不完整的副本启动（缺 `figcapture.py`），worker 在装载引擎模块时
抛 `ModuleNotFoundError: No module named 'figcapture'`。以前父进程只看 `No module named` 的
文本，把它判成 `missing_dependency`：提示用户装包、挂一键安装 offer、记进缺依赖诊断——
全是误导，缺的是 Tavotto 自己的文件，该提示的是重装 / 修复安装。

判据的主语是「名字属于 `worker._ENGINE_MODULES`（同一份清单，不另抄）**并且**异常出在
`bridgeboot.load_engine_modules` 装载引擎的那一步」。后一半不能省：`overrides` / `manifest` /
`runtime` 这些名字在 PyPI 上都有同名包，用户脚本里 `import overrides` 缺包时仍是缺依赖。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

ENGINE_DIR = Path(pool.__file__).resolve().parent

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

CODE = "engine_files_incomplete"


def _incomplete_engine_startup(tmp_path: Path, missing: str = "figcapture") -> str:
    """复制整个 engine 目录、删掉一个引擎模块，真起 worker，回它的 stderr（= worker.log 的内容）。"""
    copy = tmp_path / "engine_copy"
    copy.mkdir()
    for src in ENGINE_DIR.glob("*.py"):
        shutil.copy2(src, copy / src.name)
    (copy / f"{missing}.py").unlink()
    proc = subprocess.run(
        [WORKER_PY, str(copy / "worker.py")],
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        cwd=str(tmp_path),
    )
    assert proc.returncode != 0, "缺引擎文件时 worker 应当起不来"
    assert f"No module named '{missing}'" in proc.stderr, proc.stderr
    return proc.stderr


def _user_script_traceback(name: str) -> str:
    """用户脚本里 import 失败的 traceback：帧是 worker.build / 脚本自己，没有引擎装载帧。"""
    return (
        "Traceback (most recent call last):\n"
        '  File "worker.py", line 900, in build\n'
        "    exec(code, ns)\n"
        '  File "fig.py", line 3, in <module>\n'
        f"    import {name}\n"
        f"ModuleNotFoundError: No module named '{name}'\n"
    )


@needs_worker
def test_missing_engine_file_is_not_a_missing_dependency(tmp_path):
    stderr = _incomplete_engine_startup(tmp_path)
    err = pool._worker_error("渲染进程退出了：退出码 1。会话已作废。", "session_dead", stderr)
    assert err.code != "missing_dependency", "缺的是 Tavotto 自己的文件，不是用户的包"
    assert err.code == CODE
    assert err.engine_module == "figcapture"
    # 不是装包问题：没有模块名 / 去向，界面就不会挂安装 offer
    assert err.module == ""
    assert err.install_route == ""
    # 用户可见的文案不带机器路径（模块名是 Tavotto 自家的，可以带）
    assert str(tmp_path) not in str(err)
    assert "figcapture" in str(err)


@needs_worker
def test_missing_engine_file_response_has_no_install_offer(tmp_path):
    from tavotto import app as app_mod

    stderr = _incomplete_engine_startup(tmp_path)
    err = pool._worker_error("渲染进程退出了", "session_dead", stderr)
    body = app_mod._worker_error_payload(err)
    assert body["code"] == CODE
    assert body["engine_module"] == "figcapture"
    for key in ("dependency_repair", "install_route", "module"):
        assert key not in body, f"缺引擎文件不许带 {key}（那是缺依赖的形状）"
    assert str(tmp_path) not in body["error"]


@needs_worker
def test_missing_engine_file_is_not_noted_as_missing_dependency(tmp_path, monkeypatch):
    from tavotto import app as app_mod

    noted = []
    monkeypatch.setattr(app_mod, "_note_missing_dependency", lambda *a, **k: noted.append(a))
    err = pool._worker_error("x", "session_dead", _incomplete_engine_startup(tmp_path))
    app_mod._worker_error_payload(err)
    assert noted == [], "缺引擎文件不许记进缺依赖诊断"


@needs_worker
def test_every_listed_engine_module_is_recognised(tmp_path):
    """判据读 `_ENGINE_MODULES` 清单本身：清单里的一个新名字不需要同步改父进程。"""
    names = pool.engine_module_names()
    assert "figcapture" in names and "figsession" in names
    stderr = _incomplete_engine_startup(tmp_path, missing="wireproto")
    err = pool._worker_error("x", "session_dead", stderr)
    assert err.code == CODE and err.engine_module == "wireproto"


def test_missing_third_party_package_is_still_a_missing_dependency():
    tb = _user_script_traceback("numpy_fake_pkg_xyz")
    err = pool._worker_error("脚本执行失败", "script_error", tb)
    assert err.code == "missing_dependency"
    assert err.module == "numpy_fake_pkg_xyz"


@pytest.mark.parametrize("name", ["overrides", "manifest", "runtime", "figcapture"])
def test_user_script_importing_an_engine_sounding_name_is_still_a_missing_dependency(name):
    """同名包在 PyPI 上真实存在；异常不是出在引擎装载那一步，就是用户的缺依赖。"""
    err = pool._worker_error("脚本执行失败", "script_error", _user_script_traceback(name))
    assert err.code == "missing_dependency"
    assert err.module == name


def test_engine_error_message_has_no_traceback_paths_or_install_route():
    tb = (
        "Traceback (most recent call last):\n"
        '  File "C:\\Users\\alice\\AppData\\Local\\x\\worker.py", line 226, in <module>\n'
        "    _PKG = bridgeboot.load_engine_modules(str(HERE), _ENGINE_MODULES)\n"
        '  File "C:\\Users\\alice\\AppData\\Local\\x\\bridgeboot.py", line 199, in load_engine_modules\n'
        "    loaded = {n: importlib.import_module(n) for n in todo}\n"
        "ModuleNotFoundError: No module named 'figcapture'\n"
    )
    err = pool._worker_error("渲染进程退出了：C:\\Users\\alice\\worker.log", "session_dead", tb)
    assert err.code == CODE
    assert "alice" not in str(err)
    assert "worker.log" not in str(err)
