"""脚本写死交互（GUI）后端时，safe 与 native 两条入口都中和它（ADR 0195，维护者 2026-11-19 裁决）。

主张：`matplotlib.use("TkAgg")` / `rcParams["backend"] = "TkAgg"` / `MPLBACKEND=TkAgg` / `plt.switch_backend("TkAgg")`
这类写死 GUI 后端的写法不再切换后端：在**没有 tkinter** 的环境里也照样捕获到图、不以 `missing_dependency` 失败、
native 的 `plt.show()` 不进 GUI 主循环。**只对 GUI 后端**：Agg / pdf / svg 照样被尊重（对照用例）。
引擎自己装的包装不许被判成「脚本 patch 了 matplotlib」（图仍可完整编辑，`save_moment` 仍是 `restored`）。

「没有 tkinter」的模拟：用户解释器的 `PYTHONPATH` 里放一个 `sitecustomize.py`，把 `sys.modules["tkinter"]` 置为 `None`
（之后 `import tkinter` 就是 `ImportError`，与没装 tkinter 的解释器同形）。只作用于被测子进程，不污染开发机。
先证明这个模拟真的会咬人（`test_the_simulation_bites`），否则后面的绿是假绿。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from support import bridgekit
from tavotto.engine import bridge, pool

needs_python = bridgekit.needs_user_python

SITECUSTOMIZE = """\
import sys
sys.modules["tkinter"] = None
sys.modules["_tkinter"] = None
"""

#: 想写死 GUI 后端的几种写法；每个都在 `plt.show()` 之后断言后端不是 GUI
VARIANTS = {
    "use_tkagg": 'matplotlib.use("TkAgg")\n',
    "use_lowercase": 'matplotlib.use("tkagg")\n',
    "use_qt5agg": 'matplotlib.use("Qt5Agg")\n',
    "use_macosx": 'matplotlib.use("MacOSX")\n',
    "rcparams": 'matplotlib.rcParams["backend"] = "TkAgg"\n',
    "switch_backend": 'import matplotlib.pyplot as _p\n_p.switch_backend("TkAgg")\n',
    "use_after_pyplot": 'import matplotlib.pyplot as _p\nmatplotlib.use("TkAgg")\n',
}

SCRIPT = """\
import matplotlib
{prelude}
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(2.4, 1.8))
line, = ax.plot([0, 1, 2], [0, 1, 4])
ax.set_title("saved")
fig.savefig("fig.png")
ax.set_title("later")  # 存盘之后又改：可编辑基础要靠保存瞬间快照，包装被判成 patch 就会放弃它
plt.show()
assert matplotlib.get_backend().lower() == "agg", matplotlib.get_backend()
"""


@pytest.fixture
def no_tk(tmp_path):
    d = tmp_path / "no_tk_site"
    d.mkdir()
    (d / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    return str(d)


def _script(prelude: str) -> str:
    return SCRIPT.format(prelude=prelude.rstrip("\n"))


# --------------------------------------------------------------------------- 模拟本身
@needs_python
def test_the_simulation_bites(no_tk):
    """没有中和的参照：模拟环境里 `import tkinter` 失败，且真 matplotlib 切 TkAgg 同样失败（证明下面的绿不是假绿）。"""
    code = (
        "import matplotlib\n"
        "try:\n"
        "    import tkinter\n"
        "except ImportError:\n"
        "    print('NO_TK')\n"
        "import matplotlib.pyplot as plt\n"
        "try:\n"
        "    plt.switch_backend('TkAgg')\n"
        "except ImportError:\n"
        "    print('TK_BACKEND_FAILS')\n"
    )
    r = subprocess.run(
        [bridgekit.USER_PYTHON, "-c", code],
        env=bridgekit.child_env({"PYTHONPATH": no_tk}),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "NO_TK" in r.stdout and "TK_BACKEND_FAILS" in r.stdout, r.stdout + r.stderr


# --------------------------------------------------------------------------- native
def _native(tmp_path, body, env=None, name="fig.py"):
    proj = tmp_path / "proj"
    bridgekit.write(proj / name, body)
    report = tmp_path / "report.json"
    r = bridgekit.run_runner(
        bridgekit.USER_PYTHON,
        bridge.RUNNER_PY,
        target=proj / name,
        cwd=str(proj),
        report=report,
        out_dir=tmp_path / "out",
        env=env,
        timeout=90,  # 卡进 GUI 主循环就是超时
    )
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
    return r, data


@needs_python
@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_native_gui_backend_is_neutralized_without_tkinter(tmp_path, no_tk, variant):
    r, data = _native(
        tmp_path, _script(VARIANTS[variant]), env=bridgekit.child_env({"PYTHONPATH": no_tk})
    )
    assert r.returncode == 0, r.stderr
    assert data["script_error"] is None
    assert [f["stem"] for f in data["figures"]] == ["fig"]
    assert data["matplotlib_backend"].lower() == "agg"
    assert "missing" not in r.stderr.lower()


@needs_python
def test_native_env_mplbackend_gui_is_neutralized(tmp_path, no_tk):
    env = bridgekit.child_env({"PYTHONPATH": no_tk, "MPLBACKEND": "TkAgg"})
    r, data = _native(tmp_path, _script(""), env=env)
    assert r.returncode == 0, r.stderr
    assert data["script_error"] is None
    assert [f["stem"] for f in data["figures"]] == ["fig"]
    assert data["matplotlib_backend"].lower() == "agg"


@needs_python
def test_native_leaves_a_trace_with_only_the_backend_name(tmp_path, no_tk):
    r, data = _native(
        tmp_path, _script(VARIANTS["use_tkagg"]), env=bridgekit.child_env({"PYTHONPATH": no_tk})
    )
    assert r.returncode == 0, r.stderr
    assert data["gui_backends_neutralized"] == ["tkagg"]


@needs_python
@pytest.mark.parametrize("backend", ["Agg", "pdf", "svg", "ps"])
def test_native_non_gui_backend_is_respected(tmp_path, no_tk, backend):
    """对照：非 GUI 后端原样尊重（不中和、不留痕）。cairo 要 pycairo，开发机不一定有，判据层（登记表）另有用例。"""
    body = (
        "import matplotlib\n"
        f"matplotlib.use({backend!r})\n"
        "import matplotlib.pyplot as plt\n"
        f"assert matplotlib.get_backend().lower() == {backend.lower()!r}, matplotlib.get_backend()\n"
        "fig, ax = plt.subplots()\n"
        "ax.plot([0, 1], [0, 1])\n"
        "plt.show()\n"
    )
    r, data = _native(tmp_path, body, env=bridgekit.child_env({"PYTHONPATH": no_tk}))
    assert r.returncode == 0, r.stderr
    assert data["matplotlib_backend"].lower() == backend.lower()
    assert data["gui_backends_neutralized"] == []


@needs_python
def test_native_explicit_non_gui_choice_survives_a_later_gui_request(tmp_path, no_tk):
    """先 `use("pdf")` 再 `use("TkAgg")`：不把用户已选的非交互后端换成 agg。"""
    body = (
        "import matplotlib\n"
        "matplotlib.use('pdf')\n"
        "matplotlib.use('TkAgg')\n"
        "import matplotlib.pyplot as plt\n"
        "assert matplotlib.get_backend().lower() == 'pdf', matplotlib.get_backend()\n"
        "plt.plot([0, 1], [0, 1])\n"
        "plt.show()\n"
    )
    r, data = _native(tmp_path, body, env=bridgekit.child_env({"PYTHONPATH": no_tk}))
    assert r.returncode == 0, r.stderr
    assert data["matplotlib_backend"].lower() == "pdf"
    assert data["gui_backends_neutralized"] == ["tkagg"]


@needs_python
def test_native_hooks_do_not_import_matplotlib_early(tmp_path):
    """装配不许提前 import matplotlib（`bridgeboot` 的钩子只在用户 import 到的那一刻才装）。"""
    body = (
        "import sys\n"
        "assert 'matplotlib' not in sys.modules\n"
        "import matplotlib\n"
        "assert getattr(matplotlib.use, '_tavotto_gui_backend_neutralizer', False)\n"
        "assert 'matplotlib.pyplot' not in sys.modules\n"
    )
    r, _ = _native(tmp_path, body)
    assert r.returncode == 0, r.stderr


# --------------------------------------------------------------------------- safe worker
def _safe_build(tmp_path, source, name, env=None):
    d = tmp_path / name
    d.mkdir()
    (d / "s.py").write_text(source, encoding="utf-8")
    with mock.patch.dict(os.environ, env or {}):
        return pool.one_shot("s.py", str(d), "__main__")


@needs_python
@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_safe_gui_backend_is_neutralized_and_stays_editable(tmp_path, no_tk, variant):
    w = _safe_build(tmp_path, _script(VARIANTS[variant]), variant, {"PYTHONPATH": no_tk})
    try:
        resp = w.ensure_built()
        man = w.override("fig", [])["manifest"]
        # 引擎自己的包装不许被判成「脚本 patch 了 matplotlib」：保存瞬间快照照常还原
        assert man.get("save_moment") == "restored", man.get("save_moment_unavailable")
        assert "save_moment_unavailable" not in man
        assert resp["runtime"]["gui_backends_neutralized"], "应留下一条只含后端名的事实"
    finally:
        pool.discard(w)


@needs_python
def test_safe_leaves_a_trace_with_only_the_backend_name(tmp_path, no_tk):
    w = _safe_build(tmp_path, _script(VARIANTS["use_tkagg"]), "trace", {"PYTHONPATH": no_tk})
    try:
        resp = w.ensure_built()
        assert resp["runtime"]["gui_backends_neutralized"] == ["tkagg"]
        log = Path(w.log_path).read_text(encoding="utf-8", errors="replace")
        assert '[gui-backend] {"backend": "tkagg"}' in log
    finally:
        pool.discard(w)


@needs_python
@pytest.mark.parametrize("backend", ["Agg", "pdf", "svg"])
def test_safe_non_gui_backend_is_respected(tmp_path, no_tk, backend):
    """对照：非 GUI 后端原样交给 matplotlib（safe 的默认是 Agg，用户可以换成别的非交互后端）。"""
    body = (
        "import matplotlib\n"
        f"matplotlib.use({backend!r})\n"
        "import matplotlib.pyplot as plt\n"
        f"assert matplotlib.get_backend().lower() == {backend.lower()!r}, matplotlib.get_backend()\n"
        "fig, ax = plt.subplots()\n"
        "ax.plot([0, 1], [0, 1])\n"
        'fig.savefig("fig.png")\n'
    )
    w = _safe_build(tmp_path, body, f"nongui_{backend}", {"PYTHONPATH": no_tk})
    try:
        resp = w.ensure_built()
        assert resp["runtime"]["gui_backends_neutralized"] == []
    finally:
        pool.discard(w)


@needs_python
def test_safe_explicit_non_gui_choice_survives_a_later_gui_request(tmp_path, no_tk):
    body = (
        "import matplotlib\n"
        "matplotlib.use('pdf')\n"
        "matplotlib.use('TkAgg')\n"
        "import matplotlib.pyplot as plt\n"
        "assert matplotlib.get_backend().lower() == 'pdf', matplotlib.get_backend()\n"
        "fig, ax = plt.subplots()\n"
        "ax.plot([0, 1], [0, 1])\n"
        'fig.savefig("fig.png")\n'
    )
    w = _safe_build(tmp_path, body, "pdf_then_gui", {"PYTHONPATH": no_tk})
    try:
        assert w.ensure_built()["runtime"]["gui_backends_neutralized"] == ["tkagg"]
    finally:
        pool.discard(w)


# --------------------------------------------------------------------------- 判据（纯逻辑，不 import matplotlib 到本进程）
@needs_python
def test_judgement_comes_from_matplotlibs_own_registry():
    """判据以 matplotlib 自己的登记表为准：交互名中、非交互名与 module:// 不中、非字符串不中、大小写不敏感。"""
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(Path(bridgekit.__file__).resolve().parents[2] / 'src' / 'tavotto' / 'engine')!r})\n"
        "import bridgeboot\n"
        "n = bridgeboot.GuiBackendNeutralizer()\n"
        "names = ['TkAgg','tkagg','QtAgg','Qt5Agg','MacOSX','GTK3Agg','WXAgg','WebAgg','nbAgg',"
        "'Agg','agg','pdf','svg','ps','cairo','pgf','template','module://matplotlib_inline.backend_inline',"
        "'module://x.y','','nope']\n"
        "out = {k: n.is_gui_backend(k) for k in names}\n"
        "out['None'] = n.is_gui_backend(None)\n"
        "print(json.dumps(out))\n"
    )
    r = subprocess.run(
        [bridgekit.USER_PYTHON, "-c", code],
        env=bridgekit.child_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    gui = {"TkAgg", "tkagg", "QtAgg", "Qt5Agg", "MacOSX", "GTK3Agg", "WXAgg", "WebAgg", "nbAgg"}
    for name, verdict in got.items():
        assert verdict is (name in gui), name


def test_receipt_projection_keeps_only_backend_names():
    """自报是不可信输入：回执公开面只收短小写字母数字名、去重、限条数，其余（含像路径的）丢弃。"""
    from tavotto.engine import receipt

    raw = ["tkagg", "tkagg", "/home/u/x", "Qt5Agg", "a" * 40, 7, None, "qtagg"]
    assert receipt.project_gui_backends(raw) == ["tkagg", "qtagg"]
    assert receipt.project_gui_backends("tkagg") == []
    assert receipt.project_gui_backends(None) == []
