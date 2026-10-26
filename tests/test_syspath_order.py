"""用户脚本的 `sys.path` 顺序（risk:high：每个脚本的启动路径）。

真实 safe worker 子进程与真文件：脚本所在目录在 `sys.path[0]`（与 `python 脚本.py` 相同），项目根（图库根）
**追加在末尾**、site-packages 之后。起因（真实科研语料 dfm/celerite）：项目根里躺着整份库源码 `celerite/`，
用户环境里装着能用的 `celerite`——原先项目根 `insert(0)`，源码目录盖住了已装的包，报
`No module named 'celerite.celerite_version'`，而原生 `python paper/figures/sho.py` 好好的。

「已装的包」用一个 `PYTHONPATH` 目录模拟：它在 `sys.path` 里排在脚本目录之后、项目根之前，与 site-packages 同一段。
静态模型（`importscan` 的搜索根、`importshadow.detect(late_roots=…)`）与 worker 的真实顺序放在同一张表里对拍。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from tavotto.engine import importscan, importshadow, pool as engine_pool, workdir

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

HDR = (
    "import matplotlib\n"
    "matplotlib.use('Agg')\n"
    "import matplotlib.pyplot as plt\n"
    "fig, ax = plt.subplots(); ax.plot([0, 1])\n"
)
PROBE = "PROBE:"


def _put(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    site = tmp_path / "fake_site"  # 「已装的包」所在目录：经 PYTHONPATH 进 sys.path，排在项目根之前
    site.mkdir()
    monkeypatch.setenv("PYTHONPATH", str(site))
    yield type("P", (), {"root": root, "site": site})
    engine_pool.shutdown_all(str(root), wait=True)
    workdir.set_mode(root, workdir.MODE_SANDBOX)


def _build(root: Path, rel: str):
    workdir.set_mode(root, workdir.MODE_SANDBOX)
    return engine_pool.build(rel, str(root), "__main__")


def _probe(root: Path, rel: str, body: str) -> dict:
    """跑 `rel`（脚本末尾 `raise` 带出一份 JSON），把 worker 里观察到的事实取回来。"""
    _put(
        root,
        rel,
        HDR
        + "import json, os, sys\n"
        + body
        + f"\nraise RuntimeError({PROBE!r} + json.dumps(OUT))\n",
    )
    with pytest.raises(engine_pool.WorkerError) as err:
        _build(root, rel)
    m = re.search(
        re.escape(PROBE) + r"(\{.*\})",
        str(err.value) + "\n" + (err.value.args[1] if len(err.value.args) > 1 else ""),
    )
    assert m, str(err.value)
    return json.loads(m.group(1))


def _n(p: str) -> str:
    return os.path.normcase(os.path.realpath(p))


# --------------------------------------------------------------------------- 真实 worker
@needs_worker
def test_sys_path_order_script_dir_first_project_root_last(proj):
    _put(proj.root, "lab_style.py", "X = 1\n")
    out = _probe(proj.root, "paper/figures/sho.py", "OUT = {'path': sys.path}\n")
    path = [_n(p) for p in out["path"] if p]
    script_dir, root, site = (
        _n(str(proj.root / "paper" / "figures")),
        _n(str(proj.root)),
        _n(str(proj.site)),
    )
    assert path[0] == script_dir  # 与 `python 脚本.py` 相同
    assert path[-1] == root  # 追加在最后
    assert path.index(site) < path.index(root)  # 站点内容（这里是 PYTHONPATH 目录）先于项目根
    assert path.count(root) == 1


@needs_worker
def test_script_at_project_root_has_a_single_entry(proj):
    out = _probe(proj.root, "fig.py", "OUT = {'path': sys.path}\n")
    path = [_n(p) for p in out["path"] if p]
    assert path[0] == _n(str(proj.root)) and path.count(_n(str(proj.root))) == 1


@needs_worker
def test_installed_package_wins_over_the_project_root_copy(proj):
    # 真实形状：项目根里整份库源码（import 就炸），环境里装着能用的同名包
    _put(proj.root, "pkgx/__init__.py", "raise ImportError('unbuilt source tree')\n")
    _put(proj.site, "pkgx/__init__.py", "WHERE = 'installed'\n")
    out = _probe(proj.root, "paper/figures/sho.py", "import pkgx\nOUT = {'where': pkgx.WHERE}\n")
    assert out["where"] == "installed"


@needs_worker
def test_project_root_copy_is_used_when_nothing_is_installed(proj):
    _put(proj.root, "pkgx/__init__.py", "WHERE = 'project_root'\n")
    out = _probe(proj.root, "paper/figures/sho.py", "import pkgx\nOUT = {'where': pkgx.WHERE}\n")
    assert out["where"] == "project_root"


@needs_worker
def test_root_helper_module_is_still_importable_from_a_subdir_script(proj):
    _put(proj.root, "lab_style.py", "WHERE = 'root_helper'\n")
    out = _probe(
        proj.root,
        "paper/figures/sho.py",
        "import lab_style\nOUT = {'where': lab_style.WHERE}\n",
    )
    assert out["where"] == "root_helper"


@needs_worker
def test_script_dir_module_beats_everything(proj):
    _put(proj.root, "pkgx/__init__.py", "WHERE = 'project_root'\n")
    _put(proj.site, "pkgx/__init__.py", "WHERE = 'installed'\n")
    _put(proj.root, "paper/figures/pkgx/__init__.py", "WHERE = 'script_dir'\n")
    out = _probe(proj.root, "paper/figures/sho.py", "import pkgx\nOUT = {'where': pkgx.WHERE}\n")
    assert out["where"] == "script_dir"


# --------------------------------------------------------------------------- 静态模型与 worker 对拍
#: (名字, 布局: 相对项目根的文件, 是否在「已装」目录里也放一份, 真实 worker 里谁赢, 静态模型的档)
_CASES = [
    # 只有项目根里有、名字是标准库：标准库先（项目根排在它之后）
    ("sched", {"sched.py": "WHERE = 'root'\n"}, False, "stdlib", "stdlib"),
    # 脚本目录里有、名字是标准库：脚本目录在 sys.path[0]，遮蔽标准库
    ("sched", {"paper/figures/sched.py": "WHERE = 'script_dir'\n"}, False, "script_dir", "local"),
    # 项目根里的帮手模块，环境里没有同名包：本地
    ("helperx", {"helperx.py": "WHERE = 'root'\n"}, False, "root", "local"),
    # 项目根里的同名包 + 环境里装着：静态侧不读环境，判本地（两种结果都不需要装东西）；真实 worker 用已装的
    ("pkgx", {"pkgx/__init__.py": "WHERE = 'root'\n"}, True, "installed", "local"),
]


@needs_worker
@pytest.mark.parametrize(("name", "layout", "installed", "real_winner", "static"), _CASES)
def test_static_model_agrees_with_the_real_worker_order(
    proj, name, layout, installed, real_winner, static
):
    for rel, text in layout.items():
        _put(proj.root, rel, text)
    if installed:
        _put(proj.site, f"{name}/__init__.py", "WHERE = 'installed'\n")
    script = "paper/figures/sho.py"
    out = _probe(
        proj.root,
        script,
        f"import {name} as m\nOUT = {{'file': getattr(m, '__file__', '') or ''}}\n",
    )
    real_file = _n(out["file"]) if out["file"] else ""
    roots = {
        "script_dir": _n(str(proj.root / "paper" / "figures")),
        "installed": _n(str(proj.site)),
        "root": _n(str(proj.root)),  # 最后判：脚本目录在项目根之内
    }
    where = next((k for k, v in roots.items() if real_file.startswith(v + os.sep)), "stdlib")
    assert where == real_winner  # 真实 worker 里谁赢
    cls = {c.module: c for c in importscan.scan(proj.root, script).classes}[name]
    if static == "stdlib":
        assert cls.bucket == importscan.BUCKET_STDLIB
    else:
        assert cls.bucket == importscan.BUCKET_LOCAL and not cls.needed


# --------------------------------------------------------------------------- importscan（纯静态）
def test_project_root_file_cannot_shadow_the_stdlib_but_script_dir_file_can(tmp_path):
    _put(tmp_path, "sched.py", "x = 1\n")
    _put(tmp_path, "paper/fig.py", "import sched\n")
    c = {c.module: c for c in importscan.scan(tmp_path, "paper/fig.py").classes}["sched"]
    assert c.bucket == importscan.BUCKET_STDLIB and c.shadowing == importscan.BUCKET_STDLIB
    assert "local_file_never_imported" in c.warnings
    _put(tmp_path, "paper/sched.py", "x = 1\n")
    c2 = {c.module: c for c in importscan.scan(tmp_path, "paper/fig.py").classes}["sched"]
    assert c2.bucket == importscan.BUCKET_LOCAL and c2.shadowing == "stdlib"


# --------------------------------------------------------------------------- importshadow
_TB = (
    "Traceback (most recent call last):\n"
    '  File "{root}/paper/figures/sho.py", line 8, in <module>\n'
    "    import celerite\n"
    '  File "{pkgfile}", line 3, in <module>\n'
    "    from .celerite_version import __version__\n"
    "ModuleNotFoundError: No module named 'celerite.celerite_version'"
)


def _half_package(base: Path) -> Path:
    return _put(base, "celerite/__init__.py", "from .celerite_version import __version__\n")


def test_root_package_is_a_shadow_only_with_a_frame_inside_it(tmp_path):
    init = _half_package(tmp_path)
    tb = _TB.format(root=tmp_path, pkgfile=init)
    fact = importshadow.detect(tb, [tmp_path / "paper" / "figures"], late_roots=[tmp_path])
    assert fact and fact["search_order"] == "after_site" and "installed packages" in fact["note"]
    assert "Do not install it" not in fact["note"]
    # 只有一句消息 / 帧来自别处（已装的包）：项目根排在 site-packages 之后，说明不了什么
    msg = "ModuleNotFoundError: No module named 'celerite.celerite_version'"
    assert importshadow.detect(msg, [tmp_path / "paper" / "figures"], late_roots=[tmp_path]) is None
    other = _TB.format(root=tmp_path, pkgfile="/venv/lib/site-packages/celerite/__init__.py")
    assert (
        importshadow.detect(other, [tmp_path / "paper" / "figures"], late_roots=[tmp_path]) is None
    )


def test_script_dir_package_is_still_a_shadow_without_frame_evidence(tmp_path):
    figs = tmp_path / "paper" / "figures"
    _half_package(figs)
    msg = "ModuleNotFoundError: No module named 'celerite.celerite_version'"
    fact = importshadow.detect(msg, [figs], late_roots=[tmp_path])
    assert fact and fact["search_order"] == "before_site" and "Do not install it" in fact["note"]


def test_script_dir_beats_project_root_when_both_have_the_package(tmp_path):
    figs = tmp_path / "paper" / "figures"
    _half_package(tmp_path)
    _half_package(figs)
    msg = "ModuleNotFoundError: No module named 'celerite.celerite_version'"
    fact = importshadow.detect(msg, [figs], late_roots=[tmp_path])
    assert fact and fact["search_order"] == "before_site" and fact["project_path"] == "celerite/"
