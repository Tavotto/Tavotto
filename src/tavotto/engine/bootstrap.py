"""没有可用渲染环境时，替用户建一个（纯标准库，Flask 父进程 import）。

**绝不碰用户已有的环境。** 往用户的 conda / 系统 Python 里 `pip install` 是能
省事，但那是他做研究用的环境，被我们改坏了后果由他承担。这里的做法是在 Tavotto
自己的数据目录下建一个隔离 venv，只往里面装 matplotlib：

    <data_dir>/worker-env/

用得上它的是「机器上有 Python 但没装科学栈」的用户。已经有科学栈的用户走不到
这条路（`find_worker_python()` 先找到他们自己的环境就返回了），而这才是更好的
情况——用户的论文脚本往往还要 import scipy/pandas，那些只有他自己的环境才有。

机器上一个 Python 都没有时我们无能为力：venv 得由某个真解释器创建。那种情况
如实告诉用户去装 Python，不假装能修。

**Windows 桌面版走不到这里**：安装包自带 `runtime/`（见 `engine/runtime.py`），
`find_worker_python()` 直接就选中它——不弹「请先安装 Python」、不联网装包。
这条自建 venv 的路留给源码 / pip 安装模式，那是它本来的用途。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

from . import config, runtime

VENV_DIR_NAME = "worker-env"
INSTALL_TIMEOUT_S = 900  # 首次装 matplotlib 要下几十 MB，网络慢时给足
PROBE_TIMEOUT_S = 30

_lock = threading.Lock()
_progress: dict = {"state": "idle", "log": "", "error": None}


# ---------------------------------------------------------------------------
# 探测
# ---------------------------------------------------------------------------
def venv_python(root: Path | None = None) -> Path:
    """Tavotto 自管 venv 里的解释器路径（不保证存在）。"""
    base = (root or config.data_dir()) / VENV_DIR_NAME
    return base / ("Scripts/python.exe" if os.name == "nt" else "bin/python3")


def _probe(python: str, expr: str) -> str | None:
    """在指定解释器里求值，失败回 None。"""
    try:
        out = subprocess.run(
            [python, "-c", expr],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def matplotlib_version(python: str) -> str | None:
    return _probe(python, "import matplotlib;print(matplotlib.__version__)")


def find_base_python() -> str | None:
    """找一个能用来建 venv 的解释器——**不要求**它有 matplotlib。

    复用 pool 的候选清单（已含 conda / python.org / PATH 的常见落点），
    只是把判据从「有没有 matplotlib」换成「是不是个能建 venv 的 Python」。

    内置 runtime 明确排除：官方 embeddable 发行版不带 `ensurepip`，
    `python -m venv` 建到一半就失败，白白给用户一段看不懂的报错。
    """
    from . import pool

    seen: set[str] = set()
    for cand, source in pool._prioritized_candidates():
        if source == pool.SOURCE_BUNDLED:
            continue
        if cand in seen or not Path(cand).exists():
            continue
        seen.add(cand)
        if _probe(cand, "import venv,sys;print(sys.version_info[:2])"):
            return cand
    return None


def _runtime_block() -> dict:
    """内置 runtime 的对外视图：装了什么版本、完不完整。"""
    st = runtime.status()
    info = st.get("manifest") or {}
    return {
        "present": st["present"],
        "valid": st["valid"],
        "expected": runtime.ships_bundled_runtime(),
        "python": (info.get("python") or {}).get("version"),
        "packages": info.get("packages") or {},
        "build": info.get("build") or {},
        "code": st["code"],
        "error": st["error"],
    }


def status() -> dict:
    """渲染环境现状——前端据此决定显示什么，以及要不要给「自动安装」。

    `source` 是关键字段：`bundled` 表示用的是随包附带的 Tavotto 内置环境，
    界面显示「Tavotto 内置环境」并且**不出现任何安装引导**——那时什么都不缺。
    """
    from . import pool

    rt = _runtime_block()
    try:
        python = pool.find_worker_python()
        source = pool.source_of(python)
        return {
            "ok": True,
            "python": python,
            "source": source,
            "matplotlib": matplotlib_version(python),
            "managed": Path(python) == venv_python(),
            "bundled": source == pool.SOURCE_BUNDLED,
            "runtime": rt,
            # 谁在管 worker 的生命周期（Rust supervisor / Python 池）。
            # 冒烟脚本靠它断言「产物里真带了 workerd 且渲染真走了它」——
            # 回退是静默的，不报出来就只能靠「怎么有点慢」去猜。
            "control_plane": pool.control_plane(),
            "state": _progress["state"],
        }
    except pool.WorkerError as exc:
        code = exc.code
    base = find_base_python()
    return {
        "ok": False,
        "python": None,
        "source": "",
        "matplotlib": None,
        "managed": False,
        "bundled": False,
        "runtime": rt,
        "control_plane": pool.control_plane(),
        # 该带 runtime 却带坏了：这不是「缺 Python」，是安装文件不完整。
        # 自动安装在这种情况下毫无意义（且 embeddable 里没有 pip），必须关掉。
        "code": code,
        "can_install": base is not None and not rt["expected"],
        "base_python": base,
        "state": _progress["state"],
        "error": (runtime.repair_hint() if rt["expected"] else _progress["error"]),
    }


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------
def progress() -> dict:
    return dict(_progress)


def _append(line: str) -> None:
    _progress["log"] = (_progress["log"] + line)[-8000:]


def install(on_event=None) -> dict:
    """建 venv 并装 matplotlib。同一时刻只允许一个安装在跑。

    on_event(dict) 会在状态变化时被调用（app.py 拿它转成 SSE）。
    """
    if not _lock.acquire(blocking=False):
        return {"ok": False, "error": "安装已在进行中"}
    try:
        _progress.update(state="running", log="", error=None)
        _emit(on_event)

        # 桌面版该有内置 runtime。走到这里说明它缺了或坏了——那时该做的是重装，
        # 不是现场联网建一个 venv：用户装的是「开箱即用」的安装包，我们不能
        # 因为自己的产物残缺就把下载几十 MB 的活推给他。
        if runtime.ships_bundled_runtime():
            return _fail(runtime.repair_hint(), on_event)

        base = find_base_python()
        if base is None:
            return _fail(
                "这台机器上没找到可用的 Python。请先安装 Python 3.10 以上"
                "（python.org 或 Anaconda），再回来重试。",
                on_event,
            )

        target = venv_python()
        root = target.parent.parent
        _append(f"基础解释器: {base}\n目标环境: {root}\n")
        _emit(on_event)

        if not target.exists():
            # 残留的半个 venv 会让后续 pip 行为诡异，重建前先清干净
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)
            rc, out = _run([base, "-m", "venv", str(root)])
            _append(out)
            if rc != 0 or not target.exists():
                return _fail(f"创建虚拟环境失败（{base}）", on_event)
            _emit(on_event)

        _append("\n正在安装 matplotlib（首次需要下载几十 MB）…\n")
        _emit(on_event)
        rc, out = _run(
            [
                str(target),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--disable-pip-version-check",
                "matplotlib",
            ]
        )
        _append(out)
        if rc != 0:
            return _fail("安装 matplotlib 失败，日志见下方。", on_event)

        ver = matplotlib_version(str(target))
        if not ver:
            return _fail("装完仍然 import 不到 matplotlib。", on_event)

        # 记到用户配置里：下次启动直接用，不必重新探测
        config.set_worker_python(str(target))
        from . import pool

        pool.reset_worker_python()

        _progress.update(state="done", error=None)
        _append(f"\n✓ 完成，matplotlib {ver}\n")
        _emit(on_event)
        return {"ok": True, "python": str(target), "matplotlib": ver}
    finally:
        _lock.release()


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=INSTALL_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return 1, f"\n超时（{INSTALL_TIMEOUT_S}s）：{' '.join(cmd)}\n"
    except OSError as exc:
        return 1, f"\n无法执行 {' '.join(cmd)}: {exc}\n"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _fail(msg: str, on_event) -> dict:
    _progress.update(state="failed", error=msg)
    _emit(on_event)
    return {"ok": False, "error": msg}


def _emit(on_event) -> None:
    if on_event is not None:
        on_event(progress())


def install_async(on_event=None) -> None:
    threading.Thread(target=lambda: install(on_event), daemon=True, name="mm-bootstrap").start()
