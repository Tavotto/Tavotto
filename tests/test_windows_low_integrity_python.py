"""低完整性的 Python（TVD-B6MR 根因实验，Windows 真机）。

假设：用户的 venv 是在一个以**低完整性**运行的沙箱里建出来的，于是 venv 里的 `python.exe` 带了 Low 强制标签。
之后不管谁启动它，进程都只有低完整性：能读 `%LOCALAPPDATA%` 下的普通（中完整性）目录、已存在的目录照常可用，
但**不能在里面新建**——与反馈包的现象逐条一致（`out` 已存在就过、`out\\script-input` 新建就 WinError 5）。

主语：**被打了 Low 标签的 venv 解释器起出来的进程**，在**中完整性**的目录下新建 / 复用 / 读取。
对照组：同一份基础解释器不打标签。体检（#1594）：选环境时就拒收它。端到端：真跑到 worker 时报
`interpreter_cache_denied`（#1595）。Lab 实测 run 20261101T132333Z-42f3d0：修 #1594 之前体检对它 ok=True——与现场一致。
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from support import bridgekit
from tavotto.engine import pool as engine_pool

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="强制完整性标签是 Windows 的机制"),
    pytest.mark.skipif(bridgekit.USER_PYTHON is None, reason="找不到装有 matplotlib 的解释器"),
]

LOW_RID = 0x1000
MEDIUM_RID = 0x2000

PROBE = textwrap.dedent(
    r"""
    import ctypes, ctypes.wintypes as w, json, sys
    from pathlib import Path

    def integrity_rid():
        adv, k32 = ctypes.windll.advapi32, ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = w.HANDLE
        adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
        adv.GetTokenInformation.argtypes = [
            w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)
        ]
        adv.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
        adv.GetSidSubAuthorityCount.restype = ctypes.c_void_p
        adv.GetSidSubAuthority.argtypes = [ctypes.c_void_p, w.DWORD]
        adv.GetSidSubAuthority.restype = ctypes.c_void_p
        tok = w.HANDLE()
        if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(tok)):
            return None
        n = w.DWORD()
        adv.GetTokenInformation(tok, 25, None, 0, ctypes.byref(n))  # TokenIntegrityLevel
        buf = ctypes.create_string_buffer(n.value)
        if not adv.GetTokenInformation(tok, 25, buf, n, ctypes.byref(n)):
            return None
        psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        count = ctypes.cast(adv.GetSidSubAuthorityCount(psid), ctypes.POINTER(ctypes.c_ubyte))[0]
        return ctypes.cast(adv.GetSidSubAuthority(psid, count - 1), ctypes.POINTER(w.DWORD))[0]

    base = Path(sys.argv[1])
    out = {"rid": integrity_rid()}
    try:
        (base / "brand-new").mkdir()
        out["create"] = "ok"
    except OSError as exc:
        out["create"] = f"{type(exc).__name__}:{getattr(exc, 'winerror', None)}"
    try:
        (base / "existing").mkdir(parents=True, exist_ok=True)
        out["reuse"] = "ok"
    except OSError as exc:
        out["reuse"] = f"{type(exc).__name__}:{getattr(exc, 'winerror', None)}"
    try:
        out["read"] = (base / "existing" / "data.txt").read_text()
    except OSError as exc:
        out["read"] = f"{type(exc).__name__}:{getattr(exc, 'winerror', None)}"
    print(json.dumps(out))
    """
)


def _tail(text: str, n: int = 260) -> str:
    return " ".join(str(text).split())[-n:]


def _label_low(path: Path, inherit: bool = False) -> None:
    level = "(OI)(CI)low" if inherit else "low"
    done = subprocess.run(
        ["icacls", str(path), "/setintegritylevel", level],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",  # 控制台代码页的输出只进断言消息，乱码无妨
    )
    assert done.returncode == 0, _tail(done.stdout + done.stderr)


@pytest.fixture(scope="module")
def low_python(tmp_path_factory) -> str:
    """一个 Scripts\\python.exe 带 Low 强制标签的 venv（系统 site-packages 可见，matplotlib 用基础环境的）。"""
    venv = tmp_path_factory.mktemp("lowil") / "venv"
    subprocess.run(
        [bridgekit.USER_PYTHON, "-m", "venv", "--system-site-packages", "--without-pip", str(venv)],
        check=True,
        env=bridgekit.child_env(),
    )
    # Lab / CI 的 USER_PYTHON 本身就是 venv：`--system-site-packages` 只看得见它的**基础**解释器，看不见 matplotlib。
    # 用一个 .pth 把 USER_PYTHON 的 site-packages 接进来（与用户「venv 里装了 matplotlib」等价）
    site = subprocess.run(
        [
            bridgekit.USER_PYTHON,
            "-c",
            "import matplotlib, pathlib; print(pathlib.Path(matplotlib.__file__).parents[1])",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        env=bridgekit.child_env(),
    ).stdout.strip()
    (venv / "Lib" / "site-packages" / "tavotto_lab_userpython.pth").write_text(site + "\n")
    exe = venv / "Scripts" / "python.exe"
    _label_low(exe)
    return str(exe)


@pytest.fixture
def low_mplconfig(tmp_path, monkeypatch) -> Path:
    """一个带 Low 标签（可继承）的目录给 matplotlib 当配置 / 缓存目录——低完整性进程能在里面写。
    现场 worker 报的 matplotlib configdir 在项目盘上（不在用户目录），推测就是这样一个目录。"""
    d = tmp_path / "mpl-low"
    d.mkdir()
    _label_low(d, inherit=True)
    monkeypatch.setenv("MPLCONFIGDIR", str(d))
    return d


def _probe(python: str, base: Path) -> dict:
    proc = subprocess.run(
        [python, "-c", PROBE, str(base)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=bridgekit.child_env(),
        timeout=120,
    )
    assert proc.returncode == 0, _tail(proc.stdout + proc.stderr)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _medium_dir(tmp_path: Path) -> Path:
    base = tmp_path / "medium"
    (base / "existing").mkdir(parents=True)
    (base / "existing" / "data.txt").write_text("hello")
    return base


def test_low_integrity_python_can_read_and_reuse_but_not_create(tmp_path, low_python):
    got = _probe(low_python, _medium_dir(tmp_path))
    assert got["rid"] == LOW_RID, got
    assert got["create"] == "PermissionError:5", got  # 与反馈包同一个 WinError 5
    assert got["reuse"] == "ok", got  # 已存在的目录 `mkdir(exist_ok=True)` 照常过
    assert got["read"] == "hello", got


def test_control_same_base_python_without_the_label_creates_fine(tmp_path):
    got = _probe(bridgekit.USER_PYTHON, _medium_dir(tmp_path))
    # 不打标签就不是 Low：Lab 的非管理员账户是 Medium，GitHub 的 windows-latest 以管理员跑、是 High（0x3000）
    assert got["rid"] >= MEDIUM_RID, got
    assert got["create"] == "ok", got


def test_health_probe_refuses_the_low_integrity_python(low_python):
    """#1594：选环境时的体检就拒收它（以前体检 ok=True、存下来之后每次运行都失败——与现场一致）。"""
    from tavotto.engine import projectenv

    health = projectenv.probe_environment(low_python)
    facts = {k: health.get(k) for k in ("ok", "code", "cache_write")}
    assert facts == {"ok": False, "code": projectenv.ERROR_CACHE_DENIED, "cache_write": "denied"}, (
        facts,
        _tail(health.get("detail") or health.get("error") or ""),
    )


def test_health_probe_still_accepts_the_same_python_without_the_label():
    from tavotto.engine import projectenv

    health = projectenv.probe_environment(bridgekit.USER_PYTHON)
    assert (health.get("ok"), health.get("cache_write")) == (True, "ok"), _tail(
        health.get("detail") or health.get("error") or ""
    )


def test_tavotto_reports_interpreter_cache_denied_for_that_python(
    tmp_path, monkeypatch, low_python, low_mplconfig
):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "fig.py").write_text(
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots()\nax.plot([1, 2, 3])\n"
    )
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", low_python)
    engine_pool.reset_worker_python()
    try:
        with pytest.raises(engine_pool.WorkerError) as err:
            engine_pool.build("fig.py", str(root), "__main__")
        assert err.value.code == engine_pool.INTERPRETER_CACHE_DENIED_CODE, (
            f"{err.value.code} :: {_tail(str(err.value))}"
        )
    finally:
        engine_pool.shutdown_all(str(root), wait=True)
        monkeypatch.delenv("TAVOTTO_WORKER_PYTHON")
        engine_pool.reset_worker_python()
