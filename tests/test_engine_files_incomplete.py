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

import os
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


# ---------------------------------------------------------------------------
# P1（Codex #1085）：Python 池的启动期 EOF 也要分诊——真实 EngineWorker，不是手工喂日志
# ---------------------------------------------------------------------------
def _engine_copy_without(tmp_path: Path, missing: str) -> Path:
    copy = tmp_path / "engine_copy"
    copy.mkdir()
    for src in ENGINE_DIR.glob("*.py"):
        shutil.copy2(src, copy / src.name)
    (copy / f"{missing}.py").unlink()
    return copy / "worker.py"


_FIG = "import matplotlib.pyplot as plt\nplt.plot([1, 2])\nplt.savefig('a.png')\n"


def _build_with_broken_engine(tmp_path, monkeypatch, workerd: str | None):
    """WORKER_PY 指向缺 figcapture.py 的副本，经公开的 `pool.build` 真起 worker 子进程。"""
    from tavotto.engine import workerd_client

    monkeypatch.setenv("TAVOTTO_WORKERD", workerd or "0")
    workerd_client.reset_client()
    monkeypatch.setattr(pool, "WORKER_PY", _engine_copy_without(tmp_path, "figcapture"))
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "fig.py").write_text(_FIG, encoding="utf-8")
    try:
        with pytest.raises(pool.WorkerError) as info:
            pool.build("fig.py", str(figs), "__main__")
        return info.value
    finally:
        pool.shutdown_all(str(figs), wait=True)
        workerd_client.reset_client()


@needs_worker
def test_python_pool_startup_eof_is_classified_as_engine_files_incomplete(tmp_path, monkeypatch):
    err = _build_with_broken_engine(tmp_path, monkeypatch, None)
    assert err.code == CODE, (err.code, str(err))
    assert err.engine_module == "figcapture"
    assert err.module == "" and err.install_route == ""
    assert str(tmp_path) not in str(err)
    assert "worker.log" not in str(err)


def _workerd_exe():
    """忽略 conftest 的默认禁用开关（`TAVOTTO_WORKERD=0`），只看 cargo 产物在不在——
    与 test_worker_roundtrip.py 的 `_workerd_binary` 同一借法；不撤开关的话这里恒为 None、用例恒 skip。
    但 `TAVOTTO_WORKERD` 指了路径时照用（CI 钉到刚建的 debug 产物，免得 `find_workerd` 优先挑缓存里
    残留的旧 release 二进制）；指到不存在的文件就回 None，用例 skip，由 CI 的 junit 核对判红。"""
    from tavotto.engine import workerd_client

    if (os.environ.get("TAVOTTO_WORKERD") or "").strip().lower() not in (
        "",
        "0",
        "off",
        "false",
        "no",
    ):
        return workerd_client.find_workerd()
    saved = os.environ.pop("TAVOTTO_WORKERD", None)
    try:
        return workerd_client.find_workerd()
    finally:
        if saved is not None:
            os.environ["TAVOTTO_WORKERD"] = saved


WORKERD_EXE = _workerd_exe()


@needs_worker
@pytest.mark.skipif(
    WORKERD_EXE is None, reason="没有 tavotto-workerd 产物（先在 workerd/ 里 cargo build）"
)
def test_workerd_startup_death_is_classified_as_engine_files_incomplete(tmp_path, monkeypatch):
    err = _build_with_broken_engine(tmp_path, monkeypatch, WORKERD_EXE)
    assert err.code == CODE, (err.code, str(err))
    assert err.engine_module == "figcapture"


# ---------------------------------------------------------------------------
# P2（Codex #1085）：认真正的 bridgeboot.py 帧，不是 "load_engine_modules" 子串
# ---------------------------------------------------------------------------
def _loader_traceback(path: str, name: str = "figcapture") -> str:
    return (
        "Traceback (most recent call last):\n"
        f'  File "{path}worker.py", line 226, in <module>\n'
        "    _PKG = bridgeboot.load_engine_modules(str(HERE), _ENGINE_MODULES)\n"
        f'  File "{path}bridgeboot.py", line 199, in load_engine_modules\n'
        "    loaded = {n: importlib.import_module(n) for n in todo}\n"
        f"ModuleNotFoundError: No module named '{name}'\n"
    )


@pytest.mark.parametrize("name", ["manifest", "overrides", "runtime", "figcapture"])
def test_user_defined_load_engine_modules_is_still_a_missing_dependency(name):
    tb = (
        "Traceback (most recent call last):\n"
        '  File "/proj/fig.py", line 9, in <module>\n'
        "    load_engine_modules()\n"
        '  File "/proj/fig.py", line 5, in load_engine_modules\n'
        f"    import {name}\n"
        f"ModuleNotFoundError: No module named '{name}'\n"
    )
    err = pool._worker_error("脚本执行失败", "script_error", tb)
    assert err.code == "missing_dependency", err.code
    assert err.module == name


def test_a_bridgeboot_look_alike_file_or_function_is_not_the_loader():
    wrong_file = _loader_traceback("/proj/").replace("bridgeboot.py", "mybridgeboot.py")
    wrong_func = _loader_traceback("/proj/").replace(
        "line 199, in load_engine_modules", "line 199, in load_other"
    )
    for tb in (wrong_file, wrong_func):
        assert pool._worker_error("x", "script_error", tb).code == "missing_dependency"


@pytest.mark.parametrize(
    "path", ["/opt/tavotto/engine/", "C:\\Users\\a\\AppData\\Local\\Packages\\X\\engine\\", "C:/x/"]
)
def test_loader_frame_with_posix_or_windows_paths_is_recognised(path):
    err = pool._worker_error("x", "session_dead", _loader_traceback(path))
    assert err.code == CODE and err.engine_module == "figcapture"


def test_loader_frame_after_the_exception_line_does_not_count():
    """帧必须在 ModuleNotFoundError 之前的调用链里（之后出现的不算，如日志里别处的残片）。"""
    tb = (
        _user_script_traceback("manifest")
        + '  File "bridgeboot.py", line 199, in load_engine_modules\n'
    )
    assert pool._worker_error("x", "script_error", tb).code == "missing_dependency"
